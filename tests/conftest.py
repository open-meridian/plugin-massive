"""What the tests share: the lake's half on the SDK's `Recorder`, Massive on the
built-in synthetic exchange, a clock the tests set, the deployment's records
as the instrument store would answer them, and its US equity calendar."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from meridian.edge import RawStore
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder

from massive.convert import NEW_YORK
from massive.lake import Lake, Products
from massive.settings import REAL_TIME
from massive.source import MassiveSource
from massive.synthetic import SyntheticTransport, exchange
from massive.vendor import NS, Massive, RateLimiter

SPY, BRK, TQQQ, ISIN_ONLY = "LCL-SPY", "LCL-BRK", "LCL-TQQQ", "LCL-ISIN"
OTHER = "snaptrade"

#: The deployment's records, as ResolveInstrument answers them: SPY with this
#: source's symbol, BRK.B and TQQQ with another source's (TQQQ as a
#: custodian's book holds it, beside its CUSIP), and one record with no
#: symbol at all.
RECORDS: dict[str, list[ops.Identifier]] = {
    SPY: [ops.Identifier(scheme="symbol", value="SPY", source="massive")],
    BRK: [ops.Identifier(scheme="symbol", value="BRK.B", source=OTHER)],
    TQQQ: [
        ops.Identifier(scheme="cusip", value="74347X831"),
        ops.Identifier(scheme="symbol", value="TQQQ", source=OTHER),
    ],
    ISIN_ONLY: [ops.Identifier(scheme="isin", value="US0000000001")],
}


def at(day: str, hour: int, minute: int = 0) -> int:
    """A New York wall-clock moment, in UTC nanoseconds."""
    moment = dt.datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=NEW_YORK)
    return int(moment.timestamp()) * NS


class Clock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def answer_records(recorder: Recorder, records: Mapping[str, Sequence[ops.Identifier]]) -> None:
    """The instrument store's answer to ResolveInstrument, from `records`; and
    ResolveIdentifier's: one identifier in common with a record is that
    record, else a new one minted."""

    def reply(params: Any) -> ops.ResolveInstrumentResult:
        held = records.get(params.instrument_id)
        if held is None:
            return ops.ResolveInstrumentResult(found=False)
        return ops.ResolveInstrumentResult(
            found=True,
            instrument=ops.InstrumentRecord(
                instrument_id=params.instrument_id, identifiers=list(held), currency="USD"
            ),
        )

    recorder.answer("ResolveInstrument", reply)

    def joined(params: Any) -> ops.ResolveIdentifierResult:
        for entity, held in records.items():
            for given in params.identifiers:
                if any(
                    (h.scheme, h.value, h.source) == (given.scheme, given.value, given.source)
                    for h in held
                ):
                    return ops.ResolveIdentifierResult(found=True, instrument_id=entity)
        return ops.ResolveIdentifierResult(found=True, instrument_id="LCL-minted", minted=True)

    recorder.answer("ResolveIdentifier", joined)


def answer_calendar(recorder: Recorder, closed: Sequence[str] = ()) -> None:
    """The deployment's US equity calendar: weekends closed, and `closed`."""

    def reply(params: Any) -> ops.ReadCalendarResult:
        return ops.ReadCalendarResult(
            found=True,
            calendar_id=params.calendar_id,
            weekends=[ops.CalendarWeekend(weekdays=[ops.WEEKDAY_SATURDAY, ops.WEEKDAY_SUNDAY])],
            days=[ops.CalendarDay(date=day, kind=ops.CALENDAR_DAY_KIND_CLOSED) for day in closed],
        )

    recorder.answer("ReadCalendar", reply)


def no_wait(massive: Massive) -> Massive:
    for asset in ("stocks", "options"):
        massive.limiters[asset] = RateLimiter(per_minute=6_000_000, sleep=lambda _: None)
    return massive


def synthetic_lake(
    recorder: Any,
    clock: Clock,
    tmp: Path,
    held: Mapping[str, Any] | None = None,
    stocks: str = REAL_TIME,
    options: str = REAL_TIME,
) -> Lake:
    store = RawStore(tmp / "raw", kind="responses")
    massive = no_wait(
        Massive(
            store,
            None,
            transport=SyntheticTransport(held if held is not None else exchange(), clock_ns=clock),
            clock_ns=clock,
            keyless=True,
            sleep=lambda _: None,
        )
    )
    source = MassiveSource(massive, synthetic=True)
    lake = Lake(recorder, source, Products(tmp / "products.json"), clock)
    lake.use_plan(stocks, options)
    return lake


def want(
    *subjects: str,
    dataset: str = "suite-plugin:daily",
    data_type: str = "meridian.v1.Price",
    kinds: Sequence[ops.PriceKind] = (),
    business_date: str = "",
    standing: bool = False,
    want_id: str = "WNT-1",
    interval_ns: int = 0,
    expiration_from: str = "",
    expiration_to: str = "",
    series_keys: Sequence[str] = (),
    valid_from_ns: int = 0,
    valid_until_ns: int = 0,
) -> ops.ObservationsWantedEvent:
    return ops.ObservationsWantedEvent(
        want_id=want_id,
        dataset=dataset,
        data_type=data_type,
        subjects=[ops.SubjectRef(entity_id=s) for s in subjects],
        kinds=list(kinds),
        business_date=business_date,
        standing=standing,
        interval_ns=interval_ns,
        expiration_from=expiration_from,
        expiration_to=expiration_to,
        series_keys=[ops.Identifier(scheme="occ_osi", value=k) for k in series_keys],
        valid_from_ns=valid_from_ns,
        valid_until_ns=valid_until_ns,
    )


def calls(recorder: Recorder, name: str) -> list[Any]:
    """Each call of `name` the plugin made, its params."""
    return recorder.on(name)


#: The reasons the lake's DeclineWant takes (meridian.v1.DeclineWantRequest.reason;
#: core's lake refuses any other as a handler error).
DECLINE_REASONS = frozenset(
    {
        ops.UNANSWERED_REASON_NOT_COVERED,
        ops.UNANSWERED_REASON_SOURCE_SILENT,
        ops.UNANSWERED_REASON_BEYOND_HISTORY,
    }
)


@pytest.fixture(autouse=True)
def declines_the_lake_takes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test's Recorder refuses a decline the lake refuses, as the lake
    does: the SDK's Recorder answers any reason."""
    from meridian.errors import CallFailed

    ordinary = Recorder._operate

    async def operate(self: Recorder, method: Any, params: Any) -> Any:
        if str(method) == "DeclineWant" and params.reason not in DECLINE_REASONS:
            reason = ops.UnansweredReason.Name(params.reason)
            raise CallFailed(
                "DeclineWant",
                "handler error",
                f"reason {reason}: not_covered, source_silent or beyond_history",
            )
        return await ordinary(self, method, params)

    monkeypatch.setattr(Recorder, "_operate", operate)


@pytest.fixture
def restore_parse() -> Any:
    import massive.vendor as vendor

    held = vendor.parse
    yield
    vendor.parse = held


Parse = Callable[[str], Any]

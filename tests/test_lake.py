"""The lake's half (lake.py, chains.py) beyond the suite: each kind recorded
into the dataset its plan serves, and a want the plan does not serve
declined; a chain read for the expirations or series a want names, its open
interest dated by the calendar or not recorded; option trades streamed and
recorded under their underlying, keyed by series."""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path
from typing import Any

from meridian.errors import MeridianError
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.suites import Recorder

from massive import convert
from massive.chains import CALENDAR_RETRY_MOST_SECONDS, NS
from massive.settings import DELAYED, END_OF_DAY, REAL_TIME
from massive.synthetic import fridays

from conftest import (
    RECORDS,
    SPY,
    Clock,
    answer_calendar,
    answer_records,
    at,
    calendar,
    synthetic_lake,
    want,
)

MONDAY = at("2026-10-12", 11)
FIRST, SECOND = fridays(dt.date(2026, 10, 12))


def lake(
    tmp: Path, stocks: str = REAL_TIME, options: str = REAL_TIME, calendar: bool = True
) -> Any:
    recorder = Recorder()
    answer_records(recorder, RECORDS)
    if calendar:
        answer_calendar(recorder)
    return recorder, synthetic_lake(recorder, Clock(MONDAY), tmp, stocks=stocks, options=options)


def _given(event: ops.ObservationsWantedEvent) -> Any:
    return lambda _: event


def hear(recorder: Recorder, held: Any, *events: ops.ObservationsWantedEvent) -> None:
    async def go() -> None:
        for event in events:
            recorder.answer("ObservationsWanted", _given(event))
            await recorder.receive(observations_wanted=held.on_want)

    asyncio.run(go())


def declined(recorder: Recorder) -> list[str]:
    return [ops.UnansweredReason.Name(d.reason) for d in recorder.on("DeclineWant")]


def rows(recorder: Recorder, row: str, field: str) -> list[Any]:
    return [r for sent in recorder.on(row) for r in getattr(sent, field)]


def test_a_delayed_plan_records_into_the_delayed_datasets(tmp_path: Path) -> None:
    recorder, held = lake(tmp_path, stocks=DELAYED)
    hear(recorder, held, want(SPY, dataset="suite-plugin:live_delayed"))
    (price, *_) = rows(recorder, "RecordPrices", "prices")
    assert price.meta.source.dataset == "suite-plugin:live_delayed"
    assert held.dataset("trades") == "suite-plugin:trades_delayed"


def test_a_want_the_plan_does_not_serve_is_declined(tmp_path: Path) -> None:
    recorder, held = lake(tmp_path, stocks=DELAYED)
    hear(
        recorder,
        held,
        want(SPY, dataset="suite-plugin:live"),
        want(SPY, dataset="suite-plugin:live_delayed", data_type="meridian.v1.Quote"),
        want(SPY, dataset="suite-plugin:trades", data_type="meridian.v1.Trade", standing=True),
    )
    assert declined(recorder) == ["UNANSWERED_REASON_NOT_COVERED"] * 3
    assert recorder.on("RecordPrices") == [] and held.streamed == []


def test_an_end_of_day_plan_serves_closes_alone(tmp_path: Path) -> None:
    recorder, held = lake(tmp_path, stocks=END_OF_DAY, options=END_OF_DAY)
    hear(
        recorder,
        held,
        want(SPY, business_date="2026-10-08"),
        want(SPY, dataset="suite-plugin:live_delayed"),
        want(SPY, dataset="suite-plugin:options_delayed", data_type="meridian.v1.Statistic"),
        want(SPY, dataset="suite-plugin:options_delayed", data_type="meridian.v1.ListedSeries"),
    )
    assert len(rows(recorder, "RecordPrices", "prices")) == 1
    assert declined(recorder) == ["UNANSWERED_REASON_NOT_COVERED"] * 2
    series = rows(recorder, "RecordSeries", "series")
    assert series and recorder.on("RecordStatistics") == [] and recorder.on("RecordQuotes") == []
    assert not held.streaming


def test_a_chain_is_read_for_the_expirations_a_want_names(tmp_path: Path) -> None:
    recorder, held = lake(tmp_path)
    hear(
        recorder,
        held,
        want(
            SPY,
            dataset="suite-plugin:options",
            data_type="meridian.v1.Quote",
            expiration_from=SECOND.isoformat(),
            expiration_to=SECOND.isoformat(),
        ),
    )
    asked = [u for u in held.source.massive.transport.asked if "/v3/snapshot/options/" in u]
    assert asked and all(f"expiration_date={SECOND.isoformat()}" in u for u in asked)
    quotes = rows(recorder, "RecordQuotes", "quotes")
    keys = {q.meta.series_key[0].value for q in quotes}
    assert keys and all(k[6:12] == f"{SECOND:%y%m%d}" for k in keys)
    assert all(q.meta.subjects[0].entity_id == SPY for q in quotes)
    assert recorder.on("ResolveIdentifier") == []


def test_a_series_want_records_the_terms_named(tmp_path: Path) -> None:
    recorder, held = lake(tmp_path)
    osi = f"SPY   {FIRST:%y%m%d}P00670000"
    adjusted = f"SPY1  {FIRST:%y%m%d}C00670000"
    hear(
        recorder,
        held,
        want(
            SPY,
            dataset="suite-plugin:options",
            data_type="meridian.v1.ListedSeries",
            series_keys=[osi, adjusted],
        ),
    )
    series = {s.meta.row_key: s for s in rows(recorder, "RecordSeries", "series")}
    assert set(series) == {osi, adjusted}
    terms = {f.path: f.value for f in series[osi].fields}
    assert terms["put_or_call"] == "PUT_OR_CALL_PUT" and terms["strike_price"] == "670 USD"
    assert terms["settlement_method"] == "SETTLEMENT_METHOD_PHYSICAL"
    assert (
        terms["deliverable[0].instrument_id"] == SPY and terms["deliverable[0].quantity"] == "100"
    )
    assert series[osi].template_id == "TPL-listed-option"
    # An adjusted contract states its deliverables by symbol: none recorded.
    assert not any(f.path.startswith("deliverable") for f in series[adjusted].fields)
    assert recorder.on("RecordQuotes") == []


def test_open_interest_is_dated_the_market_day_before(tmp_path: Path) -> None:
    recorder, held = lake(tmp_path)
    recorder.answer(
        "ReadCalendar",
        lambda p: ops.ReadCalendarResult(
            found=True,
            weekends=[ops.CalendarWeekend(weekdays=[ops.WEEKDAY_SATURDAY, ops.WEEKDAY_SUNDAY])],
            days=[ops.CalendarDay(date="2026-10-09", kind=ops.CALENDAR_DAY_KIND_CLOSED)],
        ),
    )
    hear(
        recorder, held, want(SPY, dataset="suite-plugin:options", data_type="meridian.v1.Statistic")
    )
    stats = rows(recorder, "RecordStatistics", "statistics")
    interest = {s.meta.business_date for s in stats if s.kind == ops.STATISTIC_KIND_OPEN_INTEREST}
    volume = {s.meta.business_date for s in stats if s.kind == ops.STATISTIC_KIND_VOLUME}
    # Friday the 9th closed: Monday's open interest is Thursday's.
    assert interest == {"2026-10-08"} and volume == {"2026-10-12"}


def test_no_calendar_no_open_interest(tmp_path: Path) -> None:
    recorder, held = lake(tmp_path, calendar=False)
    recorder.answer("ReadCalendar", lambda p: ops.ReadCalendarResult(found=False))
    hear(
        recorder, held, want(SPY, dataset="suite-plugin:options", data_type="meridian.v1.Statistic")
    )
    stats = rows(recorder, "RecordStatistics", "statistics")
    assert stats and all(s.kind == ops.STATISTIC_KIND_VOLUME for s in stats)
    assert "was not found" in held.chains.calendar_error


def test_a_failed_calendar_read_is_tried_again_after_a_backoff(tmp_path: Path) -> None:
    recorder, held = lake(tmp_path, calendar=False)
    failing = [True]

    def reply(params: Any) -> Any:
        if failing[0]:
            raise MeridianError("the sidecar did not answer")
        return calendar(params)

    recorder.answer("ReadCalendar", reply)
    chains, clock = held.chains, held.clock_ns
    friday = dt.date(2026, 10, 9)

    def is_open() -> bool | None:
        held_open: bool | None = asyncio.run(chains.is_open(friday))
        return held_open

    assert is_open() is None and "was not read" in chains.calendar_error
    assert len(recorder.on("ReadCalendar")) == 1
    clock.now += 10 * NS  # within the first backoff: not read again
    assert is_open() is None and len(recorder.on("ReadCalendar")) == 1
    clock.now += 21 * NS  # past it: read again, failing again
    assert is_open() is None and len(recorder.on("ReadCalendar")) == 2
    clock.now += 31 * NS  # the backoff doubled to a minute
    assert is_open() is None and len(recorder.on("ReadCalendar")) == 2
    failing[0] = False
    clock.now += 30 * NS
    assert is_open() is True and len(recorder.on("ReadCalendar")) == 3
    assert chains.calendar_error == "" and chains.calendar_failures == 0


def test_the_calendar_backoff_is_bounded(tmp_path: Path) -> None:
    _, held = lake(tmp_path)
    chains = held.chains
    for _ in range(40):
        chains._calendar_failed("the calendar was not read")
    assert chains.calendar_retry_ns - held.clock_ns() == CALENDAR_RETRY_MOST_SECONDS * NS


def test_a_delayed_options_plan_records_no_quote(tmp_path: Path) -> None:
    recorder, held = lake(tmp_path, options=DELAYED)
    hear(
        recorder,
        held,
        want(SPY, dataset="suite-plugin:options_delayed", data_type="meridian.v1.Statistic"),
        want(
            SPY, dataset="suite-plugin:options_delayed", data_type="meridian.v1.Quote", want_id="W2"
        ),
        want(SPY, dataset="suite-plugin:options", data_type="meridian.v1.Statistic", want_id="W3"),
    )
    assert recorder.on("RecordQuotes") == []
    stats = rows(recorder, "RecordStatistics", "statistics")
    assert stats and {s.meta.source.dataset for s in stats} == {"suite-plugin:options_delayed"}
    assert declined(recorder) == ["UNANSWERED_REASON_NOT_COVERED"] * 2


def test_option_trades_are_streamed_for_the_series_wanted(tmp_path: Path) -> None:
    recorder, held = lake(tmp_path)
    osi = f"SPY   {FIRST:%y%m%d}C00670000"
    hear(
        recorder,
        held,
        want(
            SPY,
            dataset="suite-plugin:options",
            data_type="meridian.v1.Trade",
            standing=True,
            series_keys=[osi],
            want_id="WNT-T",
        ),
    )
    ticker = convert.ticker_of(osi)
    assert held.chains.streamed == [ticker]
    stated = [
        *convert.streamed(
            {
                "ev": "T",
                "sym": ticker,
                "x": 302,
                "p": 8,
                "s": 3,
                "c": [219],
                "t": MONDAY // 10**6,
                "q": 7,
            },
            "stream#0",
        ),
        *convert.streamed(
            {
                "ev": "T",
                "sym": ticker,
                "x": 302,
                "p": 8,
                "s": 3,
                "c": [233],
                "t": MONDAY // 10**6,
                "q": 8,
            },
            "stream#1",
        ),
    ]

    async def go() -> None:
        await held.chains.heard(stated)
        await held.chains.heard(stated)  # heard twice: recorded once
        await held.chains.flush(force=True)

    asyncio.run(go())
    trades = rows(recorder, "RecordTrades", "trades")
    assert len(trades) == 2
    first, second = trades
    assert first.meta.series_key[0].value == osi and first.meta.subjects[0].entity_id == SPY
    assert first.meta.source.dataset == "suite-plugin:options" and first.source_sequence == 7
    assert first.aggressor == ops.AGGRESSOR_UNSPECIFIED
    named = ops.TradeCharacteristic.Name
    assert [named(c) for c in first.attributes.characteristics] == [
        "TRADE_CHARACTERISTIC_INTERMARKET_SWEEP",
        "TRADE_CHARACTERISTIC_OTHER_AUCTION",
    ]
    assert [named(c) for c in second.attributes.characteristics] == [
        "TRADE_CHARACTERISTIC_MULTI_LEG"
    ]
    assert [u.code for u in first.meta.unconverted] == ["302"]

    async def withdraw() -> None:
        recorder.answer(
            "WantWithdrawn", lambda _: [ops.WantWithdrawnEvent(want_id="WNT-T", dataset="x")]
        )
        await recorder.receive(want_withdrawn=held.on_withdrawn)

    asyncio.run(withdraw())
    assert held.chains.streamed == []


def test_a_range_of_option_trades_is_not_read(tmp_path: Path) -> None:
    recorder, held = lake(tmp_path)
    hear(recorder, held, want(SPY, dataset="suite-plugin:options", data_type="meridian.v1.Trade"))
    assert declined(recorder) == ["UNANSWERED_REASON_NOT_COVERED"]


def test_a_standing_chain_is_kept_current_once_a_minute(tmp_path: Path) -> None:
    recorder, held = lake(tmp_path)
    hear(
        recorder,
        held,
        want(
            SPY,
            dataset="suite-plugin:options",
            data_type="meridian.v1.Quote",
            standing=True,
            expiration_from=FIRST.isoformat(),
            expiration_to=FIRST.isoformat(),
        ),
    )
    asked = held.source.massive.transport.asked

    def reads() -> int:
        return len([u for u in asked if "/v3/snapshot/options/" in u])

    first = reads()
    asyncio.run(held.chains.refresh())
    assert reads() == first  # not yet a minute
    held.clock_ns.now += 61 * 10**9
    asyncio.run(held.chains.refresh())
    assert reads() > first
    # Unchanged quotes are not recorded again; the liveness is.
    assert len(recorder.on("RecordLiveness")) >= 2


def test_the_stream_is_caught_up_after_a_reconnect(tmp_path: Path) -> None:
    recorder, held = lake(tmp_path)
    clock = held.clock_ns
    hear(
        recorder,
        held,
        want(SPY, dataset="suite-plugin:trades", data_type="meridian.v1.Trade", standing=True),
    )
    since = {"SPY": clock.now - 120 * 10**9}
    # The history's four trades since the last heard, then none again.
    assert asyncio.run(held.caught_up(since)) == 4
    assert asyncio.run(held.caught_up(since)) == 0
    keys = [t.meta.row_key for t in rows(recorder, "RecordTrades", "trades")]
    assert keys[0] == "SPY:t:2026-10-12:4101@LCL-SPY" and len(keys) == len(set(keys)) == 4

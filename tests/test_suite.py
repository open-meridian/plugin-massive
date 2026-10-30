"""The data suite, the SDK's own (meridian.suites), run unchanged against this
plugin: every case mapped to the plugin's own conversion, from Massive's
synthetic exchange and stream, through the lake's half it runs in production
(lake.py, chains.py), against the suite's `Recorder`.

Presented: closes, bars (with Massive's VWAP and trade count), subjects and
venues; trades streamed with their conditions converted from Massive's
rules, an odd lot among them, and a code the table does not hold kept as
reported; quotes, two-sided and one-sided; a restart's gap backfilled from
Massive's trade history, which names each trade by the sequence number a
streamed one is keyed by; the licences read; the calendar read, which dates
a chain's open interest; the bounded lake query; the dataset's liveness;
the feed delay; the chain's series, open interest and quotes, never
resolved; a crossed quote recorded invalid and a quote sent again under a
new sequence number recorded suspicious, by the edge's checks.

Not presented, each with why (`NOT_PRESENTED`): a currency's rate and a
crypto asset's price (Massive's other feeds, another plugin's); an
aggressor (Massive's prints state none); a withdrawn trade (Massive's
stream sends no cancel); a settlement date; outcomes and event groups; the
rates and filings slices, holdings, dividends and ratings (feeds this
version does not read); and rows after a sequence gap: Massive's sequence
numbers increase per ticker but skip by design, so a skip is no gap.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from meridian.plugin.v1 import operations_pb2 as ops
from meridian.roles.data.testing import RATES_CASES, feed_delay, lake_query
from meridian.suites import Recorder, run

from massive import convert
from massive.lake import LIVE_KINDS, Standing
from massive.synthetic import exchange

from conftest import (
    BRK,
    ISIN_ONLY,
    RECORDS,
    SPY,
    TQQQ,
    Clock,
    answer_calendar,
    answer_records,
    at,
    synthetic_lake,
    want,
)

DAY = "2026-10-08"
#: Monday morning after the synthetic week: every day of it final.
AFTER = at("2026-10-12", 9)
#: Friday 2026-10-09, 15:00 New York: the session still open.
DURING = at("2026-10-09", 15)

Producer = Callable[[Recorder], Awaitable[None]]

NOT_PRESENTED: dict[str, str] = {
    "an-fx-rate": "Massive's stock and option feeds state no currency's rate",
    "a-stablecoin-quote": "Massive's stock and option feeds state no crypto asset's price",
    "the-takers-side-stated": "Massive's prints never state which side took liquidity",
    "the-makers-side-inverted": "Massive's prints state neither side of a trade",
    "a-trade-withdrawn": (
        "Massive's stream sends no cancel; its history's correction indicator is kept as reported"
    ),
    "asks-for-a-settlement-date": (
        "records bars and trades as Massive publishes them; never computes a settlement date"
    ),
    "records-an-outcome": "Massive's stock and option feeds list no event contract",
    "restates-a-disputed-outcome": "Massive's stock and option feeds list no event contract",
    "records-a-partial-payout": "Massive's stock and option feeds list no event contract",
    "reports-an-event-group": "Massive's stock and option feeds list no event contract",
    "reports-a-flag-the-venue-does-not-state": (
        "Massive's stock and option feeds list no event contract"
    ),
    **{case: "this version reads no rates" for case in RATES_CASES},
    **{
        case: "this version reads no filings"
        for case in ("a-filing-recorded", "a-fact-restated", "a-fact-confirmed", "resolves-a-filer")
    },
    **{
        case: "this version reads no holdings report"
        for case in ("a-complete-holdings-report", "a-cusip-used-as-key", "a-stake-crossed")
    },
    "a-dividend-declared": "this version reads no corporate action",
    "a-rating-action": "Massive's stock and option feeds state no rating",
    "rows-after-a-gap-are-suspicious": (
        "Massive's sequence numbers increase per ticker but skip by design: a skip is no gap"
    ),
}

#: A moment in the session, for the stream's events: epoch milliseconds.
LIVE_MS = DURING // 1_000_000 - 60_000


def event(**fields: Any) -> dict[str, Any]:
    """One of Massive's stream events, its prices Decimals as parsed."""
    said: dict[str, Any] = {"t": LIVE_MS, **fields}
    for name in ("p", "bp", "ap"):
        if isinstance(said.get(name), str):
            said[name] = Decimal(said[name])
    return said


def producers(tmp: Path) -> dict[str, Producer]:
    def lake(recorder: Recorder, now: int = AFTER):  # type: ignore[no-untyped-def]
        answer_records(recorder, RECORDS)
        answer_calendar(recorder)
        return synthetic_lake(recorder, Clock(now), tmp / uuid.uuid4().hex)

    async def wanted(
        recorder: Recorder, event: ops.ObservationsWantedEvent, now: int = AFTER
    ) -> Any:
        held = lake(recorder, now)
        recorder.answer("ObservationsWanted", lambda _: event)
        await recorder.receive(observations_wanted=held.on_want)
        return held

    async def daily_close(recorder: Recorder) -> None:
        # The plugin resolves a listing by its own symbol, then records the
        # close the lake wants for it.
        held = lake(recorder)
        await held.seed(["BRK.B"])
        (subject,) = held.products.symbols
        recorder.answer("ObservationsWanted", lambda _: want(subject, business_date=DAY))
        await recorder.receive(observations_wanted=held.on_want)

    async def forming(recorder: Recorder) -> None:
        clock = Clock(DURING)
        answer_records(recorder, RECORDS)
        held = synthetic_lake(recorder, clock, tmp / "forming")
        await held.daily([SPY], dt.date(2026, 10, 9), dt.date(2026, 10, 9))
        clock.now = at("2026-10-09", 16, 30)
        await held.tick()

    async def exact(recorder: Recorder) -> None:
        # TQQQ's close on 2026-10-09 is stated to eighteen places.
        await wanted(recorder, want(TQQQ, business_date="2026-10-09"))

    async def bar(recorder: Recorder) -> None:
        # A day Massive states with no VWAP: BRK.B's 2026-10-08, its `vw`
        # left out.
        held = exchange()
        held["daily"]["BRK.B"] = [
            {k: v for k, v in day.items() if k != "vw"} for day in held["daily"]["BRK.B"]
        ]
        answer_records(recorder, RECORDS)
        made = synthetic_lake(recorder, Clock(AFTER), tmp / uuid.uuid4().hex, held=held)
        asked = want(BRK, data_type="meridian.v1.Bar", business_date=DAY)
        recorder.answer("ObservationsWanted", lambda _: asked)
        await recorder.receive(observations_wanted=made.on_want)

    async def streamed(recorder: Recorder, *messages: dict[str, Any], subject: str = SPY) -> Any:
        # A standing want of the trades: the listing streamed, and what the
        # stream states recorded, as the plugin's stream hands it on.
        held = lake(recorder, DURING)
        asked = want(
            subject, dataset="suite-plugin:trades", data_type="meridian.v1.Trade", standing=True
        )
        recorder.answer("ObservationsWanted", lambda _: asked)
        await recorder.receive(observations_wanted=held.on_want)
        for n, said in enumerate(messages):
            await held.heard(convert.streamed(said, f"stream/suite#{n}"))
            await held.flush(force=True)
        return held

    def trade(**fields: Any) -> dict[str, Any]:
        return event(ev="T", sym="SPY", x=12, z=1, **fields)

    def quote(**fields: Any) -> dict[str, Any]:
        return event(ev="Q", sym="SPY", bx=12, ax=10, z=1, **fields)

    async def converted(recorder: Recorder) -> None:
        # Massive's code 37, an odd lot: its rules say not the high and low,
        # not the open and close, the volume counted.
        await streamed(recorder, trade(p="673.81", s=7, ds="7.0", c=[37], q=4102, pt=LIVE_MS - 1))

    async def not_converted(recorder: Recorder) -> None:
        # A code Massive's table does not hold: kept as reported.
        await streamed(recorder, trade(p="673.83", s=50, c=[999], q=4104))

    async def standing_streamed(recorder: Recorder) -> None:
        await streamed(recorder, trade(p="673.88", s=100, c=[], q=4105, pt=LIVE_MS - 1))

    async def quoted(recorder: Recorder) -> None:
        await streamed(recorder, quote(bp="673.87", bs=300, ap="673.89", **{"as": 200}, q=1))

    async def crossed(recorder: Recorder) -> None:
        await streamed(recorder, quote(bp="673.95", bs=300, ap="673.89", **{"as": 200}, q=1))

    async def duplicate(recorder: Recorder) -> None:
        # The same quote, sent again under a new sequence number.
        said = {"bp": "673.87", "bs": 300, "ap": "673.89", "as": 200}
        await streamed(recorder, quote(**said, q=1), quote(**said, q=2))

    async def one_sided(recorder: Recorder) -> None:
        # BRK.B's offer side left out.
        await streamed(
            recorder,
            event(ev="Q", sym="BRK.B", bx=10, bp="490.58", bs=100, q=7),
            subject=BRK,
        )

    async def alive(recorder: Recorder) -> None:
        # A status message, no price changed: the stream's liveness.
        await streamed(recorder, {"ev": "status", "status": "success", "message": "subscribed"})

    async def delayed(recorder: Recorder, latency: Any) -> None:
        # Quotes streamed for a standing want, each one's age at the edge
        # recorded, then reported.
        held = lake(recorder, DURING)
        held.latency = latency
        asked = want(SPY, dataset="suite-plugin:live", data_type="meridian.v1.Quote", standing=True)
        recorder.answer("ObservationsWanted", lambda _: asked)
        await recorder.receive(observations_wanted=held.on_want)
        await held.heard(
            convert.streamed(
                quote(bp="673.87", bs=300, ap="673.89", **{"as": 200}, q=3), "stream/suite#d"
            )
        )
        await held.flush(force=True)

    async def chain(recorder: Recorder) -> None:
        # SPY's chain wanted of `options`: its series, quotes and open
        # interest recorded under SPY, keyed by OSI symbol, the open
        # interest dated by the deployment's calendar.
        await wanted(
            recorder,
            want(SPY, dataset="suite-plugin:options", data_type="meridian.v1.Statistic"),
        )

    async def restarted(recorder: Recorder) -> None:
        # The plugin starts again; the lake holds the last trade of SPY it
        # recorded, two minutes ago, and Massive's history states trades
        # since. The first standing want of SPY's trades backfills them.
        now = DURING
        held = lake(recorder, now)
        last = ops.Trade(
            meta=ops.ObservationMeta(
                row_key="SPY:t:2026-10-09:4100@LCL-SPY",
                subjects=[ops.SubjectRef(entity_id=SPY)],
                source=ops.Source(dataset="suite-plugin:trades"),
                valid_from_ns=now - 120 * 10**9,
            )
        )
        recorder.answer("ListTrades", lambda _: ops.ListTradesResult(trades=[last]))
        asked = want(
            SPY, dataset="suite-plugin:trades", data_type="meridian.v1.Trade", standing=True
        )
        recorder.answer("ObservationsWanted", lambda _: asked)
        await recorder.receive(observations_wanted=held.on_want)

    async def ambiguous(recorder: Recorder) -> None:
        held = lake(recorder)
        recorder.answer(
            "ResolveIdentifier",
            lambda _: ops.ResolveIdentifierResult(
                found=False, miss_reason=ops.MISS_REASON_AMBIGUOUS
            ),
        )
        await held.seed(["TQQQ"])

    async def venue(recorder: Recorder) -> None:
        # SPY's last trade printed on the exchange Massive codes 12, resolved
        # by that code and its MIC.
        await wanted(recorder, want(SPY, dataset="suite-plugin:live"))

    async def venue_not_held(recorder: Recorder) -> None:
        held = lake(recorder)
        recorder.answer(
            "ResolveVenue",
            lambda _: ops.ResolveVenueResult(found=False, miss_reason=ops.MISS_REASON_NOT_FOUND),
        )
        recorder.answer("ObservationsWanted", lambda _: want(SPY, dataset="suite-plugin:live"))
        await recorder.receive(observations_wanted=held.on_want)

    async def recorded_against(recorder: Recorder) -> None:
        await wanted(recorder, want(SPY, business_date=DAY))

    async def elsewhere(recorder: Recorder) -> None:
        # TQQQ as a custodian's book holds it, by its CUSIP and the
        # custodian's symbol: the record read (W3.6), Massive's symbol found.
        await wanted(recorder, want(TQQQ, business_date=DAY))

    async def declined(recorder: Recorder) -> None:
        await wanted(recorder, want(ISIN_ONLY, business_date=DAY))

    async def licences(recorder: Recorder) -> None:
        from massive.__main__ import read_licences
        from massive.page import State

        recorder.answer(
            "ListDatasets",
            lambda _: ops.ListDatasetsResult(
                licences=[ops.DatasetLicence(dataset="suite-plugin:daily", kept=True)]
            ),
        )
        state = State()
        await read_licences(recorder, state)
        assert state.licensed == {"suite-plugin:daily"}

    async def withdrawn(recorder: Recorder) -> None:
        clock = Clock(AFTER)
        answer_records(recorder, RECORDS)
        held = synthetic_lake(recorder, clock, tmp / "withdrawn")
        held.standing["WNT-9"] = Standing("WNT-9", (SPY,), LIVE_KINDS, AFTER)
        recorder.answer(
            "WantWithdrawn",
            lambda _: [ops.WantWithdrawnEvent(want_id="WNT-9", dataset="suite-plugin:live")],
        )
        await recorder.receive(want_withdrawn=held.on_withdrawn)
        clock.now += 3600 * 10**9
        await held.tick()

    return {
        "a-daily-close": daily_close,
        "the-forming-day-restated": forming,
        "an-exact-price": exact,
        "a-daily-bar": bar,
        "an-asset-that-does-not-resolve": ambiguous,
        "a-venue-resolved": venue,
        "a-venue-not-held": venue_not_held,
        "a-want-recorded-against": recorded_against,
        "a-subject-another-source-resolved": elsewhere,
        "a-subject-declined": declined,
        "a-standing-want-withdrawn": withdrawn,
        "a-trade-recorded": standing_streamed,
        "a-condition-converted": converted,
        "a-condition-not-converted": not_converted,
        "a-quote-recorded": quoted,
        "a-one-sided-quote": one_sided,
        "a-standing-want-streamed": standing_streamed,
        "a-restarts-gap-backfilled": restarted,
        "reads-its-datasets-licences": licences,
        "reads-a-calendar": chain,
        "queries-the-lake-within-bounds": lake_query(SPY, "trades"),
        "records-a-datasets-liveness": alive,
        "reports-its-feed-delay": feed_delay(delayed),
        "records-a-chains-series": chain,
        "records-open-interest": chain,
        "a-chain-row-is-never-resolved": chain,
        "a-crossed-quote-is-recorded-invalid": crossed,
        "a-duplicate-is-recorded-suspicious": duplicate,
    }


def test_the_data_suite_passes_unchanged(tmp_path: Path) -> None:
    report = run("data", producers(tmp_path), not_presented=NOT_PRESENTED)
    assert report.passed, report.failures
    # Its 28 cases, and the three every suite holds that the SDK answers.
    assert len(report.passed_cases) == 31
    assert report.answered_by_sdk == [
        "hears-and-reads-a-replacement",
        "reads-the-template-catalogue",
        "hears-and-reads-an-event-group",
    ]
    assert sorted(report.not_presented) == sorted(NOT_PRESENTED)


def test_a_price_read_through_a_float_fails_the_suite(tmp_path: Path, restore_parse: None) -> None:
    import json

    import massive.vendor as vendor

    vendor.parse = lambda text: json.loads(text, parse_float=float)
    report = run("data", producers(tmp_path), not_presented=NOT_PRESENTED)
    assert "an-exact-price" in report.failures, report.failures

"""What the plugin records in the lake, and how it answers what the lake wants
(contracts v18 and v19, W10): the SDK's listings feed
(`meridian.roles.data.listings`) for the stock datasets, this module keeping
what is Massive's alone -- its source's name, its history's reach, its trade
conditions' rules, its sequence numbers, its exchanges' MICs and its two
clocks -- and chains.py for the option chains.

**One dataset per timeliness** (rulings 382 and 551): Massive's real-time
and 15-minute delayed plans are two datasets each, `live` and
`live_delayed`, `trades` and `trades_delayed`, `options` and
`options_delayed`, each with its declared delay. The feed records each kind
into the dataset the plan settings name (`use_plan`): the SDK's feed works
by its `live` and `trades` keys, and this module names the dataset in force.
A want of the other timeliness is declined, not covered: this plan does not
serve it; so is a want an end-of-day plan cannot answer (live prices,
trades), and one of quotes on a delayed plan, which includes none. Daily
closes are final at the close whatever the plan, so `daily` is one.

**A decline names a reason the lake takes**: `not_covered`,
`source_silent` or `beyond_history`.

**Trades caught up.** Massive numbers a listing's trades within a session
(the SIP's sequence number) and its trade history states the same number,
so a trade's row is keyed alike from the stream and the history: a range a
reader wants is read from the history, and the gap after a reconnect or a
restart caught up from it, a trade already recorded never sent again (the
SDK's feed).

**Two clocks** (rulings 368 to 371: every source clock kept): a trade's or
quote's SIP time is its `consolidated` source time and its time of record;
the participant's, where Massive states one, its `event` time.

**Venues by MIC** (W3.14): a price's or trade's exchange is resolved by
Massive's code at this source, and by its ISO 10383 MIC from Massive's
exchanges list, so a deployment's venue master matches it by MIC without
learning Massive's codes.

**The edge, done here for now** (contract v28, rulings 424 and 518): each
live price, quote and trade recorded unasked or for a standing want has its
feed delay recorded (`roles.Latency.aged`, the `edge` hop) and is judged by
the SDK's quality checks (`quality.EdgeChecks`). The SDK's listings feed
records neither in 0.33.3; this glue goes when it does.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence
from typing import Any

from meridian.operations import SourceTime
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.plugin.v1.operations_pb2 import Identifier
from meridian.roles.data import listings
from meridian.roles.data.listings import (
    BAR,
    DAILY,
    LIVE,
    LIVE_CADENCE_SECONDS,
    LIVE_KINDS,
    PRICE,
    QUOTE,
    TRADE,
    TRADES,
    Counts,
    Forming,
    Listings,
    Source,
    Standing,
    row_key,
)
from meridian.roles.data.quality import EdgeChecks

from .chains import OPTIONS, OPTIONS_DELAYED, Chains
from .conditions import Attributes, Conditions
from .convert import SOURCE, new_york_date
from .settings import DELAYED, END_OF_DAY, REAL_TIME

#: The live and trades kinds delayed 15 minutes (ruling 551).
LIVE_DELAYED = "live_delayed"
TRADES_DELAYED = "trades_delayed"

__all__ = [
    "BAR", "DAILY", "LIVE", "LIVE_DELAYED", "OPTIONS", "OPTIONS_DELAYED", "LIVE_CADENCE_SECONDS",
    "LIVE_KINDS", "PRICE", "QUOTE", "TRADE", "TRADES", "TRADES_DELAYED", "Counts", "Forming",
    "Lake", "Products", "Source", "Standing", "row_key",
]  # fmt: skip

#: The earliest day Massive's stock history reaches: 10 September 2003. How
#: far back a key reaches is its plan's (two years on Basic).
HISTORY_FROM = dt.date(2003, 9, 10)
#: The platform's US equity venues (NYSE markets) calendar, read from the
#: platform on 2026-10-14 (the v24 plan's O10), as the retail pair names it:
#: the daily and chain datasets' business dates are its open days.
US_EQUITY_CALENDAR = "CAL-01M4VF0F4XGP3RATCCSKDPZSBP"
#: Each kind's datasets, real time and delayed.
TIMED = {LIVE: LIVE_DELAYED, TRADES: TRADES_DELAYED, OPTIONS: OPTIONS_DELAYED}
UNTIMED = {delayed: kind for kind, delayed in TIMED.items()}
#: The conditions while no source is set: every code not known.
NO_CONDITIONS = Conditions()

#: Which listing each subject is, kept in the plugin's storage.
Products = Listings


class Lake(listings.Feed):
    """Massive's listings in the lake: the SDK's feed, Massive's prints."""

    source_name = SOURCE
    history_from = HISTORY_FROM
    calendar_id = US_EQUITY_CALENDAR

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: The option chains wanted, counted as one dataset.
        self.counts[OPTIONS] = listings.Counts()
        self.chains = Chains(self)
        #: What each asset class's plan serves (settings.py).
        self.plans = {"stocks": DELAYED, "options": DELAYED}
        #: The plugin's `roles.Latency`, where it keeps one, and the edge's
        #: checks.
        self.latency: Any = None
        self.quality = EdgeChecks(clock_ns=self.clock_ns)

    # ── Plans and names ──────────────────────────────────────────────────

    def timeliness(self, asset_class: str) -> str:
        return self.plans[asset_class]

    def use_plan(self, stocks: str, options: str) -> None:
        """The plans as the settings stand: on a change, the standing wants
        of the kinds whose dataset changed are let go, since they were asked
        of the other dataset; the lake asks again of this one."""
        before = dict(self.plans)
        self.plans = {"stocks": stocks, "options": options}
        if before["stocks"] != stocks:
            for want_id, standing in list(self.standing.items()):
                if standing.dataset in (LIVE, TRADES):
                    self.standing.pop(want_id, None)
            self.streaming = stocks != END_OF_DAY
            self.restream()
        if before["options"] != options:
            self.chains.reset()

    def named(self, key: str) -> str:
        """A declared dataset's ID in the deployment, by its own key."""
        return super().dataset(key)

    def in_force(self, key: str) -> str:
        """The dataset key a kind is recorded into now."""
        if key in (LIVE, TRADES) and self.plans["stocks"] != REAL_TIME:
            return TIMED[key]
        if key == OPTIONS and self.plans["options"] != REAL_TIME:
            return OPTIONS_DELAYED
        return key

    def dataset(self, key: str) -> str:
        """The dataset the feed's `key` is recorded into now."""
        return self.named(self.in_force(key))

    def _key_of(self, dataset: str) -> str:
        key = super()._key_of(dataset)
        return UNTIMED.get(key, key)

    # ── What is Massive's (listings.Feed) ────────────────────────────────

    def attributes(self, stated: Any) -> listings.Attributes:
        said = self._conditions("stocks", stated.conditions)
        return listings.Attributes(
            said.characteristics, said.consolidated, said.market_centre, said.unconverted
        )

    def option_attributes(self, codes: Sequence[int]) -> Attributes:
        return self._conditions("options", codes)

    def _conditions(self, asset_class: str, codes: Sequence[int]) -> Attributes:
        held = getattr(self.source, "conditions", None) or NO_CONDITIONS
        attributes: Attributes = held.attributes(asset_class, codes)
        return attributes

    def withdrawn_unrecorded(self, stated: Any) -> listings.Attributes | None:
        # Massive's stream states no cancel: never reached.
        return None

    def sequence_of(self, stated: Any) -> int | None:
        return int(stated.sequence) if getattr(stated, "sequence", None) is not None else None

    async def venue_of(self, code: str) -> tuple[str, list[ops.AsReported]]:
        """The venue master's ID for an exchange Massive printed: by its code
        at this source and, where Massive's exchanges list gives one, its
        MIC."""
        if not code:
            return "", []
        now = self.clock_ns()
        held = self._venues.get(code)
        if held is not None and (held[0] or now - held[1] < listings.VENUE_MISS_NS):
            return held[0], [] if held[0] else [self._venue_reported(code)]
        identifiers = [Identifier(scheme="symbol", value=code, source=self.source_name)]
        known = getattr(self.source, "exchanges", {}).get(code)
        if known is not None and known.mic:
            identifiers.append(Identifier(scheme="iso10383", value=known.mic))
        found = await self.plugin.resolve_venue(identifiers=identifiers, as_of_ns=now)
        if found.found and found.venue.venue_id:
            self._venues[code] = (found.venue.venue_id, now)
            return found.venue.venue_id, []
        await self.plugin.report_missing_venue(
            source=self.source_name,
            identifiers=identifiers,
            as_of_ns=now,
            reason=found.miss_reason or ops.MISS_REASON_NOT_FOUND,
        )
        self._venues[code] = ("", now)
        return "", [self._venue_reported(code)]

    @staticmethod
    def _clocks(row: Any, stated: Any) -> Any:
        """The row with both of Massive's clocks: the SIP's as consolidated,
        the participant's as the event."""
        if row is None or row.meta is None:
            return row
        times = [SourceTime(kind="consolidated", at_ns=stated.at_ns)]
        participant = getattr(stated, "participant_ns", None)
        if participant:
            times.insert(0, SourceTime(kind="event", at_ns=participant))
        return dataclasses.replace(row, meta=dataclasses.replace(row.meta, source_times=times))

    async def trade_row(self, stated: Any, subject: str) -> Any:
        return self._clocks(await super().trade_row(stated, subject), stated)

    async def quote_row(self, stated: Any, subject: str) -> Any:
        return self._clocks(await super().quote_row(stated, subject), stated)

    # ── The edge (contract v28) ──────────────────────────────────────────

    def at_the_edge(self, key: str, rows: list[Any], want_id: str) -> list[Any]:
        """Rows of the dataset `key` recorded unasked or for a standing
        want, their age at the edge recorded and judged by the SDK's
        checks; rows pulled for a read as they are. A chain's rows are
        judged series by series (their subject is the underlying)."""
        standing = {w.want_id for w in self.standing.values()}
        for held in self.chains.underlyings.values():
            standing |= set(held.standing) | set(held.trades)
        if want_id and want_id not in standing:
            return rows
        dataset = self.named(key)
        if self.latency is not None:
            for row in rows:
                self.latency.aged(dataset, "AGE_HOP_EDGE", row)
        if key not in (OPTIONS, OPTIONS_DELAYED):
            return self.quality.judged(key, rows)
        out: list[Any] = []
        for row in rows:
            series = row.meta.series_key[0].value if row.meta.series_key else ""
            out.extend(self.quality.judged(f"{key}/{series}", [row]))
        return out

    async def _record(self, key: str, data_type: str, rows: Any, want_id: str) -> None:
        if key in (LIVE, TRADES) and data_type in (PRICE, QUOTE, TRADE):
            rows = self.at_the_edge(self.in_force(key), list(rows), want_id)
        await super()._record(key, data_type, rows, want_id)

    # ── Wants ────────────────────────────────────────────────────────────

    def refused(self, want: ops.ObservationsWantedEvent) -> str:
        """Why a want of a stock dataset is not this plan's, or ""."""
        key = self._key_of(want.dataset)
        if key not in (LIVE, TRADES):
            return ""
        if want.dataset != self.dataset(key):
            return "not_covered"
        plan = self.plans["stocks"]
        if plan == END_OF_DAY:
            # Basic serves no snapshot, trades or stream.
            return "not_covered"
        if key == LIVE and want.data_type == QUOTE and plan != REAL_TIME:
            # A delayed plan includes no quote.
            return "not_covered"
        return ""

    async def on_want(self, heard: Any) -> None:
        """A want of a chain is chains.py's; one this plan does not serve is
        declined, not covered; any other is the SDK's to answer."""
        want: ops.ObservationsWantedEvent = heard.message
        key = self._key_of(want.dataset)
        await self.reference()
        if key == OPTIONS:
            await self.chains.on_want(want)
            return
        reason = self.refused(want)
        if reason:
            self.counts[key].wants += 1
            subjects = [s.entity_id for s in want.subjects]
            await self._decline(key, want.want_id, subjects, reason, settled=False)
            return
        await super().on_want(heard)

    async def on_withdrawn(self, heard: Any) -> None:
        """A standing want withdrawn, a chain's among them."""
        self.chains.withdraw(heard.message.want_id)
        await super().on_withdrawn(heard)

    async def reference(self) -> None:
        """Massive's exchanges and trade conditions, read once a day before
        anything is converted with them (and the options' while option
        trades are streamed); a failure is said and asked again later."""
        source: Any = self.source
        if source is None or not hasattr(source, "reference"):
            return
        today = new_york_date(self.clock_ns())
        if source.conditions.due("stocks", today):
            await self._fetch(lambda: source.reference(today))
        if self.chains.streamed and source.conditions.due("options", today):
            await self._fetch(lambda: source.option_conditions(today))

    async def tick(self) -> None:
        """The reference tables, the SDK's timetable, then the chains due
        (chains.py)."""
        await self.reference()
        await super().tick()
        await self.chains.refresh()

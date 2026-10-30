"""Massive's option chains in the lake (contract v28: meridian-design
spec/the-options-and-event-contracts-sample-plugin; rulings 561, 567, 617,
660 and 821).

**Two datasets, one per timeliness** (ruling 660, after 551 and 382):
`options`, real time, and `options_delayed`, 15 minutes delayed, each OPRA's
data as Massive carries it, licensed and entitled apart from the equity
datasets. The chain is recorded into the one the Options plan setting names
(lake.py); a want of the other is declined, not covered. A real-time plan
includes OPRA's quotes; a delayed one does not, so `options_delayed` holds
no quote. An end-of-day plan (Basic) lists contracts alone: its series are
recorded into `options_delayed`, which then holds no statistic either.

**Never minted, never resolved** (ruling 561, W10.4): every row names the
underlying's record as its subject and the series by its OCC OSI symbol
(`occ_osi`, W1.26) as its series key, through the SDK's helpers
(`meridian.roles.chains`): each series' terms as the platform's
listed-option template's fields (`TPL-listed-option`, version 1; ruling
617), its best bid and offer as a quote, its open interest and volume as
statistics, its trades as trades. A series gets an `INS-` record only when a
reader asks for one (RequestSeriesRecords, W6.37), never this plugin.

**What Massive states, and what it does not:**
- The series: `/v3/reference/options/contracts`, every contract of an
  underlying expiring from today, read once a day: its style (Massive's
  `exercise_style`), its settlement method from its CFI code's delivery
  letter (ISO 10962: `P` physical, `C` cash), its multiplier
  (`shares_per_contract`) and, for a standard contract -- its root its
  underlying's, 100 shares, nothing more -- 100 shares of the underlying as
  its deliverable. An adjusted contract states its deliverables by symbol
  (`additional_underlyings`), not by the deployment's records, so none is
  recorded. No settlement time is stated.
- Quotes, volume and open interest: `/v3/snapshot/options/{underlying}`, one
  expiration a read. A quote's sizes are in contracts; a side with no price
  is empty. Open interest is the count at the end of the last trading day,
  so it is dated the market day before today by the deployment's US equity
  calendar (the datasets' `calendar_id`, ReadCalendar); where the calendar
  does not answer, no open interest is recorded rather than misdated.
  Volume is dated by the day its bar was last updated, as Massive states it.
  A value Massive leaves out records no row, never a zero (W10.4). Greeks,
  implied volatility and Massive's fair market value are never read: the
  engine computes its own (ruling 565).
- Trades: OPRA's prints, streamed over Massive's options WebSocket for the
  series a standing want names (by series key, or every series of the
  expirations it names), up to `STREAM_LIMIT`; each keyed by its series and
  OPRA's sequence number, its attributes from Massive's OPRA condition rules
  (conditions.py), never an aggressor: an inferred side is derived, never
  recorded as one. A range of an option's trades is not read.

**Bounded by what readers ask** (rulings 152 and 821): a want names its
underlyings and, from contract v28's wants, the expirations it shows
(`expiration_from`, `expiration_to`) or its series; only those are read. A
want of quotes or statistics reads each expiration once, a standing want's
kept current at most once a minute, within `READS_PER_MINUTE`, the
expirations a reader waits for first; past that, less often. Only what
changed is sent again: a quote when its prices or sizes changed (ruling 770),
a statistic when its value did; each pass that read a chain records the
dataset's liveness instead, which confirms every quote in force.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import OrderedDict, deque
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from meridian.edge import as_reported
from meridian.errors import MeridianError
from meridian.operations import Eligibility, Money, Quote, SourceTime, Trade, TradeAttributes
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.roles import chains as helpers
from meridian.roles.data.listings import Counts

from .convert import (
    NEW_YORK,
    STANDARD_SHARES,
    ChainOption,
    Contract,
    SourceQuote,
    SourceTrade,
    new_york_date,
    ticker_of,
)
from .settings import DELAYED, END_OF_DAY, REAL_TIME

log = logging.getLogger("massive.chains")

#: The chain's datasets (ruling 660): real time, and delayed 15 minutes.
OPTIONS = "options"
OPTIONS_DELAYED = "options_delayed"
SERIES = "meridian.v1.ListedSeries"
STATISTIC = "meridian.v1.Statistic"
QUOTE = "meridian.v1.Quote"
TRADE = "meridian.v1.Trade"
#: What each chain dataset holds: a delayed plan includes no OPRA quote.
DATA_TYPES = {
    OPTIONS: (SERIES, STATISTIC, QUOTE, TRADE),
    OPTIONS_DELAYED: (SERIES, STATISTIC, TRADE),
}
#: How often a standing want's expiration is read again, at most (152).
CADENCE_SECONDS = 60
#: The chain snapshot reads a minute, at most, on a paid plan.
READS_PER_MINUTE = 30
#: The most series one options session streams: Massive's limit on one
#: connection's option quotes, kept for trades too.
STREAM_LIMIT = 1_000
#: The platform's listed-option template (ruling 617) and its version.
TEMPLATE_ID = "TPL-listed-option"
TEMPLATE_VERSION = "1"
CURRENCY = "USD"
#: How far back a market day is looked for.
LOOK_BACK_DAYS = 14
#: How many rows' last sendings are remembered, to send only what changed.
REMEMBERED = 200_000
#: How many option trades recorded are remembered, so none is sent twice.
RECENT_TRADES = 50_000
BATCH = 500
NS = 1_000_000_000
MINUTE_NS = 60 * NS
#: Streamed option trades wait at most this long before they are recorded.
TRADE_EVERY_NS = NS // 2


@dataclass
class Underlying:
    """One underlying whose chain is wanted: its listing, its contracts as
    listed today, when each expiration was last read, the wants waiting on an
    expiration, the standing wants keeping expirations current, and the
    series each standing want of trades streams."""

    subject: str
    symbol: str
    terms: dict[str, Contract] = field(default_factory=dict)
    listed_on: dt.date | None = None
    read_at: dict[dt.date, int] = field(default_factory=dict)
    pending: dict[str, set[dt.date]] = field(default_factory=dict)
    standing: dict[str, set[dt.date]] = field(default_factory=dict)
    trades: dict[str, list[str]] = field(default_factory=dict)
    series: set[str] = field(default_factory=set)

    @property
    def expirations(self) -> list[dt.date]:
        return sorted({c.expiration for c in self.terms.values()})

    @property
    def wanted(self) -> bool:
        return bool(self.pending or self.standing or self.trades or self.series)


def _eligibility(flags: Sequence[bool | None]) -> Eligibility:
    """Whether a trade may set each statistic: eligible, not eligible, or
    unset where a code did not convert (not known)."""

    def one(flag: bool | None) -> str | None:
        return None if flag is None else ("eligible" if flag else "not_eligible")

    high_low, opening, closing, volume = flags
    return Eligibility(
        high_low=one(high_low), open=one(opening), close=one(closing), volume=one(volume)
    )


def _window(want: Any) -> tuple[dt.date | None, dt.date | None]:
    first = dt.date.fromisoformat(want.expiration_from) if want.expiration_from else None
    last = dt.date.fromisoformat(want.expiration_to) if want.expiration_to else None
    return first, last


def _within(day: dt.date, first: dt.date | None, last: dt.date | None) -> bool:
    return (first is None or day >= first) and (last is None or day <= last)


class Chains:
    """The chains wanted of `lake` (lake.Lake), read from its source and
    recorded, and the series whose trades are streamed."""

    def __init__(self, lake: Any) -> None:
        self.lake = lake
        self.underlyings: dict[str, Underlying] = {}
        #: When each chain read of the last minute was made.
        self.calls: deque[int] = deque()
        #: Each row's last sending, by (data type, dataset, row key).
        self.sent: OrderedDict[tuple[str, str, str], str] = OrderedDict()
        #: Each trade recorded, by row key: none sent twice.
        self.recent: OrderedDict[str, None] = OrderedDict()
        #: Market days as the deployment's calendar states them.
        self.days: dict[dt.date, bool] = {}
        self.calendar_read: tuple[dt.date, dt.date] | None = None
        self.calendar_error = ""
        #: Chains read since start; when a chain was last read or a series'
        #: message heard, and the liveness last recorded.
        self.read_count = 0
        self.alive_ns = 0
        self.liveness_ns = 0
        #: The series streamed, as Massive's tickers, and those past the limit.
        self.streamed: list[str] = []
        self.past_limit: set[str] = set()
        self.streamer: Any = None
        self.pending_trades: list[Trade] = []
        self._trades_at = 0

    @property
    def counts(self) -> Counts:
        counts: Counts = self.lake.counts[OPTIONS]
        return counts

    # ── Names ────────────────────────────────────────────────────────────

    def timeliness(self) -> str:
        return str(self.lake.timeliness("options"))

    def key(self) -> str:
        """The chain's dataset the plan provides now."""
        return OPTIONS if self.timeliness() == REAL_TIME else OPTIONS_DELAYED

    def dataset(self) -> str:
        return str(self.lake.named(self.key()))

    def now(self) -> int:
        return int(self.lake.clock_ns())

    # ── Wants (W10.7) ────────────────────────────────────────────────────

    def refused(self, want: Any) -> str:
        """Why a want is not this plan's to answer, or "" where it is."""
        if want.dataset != self.dataset():
            return "not_covered"
        if want.data_type not in DATA_TYPES[self.key()]:
            return "not_covered"
        plan = self.timeliness()
        if plan == END_OF_DAY and want.data_type != SERIES:
            # Basic lists contracts alone: no snapshot, no stream.
            return "not_covered"
        if want.data_type == TRADE and not want.standing:
            # A range of an option's trades is not read: streamed only.
            return "not_covered"
        if self.lake.source is None:
            return "source_silent"
        return ""

    async def on_want(self, want: Any) -> None:
        """A want of a chain's series, quotes, statistics or trades: each
        subject an underlying, the expirations or series it names read (and
        kept current, standing), or declined with why."""
        subjects = [s.entity_id for s in want.subjects]
        self.counts.wants += 1
        reason = self.refused(want)
        if reason:
            await self.decline(want.want_id, subjects, reason, settled=False)
            return
        day = want.business_date
        stated = {*(await self.dates()).values(), self.lake.today()} if day else set()
        if want.data_type == STATISTIC and day and day not in stated:
            # Massive's snapshot states the latest figures alone: open
            # interest of the last market day, volume of the day so far.
            await self.decline(want.want_id, subjects, "beyond_history", day=day)
            return
        first, last = _window(want)
        keys = {k.value for k in want.series_keys if k.scheme == helpers.OCC_OSI}
        declined: dict[str, list[str]] = {}
        for subject in subjects:
            held = await self.underlying(subject)
            if isinstance(held, str):
                declined.setdefault(held, []).append(subject)
                continue
            chosen = [
                c
                for c in held.terms.values()
                if _within(c.expiration, first, last) and (not keys or c.osi in keys)
            ]
            if not chosen:
                declined.setdefault("not_covered", []).append(subject)
                self._forget(held)
                continue
            if want.data_type == SERIES:
                await self.record_series(held, chosen, want.want_id)
                if want.standing:
                    held.series.add(want.want_id)
            elif want.data_type == TRADE:
                held.trades[want.want_id] = sorted(c.osi for c in chosen)
            else:
                days = {c.expiration for c in chosen}
                held.pending[want.want_id] = set(days)
                if want.standing:
                    held.standing[want.want_id] = set(days)
            self._forget(held)
        for why, refused in sorted(declined.items()):
            await self.decline(want.want_id, refused, why, settled=why != "source_silent")
        if want.data_type == TRADE:
            past = self.restream()
            await self.lake.reference()
            over = [
                s
                for s in subjects
                if any(
                    ticker_of(osi) in past
                    for osi in self.underlyings.get(s, Underlying(s, "")).trades.get(
                        want.want_id, []
                    )
                )
            ]
            await self.decline(want.want_id, over, "not_covered", day=f"{self.lake.today()}:stream")
        await self.refresh()

    def withdraw(self, want_id: str) -> None:
        """A standing want withdrawn: its chains no longer kept current, its
        series no longer streamed for it."""
        streamed = False
        for held in list(self.underlyings.values()):
            held.standing.pop(want_id, None)
            held.pending.pop(want_id, None)
            held.series.discard(want_id)
            streamed = held.trades.pop(want_id, None) is not None or streamed
            self._forget(held)
        if streamed:
            self.restream()

    def reset(self) -> None:
        """The plan changed: every chain was wanted of the other dataset, and
        the lake asks again of this one."""
        self.underlyings.clear()
        self.restream()

    async def decline(
        self,
        want_id: str,
        subjects: Sequence[str],
        reason: str,
        *,
        day: str = "",
        settled: bool = True,
    ) -> None:
        await self.lake._decline(OPTIONS, want_id, subjects, reason, day=day, settled=settled)

    async def underlying(self, subject: str) -> Underlying | str:
        """The underlying a subject is, its contracts listed today; or why
        its chain is not read: no listing, or no contract listed
        (`not_covered`), or Massive not answering (`source_silent`)."""
        lake = self.lake
        source = lake.source
        symbol = await lake.symbol_of(subject)
        if symbol is None or source is None:
            return "not_covered" if symbol is None else "source_silent"
        held = self.underlyings.get(subject) or Underlying(subject, symbol)
        today = new_york_date(self.now())
        if held.listed_on != today:
            listed = await lake._fetch(lambda: source.contracts(symbol, today))
            if listed is None:
                return "source_silent"
            terms: dict[str, Contract] = {}
            for each in listed:
                if isinstance(each, Contract):
                    terms[each.osi] = each
                else:
                    self.counts.unconverted += 1
            held.terms, held.listed_on = terms, today
            for day in list(held.read_at):
                if day < today:
                    del held.read_at[day]
            for days in (*held.pending.values(), *held.standing.values()):
                days.intersection_update(held.expirations)
        if not held.terms:
            return "not_covered"
        self.underlyings[subject] = held
        return held

    def _forget(self, held: Underlying) -> None:
        for want_id in [w for w, left in held.pending.items() if not left]:
            del held.pending[want_id]
        if not held.wanted:
            self.underlyings.pop(held.subject, None)

    # ── The timetable ────────────────────────────────────────────────────

    def budget(self) -> int:
        """The chain reads still allowed this minute."""
        now = self.now()
        while self.calls and self.calls[0] <= now - MINUTE_NS:
            self.calls.popleft()
        return READS_PER_MINUTE - len(self.calls)

    async def refresh(self) -> int:
        """Read what is due within the minute's budget: the expirations a
        want waits for first, then a standing want's unread longest, nearest
        expiration first; and each day, a standing want's contracts listed
        again and their series recorded where they changed. How many chains
        were read."""
        if self.lake.source is None:
            return 0
        today = new_york_date(self.now())
        for held in list(self.underlyings.values()):
            if held.listed_on != today and (held.standing or held.series or held.trades):
                found = await self.underlying(held.subject)
                if isinstance(found, str):
                    log.warning("%s's contracts not listed today: %s", held.symbol, found)
                    continue
                if found.series:
                    await self.record_series(found, list(found.terms.values()), "")
                if found.trades:
                    self.restream()
        now = self.now()
        due: list[tuple[int, int, dt.date, str]] = []
        for held in self.underlyings.values():
            for expiry in held.expirations:
                waiting = any(expiry in left for left in held.pending.values())
                kept = any(expiry in days for days in held.standing.values())
                last = held.read_at.get(expiry, 0)
                stale = kept and now - last >= CADENCE_SECONDS * NS
                if waiting or stale:
                    due.append((0 if waiting else 1, last, expiry, held.subject))
        read = 0
        for _, _, expiry, subject in sorted(due)[: max(self.budget(), 0)]:
            wanted = self.underlyings.get(subject)
            if wanted is not None:
                await self.read(wanted, expiry)
                read += 1
        await self.alive()
        return read

    async def alive(self) -> bool:
        """The chain's sign of life since the last (contract v27, W10.4,
        ruling 418): the chain dataset's liveness, recorded once a pass for
        its consolidated view, which confirms every quote in force, an
        unchanged one never sent again. A refusal is said, never raised."""
        if self.alive_ns <= self.liveness_ns:
            return False
        self.liveness_ns = self.alive_ns
        try:
            await self.lake.plugin.record_liveness(
                liveness=[
                    ops.DatasetLiveness(
                        dataset=self.dataset(), venue_id="", alive_at_ns=self.alive_ns
                    )
                ]
            )
        except (MeridianError, ValueError) as refused:
            log.warning("the chain's liveness not recorded: %s", refused)
            return False
        return True

    async def read(self, held: Underlying, expiry: dt.date) -> None:
        """One expiration's chain read and recorded, against the want waiting
        for it, else the standing want keeping it current."""
        source = self.lake.source
        self.calls.append(self.now())
        listed = await self.lake._fetch(lambda: source.chain(held.symbol, expiry))
        waiting = sorted(w for w, left in held.pending.items() if expiry in left)
        held.read_at[expiry] = self.now()
        for want_id in waiting:
            held.pending[want_id].discard(expiry)
        if listed is None:
            for want_id in waiting:
                await self.decline(want_id, [held.subject], "source_silent", settled=False)
        else:
            self.read_count += 1
            self.alive_ns = held.read_at[expiry]
            keeping = [w for w, days in held.standing.items() if expiry in days]
            want_id = waiting[0] if waiting else min(keeping, default="")
            await self.record(held, listed, want_id)
        self._forget(held)

    # ── Dates ────────────────────────────────────────────────────────────

    async def is_open(self, day: dt.date) -> bool | None:
        """Whether a day is a US equity market day by the deployment's
        calendar (ReadCalendar); None where the calendar did not answer."""
        if day in self.days:
            return self.days[day]
        today = new_york_date(self.now())
        span = (today - dt.timedelta(days=LOOK_BACK_DAYS + 7), today + dt.timedelta(days=7))
        if self.calendar_read == span:
            return None
        self.calendar_read = span
        try:
            answer = await self.lake.plugin.read_calendar(
                calendar_id=self.lake.calendar_id, from_date=span[0], to_date=span[1]
            )
        except (MeridianError, ValueError) as failed:
            self.calendar_error = f"the calendar was not read: {failed}"[:300]
            log.warning("%s", self.calendar_error)
            return None
        if not answer.found:
            self.calendar_error = f"the calendar {self.lake.calendar_id} was not found"
            return None
        self.calendar_error = ""
        weekends = {int(w) for weekend in answer.weekends for w in weekend.weekdays}
        closed = {c.date for c in answer.days if c.kind == ops.CALENDAR_DAY_KIND_CLOSED and c.date}
        opened = {c.date for c in answer.days if c.kind == ops.CALENDAR_DAY_KIND_OPEN and c.date}
        day_of = span[0]
        while day_of <= span[1]:
            text = day_of.isoformat()
            weekday = ops.WEEKDAY_MONDAY + day_of.weekday()
            self.days[day_of] = text in opened or (text not in closed and weekday not in weekends)
            day_of += dt.timedelta(days=1)
        return self.days.get(day)

    async def dates(self) -> dict[str, str]:
        """The trading date each statistic read now is for: open interest the
        market day before today's; volume is dated by its own bar."""
        today = dt.datetime.fromtimestamp(self.now() / NS, NEW_YORK).date()
        for back in range(1, LOOK_BACK_DAYS + 1):
            day = today - dt.timedelta(days=back)
            held = await self.is_open(day)
            if held is None:
                return {}
            if held:
                return {"open_interest": day.isoformat()}
        return {}

    # ── Recording (W10.4) ────────────────────────────────────────────────

    def _changed(self, data_type: str, dataset: str, row_key: str, said: str) -> bool:
        """Whether a row says something other than its last sending."""
        mark = (data_type, dataset, row_key)
        if self.sent.get(mark) == said:
            return False
        self.sent[mark] = said
        self.sent.move_to_end(mark)
        while len(self.sent) > REMEMBERED:
            self.sent.popitem(last=False)
        return True

    def series_row(self, held: Underlying, contract: Contract, dataset: str) -> Any | None:
        raw = self.lake.plugin.raw_record(contract.raw_key[:256])
        try:
            return helpers.option_series(
                held.subject,
                contract.osi,
                template_id=TEMPLATE_ID,
                template_version=TEMPLATE_VERSION,
                dataset=dataset,
                currency=CURRENCY,
                exercise_style=contract.exercise_style,
                settlement_method=contract.settlement_method,
                contract_multiplier=contract.multiplier.normalize(),
                deliverable=[(held.subject, STANDARD_SHARES)] if contract.standard else (),
                valid_from_ns=self.now(),
                raw=raw,
            )
        except ValueError:
            self.counts.unconverted += 1
            return None

    async def record_series(
        self, held: Underlying, chosen: Sequence[Contract], want_id: str
    ) -> None:
        """The terms of the contracts chosen, each sent only where changed."""
        dataset = self.dataset()
        rows: list[Any] = []
        for contract in chosen:
            row = self.series_row(held, contract, dataset)
            if row is None:
                continue
            terms = repr([(f.path, f.value) for f in row.fields])
            if self._changed(SERIES, dataset, contract.osi, terms):
                rows.append(row)
        if rows:
            await self.send("series", rows, want_id)

    async def record(self, held: Underlying, listed: Sequence[ChainOption], want_id: str) -> None:
        """An expiration's series, quotes and statistics, each sent only where
        it changed: its quotes judged at the edge."""
        dataset = self.dataset()
        now = self.now()
        dates = await self.dates()
        quotes_held = self.key() == OPTIONS
        series: list[Any] = []
        quotes: list[Any] = []
        statistics: list[Any] = []
        for option in listed:
            self.counts.unconverted += len(option.unconverted)
            contract = held.terms.get(option.osi)
            raw = self.lake.plugin.raw_record(option.raw_key[:256])
            key = helpers.osi_key(option.osi)
            if contract is not None:
                row = self.series_row(held, contract, dataset)
                if row is not None:
                    terms = repr([(f.path, f.value) for f in row.fields])
                    if self._changed(SERIES, dataset, option.osi, terms):
                        series.append(row)
            quote = self.quote(held.subject, option, key, dataset, raw) if quotes_held else None
            if quote is not None:
                said = repr((quote.bid, quote.ask, quote.bid_quantity, quote.ask_quantity))
                if self._changed(QUOTE, dataset, option.osi, said):
                    quotes.append(quote)
            stated: list[tuple[str, Decimal | None, str]] = [
                ("open_interest", option.open_interest, dates.get("open_interest", "")),
                (
                    "volume",
                    option.volume,
                    option.volume_date.isoformat() if option.volume_date else "",
                ),
            ]
            for kind, value, day in stated:
                if value is None or not day:
                    continue
                stat = helpers.statistic(
                    held.subject, key, kind, value, day, dataset=dataset, valid_from_ns=now, raw=raw
                )
                assert stat.meta is not None
                if self._changed(STATISTIC, dataset, stat.meta.row_key, str(value)):
                    statistics.append(stat)
        if series:
            await self.send("series", series, want_id)
        if quotes:
            await self.send("quotes", self.lake.at_the_edge(self.key(), quotes, want_id), want_id)
        if statistics:
            await self.send("statistics", statistics, want_id)

    def quote(
        self, subject: str, option: ChainOption, key: Any, dataset: str, raw: Any
    ) -> Quote | None:
        """A series' best bid and offer under the underlying, keyed by the
        series; none where Massive states no side or no time."""
        if option.quote_at_ns is None or (option.bid is None and option.ask is None):
            return None
        return Quote(
            meta=helpers.series_meta(
                subject,
                key,
                row_key=f"{option.osi}:q:{option.quote_at_ns}",
                dataset=dataset,
                valid_from_ns=option.quote_at_ns,
                raw=raw,
                source_times=[SourceTime(kind="event", at_ns=option.quote_at_ns)],
            ),
            bid=None if option.bid is None else Money(option.bid, CURRENCY),
            ask=None if option.ask is None else Money(option.ask, CURRENCY),
            bid_quantity=option.bid_size if option.bid is not None else None,
            ask_quantity=option.ask_size if option.ask is not None else None,
        )

    async def send(self, kind: str, rows: Sequence[Any], want_id: str) -> None:
        """Rows in the lake's batches, against the want, counted."""
        call = getattr(self.lake.plugin, f"record_{kind}")
        counts = self.counts
        for start in range(0, len(rows), BATCH):
            done = await call(**{kind: list(rows[start : start + BATCH])}, want_id=want_id)
            counts.recorded += int(getattr(done, "recorded", 0))
            counts.restated += int(getattr(done, "restated", 0))
            counts.unchanged += int(getattr(done, "unchanged", 0))
            counts.last_recorded_at_ns = self.now()
            if want_id:
                held = self.lake.by_want.setdefault(want_id, [0, 0])
                held[0] += int(getattr(done, "recorded", 0)) + int(getattr(done, "restated", 0))

    # ── The options stream: trades ───────────────────────────────────────

    def restream(self) -> set[str]:
        """Stream the series under the standing wants of trades, in the order
        they were wanted, as many as one session may; those past that, as
        Massive's tickers."""
        ordered: list[str] = []
        if self.timeliness() in (REAL_TIME, DELAYED):
            for held in self.underlyings.values():
                for osis in held.trades.values():
                    for osi in osis:
                        ticker = ticker_of(osi)
                        if ticker not in ordered:
                            ordered.append(ticker)
        accepted = ordered[:STREAM_LIMIT]
        self.streamed = accepted
        self.past_limit = set(ordered) - set(accepted)
        if self.streamer is not None:
            self.streamer.want(accepted)
        return self.past_limit

    def underlying_of(self, osi: str) -> list[Underlying]:
        """The underlyings whose standing wants stream a series."""
        return [
            held
            for held in self.underlyings.values()
            if any(osi in osis for osis in held.trades.values())
        ]

    async def heard(self, stated: Sequence[Any]) -> None:
        """What the options stream stated: each trade of a series streamed
        made a row, waiting to be recorded. Quotes are not asked for."""
        self.alive_ns = self.now()
        streamed = set(self.streamed)
        for each in stated:
            if not isinstance(each, SourceTrade) or ticker_of(each.osi) not in streamed:
                continue
            for held in self.underlying_of(each.osi):
                row = self.trade_row(held.subject, each)
                if row is not None:
                    self.pending_trades.append(row)

    def trade_row(self, subject: str, stated: SourceTrade) -> Trade | None:
        """An option's trade under its underlying, keyed by its series: its
        venue as Massive codes it, kept as reported (OPRA's participants are
        resolved by the deployment's venues no more than by their codes),
        its attributes from Massive's OPRA condition rules, no aggressor."""
        if stated.price is None or stated.quantity is None:
            self.counts.unconverted += len(stated.unconverted) or 1
            return None
        said = self.lake.option_attributes(stated.conditions)
        self.counts.unconverted += len(said.unconverted)
        meta = helpers.series_meta(
            subject,
            helpers.osi_key(stated.osi),
            row_key=f"{stated.row_key}@{subject}",
            dataset=self.dataset(),
            valid_from_ns=stated.at_ns,
            raw=self.lake.plugin.raw_record(stated.raw_key[:256]),
            source_times=[SourceTime(kind="event", at_ns=stated.at_ns)],
        )
        reported = [as_reported("massive:venue", stated.venue_code)] if stated.venue_code else []
        meta = replace(meta, unconverted=[*reported, *said.unconverted, *stated.unconverted])
        return Trade(
            meta=meta,
            price=Money(stated.price, CURRENCY),
            quantity=stated.quantity,
            attributes=TradeAttributes(
                consolidated=_eligibility(said.consolidated),
                market_centre=_eligibility(said.market_centre),
                characteristics=list(said.characteristics),
            ),
            source_sequence=stated.sequence,
        )

    async def flush(self, force: bool = False) -> None:
        """Record the option trades heard, every half second or 500 at a
        time, none twice; and the dataset's liveness."""
        await self.alive()
        now = self.now()
        due = force or len(self.pending_trades) >= BATCH or now - self._trades_at >= TRADE_EVERY_NS
        if not self.pending_trades or not due:
            return
        rows, self.pending_trades = self.pending_trades, []
        self._trades_at = now
        fresh: list[Trade] = []
        for row in rows:
            assert row.meta is not None
            if row.meta.row_key in self.recent:
                self.counts.unchanged += 1
                continue
            self.recent[row.meta.row_key] = None
            fresh.append(row)
        while len(self.recent) > RECENT_TRADES:
            self.recent.popitem(last=False)
        if fresh:
            await self.send("trades", self.lake.at_the_edge(self.key(), fresh, ""), "")


__all__ = [
    "CADENCE_SECONDS", "DATA_TYPES", "OPTIONS", "OPTIONS_DELAYED", "QUOTE", "SERIES",
    "STATISTIC", "STREAM_LIMIT", "TRADE", "Chains", "SourceQuote", "Underlying",
]  # fmt: skip

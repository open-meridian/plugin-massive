"""Massive, as the lake's half of the plugin asks it (lake.Source): blocking
calls, which the SDK's feed runs in a thread.

Daily bars from the daily aggregates, one listing a call; live prices and
quotes from the stocks snapshot, a hundred listings a call; trades from a
listing's trade history, for a range a reader wants and for the gap after a
reconnect or a restart (the SDK's feed asks, `trades`); an underlying's
option contracts and one expiration's chain snapshot (chains.py); and the
reference tables, conditions and exchanges, read once a day. Each listing
is asked and named in the one spelling (`BRK.B`), which Massive shares.
"""

from __future__ import annotations

import datetime as dt
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from . import convert
from .conditions import Conditions
from .convert import ChainOption, Contract, DailyBar, Exchange, Quoted, SourceQuote, SourceTrade
from .vendor import SYMBOLS_PER_CALL, Massive, VendorError


@dataclass(frozen=True)
class Answer:
    """Whether Massive answered the key, and what it said where it did not."""

    answered: bool
    detail: str = ""


class MassiveSource:
    """lake.Source on Massive's REST API."""

    def __init__(self, massive: Massive, synthetic: bool = False) -> None:
        self.massive = massive
        self.synthetic = synthetic
        self.conditions = Conditions()
        self.exchanges: dict[str, Exchange] = {}
        self._exchanges_on: dt.date | None = None
        self._answer: Answer | None = None
        self._lock = threading.Lock()
        #: Listings Massive answered it does not know (an empty result for a
        #: daily range is no proof), never asked again: none so far.
        self.invalid: set[str] = set()

    # ── Whether the key answers ──────────────────────────────────────────

    def probe(self, again: bool = False) -> Answer:
        """Whether Massive answers the key: its exchanges list, which every
        plan includes, asked once and again when the key changes."""
        with self._lock:
            if self._answer is None or again:
                try:
                    self.reference(dt.date.today(), force=True)
                    self._answer = Answer(True)
                except VendorError as refused:
                    self._answer = Answer(False, f"{refused.status} {refused.detail}")
            return self._answer

    @property
    def known(self) -> Answer | None:
        return self._answer

    def reference(self, today: dt.date, force: bool = False) -> None:
        """Massive's exchanges and stock trade conditions, read once a day."""
        if force or self._exchanges_on != today:
            self.exchanges = convert.exchanges(self.massive.exchanges("stocks"))
            self._exchanges_on = today
        if force or self.conditions.due("stocks", today):
            self.conditions.load("stocks", self.massive.conditions("stocks"), today)

    def option_conditions(self, today: dt.date) -> None:
        """Massive's option trade conditions (OPRA's), read once a day."""
        if self.conditions.due("options", today):
            self.conditions.load("options", self.massive.conditions("options"), today)

    # ── Stocks (lake.Source) ─────────────────────────────────────────────

    def daily(self, symbols: Sequence[str], first: dt.date, last: dt.date) -> list[DailyBar]:
        bars: list[DailyBar] = []
        for symbol in symbols:
            for page in self.massive.daily(symbol, first.isoformat(), last.isoformat()):
                bars.extend(convert.daily(page, symbol))
        return bars

    def _snapshots(self, symbols: Sequence[str]) -> list[Any]:
        pages = []
        for start in range(0, len(symbols), SYMBOLS_PER_CALL):
            pages.append(self.massive.snapshot(list(symbols[start : start + SYMBOLS_PER_CALL])))
        return pages

    def live(self, symbols: Sequence[str]) -> list[Quoted]:
        return [q for page in self._snapshots(symbols) for q in convert.snapshot_live(page)]

    def quotes(self, symbols: Sequence[str]) -> list[SourceQuote]:
        return [q for page in self._snapshots(symbols) for q in convert.snapshot_quotes(page)]

    def trades(self, symbols: Sequence[str], start_ns: int, end_ns: int) -> list[SourceTrade]:
        """Each listing's trades from `start_ns` to before `end_ns`."""
        found: list[SourceTrade] = []
        for symbol in symbols:
            for page in self.massive.trades(symbol, start_ns, end_ns):
                found.extend(convert.history_trades(page, symbol))
        return found

    # ── Options (chains.py) ──────────────────────────────────────────────

    def contracts(
        self, underlying: str, first: dt.date, last: dt.date | None = None
    ) -> list[Contract | Any]:
        """An underlying's contracts expiring from `first` (to `last`): each
        contract's terms, or what did not convert, as reported."""
        out: list[Contract | Any] = []
        for page in self.massive.contracts(
            underlying, first.isoformat(), last.isoformat() if last else ""
        ):
            out.extend(convert.contracts(page))
        return out

    def chain(self, underlying: str, expiration: dt.date) -> list[ChainOption]:
        """One expiration's chain snapshot."""
        out: list[ChainOption] = []
        for page in self.massive.chain(underlying, expiration.isoformat()):
            out.extend(convert.chain(page))
        return out

"""Massive's trade conditions, converted to the platform's trade attributes at
the edge (contract v19, W10.4).

Massive numbers the SIPs' sale conditions (CTA's, UTP's, OPRA's) with its
own codes, one table per asset class, and publishes each code's update rules
(`/v3/reference/conditions`): whether a trade carrying it may set the high
and low, the open and close, and the volume, of the consolidated bar and of
its market centre's -- exactly the platform's eligibilities. So the table
is read from Massive (daily, its answer kept as a raw record) rather than
written here, and a trade's attributes are its codes' rules together: a
statistic a trade may set only where every code it carries allows it
(Massive's open-and-close rule sets both the open and the close).

Its characteristics are named from each code's name, where the name says
one plainly (`Odd Lot Trade`, `Intermarket Sweep`, `Form T`, `Opening
Prints`, OPRA's `Multi Leg ...`, ...). A code the table does not hold --
one Massive added after it was read, or the table not read yet -- is kept as
reported (`massive:stocks.condition`), and the trade's eligibilities are
then not known: never guessed.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from meridian.edge import as_reported
from meridian.plugin.v1.operations_pb2 import AsReported

from .vendor import Fetched

Flags = tuple[bool | None, bool | None, bool | None, bool | None]
NOT_KNOWN: Flags = (None, None, None, None)
EVERY: Flags = (True, True, True, True)


@dataclass(frozen=True)
class Attributes:
    """A trade's attributes as converted: each eligibility True, False, or
    None where what Massive said did not convert (not known)."""

    characteristics: tuple[str, ...]
    consolidated: Flags
    market_centre: Flags
    unconverted: tuple[AsReported, ...] = ()


@dataclass(frozen=True)
class Condition:
    """One of Massive's codes: its name, its rules and its characteristics."""

    code: int
    name: str
    consolidated: Flags
    market_centre: Flags
    characteristics: tuple[str, ...]


#: The characteristics a code's name says, by a word or phrase in it (lower
#: case), in order: the first phrases are checked before their parts.
NAMED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("extended trading hours (sold out of sequence)", ("extended_hours", "out_of_sequence")),
    ("non iso", ()),
    ("odd lot", ("odd_lot",)),
    ("intermarket sweep", ("intermarket_sweep",)),
    ("iso", ("intermarket_sweep",)),
    ("form t", ("extended_hours",)),
    ("out of sequence", ("out_of_sequence",)),
    ("average price", ("average_price",)),
    ("derivatively priced", ("derivatively_priced",)),
    ("prior reference price", ("prior_reference_price",)),
    ("contingent", ("contingent",)),
    ("cross", ("cross",)),
    ("re-opening prints", ("other_auction",)),
    ("opening prints", ("opening_auction",)),
    ("closing prints", ("closing_auction",)),
    ("multi leg", ("multi_leg",)),
    ("stock option", ("stock_option_tied",)),
    ("floor", ("floor",)),
    ("auction", ("other_auction",)),
    ("late", ("late_report",)),
    ("next day", ("non_regular_settlement",)),
    ("cash sale", ("non_regular_settlement",)),
    ("seller", ("non_regular_settlement",)),
)


def characteristics_of(name: str) -> tuple[str, ...]:
    """The platform's characteristics a code's name says, each phrase matched
    as whole words, and taken out once matched so its parts are not matched
    again (`Non ISO` is no sweep)."""
    taken = name.lower()
    found: list[str] = []
    for phrase, named in NAMED:
        pattern = re.compile(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])")
        if pattern.search(taken):
            taken = pattern.sub(" ", taken)
            for characteristic in named:
                if characteristic not in found:
                    found.append(characteristic)
    return tuple(found)


def _flag(rules: Any, name: str) -> bool | None:
    held = rules.get(name) if isinstance(rules, dict) else None
    return held if isinstance(held, bool) else None


def _flags(rules: Any) -> Flags:
    """Massive's rules for one bar: high and low, open, close, volume."""
    open_close = _flag(rules, "updates_open_close")
    return (
        _flag(rules, "updates_high_low"),
        open_close,
        open_close,
        _flag(rules, "updates_volume"),
    )


class Conditions:
    """Massive's trade conditions of each asset class, as last read."""

    def __init__(self) -> None:
        self.table: dict[str, dict[int, Condition]] = {"stocks": {}, "options": {}}
        self.read_on: dict[str, dt.date] = {}

    def load(self, asset_class: str, pages: Iterable[Fetched], today: dt.date) -> int:
        """The table of an asset class from the pages Massive answered; how
        many codes it holds."""
        held: dict[int, Condition] = {}
        for fetched in pages:
            results = fetched.value.get("results") if isinstance(fetched.value, dict) else None
            for item in results if isinstance(results, list) else []:
                code = item.get("id") if isinstance(item, dict) else None
                if not isinstance(code, int) or isinstance(code, bool):
                    continue
                rules = (
                    item.get("update_rules") if isinstance(item.get("update_rules"), dict) else {}
                )
                held[code] = Condition(
                    code=code,
                    name=str(item.get("name") or ""),
                    consolidated=_flags(rules.get("consolidated")),
                    market_centre=_flags(rules.get("market_center")),
                    characteristics=characteristics_of(str(item.get("name") or "")),
                )
        self.table[asset_class] = held
        self.read_on[asset_class] = today
        return len(held)

    def due(self, asset_class: str, today: dt.date) -> bool:
        """Whether the table is to be read again: never read, or not today."""
        return self.read_on.get(asset_class) != today

    def attributes(self, asset_class: str, codes: Iterable[int]) -> Attributes:
        """A trade's attributes from its codes: every code's rules together,
        its characteristics; not known where any code is not in the table or
        its rules are not stated."""
        table = self.table.get(asset_class, {})
        consolidated: list[bool | None] = list(EVERY)
        centre: list[bool | None] = list(EVERY)
        characteristics: list[str] = []
        unconverted: list[AsReported] = []
        for code in codes:
            rule = table.get(code)
            if rule is None:
                unconverted.append(as_reported(f"massive:{asset_class}.condition", str(code)))
                continue
            for named in rule.characteristics:
                if named not in characteristics:
                    characteristics.append(named)
            consolidated = [
                _and(a, b) for a, b in zip(consolidated, rule.consolidated, strict=True)
            ]
            centre = [_and(a, b) for a, b in zip(centre, rule.market_centre, strict=True)]
        if unconverted:
            return Attributes(tuple(characteristics), NOT_KNOWN, NOT_KNOWN, tuple(unconverted))
        return Attributes(
            tuple(characteristics),
            tuple(consolidated),  # type: ignore[arg-type]
            tuple(centre),  # type: ignore[arg-type]
        )


def _and(held: bool | None, rule: bool | None) -> bool | None:
    """Two rules together: not eligible where either says not; not known
    where either is not stated and neither says not."""
    if held is False or rule is False:
        return False
    if held is None or rule is None:
        return None
    return True

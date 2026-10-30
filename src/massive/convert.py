"""Massive's market data, converted to what the plugin records.

The edge is the only place Massive's vocabulary is read. Each converter takes
what Massive sent, parsed with Decimals (vendor.parse), and the raw record it
was kept as, and returns what is stated, named by the listing's one spelling
(`BRK.B`, which Massive spells the same) or an option's OCC OSI symbol, with
Massive's own exchange code, which lake.py resolves to the deployment's
instrument and venue before anything is recorded.

- **The daily close on the declared day.** The daily dataset's day is New
  York's, ending at 16:00. Massive's daily bar names its window's start in
  epoch milliseconds, midnight New York of the business date, and states
  its VWAP (`vw`) and its number of trades (`n`). Bars are asked unadjusted:
  the close as it traded; a signal plugin adjusts.
- **Live prices** (the stocks snapshot): the last trade's price at its SIP
  time, on the exchange Massive codes (`x`), and the national best bid and
  offer (`p`, `P`) at its SIP time, sizes in shares (`s`, `S`), no venue.
- **Trades** (the stream's `T`, the history's `/v3/trades`): the exchange,
  the price, the size (its fractional text where Massive gives one), the
  conditions, the SIP time and the participant's, and the SIP sequence
  number, which numbers a listing's trades within a session. Each trade's
  row is keyed by the listing, its New York date and that number, the same
  from the stream and the history, so a trade caught up after a reconnect or
  a restart is the row the stream recorded. The stream states times in
  milliseconds and the history in nanoseconds; both are recorded to the
  millisecond, so the two agree. Massive's stream sends no cancel; a
  history's correction indicator, where set, is kept as reported.
- **Quotes** (the stream's `Q`): the national best bid and offer, sizes in
  shares, the SIP time and the participant's, the sequence number; a side
  Massive leaves out is empty.
- **Options**: a contract's ticker (`O:SPY261016C00575000`) is its OSI
  symbol with its root padded to six; its terms from the contracts list
  (`exercise_style`, `shares_per_contract`, its CFI code's delivery letter
  for the settlement method, its deliverables); its quote, the day's volume
  and its open interest from the chain snapshot; its trades and quotes
  streamed as the stock's are.
- **Exact.** A value that is not a number, or past the contract's Decimal (18
  places), does not convert: the row is not recorded, and Massive's text is
  kept as reported (`massive:trades.price`) for the Datasets page to count.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from meridian.edge import as_reported
from meridian.plugin.v1.operations_pb2 import AsReported

from .vendor import NS, Fetched

SOURCE = "massive"
NEW_YORK = ZoneInfo("America/New_York")
#: The session's close, the daily dataset's day end: 16:00 New York.
DAY_END_MINUTE = 16 * 60
MOST_PLACES = 18
MOST_DIGITS = 38
MS = 1_000_000

#: A US listing in the one spelling: a root of capitals (digits after the
#: first), and a class or series after a dot.
SYMBOL = re.compile(r"[A-Z][A-Z0-9]{0,5}(?:\.[A-Z0-9]{1,4})?")
#: Massive's option ticker: `O:`, a root of one to six, yymmdd, C or P, and
#: the strike in thousandths, eight digits.
OPTION_TICKER = re.compile(r"^O:(?P<root>[A-Z0-9]{1,6})(?P<rest>\d{6}[CP]\d{8})$")


def spelled(symbol: str) -> str | None:
    """The listing in the one spelling this plugin names it by (`BRK.B`), from
    a symbol in any of the usual spellings, or None where it is not one."""
    candidate = symbol.strip().upper().replace("/", ".").replace("-", ".")
    return candidate if SYMBOL.fullmatch(candidate) else None


def osi_of(ticker: str) -> str | None:
    """The OCC OSI symbol of Massive's option ticker, or None."""
    found = OPTION_TICKER.match(ticker.strip())
    if found is None:
        return None
    return f"{found.group('root'):<6}{found.group('rest')}"


def ticker_of(osi: str) -> str:
    """Massive's option ticker for an OSI symbol."""
    return "O:" + osi[:6].rstrip() + osi[6:]


# ── Days ────────────────────────────────────────────────────────────────────


def new_york_date(at_ns: int) -> dt.date:
    return dt.datetime.fromtimestamp(at_ns / NS, NEW_YORK).date()


# ── Values ──────────────────────────────────────────────────────────────────


def exact(value: object) -> Decimal | None:
    """A number exactly as Massive stated it, or None where it is not one the
    contract carries: not a number, not finite, or past 18 places."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, Decimal):
        number = value
    elif isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?", value.strip()):
        number = Decimal(value.strip())
    else:
        return None
    if not number.is_finite():
        return None
    _, digits, exponent = number.as_tuple()
    if not isinstance(exponent, int) or -exponent > MOST_PLACES:
        return None
    if len(digits) + max(exponent, 0) > MOST_DIGITS:
        return None
    return number


def whole(value: object) -> int | None:
    """A whole number Massive stated (a time, a sequence, a count), or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    return None


def ms_ns(value: object) -> int | None:
    """Epoch milliseconds, in nanoseconds."""
    held = whole(value)
    return held * MS if held is not None and held > 0 else None


def ns_ms(value: object) -> int | None:
    """Epoch nanoseconds, to the millisecond (as the stream states them)."""
    held = whole(value)
    return (held // MS) * MS if held is not None and held > 0 else None


@dataclass
class _Values:
    endpoint: str
    unconverted: list[AsReported] = field(default_factory=list)

    def number(self, name: str, value: object, *, positive: bool = False) -> Decimal | None:
        converted = exact(value)
        if converted is None or (positive and converted <= 0) or converted < 0:
            if value is not None:
                self.unconverted.append(
                    as_reported(f"{SOURCE}:{self.endpoint}.{name}", str(value)[:64])
                )
            return None
        return converted

    def keep(self, name: str, value: object) -> None:
        """Something Massive stated that is kept as reported, not converted."""
        self.unconverted.append(as_reported(f"{SOURCE}:{self.endpoint}.{name}", str(value)[:64]))


def _results(fetched: Fetched, key: str = "results") -> list[Any]:
    held = fetched.value.get(key) if isinstance(fetched.value, dict) else None
    return [item for item in held if isinstance(item, dict)] if isinstance(held, list) else []


# ── Daily bars ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DailyBar:
    """One listing's day, as Massive's daily aggregate states it."""

    symbol: str
    business_date: dt.date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal | None
    vwap: Decimal | None
    trade_count: int | None
    raw_key: str
    unconverted: tuple[AsReported, ...] = ()

    @property
    def row_key(self) -> str:
        return f"{self.symbol}:1d:{self.business_date.isoformat()}"

    @property
    def complete(self) -> bool:
        return self.close is not None and None not in (self.open, self.high, self.low)


def daily(fetched: Fetched, symbol: str) -> Iterator[DailyBar]:
    """Every day of one listing's daily aggregates, named by its one spelling."""
    for index, item in enumerate(_results(fetched)):
        start = ms_ns(item.get("t"))
        if start is None:
            continue
        values = _Values("aggs")
        count = whole(item.get("n"))
        yield DailyBar(
            symbol=symbol,
            business_date=new_york_date(start),
            open=values.number("o", item.get("o"), positive=True),
            high=values.number("h", item.get("h"), positive=True),
            low=values.number("l", item.get("l"), positive=True),
            close=values.number("c", item.get("c"), positive=True),
            volume=values.number("v", item.get("v")),
            vwap=values.number("vw", item.get("vw"), positive=True)
            if item.get("vw") is not None
            else None,
            trade_count=count if count is not None and count >= 0 else None,
            raw_key=f"{fetched.raw_key}#{index}",
            unconverted=tuple(values.unconverted),
        )


# ── Live prices and quotes (the snapshot) ───────────────────────────────────


@dataclass(frozen=True)
class Quoted:
    """One live price: the last trade's (`last`), or a quote's side."""

    symbol: str
    kind: str
    amount: Decimal | None
    at_ns: int
    venue_code: str
    raw_key: str
    unconverted: tuple[AsReported, ...] = ()

    @property
    def row_key(self) -> str:
        return f"{self.symbol}:{self.kind}:{self.at_ns}"


@dataclass(frozen=True)
class SourceQuote:
    """A listing's national best bid and offer, sizes in shares."""

    symbol: str
    at_ns: int
    bid: Decimal | None
    ask: Decimal | None
    bid_size: Decimal | None
    ask_size: Decimal | None
    raw_key: str
    unconverted: tuple[AsReported, ...] = ()
    venue_code: str = ""
    participant_ns: int | None = None
    sequence: int | None = None

    @property
    def row_key(self) -> str:
        said = f"{self.symbol}:q:{self.at_ns}"
        return said if self.sequence is None else f"{said}:{self.sequence}"


def _side(values: _Values, price: object, size: object, name: str) -> tuple[Any, Any]:
    """One side of a quote: empty where Massive states no price, or 0."""
    amount = exact(price)
    if price is None or amount == 0:
        return None, None
    converted = values.number(name, price, positive=True)
    quantity = values.number(f"{name}_size", size) if size is not None else None
    return converted, quantity


def snapshot_live(fetched: Fetched) -> Iterator[Quoted]:
    """`last`, `bid` and `ask` of each listing of the stocks snapshot: the
    last trade's where the plan includes trades, the quote's sides where it
    includes quotes. A side with no price is not sent."""
    for item in _results(fetched, "tickers"):
        symbol = spelled(str(item.get("ticker") or ""))
        if symbol is None:
            continue
        trade = item.get("lastTrade") if isinstance(item.get("lastTrade"), dict) else {}
        quote = item.get("lastQuote") if isinstance(item.get("lastQuote"), dict) else {}
        for kind, held, price, venue in (
            ("last", trade, "p", "x"),
            ("bid", quote, "p", ""),
            ("ask", quote, "P", ""),
        ):
            at = whole(held.get("t"))
            if not at or held.get(price) is None or exact(held.get(price)) == 0:
                continue
            values = _Values("snapshot")
            yield Quoted(
                symbol=symbol,
                kind=kind,
                amount=values.number(price, held.get(price), positive=True),
                at_ns=at,
                venue_code=str(held.get(venue)) if venue and held.get(venue) is not None else "",
                raw_key=f"{fetched.raw_key}#{symbol}/{kind}",
                unconverted=tuple(values.unconverted),
            )


def snapshot_quotes(fetched: Fetched) -> Iterator[SourceQuote]:
    """Each listing's best bid and offer, with sizes, where the plan includes
    quotes (the snapshot leaves `lastQuote` out otherwise)."""
    for item in _results(fetched, "tickers"):
        symbol = spelled(str(item.get("ticker") or ""))
        quote = item.get("lastQuote")
        if symbol is None or not isinstance(quote, dict):
            continue
        at = whole(quote.get("t"))
        if not at:
            continue
        values = _Values("snapshot")
        bid, bid_size = _side(values, quote.get("p"), quote.get("s"), "p")
        ask, ask_size = _side(values, quote.get("P"), quote.get("S"), "P")
        yield SourceQuote(
            symbol=symbol,
            at_ns=at,
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=ask_size,
            raw_key=f"{fetched.raw_key}#{symbol}/quote",
            unconverted=tuple(values.unconverted),
        )


# ── Trades ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SourceTrade:
    """One trade, as Massive states it: a listing's, or (with `osi`) an
    option series'."""

    symbol: str
    at_ns: int
    venue_code: str
    price: Decimal | None
    quantity: Decimal | None
    conditions: tuple[int, ...]
    sequence: int
    raw_key: str
    unconverted: tuple[AsReported, ...] = ()
    participant_ns: int | None = None
    cancelled: bool = False
    osi: str = ""

    @property
    def row_key(self) -> str:
        day = new_york_date(self.at_ns).isoformat()
        return f"{self.osi or self.symbol}:t:{day}:{self.sequence}"


def _trade(
    endpoint: str,
    symbol: str,
    at_ns: int | None,
    venue: object,
    price: object,
    size: object,
    fraction: object,
    conditions: object,
    sequence: object,
    participant_ns: int | None,
    raw_key: str,
    osi: str = "",
) -> SourceTrade | None:
    number = whole(sequence)
    if at_ns is None or number is None:
        return None
    values = _Values(endpoint)
    stated = fraction if isinstance(fraction, str) and fraction else size
    return SourceTrade(
        symbol=symbol,
        at_ns=at_ns,
        venue_code=str(venue) if venue is not None else "",
        price=values.number("price", price, positive=True),
        quantity=values.number("size", stated, positive=True),
        conditions=tuple(c for c in (conditions or ()) if isinstance(c, int))
        if isinstance(conditions, list)
        else (),
        sequence=number,
        raw_key=raw_key,
        unconverted=tuple(values.unconverted),
        participant_ns=participant_ns,
        osi=osi,
    )


def history_trades(fetched: Fetched, symbol: str) -> Iterator[SourceTrade]:
    """Every trade of one listing's `/v3/trades` page."""
    for index, item in enumerate(_results(fetched)):
        made = _trade(
            "trades",
            symbol,
            ns_ms(item.get("sip_timestamp")),
            item.get("exchange"),
            item.get("price"),
            item.get("size"),
            item.get("decimal_size"),
            item.get("conditions"),
            item.get("sequence_number"),
            ns_ms(item.get("participant_timestamp")),
            f"{fetched.raw_key}#{index}",
        )
        if made is None:
            continue
        correction = whole(item.get("correction"))
        if correction:
            # A corrected or cancelled print's indicator, kept as reported:
            # Massive documents no code for what each means.
            made = replace(
                made,
                unconverted=(
                    *made.unconverted,
                    as_reported(f"{SOURCE}:trades.correction", str(correction)),
                ),
            )
        yield made


# ── The stream ──────────────────────────────────────────────────────────────


def streamed(message: Any, raw_key: str) -> list[Any]:
    """What one of Massive's stream events states: a trade (`T`), a quote
    (`Q`), of a listing or of an option series; nothing for a status or an
    event not converted (the aggregates are not asked for)."""
    if not isinstance(message, dict):
        return []
    event = message.get("ev")
    ticker = str(message.get("sym") or "")
    osi = osi_of(ticker) if ticker.startswith("O:") else None
    symbol = osi if osi is not None else spelled(ticker)
    if symbol is None:
        return []
    if event == "T":
        made = _trade(
            "stream.T",
            symbol,
            ms_ns(message.get("t")),
            message.get("x"),
            message.get("p"),
            message.get("s"),
            message.get("ds"),
            message.get("c"),
            message.get("q"),
            ms_ns(message.get("pt")),
            raw_key,
            osi=osi or "",
        )
        return [made] if made is not None else []
    if event == "Q":
        at = ms_ns(message.get("t"))
        if at is None:
            return []
        values = _Values("stream.Q")
        bid, bid_size = _side(values, message.get("bp"), message.get("bs"), "bp")
        ask, ask_size = _side(values, message.get("ap"), message.get("as"), "ap")
        quote = SourceQuote(
            symbol=symbol,
            at_ns=at,
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=ask_size,
            raw_key=raw_key,
            unconverted=tuple(values.unconverted),
            participant_ns=ms_ns(message.get("pt")),
            sequence=whole(message.get("q")),
        )
        return [quote]
    return []


# ── Options ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Contract:
    """One option contract's terms, as Massive's contracts list states them."""

    osi: str
    underlying: str
    put_or_call: str
    exercise_style: str
    settlement_method: str
    expiration: dt.date
    strike: Decimal
    multiplier: Decimal
    standard: bool
    raw_key: str
    unconverted: tuple[AsReported, ...] = ()


#: The CFI code's fifth letter for a listed option (ISO 10962): its delivery.
DELIVERY = {"P": "SETTLEMENT_METHOD_PHYSICAL", "C": "SETTLEMENT_METHOD_CASH"}
STYLES = {"american": "EXERCISE_STYLE_AMERICAN", "european": "EXERCISE_STYLE_EUROPEAN"}
RIGHTS = {"call": "call", "put": "put"}
#: What one standard equity contract delivers: 100 shares of its underlying.
STANDARD_SHARES = 100


def contracts(fetched: Fetched) -> Iterator[Contract | AsReported]:
    """Each contract of a contracts page; what did not convert as reported
    (an `AsReported`), for the Datasets page to count: a style Massive calls
    neither American nor European, a CFI code naming no delivery, a ticker
    that is no OSI symbol."""
    for index, item in enumerate(_results(fetched)):
        ticker = str(item.get("ticker") or "")
        osi = osi_of(ticker)
        if osi is None:
            yield as_reported(f"{SOURCE}:contracts.ticker", ticker[:64])
            continue
        style = STYLES.get(str(item.get("exercise_style") or "").lower())
        cfi = str(item.get("cfi") or "")
        delivery = DELIVERY.get(cfi[4]) if len(cfi) == 6 and cfi.startswith("O") else None
        right = RIGHTS.get(str(item.get("contract_type") or "").lower())
        strike = exact(item.get("strike_price"))
        multiplier = exact(item.get("shares_per_contract"))
        try:
            expiration = dt.date.fromisoformat(str(item.get("expiration_date")))
        except ValueError:
            expiration = None
        if style is None:
            yield as_reported(f"{SOURCE}:contracts.exercise_style", str(item.get("exercise_style")))
            continue
        if delivery is None:
            yield as_reported(f"{SOURCE}:contracts.cfi", cfi[:64])
            continue
        if right is None or strike is None or multiplier is None or expiration is None:
            yield as_reported(f"{SOURCE}:contracts.ticker", ticker[:64])
            continue
        extra = item.get("additional_underlyings")
        root = osi[:6].rstrip()
        standard = (
            delivery == "SETTLEMENT_METHOD_PHYSICAL"
            and not extra
            and multiplier == STANDARD_SHARES
            and root == str(item.get("underlying_ticker") or "")
        )
        yield Contract(
            osi=osi,
            underlying=str(item.get("underlying_ticker") or ""),
            put_or_call=right,
            exercise_style=style,
            settlement_method=delivery,
            expiration=expiration,
            strike=strike,
            multiplier=multiplier,
            standard=standard,
            raw_key=f"{fetched.raw_key}#{index}",
        )


@dataclass(frozen=True)
class ChainOption:
    """One contract of an expiration's chain snapshot: its quote (where the
    plan includes quotes), the day's volume, dated by the day's bar, and its
    open interest."""

    osi: str
    bid: Decimal | None
    ask: Decimal | None
    bid_size: Decimal | None
    ask_size: Decimal | None
    quote_at_ns: int | None
    volume: Decimal | None
    volume_date: dt.date | None
    open_interest: Decimal | None
    raw_key: str
    unconverted: tuple[AsReported, ...] = ()


def chain(fetched: Fetched) -> Iterator[ChainOption]:
    """Each contract of a chain snapshot's page."""
    for index, item in enumerate(_results(fetched)):
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        osi = osi_of(str(details.get("ticker") or ""))
        if osi is None:
            continue
        values = _Values("chain")
        quote = item.get("last_quote") if isinstance(item.get("last_quote"), dict) else {}
        bid, bid_size = _side(values, quote.get("bid"), quote.get("bid_size"), "bid")
        ask, ask_size = _side(values, quote.get("ask"), quote.get("ask_size"), "ask")
        day = item.get("day") if isinstance(item.get("day"), dict) else {}
        day_at = whole(day.get("last_updated"))
        volume = (
            values.number("volume", day.get("volume")) if day.get("volume") is not None else None
        )
        interest = item.get("open_interest")
        yield ChainOption(
            osi=osi,
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=ask_size,
            quote_at_ns=whole(quote.get("last_updated")) or None,
            volume=volume,
            volume_date=new_york_date(day_at) if day_at else None,
            open_interest=values.number("open_interest", interest)
            if interest is not None
            else None,
            raw_key=f"{fetched.raw_key}#{index}",
            unconverted=tuple(values.unconverted),
        )


# ── Reference ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Exchange:
    """One of Massive's exchange codes: its MIC and name."""

    code: str
    mic: str
    name: str


def exchanges(fetched: Fetched) -> dict[str, Exchange]:
    """Massive's exchanges by its code, each with its MIC where it states one."""
    out: dict[str, Exchange] = {}
    for item in _results(fetched):
        code = whole(item.get("id"))
        if code is None:
            continue
        mic = str(item.get("mic") or "")
        out[str(code)] = Exchange(str(code), mic if re.fullmatch(r"[A-Z0-9]{4}", mic) else "",
                                  str(item.get("name") or "")[:80])  # fmt: skip
    return out

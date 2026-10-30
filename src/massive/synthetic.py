"""Synthetic Massive: answers the plugin's calls from the built-in exchange,
with no key and no network (the `sample_data` setting, for development, the
tests and the e2e).

`synthetic/exchange.json` is shaped on Massive's documented responses with
every value invented (real responses stay in a developer's own storage,
never committed): daily aggregates for SPY, BRK.B and TQQQ over 2026-10-05
to 2026-10-09, TQQQ's last close to eighteen places; the stocks snapshot,
BRK.B's offer side left out; SPY's trade history; trade conditions with
their update rules and exchanges with their MICs; SPY's option chain --
generated for the first two Fridays after the day asked, so it is always
current, with one adjusted contract -- and the stream's events, stamped with
the moment they are sent. Its numbers are text in the file and go out as
JSON numbers, exactly as written, as Massive sends them; a list is paged at
`next_url`, a cursor at a time, as Massive pages one.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import time
import urllib.parse
from collections.abc import Callable, Mapping
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any

from .convert import NEW_YORK, osi_of
from .vendor import API, Response

NS = 1_000_000_000
MS = 1_000_000
#: A list's page, as the synthetic exchange pages one: small, so paging is
#: exercised.
PAGE = 4
#: The fields kept as text even where they look like numbers.
TEXT_FIELDS = frozenset({"ticker", "sym", "id", "i", "ds", "decimal_size", "date", "mic", "name"})


def exchange(path: Path | None = None) -> dict[str, Any]:
    """The built-in exchange, or the one at `path` (a developer's recording)."""
    if path is not None:
        loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return loaded
    text = resources.files("massive").joinpath("synthetic/exchange.json").read_text("utf-8")
    built: dict[str, Any] = json.loads(text)
    return built


def dumps(value: Any, key: str = "") -> str:
    """JSON with each number written as its text."""
    if isinstance(value, dict):
        return "{" + ",".join(f"{json.dumps(k)}:{dumps(v, k)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(dumps(v, key) for v in value) + "]"
    if isinstance(value, str) and key not in TEXT_FIELDS and re.fullmatch(r"-?\d+(\.\d+)?", value):
        return value
    return json.dumps(value)


def midnight_ms(day: str) -> int:
    """A New York date's midnight, in epoch milliseconds."""
    moment = dt.datetime.fromisoformat(day).replace(tzinfo=NEW_YORK)
    return int(moment.timestamp()) * 1000


def fridays(today: dt.date) -> list[dt.date]:
    """The first two Fridays after `today`: the synthetic chain's expirations."""
    ahead = (4 - today.weekday()) % 7 or 7
    first = today + dt.timedelta(days=ahead)
    return [first, first + dt.timedelta(days=7)]


def option_ticker(root: str, expiry: dt.date, right: str, strike: str) -> str:
    thousandths = int(Decimal(strike) * 1000)
    return f"O:{root}{expiry:%y%m%d}{right[0].upper()}{thousandths:08d}"


class SyntheticTransport:
    """vendor.Transport on the built-in exchange."""

    def __init__(
        self, held: Mapping[str, Any] | None = None, clock_ns: Callable[[], int] = time.time_ns
    ) -> None:
        self.held = dict(held) if held is not None else exchange()
        self.clock_ns = clock_ns
        self.asked: list[str] = []

    def __call__(self, url: str, headers: Mapping[str, str]) -> Response:
        self.asked.append(url)
        parsed = urllib.parse.urlparse(url)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        try:
            body = self.answer(parsed.path, query)
        except LookupError:
            return self._json(404, {"status": "NOT_FOUND", "message": "Not found"})
        return self._json(200, body)

    @staticmethod
    def _json(status: int, body: Any) -> Response:
        return Response(status, {"content-type": "application/json"}, dumps(body).encode())

    def _page(self, path: str, query: Mapping[str, str], rows: list[Any]) -> dict[str, Any]:
        start = int(query.get("cursor", "0") or 0)
        page = rows[start : start + PAGE]
        out: dict[str, Any] = {"status": "OK", "request_id": f"synthetic-{start}", "results": page}
        if start + PAGE < len(rows):
            following = {**query, "cursor": str(start + PAGE)}
            out["next_url"] = f"{API}{path}?{urllib.parse.urlencode(following)}"
        return out

    def today(self) -> dt.date:
        return dt.datetime.fromtimestamp(self.clock_ns() / NS, NEW_YORK).date()

    def answer(self, path: str, query: Mapping[str, str]) -> Any:
        held = self.held
        found = re.fullmatch(r"/v2/aggs/ticker/([^/]+)/range/1/day/([^/]+)/([^/]+)", path)
        if found:
            symbol, first, last = (urllib.parse.unquote(g) for g in found.groups())
            days = [
                {k: v for k, v in d.items() if k != "date"} | {"t": midnight_ms(d["date"])}
                for d in held["daily"].get(symbol, [])
                if first <= d["date"] <= last
            ]
            return {"ticker": symbol, "adjusted": False, "queryCount": len(days),
                    "resultsCount": len(days), "status": "OK", "results": days}  # fmt: skip
        if path == "/v2/snapshot/locale/us/markets/stocks/tickers":
            asked = set(query.get("tickers", "").split(","))
            tickers = [t for t in held["snapshot"] if t["ticker"] in asked]
            return {"status": "OK", "count": len(tickers), "tickers": tickers}
        found = re.fullmatch(r"/v3/trades/([^/]+)", path)
        if found:
            return self._page(path, query, self.trades(urllib.parse.unquote(found.group(1)), query))
        if path == "/v3/reference/conditions":
            return self._page(path, query, held["conditions"].get(query.get("asset_class", ""), []))
        if path == "/v3/reference/exchanges":
            rows = held["exchanges"].get(query.get("asset_class", ""), [])
            return {"status": "OK", "count": len(rows), "results": rows}
        if path == "/v3/reference/options/contracts":
            return self._page(path, query, self.contracts(query))
        found = re.fullmatch(r"/v3/snapshot/options/([^/]+)", path)
        if found:
            return self._page(path, query, self.chain(found.group(1), query))
        raise LookupError(path)

    def trades(self, symbol: str, query: Mapping[str, str]) -> list[Any]:
        """A listing's trade history: its trades, a minute before now."""
        base = (self.clock_ns() // MS - 60_000) * MS
        start = int(query.get("timestamp.gte", "0") or 0)
        end = int(query.get("timestamp.lt", "0") or 0) or 2**63
        rows = []
        for trade in self.held["trades"].get(symbol, []):
            at = base + int(trade["offset_ms"]) * MS
            if start <= at < end:
                said = {k: v for k, v in trade.items() if k != "offset_ms"}
                rows.append(said | {"sip_timestamp": at, "participant_timestamp": at - 210_000})
        return rows

    def _series(self, symbol: str) -> list[dict[str, Any]]:
        held = self.held["options"].get(symbol)
        if held is None:
            return []
        out: list[dict[str, Any]] = []
        for expiry in fridays(self.today()):
            for right in ("call", "put"):
                for strike in held["strikes"]:
                    out.append({"root": symbol, "expiry": expiry, "right": right, "strike": strike})
            adjusted = held["adjusted"]
            out.append(
                {
                    "root": adjusted["root"],
                    "expiry": expiry,
                    "right": "call",
                    "strike": adjusted["strike"],
                    "deliverables": adjusted["deliverables"],
                }
            )
        return out

    def contracts(self, query: Mapping[str, str]) -> list[Any]:
        symbol = query.get("underlying_ticker", "")
        first = query.get("expiration_date.gte", "")
        last = query.get("expiration_date.lte", "")
        rows = []
        for one in self._series(symbol):
            day = one["expiry"].isoformat()
            if (first and day < first) or (last and day > last):
                continue
            row = {
                "cfi": "OCASPS" if one["right"] == "call" else "OPASPS",
                "contract_type": one["right"],
                "exercise_style": "american",
                "expiration_date": day,
                "primary_exchange": "BATO",
                "shares_per_contract": 100,
                "strike_price": one["strike"],
                "ticker": option_ticker(one["root"], one["expiry"], one["right"], one["strike"]),
                "underlying_ticker": symbol,
            }
            if "deliverables" in one:
                row["additional_underlyings"] = one["deliverables"]
            rows.append(row)
        return rows

    def chain(self, symbol: str, query: Mapping[str, str]) -> list[Any]:
        held = self.held["options"].get(symbol)
        if held is None:
            raise LookupError(symbol)
        day = query.get("expiration_date", "")
        now = self.clock_ns()
        rows = []
        for one in self._series(symbol):
            if one["expiry"].isoformat() != day or "deliverables" in one:
                continue
            right = one["right"]
            quote = held["quote"][right]
            rows.append(
                {
                    "details": {
                        "contract_type": right,
                        "exercise_style": "american",
                        "expiration_date": day,
                        "shares_per_contract": 100,
                        "strike_price": one["strike"],
                        "ticker": option_ticker(symbol, one["expiry"], right, one["strike"]),
                    },
                    "day": {"volume": held["volume"][right], "last_updated": now - 60 * NS},
                    "last_quote": {**quote, "last_updated": now - 2 * NS, "timeframe": "REAL-TIME"},
                    "open_interest": held["open_interest"][right],
                    "underlying_asset": {"ticker": symbol, "timeframe": "REAL-TIME"},
                }
            )
        return rows


class SyntheticSocket:
    """A WebSocket on the built-in exchange's stream: it says connected,
    takes any key, and on each subscription sends the events of the tickers
    subscribed, stamped now; then falls silent, held open."""

    def __init__(self, cluster: str, held: Mapping[str, Any], clock_ns: Callable[[], int]) -> None:
        self.cluster, self.held, self.clock_ns = cluster, held, clock_ns
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.queue.put_nowait(dumps([{"ev": "status", "status": "connected",
                                      "message": "Connected Successfully"}]))  # fmt: skip
        self.closed = False

    async def send(self, text: str) -> None:
        said = json.loads(text)
        action = said.get("action")
        if action == "auth":
            await self.queue.put(dumps([{"ev": "status", "status": "auth_success",
                                         "message": "authenticated"}]))  # fmt: skip
            return
        if action != "subscribe":
            return
        tickers = sorted({p.split(".", 1)[1] for p in str(said.get("params", "")).split(",") if p})
        channels = {p.split(".", 1)[0] for p in str(said.get("params", "")).split(",") if p}
        now_ms = self.clock_ns() // MS
        events: list[Any] = []
        for template in self.held["stream"].get(self.cluster, []):
            if template["ev"] not in channels:
                continue
            for ticker in tickers if self.cluster == "options" else [template.get("sym")]:
                if ticker not in tickers or (self.cluster == "options" and not osi_of(ticker)):
                    continue
                event = {k: v for k, v in template.items() if k != "offset_ms"}
                at = now_ms + int(template["offset_ms"])
                event.update({"sym": ticker, "t": at})
                if self.cluster == "stocks":
                    event["pt"] = at - 1
                events.append(event)
        await self.queue.put(dumps([{"ev": "status", "status": "success",
                                     "message": f"subscribed to: {said['params']}"}]))  # fmt: skip
        if events:
            await self.queue.put(dumps(events))

    async def recv(self) -> str:
        if self.closed:
            from .ws import Closed

            raise Closed("closed")
        return await self.queue.get()

    async def close(self) -> None:
        self.closed = True


def synthetic_connect(
    held: Mapping[str, Any] | None = None, clock_ns: Callable[[], int] = time.time_ns
) -> Callable[[str], Any]:
    """A `connect` for stream.MassiveFeed on the built-in exchange."""
    source = dict(held) if held is not None else exchange()

    async def connect(url: str) -> SyntheticSocket:
        cluster = url.rstrip("/").rsplit("/", 1)[-1]
        return SyntheticSocket(cluster, source, clock_ns)

    return connect

"""Massive's REST API: the one module that speaks to it.

REST at api.massive.com (Massive was Polygon.io until October 2025; its
documentation is public at massive.com/docs): for stocks,
`/v2/aggs/ticker/{T}/range/1/day/{from}/{to}` (a listing's daily bars,
unadjusted), `/v2/snapshot/locale/us/markets/stocks/tickers` (many listings'
latest trade and best bid and offer) and `/v3/trades/{T}` (a listing's
trades over a range, each with its SIP sequence number); for options,
`/v3/reference/options/contracts` (an underlying's contracts, their terms)
and `/v3/snapshot/options/{underlying}` (one expiration's contracts, each
with its latest quote, the day's volume and its open interest; greeks and
implied volatility never read: the engine computes its own, ruling 565);
and `/v3/reference/conditions` and `/v3/reference/exchanges`, Massive's
trade conditions with their update rules and its exchange codes with their
MICs. Every answer is JSON; a list continues at `next_url`, on the same
host, which is followed and never anywhere else.

**The key.** Massive's API key, from `settings.Credentials`, goes in the
`Authorization` header and nowhere else: never in an address (Massive also
takes it as a query parameter; this client never sends it so), a raw
record or a log line. A `next_url` is followed only on api.massive.com, and
any text holding the key is redacted before it is kept, as a last guard.

**Pacing, by plan.** Massive's free plans (Basic) allow five calls a minute;
its paid plans state no limit. Each asset class has its own pace, set from
its plan (`pace`): five a minute for an end-of-day plan, otherwise at most
`PAID_PER_MINUTE`, one at a time. A 429 is waited out (its Retry-After,
else the plan's interval) and asked again at most three times, each counted
for the Connection page.

**Exact.** Prices are JSON numbers, parsed as Decimals from their own text,
never a float; integers stay integers (Massive's nanosecond timestamps
among them).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from meridian.edge import RawStore

from .settings import Credentials

API = "https://api.massive.com"
HOST = "api.massive.com"
#: Massive's WebSocket clusters, by timeliness: the real-time one and the
#: one 15 minutes delayed, each with a path per asset class.
STREAMS = {"real_time": "wss://socket.massive.com", "delayed": "wss://delayed.massive.com"}
USER_AGENT = "meridian-massive/0.1.1 (+https://open-meridian.dev)"
NS = 1_000_000_000

#: Massive's free (end-of-day) plans: five calls a minute.
EOD_PER_MINUTE = 5
#: Massive's paid plans state no limit; this client spends at most this.
PAID_PER_MINUTE = 300
#: The most listings one snapshot call names.
SYMBOLS_PER_CALL = 100
#: The most pages one read follows: a guard, far past any chain's.
MOST_PAGES = 200
#: The most a page holds, by endpoint: Massive's maxima.
TRADES_LIMIT = 50_000
CONTRACTS_LIMIT = 1_000
CHAIN_LIMIT = 250
CONDITIONS_LIMIT = 1_000
#: How many times a 429 is waited out before the call fails.
RETRIES = 3
KEPT_HEADERS = ("date", "content-type", "retry-after", "x-request-id")
REDACTED = "[redacted]"


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes


Transport = Callable[[str, Mapping[str, str]], Response]


def urllib_transport(url: str, headers: Mapping[str, str]) -> Response:
    """One GET over HTTPS, with the standard library."""
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=30) as answer:
            return Response(
                answer.status, {k.lower(): v for k, v in answer.headers.items()}, answer.read()
            )
    except urllib.error.HTTPError as answer:
        return Response(
            answer.code, {k.lower(): v for k, v in answer.headers.items()}, answer.read()
        )


def parse(text: str) -> Any:
    """JSON with every non-integer number a Decimal from its own text."""
    return json.loads(text, parse_float=Decimal)


class NoKey(Exception):
    """No key is set: nothing is asked."""


class VendorError(Exception):
    """Massive answered with an error: its status and what it said."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status} {detail}")
        self.status, self.detail = status, detail

    @property
    def not_entitled(self) -> bool:
        """Massive's answer to a call the key's plan does not include."""
        return self.status == 403


class RateLimiter:
    """At most `per_minute` calls a minute, evenly, one at a time."""

    def __init__(
        self,
        per_minute: int = PAID_PER_MINUTE,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.per_minute = per_minute
        self._clock, self._sleep = clock, sleep
        self._next = 0.0

    @property
    def interval(self) -> float:
        return 60.0 / self.per_minute

    def acquire(self) -> None:
        now = self._clock()
        if now < self._next:
            self._sleep(self._next - now)
            now = self._next
        self._next = now + self.interval


@dataclass
class Usage:
    """What the client spent, for the Connection page: never a key."""

    calls: int = 0
    rate_limited: int = 0
    last_status: int = 0
    last_call_at_ns: int = 0
    last_error: str = ""
    recent: deque[int] = field(default_factory=deque)

    def note(self, at_ns: int, status: int) -> None:
        self.calls += 1
        self.last_status = status
        self.last_call_at_ns = at_ns
        self.recent.append(at_ns)
        while self.recent and self.recent[0] <= at_ns - 60 * NS:
            self.recent.popleft()

    def last_minute(self, now_ns: int) -> int:
        return sum(1 for at in self.recent if at > now_ns - 60 * NS)


@dataclass(frozen=True)
class Fetched:
    """One answer: its parsed body and the raw record it was kept as."""

    value: Any
    raw_key: str
    status: int = 200


class Massive:
    """Massive's REST API, every answer kept first."""

    def __init__(
        self,
        store: RawStore,
        credentials: Credentials | None,
        transport: Transport = urllib_transport,
        base: str = API,
        keyless: bool = False,
        clock_ns: Callable[[], int] = time.time_ns,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store = store
        self.credentials = credentials
        self.transport = transport
        self.base = base.rstrip("/")
        self.keyless = keyless
        self.clock_ns = clock_ns
        self.sleep = sleep
        self.usage = Usage()
        self.limiters = {
            "stocks": RateLimiter(sleep=sleep),
            "options": RateLimiter(sleep=sleep),
        }

    def pace(self, asset_class: str, per_minute: int) -> None:
        """Each asset class's calls a minute, from its plan."""
        limiter = self.limiters[asset_class]
        if limiter.per_minute != per_minute:
            limiter.per_minute = per_minute

    # ── One call ─────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if self.credentials is not None:
            headers.update(self.credentials.headers())
        return headers

    def _redacted(self, text: str) -> str:
        if self.credentials is None:
            return text
        for secret in self.credentials.secrets():
            text = text.replace(secret, REDACTED)
        return text

    def _url(self, path: str, params: Mapping[str, object]) -> str:
        query = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None and v != ""}
        )
        return f"{self.base}{path}" + (f"?{query}" if query else "")

    def get_url(self, url: str, *, asset_class: str, call: str, name: str) -> Fetched:
        """GET `url`, on Massive's host alone: kept, then parsed. A 429 is
        waited out; any other status not 200 is a `VendorError`."""
        if self.credentials is None and not self.keyless:
            raise NoKey("no Massive API key: enter it in Settings")
        if urllib.parse.urlparse(url).hostname != urllib.parse.urlparse(self.base).hostname:
            raise VendorError(0, f"refused to follow a link off Massive's host: {url[:80]}")
        limiter = self.limiters[asset_class]
        for attempt in range(RETRIES + 1):
            limiter.acquire()
            sent = self.clock_ns()
            try:
                answer = self.transport(url, self._headers())
            except OSError as failed:
                self.usage.last_error = f"{type(failed).__name__}: {failed}"[:300]
                raise
            received = self.clock_ns()
            self.usage.note(received, answer.status)
            text = self._redacted(answer.body.decode("utf-8", errors="replace"))
            raw_key = self.store.keep(
                call,
                name,
                {
                    "received_at_ns": received,
                    "sent_at_ns": sent,
                    "request": {"method": "GET", "url": self._redacted(url)},
                    "status": answer.status,
                    "headers": {k: v for k, v in answer.headers.items() if k in KEPT_HEADERS},
                    "body": text,
                },
            )
            if answer.status == 429 and attempt < RETRIES:
                self.usage.rate_limited += 1
                wait = answer.headers.get("retry-after", "")
                self.sleep(float(wait) if wait.isdigit() else limiter.interval)
                continue
            if answer.status != 200:
                detail = _message(text) or f"HTTP {answer.status}"
                self.usage.last_error = f"{answer.status} {detail}"[:300]
                raise VendorError(answer.status, detail[:200])
            try:
                value = parse(text)
            except ValueError:
                self.usage.last_error = "not JSON"
                raise VendorError(answer.status, "the answer was not JSON") from None
            self.usage.last_error = ""
            return Fetched(value, raw_key, answer.status)
        raise VendorError(429, "rate limited")  # pragma: no cover - the loop returns or raises

    def get(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
        *,
        asset_class: str = "stocks",
        call: str,
        name: str,
    ) -> Fetched:
        return self.get_url(
            self._url(path, params or {}), asset_class=asset_class, call=call, name=name
        )

    def pages(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
        *,
        asset_class: str = "stocks",
        call: str,
        name: str,
    ) -> Iterator[Fetched]:
        """Every page of a list, following `next_url` on Massive's host."""
        url = self._url(path, params or {})
        for _ in range(MOST_PAGES):
            fetched = self.get_url(url, asset_class=asset_class, call=call, name=name)
            yield fetched
            following = fetched.value.get("next_url") if isinstance(fetched.value, dict) else None
            if not following:
                return
            url = str(following)

    # ── Stocks ───────────────────────────────────────────────────────────

    def daily(self, symbol: str, first: str, last: str) -> Iterator[Fetched]:
        """One listing's daily bars from `first` to `last`, unadjusted: the
        close as it traded, which a signal plugin adjusts."""
        return self.pages(
            f"/v2/aggs/ticker/{urllib.parse.quote(symbol)}/range/1/day/{first}/{last}",
            {"adjusted": "false", "sort": "asc", "limit": TRADES_LIMIT},
            call="daily",
            name=symbol,
        )

    def snapshot(self, symbols: Sequence[str]) -> Fetched:
        """Many listings' latest trade and best bid and offer."""
        return self.get(
            "/v2/snapshot/locale/us/markets/stocks/tickers",
            {"tickers": ",".join(symbols)},
            call="snapshot",
            name=symbols[0] if len(symbols) == 1 else f"{len(symbols)}-listings",
        )

    def trades(self, symbol: str, start_ns: int, end_ns: int) -> Iterator[Fetched]:
        """One listing's trades from `start_ns` to before `end_ns`, oldest
        first."""
        return self.pages(
            f"/v3/trades/{urllib.parse.quote(symbol)}",
            {
                "timestamp.gte": start_ns,
                "timestamp.lt": end_ns,
                "order": "asc",
                "sort": "timestamp",
                "limit": TRADES_LIMIT,
            },
            call="trades",
            name=symbol,
        )

    def conditions(self, asset_class: str) -> Iterator[Fetched]:
        """Massive's trade conditions of an asset class, with their update
        rules."""
        return self.pages(
            "/v3/reference/conditions",
            {"asset_class": asset_class, "data_type": "trade", "limit": CONDITIONS_LIMIT},
            asset_class=asset_class,
            call="conditions",
            name=asset_class,
        )

    def exchanges(self, asset_class: str) -> Fetched:
        """Massive's exchanges of an asset class: its codes, with their MICs."""
        return self.get(
            "/v3/reference/exchanges",
            {"asset_class": asset_class, "locale": "us"},
            asset_class=asset_class,
            call="exchanges",
            name=asset_class,
        )

    # ── Options ──────────────────────────────────────────────────────────

    def contracts(self, underlying: str, first: str, last: str = "") -> Iterator[Fetched]:
        """An underlying's option contracts expiring from `first` (to
        `last`, where given), as of today: their terms."""
        return self.pages(
            "/v3/reference/options/contracts",
            {
                "underlying_ticker": underlying,
                "expiration_date.gte": first,
                "expiration_date.lte": last,
                "order": "asc",
                "sort": "expiration_date",
                "limit": CONTRACTS_LIMIT,
            },
            asset_class="options",
            call="contracts",
            name=underlying,
        )

    def chain(self, underlying: str, expiration: str) -> Iterator[Fetched]:
        """One expiration's contracts of an underlying, each with its latest
        quote (where the plan includes quotes), the day's bar and its open
        interest."""
        return self.pages(
            f"/v3/snapshot/options/{urllib.parse.quote(underlying)}",
            {"expiration_date": expiration, "limit": CHAIN_LIMIT},
            asset_class="options",
            call="chain",
            name=f"{underlying}-{expiration}",
        )


def _message(text: str) -> str:
    """What Massive said of an error: its `message` or `error`, else none."""
    try:
        said = json.loads(text)
    except ValueError:
        return ""
    if not isinstance(said, dict):
        return ""
    return str(said.get("message") or said.get("error") or said.get("status") or "")

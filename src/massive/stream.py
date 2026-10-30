"""Massive's market data streams, over WebSocket, kept open (contract v19).

What is streamed is what the lake keeps current: the listings under a
standing want of the stock datasets (lake.py says which) on the stocks
cluster, and the option series under a standing want of option trades
(chains.py) on the options cluster; nothing while there is none; nothing of
it kept here. One session per cluster, as Massive allows one connection per
asset class by default.

**Massive's protocol** (massive.com/docs/websocket/quickstart): connect to
the cluster of the plan's timeliness -- `wss://socket.massive.com/stocks`
real time, `wss://delayed.massive.com/stocks` 15 minutes delayed, the same
with `/options` -- and Massive says `connected`; send the key in an `auth`
message and wait for `auth_success` (`auth_failed`: the key is refused,
tried again only after a while); then `subscribe` and `unsubscribe` with a
channel and a ticker each (`T.SPY`, `Q.SPY`, `T.O:SPY261016C00575000`).
Massive answers with JSON arrays of events, many to a frame at busy times.
Stocks are streamed as trades and, on a real-time plan, quotes (a delayed
plan includes none); options as trades.

**Reconnect.** A connection that breaks, closes or goes silent is opened
again a second later, then doubling to a minute; the subscriptions are sent
again from what is wanted. The gap is caught up from Massive's trade history
by the SDK's feed, a trade recorded already never sent again (lake.py). Each
reconnect is counted on the Connection page.

**Every frame is a raw record first**, a line of the day's stream record,
before anything converts it; the key is never in one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from meridian.edge import RawStore

from . import convert, ws
from .settings import Credentials

log = logging.getLogger("massive.stream")

#: How long a session may say nothing before it is taken for broken: a
#: quiet listing may print nothing for minutes, so long.
SILENT_SECONDS = 600.0
#: How often what is heard is flushed to the lake, at the most.
FLUSH_SECONDS = 0.5
#: The waits between sessions: a second, doubling to a minute.
FIRST_WAIT, MOST_WAIT = 1.0, 60.0
#: After the key is refused, how long before it is tried again.
REFUSED_WAIT = 300.0
#: How many subscriptions one message names at most.
PER_MESSAGE = 200
#: How long the connected and authenticated answers are waited for.
ANSWER_SECONDS = 15.0


class Refused(Exception):
    """Massive refused the key: tried again only after a while."""


class FeedError(Exception):
    """Massive said something went wrong in the session."""


class Feed(Protocol):
    """One session with a stream: opened, subscribed, read, closed."""

    name: str

    async def open(self) -> None: ...

    async def subscribe(self, symbols: Sequence[str]) -> None: ...

    async def receive(self) -> list[Any]: ...

    async def close(self) -> None: ...


def _textual(value: Any) -> Any:
    """A parsed message with each number as its text, as it arrived."""
    if isinstance(value, dict):
        return {k: _textual(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_textual(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


class MassiveFeed:
    """Feed on one of Massive's clusters (`stocks`, `options`), every frame
    kept. `channels` are the events streamed for each ticker (`T`, `Q`)."""

    def __init__(
        self,
        cluster: str,
        base: str,
        credentials: Credentials,
        channels: Sequence[str],
        store: RawStore,
        connect: Callable[[str], Awaitable[Any]] = ws.connect,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.cluster, self.base, self.credentials = cluster, base.rstrip("/"), credentials
        self.channels = tuple(channels)
        self.store, self.connect, self.clock_ns = store, connect, clock_ns
        self.name = f"massive {cluster}"
        self.socket: Any = None
        self.subscribed: list[str] = []
        self.said = ""

    def _keep(self, text: str) -> str:
        return self.store.keep_line(
            "stream", self.cluster, {"received_at_ns": self.clock_ns(), "body": text}
        )

    async def _statuses(self) -> list[dict[str, Any]]:
        text = await asyncio.wait_for(self.socket.recv(), ANSWER_SECONDS)
        self._keep(text)
        said = json.loads(text)
        return [m for m in said if isinstance(m, dict)] if isinstance(said, list) else []

    async def open(self) -> None:
        self.subscribed = []
        self.socket = await self.connect(f"{self.base}/{self.cluster}")
        for _ in range(3):
            statuses = await self._statuses()
            if any(m.get("status") == "connected" for m in statuses):
                break
        await self.socket.send(json.dumps({"action": "auth", "params": self.credentials.key}))
        for _ in range(3):
            for message in await self._statuses():
                status = str(message.get("status") or "")
                if status == "auth_success":
                    return
                if status in ("auth_failed", "auth_timeout"):
                    raise Refused(str(message.get("message") or status)[:200])
        raise FeedError("Massive did not answer the authentication")

    def _params(self, tickers: Sequence[str]) -> list[str]:
        named = [f"{channel}.{ticker}" for ticker in tickers for channel in self.channels]
        return [",".join(named[i : i + PER_MESSAGE]) for i in range(0, len(named), PER_MESSAGE)]

    async def subscribe(self, symbols: Sequence[str]) -> None:
        wanted = sorted(symbols)
        gone = [s for s in self.subscribed if s not in wanted]
        added = [s for s in wanted if s not in self.subscribed]
        for params in self._params(gone):
            await self.socket.send(json.dumps({"action": "unsubscribe", "params": params}))
        for params in self._params(added):
            await self.socket.send(json.dumps({"action": "subscribe", "params": params}))
        self.subscribed = wanted

    async def receive(self) -> list[Any]:
        text = await self.socket.recv()
        key = self._keep(text)
        try:
            said = json.loads(text, parse_float=Decimal)
        except ValueError:
            raise FeedError(f"not JSON: {text[:80]!r}") from None
        out: list[Any] = []
        for message in said if isinstance(said, list) else [said]:
            if isinstance(message, dict) and message.get("ev") == "status":
                status = str(message.get("status") or "")
                if status in ("auth_failed", "auth_timeout"):
                    raise Refused(str(message.get("message") or status)[:200])
                if status not in ("success", "connected", "auth_success"):
                    # A subscription the plan does not include, or a limit:
                    # said on the Connection page, the session kept.
                    self.said = f"{status}: {message.get('message') or ''}"[:300]
                continue
            out.extend(convert.streamed(message, key))
        return out

    async def close(self) -> None:
        if self.socket is not None:
            with contextlib.suppress(Exception):
                await self.socket.close()
        self.socket = None


@dataclass
class Session:
    """A stream as the Connection page shows it: never a key."""

    state: str = "idle"
    subscribed: int = 0
    opened: int = 0
    reconnects: int = 0
    messages: int = 0
    last_message_at_ns: int = 0
    last_error: str = ""
    since_ns: int = 0
    symbols: list[str] = field(default_factory=list)


class Restart(Exception):
    """The settings changed: the session is closed and opened again."""


class Streamer:
    """Keeps one session open while anything is wanted, subscribed to what
    is; hands what it hears to `heard`, and flushes with `flush`."""

    def __init__(
        self,
        make_feed: Callable[[], Feed | None],
        heard: Callable[[list[Any]], Awaitable[None]],
        flush: Callable[[], Awaitable[None]],
        clock_ns: Callable[[], int] = time.time_ns,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        silent_seconds: float = SILENT_SECONDS,
    ) -> None:
        self.make_feed, self.heard, self.flush = make_feed, heard, flush
        self.clock_ns, self.sleep, self.silent_seconds = clock_ns, sleep, silent_seconds
        self.wanted: list[str] = []
        self.session = Session()
        self._changed = asyncio.Event()
        self._restart = False
        #: Called each time a session opens anew after the first, with the
        #: moment the last one was last heard: the gap to catch up.
        self.reopened: Callable[[int], Awaitable[None]] | None = None

    def want(self, symbols: Sequence[str]) -> None:
        """Stream these, and only these, from now on."""
        if list(symbols) != self.wanted:
            self.wanted = list(symbols)
            self._changed.set()

    def restart(self) -> None:
        """Open the session again: the key or the plan changed."""
        self._restart = True
        self._changed.set()

    async def _idle(self, state: str) -> None:
        self.session.state = state
        self.session.subscribed, self.session.symbols = 0, []
        self._changed.clear()
        self._restart = False
        await self._changed.wait()

    async def run(self, sessions: int | None = None) -> None:
        """Forever (or for `sessions` sessions, in the tests)."""
        wait = FIRST_WAIT
        held = 0
        while sessions is None or held < sessions:
            if not self.wanted:
                await self._idle("idle")
                continue
            feed = self.make_feed()
            if feed is None:
                await self._idle("no key")
                continue
            held += 1
            self._restart = False
            try:
                await self._session(feed)
                wait = FIRST_WAIT
                continue
            except Restart:
                wait = FIRST_WAIT
                continue
            except Refused as refused:
                self.session.state, self.session.last_error = "refused", f"refused: {refused}"
                log.warning("Massive refused the stream: %s", refused)
                await self._pause(REFUSED_WAIT)
                continue
            except (ws.Closed, FeedError, OSError, TimeoutError, ValueError) as broken:
                self.session.last_error = f"{type(broken).__name__}: {broken}"[:300]
                log.warning("the stream stopped: %s", self.session.last_error)
            finally:
                await feed.close()
            self.session.reconnects += 1
            self.session.state = "waiting"
            await self._pause(wait)
            wait = min(wait * 2, MOST_WAIT)

    async def _pause(self, seconds: float) -> None:
        """Wait, or less where what is wanted changes meanwhile."""
        if self._restart:
            return
        changed = asyncio.ensure_future(self._changed.wait())
        slept = asyncio.ensure_future(self.sleep(seconds))
        try:
            await asyncio.wait({changed, slept}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            changed.cancel()
            slept.cancel()

    async def _session(self, feed: Feed) -> None:
        self.session.state = "connecting"
        await feed.open()
        last_heard = self.session.last_message_at_ns
        self.session.opened += 1
        self.session.since_ns = self.clock_ns()
        self._changed.clear()
        await feed.subscribe(self.wanted)
        self._note_subscribed()
        self.session.state = "streaming"
        if self.session.opened > 1 and last_heard and self.reopened is not None:
            await self.reopened(last_heard)
        pending: asyncio.Future[list[Any]] | None = None
        quiet_since = time.monotonic()
        try:
            while True:
                if pending is None:
                    pending = asyncio.ensure_future(feed.receive())
                changed: asyncio.Future[Any] = asyncio.ensure_future(self._changed.wait())
                done, _ = await asyncio.wait(
                    {pending, changed}, timeout=FLUSH_SECONDS, return_when=asyncio.FIRST_COMPLETED
                )
                if changed in done:
                    self._changed.clear()
                    if self._restart:
                        raise Restart()
                    if not self.wanted:
                        return
                    await feed.subscribe(self.wanted)
                    self._note_subscribed()
                else:
                    changed.cancel()
                if pending in done:
                    stated = pending.result()
                    pending = None
                    quiet_since = time.monotonic()
                    now = self.clock_ns()
                    self.session.messages += 1
                    self.session.last_message_at_ns = now
                    said = getattr(feed, "said", "")
                    if said:
                        self.session.last_error = said
                    await self.heard(stated)
                elif time.monotonic() - quiet_since > self.silent_seconds:
                    raise ws.Closed(f"nothing heard for {self.silent_seconds:.0f} seconds")
                await self.flush()
        finally:
            if pending is not None:
                pending.cancel()
                with contextlib.suppress(BaseException):
                    await pending
            await self.flush()

    def _note_subscribed(self) -> None:
        self.session.subscribed = len(self.wanted)
        self.session.symbols = list(self.wanted)

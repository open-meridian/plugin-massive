"""Massive's streams (stream.py, ws.py): the key sent in the `auth` message
alone, subscriptions sent as what is wanted changes, a refused key raised,
each frame kept, the events handed on; a session reopened after a drop; the
WebSocket's frames as RFC 6455 says."""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path
from typing import Any

import pytest
from meridian.edge import RawStore

from massive import ws
from massive.settings import Credentials
from massive.stream import MassiveFeed, Refused, Streamer
from massive.synthetic import exchange, synthetic_connect

KEY = Credentials("massive-test-key-000000000000000000000002")


class Socket:
    """A socket scripted with what the server says, recording what is sent."""

    def __init__(self, *said: Any) -> None:
        self.said = [json.dumps(s) for s in said]
        self.sent: list[dict[str, Any]] = []

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def recv(self) -> str:
        if not self.said:
            raise ws.Closed("nothing more")
        return self.said.pop(0)

    async def close(self) -> None:
        pass


def feed(tmp: Path, socket: Socket, channels: tuple[str, ...] = ("T", "Q")) -> MassiveFeed:
    async def connect(url: str) -> Socket:
        assert url == "wss://socket.massive.com/stocks"
        return socket

    return MassiveFeed(
        "stocks",
        "wss://socket.massive.com",
        KEY,
        channels,
        RawStore(tmp, kind="responses"),
        connect=connect,
    )


CONNECTED = [{"ev": "status", "status": "connected", "message": "Connected Successfully"}]
AUTHENTICATED = [{"ev": "status", "status": "auth_success", "message": "authenticated"}]


def test_a_session_authenticates_then_subscribes(tmp_path: Path) -> None:
    socket = Socket(
        CONNECTED,
        AUTHENTICATED,
        [
            {"ev": "T", "sym": "SPY", "x": 12, "p": 673.8, "s": 100, "t": 1, "q": 4},
            {"ev": "status", "status": "error", "message": "not entitled to Q.SPY"},
        ],
    )
    held = feed(tmp_path, socket)

    async def go() -> list[Any]:
        await held.open()
        await held.subscribe(["SPY", "BRK.B"])
        await held.subscribe(["SPY"])
        return await held.receive()

    stated = asyncio.run(go())
    assert socket.sent[0] == {"action": "auth", "params": KEY.key}
    assert socket.sent[1] == {"action": "subscribe", "params": "T.BRK.B,Q.BRK.B,T.SPY,Q.SPY"}
    assert socket.sent[2] == {"action": "unsubscribe", "params": "T.BRK.B,Q.BRK.B"}
    assert [s.symbol for s in stated] == ["SPY"] and str(stated[0].price) == "673.8"
    assert "not entitled" in held.said
    kept = list(RawStore(tmp_path, kind="responses").records())
    assert len(kept) == 3 and not any(KEY.key in json.dumps(k) for k in kept)


def test_a_refused_key_is_raised(tmp_path: Path) -> None:
    socket = Socket(CONNECTED, [{"ev": "status", "status": "auth_failed", "message": "bad"}])
    with pytest.raises(Refused, match="bad"):
        asyncio.run(feed(tmp_path, socket).open())


def test_a_delayed_plan_streams_trades_alone(tmp_path: Path) -> None:
    socket = Socket(CONNECTED, AUTHENTICATED)
    held = feed(tmp_path, socket, channels=("T",))

    async def go() -> None:
        await held.open()
        await held.subscribe(["SPY"])

    asyncio.run(go())
    assert socket.sent[1] == {"action": "subscribe", "params": "T.SPY"}


def test_the_streamer_hands_the_synthetic_stream_on(tmp_path: Path) -> None:
    heard: list[Any] = []

    async def hear(stated: list[Any]) -> None:
        heard.extend(stated)

    async def flush() -> None:
        pass

    store = RawStore(tmp_path, kind="responses")
    connect = synthetic_connect(exchange())

    def make() -> MassiveFeed:
        return MassiveFeed(
            "stocks", "wss://synthetic.invalid", KEY, ("T", "Q"), store, connect=connect
        )

    streamer = Streamer(make, hear, flush, silent_seconds=0.2)
    streamer.want(["SPY"])

    async def go() -> None:
        # The SDK's supervisor (0.35): one session held.
        task = asyncio.ensure_future(streamer.run(once=True))
        for _ in range(100):
            if len(heard) >= 4:
                break
            await asyncio.sleep(0.01)
        streamer.want([])
        await asyncio.wait_for(task, 5)

    asyncio.run(go())
    assert {type(s).__name__ for s in heard} == {"SourceTrade", "SourceQuote"}
    assert all(s.symbol == "SPY" for s in heard)
    assert streamer.state.opened == 1 and streamer.state.messages >= 1


def test_a_dropped_session_is_opened_again(tmp_path: Path) -> None:
    opened: list[int] = []
    reopened: list[int] = []

    def make() -> MassiveFeed:
        opened.append(1)
        socket = Socket(
            CONNECTED,
            AUTHENTICATED,
            [{"ev": "T", "sym": "SPY", "x": 1, "p": 1, "s": 1, "t": 1, "q": 1}],
        )
        return feed(tmp_path, socket)

    async def nothing(*_: Any) -> None:
        pass

    async def no_sleep(_: float) -> None:
        pass

    async def caught(since: dict[str, int]) -> int:
        reopened.append(since["SPY"])
        return 0

    streamer = Streamer(make, nothing, nothing, caught_up=caught, sleep=no_sleep)
    streamer.want(["SPY"])

    async def twice() -> None:
        # The SDK's supervisor (0.35): each session held once.
        await streamer.run(once=True)
        await streamer.run(once=True)

    asyncio.run(asyncio.wait_for(twice(), 5))
    assert len(opened) == 2 and streamer.state.reconnects == 2
    assert len(reopened) == 1 and reopened[0] > 0


def test_frames_as_rfc_6455_says() -> None:
    async def go() -> tuple[str, bytes]:
        reader = asyncio.StreamReader()
        written: list[bytes] = []

        class Writer:
            def write(self, data: bytes) -> None:
                written.append(data)

            async def drain(self) -> None:
                pass

            def close(self) -> None:
                pass

            async def wait_closed(self) -> None:
                pass

        # The server's frames: a ping, then a text message in two fragments.
        reader.feed_data(bytes([0x89, 2]) + b"hi")
        reader.feed_data(bytes([0x01, 3]) + b'[{"')
        reader.feed_data(bytes([0x80, 2]) + b"}]")
        socket = ws.WebSocket(reader, Writer())  # type: ignore[arg-type]
        text = await socket.recv()
        reader.feed_data(bytes([0x88, 2]) + struct.pack("!H", 1000))
        with pytest.raises(ws.Closed, match="1000"):
            await socket.recv()
        return text, written[0]

    text, pong = asyncio.run(go())
    assert text == '[{"}]'
    # The pong, masked as a client's frame must be, carrying the ping's data.
    assert pong[0] == 0x8A and pong[1] == 0x80 | 2
    mask = pong[2:6]
    assert bytes(b ^ mask[i % 4] for i, b in enumerate(pong[6:])) == b"hi"


def test_a_message_that_converts_to_nothing_is_still_handed_on(tmp_path: Path) -> None:
    # A status frame converts to no statement, yet is the stream's sign of
    # life: handed on empty, so the feed's liveness is recorded.
    handed: list[list[Any]] = []

    async def hear(stated: list[Any]) -> None:
        handed.append(list(stated))

    async def nothing(*_: Any) -> None:
        pass

    socket = Socket(
        CONNECTED, AUTHENTICATED, [{"ev": "status", "status": "success", "message": "subscribed"}]
    )
    streamer = Streamer(lambda: feed(tmp_path, socket), hear, nothing)
    streamer.want(["SPY"])
    asyncio.run(asyncio.wait_for(streamer.run(once=True), 5))
    assert handed == [[]] and streamer.state.messages == 1

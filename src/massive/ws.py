"""A WebSocket client on asyncio's streams, as Massive's stream needs one (RFC
6455, client side): the plugin depends on nothing but the SDK, so there is
no WebSocket library. The transport is this plugin's own, which the SDK
leaves to it.

A TLS connection; the HTTP upgrade, its accept checked against the key sent;
text frames sent masked, as a client must; text and binary messages
received, their fragments joined; a ping answered with its pong; a close
answered and raised as `Closed`. No extension is asked for, so nothing
arrives compressed.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import os
import ssl
import struct
import urllib.parse

#: The accept's suffix (RFC 6455, 1.3).
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
#: The longest message read: Massive's frames are far shorter.
MOST_BYTES = 16 * 1024 * 1024
TEXT, BINARY, CLOSE, PING, PONG, CONTINUATION = 0x1, 0x2, 0x8, 0x9, 0xA, 0x0
USER_AGENT = "meridian-massive/0.1.1 (+https://open-meridian.dev)"


class Closed(Exception):
    """The connection closed, or broke."""


def _frame(opcode: int, payload: bytes) -> bytes:
    """One final frame, masked."""
    head = bytes([0x80 | opcode])
    size = len(payload)
    if size < 126:
        head += bytes([0x80 | size])
    elif size < 1 << 16:
        head += bytes([0x80 | 126]) + struct.pack("!H", size)
    else:
        head += bytes([0x80 | 127]) + struct.pack("!Q", size)
    mask = os.urandom(4)
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


class WebSocket:
    """One client connection."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader, self.writer = reader, writer
        self.closed = False

    async def send(self, text: str) -> None:
        if self.closed:
            raise Closed("the connection is closed")
        self.writer.write(_frame(TEXT, text.encode("utf-8")))
        await self.writer.drain()

    async def _read_frame(self) -> tuple[bool, int, bytes]:
        try:
            first, second = await self.reader.readexactly(2)
            size = second & 0x7F
            if size == 126:
                (size,) = struct.unpack("!H", await self.reader.readexactly(2))
            elif size == 127:
                (size,) = struct.unpack("!Q", await self.reader.readexactly(8))
            if size > MOST_BYTES:
                raise Closed(f"a frame of {size} bytes, past {MOST_BYTES}")
            mask = await self.reader.readexactly(4) if second & 0x80 else b""
            payload = await self.reader.readexactly(size)
        except asyncio.IncompleteReadError as cut:
            raise Closed("the connection broke") from cut
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return bool(first & 0x80), first & 0x0F, payload

    async def recv(self) -> str:
        """The next message, as text; `Closed` once the connection closes."""
        parts: list[bytes] = []
        while True:
            final, opcode, payload = await self._read_frame()
            if opcode == PING:
                self.writer.write(_frame(PONG, payload))
                await self.writer.drain()
                continue
            if opcode == PONG:
                continue
            if opcode == CLOSE:
                with contextlib.suppress(Exception):
                    self.writer.write(_frame(CLOSE, payload[:2]))
                    await self.writer.drain()
                self.closed = True
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1005
                reason = payload[2:].decode("utf-8", errors="replace")
                raise Closed(f"closed by the server: {code} {reason}".strip())
            if opcode not in (TEXT, BINARY, CONTINUATION):
                raise Closed(f"an unknown opcode {opcode}")
            parts.append(payload)
            if sum(len(p) for p in parts) > MOST_BYTES:
                raise Closed("a message past the most read")
            if final:
                return b"".join(parts).decode("utf-8", errors="replace")

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            with contextlib.suppress(Exception):
                self.writer.write(_frame(CLOSE, struct.pack("!H", 1000)))
                await self.writer.drain()
        with contextlib.suppress(Exception):
            self.writer.close()
            await self.writer.wait_closed()


async def connect(url: str, timeout: float = 15.0) -> WebSocket:
    """Open `url` (wss:// or ws://), upgraded to a WebSocket."""
    parsed = urllib.parse.urlparse(url)
    secure = parsed.scheme == "wss"
    host = parsed.hostname or ""
    port = parsed.port or (443 if secure else 80)
    context = ssl.create_default_context() if secure else None
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(
            host, port, ssl=context, server_hostname=host if secure else None,
            limit=MOST_BYTES,
        ),
        timeout,
    )  # fmt: skip
    key = base64.b64encode(os.urandom(16)).decode()
    path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
    writer.write(
        (
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\nUser-Agent: {USER_AGENT}\r\n\r\n"
        ).encode()
    )
    await writer.drain()
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as cut:
        writer.close()
        raise Closed("the upgrade was not answered") from cut
    lines = head.decode("latin-1").split("\r\n")
    if " 101 " not in f"{lines[0]} ":
        writer.close()
        raise Closed(f"the upgrade was refused: {lines[0][:80]}")
    headers = {
        name.strip().lower(): value.strip()
        for name, _, value in (line.partition(":") for line in lines[1:] if line)
    }
    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    if headers.get("sec-websocket-accept") != accept:
        writer.close()
        raise Closed("the upgrade's accept did not match its key")
    return WebSocket(reader, writer)

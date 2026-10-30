"""The one module that speaks to Massive (vendor.py): the key in its header
and nowhere else, every answer kept first, a list followed on Massive's host
alone, a 429 waited out, a refusal raised, the pace a plan allows."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from meridian.edge import RawStore

from massive.settings import Credentials
from massive.vendor import API, Massive, NoKey, RateLimiter, Response, VendorError

KEY = Credentials("massive-test-key-000000000000000000000001")


class Scripted:
    """A transport answering from a script, recording what it was asked."""

    def __init__(self, *answers: Response) -> None:
        self.answers = list(answers)
        self.asked: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: Mapping[str, str]) -> Response:
        self.asked.append((url, dict(headers)))
        return self.answers.pop(0)


def ok(body: object, status: int = 200, **headers: str) -> Response:
    return Response(status, headers, json.dumps(body).encode())


def client(tmp: Path, transport: Scripted, credentials: Credentials | None = KEY) -> Massive:
    massive = Massive(RawStore(tmp, kind="responses"), credentials, transport=transport,
                      sleep=lambda _: None)  # fmt: skip
    for asset in ("stocks", "options"):
        massive.limiters[asset] = RateLimiter(per_minute=6_000_000, sleep=lambda _: None)
    return massive


def test_the_key_goes_in_the_header_alone(tmp_path: Path) -> None:
    transport = Scripted(ok({"status": "OK", "results": [], "echo": KEY.key}))
    massive = client(tmp_path, transport)
    fetched = massive.exchanges("stocks")
    url, headers = transport.asked[0]
    assert headers["Authorization"] == f"Bearer {KEY.key}"
    assert KEY.key not in url and "apiKey" not in url
    assert url.startswith(f"{API}/v3/reference/exchanges?")
    kept = RawStore(tmp_path, kind="responses").get(fetched.raw_key)
    assert kept is not None and KEY.key not in json.dumps(kept)
    assert "[redacted]" in kept["body"] and kept["status"] == 200
    assert "Authorization" not in json.dumps(kept)
    assert KEY.key not in repr(KEY)


def test_no_key_asks_nothing(tmp_path: Path) -> None:
    transport = Scripted()
    with pytest.raises(NoKey):
        client(tmp_path, transport, credentials=None).exchanges("stocks")
    assert transport.asked == []


def test_a_list_is_followed_on_massives_host_alone(tmp_path: Path) -> None:
    following = f"{API}/v3/reference/options/contracts?cursor=abc"
    transport = Scripted(
        ok({"status": "OK", "results": [{"n": 1}], "next_url": following}),
        ok({"status": "OK", "results": [{"n": 2}], "next_url": "https://elsewhere.example/x"}),
    )
    massive = client(tmp_path, transport)
    pages = massive.contracts("SPY", "2026-10-12")
    assert next(pages).value["results"] == [{"n": 1}]
    assert next(pages).value["results"] == [{"n": 2}]
    with pytest.raises(VendorError, match="off Massive's host"):
        next(pages)
    assert [url for url, _ in transport.asked][1] == following
    assert all("elsewhere" not in url for url, _ in transport.asked)


def test_a_rate_limit_is_waited_out(tmp_path: Path) -> None:
    transport = Scripted(
        ok({"status": "ERROR"}, status=429, **{"retry-after": "2"}), ok({"status": "OK"})
    )
    waited: list[float] = []
    massive = client(tmp_path, transport)
    massive.sleep = waited.append
    assert massive.exchanges("stocks").value == {"status": "OK"}
    assert waited == [2.0] and massive.usage.rate_limited == 1 and massive.usage.calls == 2


def test_a_plan_that_does_not_include_a_call_is_refused(tmp_path: Path) -> None:
    said = {"status": "NOT_AUTHORIZED", "message": "You are not entitled to this data."}
    transport = Scripted(ok(said, status=403))
    massive = client(tmp_path, transport)
    with pytest.raises(VendorError) as refused:
        massive.snapshot(["SPY"])
    assert refused.value.not_entitled and "not entitled" in refused.value.detail
    assert massive.usage.last_status == 403 and "403" in massive.usage.last_error


def test_numbers_are_read_exactly(tmp_path: Path) -> None:
    transport = Response(
        200, {}, b'{"results": [{"c": 93.123456789012345678, "t": 1791576000123456789}]}'
    )
    massive = client(tmp_path, Scripted(transport))
    value = massive.get("/v2/x", call="t", name="t").value["results"][0]
    assert str(value["c"]) == "93.123456789012345678"
    assert value["t"] == 1791576000123456789


def test_the_pace_of_an_end_of_day_plan() -> None:
    slept: list[float] = []
    now = [0.0]
    limiter = RateLimiter(per_minute=5, clock=lambda: now[0], sleep=slept.append)
    limiter.acquire()
    limiter.acquire()
    assert slept == [12.0]

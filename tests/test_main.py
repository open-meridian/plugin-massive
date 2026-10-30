"""The process's choices from its settings (`__main__`): the client and its
pace by plan, the stream of each cluster by plan, and the pages on sample
data (preview.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from meridian.edge import RawStore

from massive.__main__ import feed_for, source_for
from massive.page import State
from massive.preview import PAGES, main
from massive.settings import config_from

KEY = "massive-test-key-000000000000000000000004"


def state(**values: object) -> State:
    return State(config=config_from(values))


def test_no_key_no_client_no_stream(tmp_path: Path) -> None:
    held = state()
    store = RawStore(tmp_path, kind="responses")
    assert source_for(held, store) == (None, None)
    assert feed_for(held, store, "stocks") is None


def test_each_plan_paces_and_streams_its_own(tmp_path: Path) -> None:
    held = state(
        massive_api_key=KEY, stocks_timeliness="end_of_day", options_timeliness="real_time"
    )
    store = RawStore(tmp_path, kind="responses")
    massive, source = source_for(held, store)
    assert massive is not None and source is not None
    assert massive.limiters["stocks"].per_minute == 5
    assert massive.limiters["options"].per_minute == 300
    assert feed_for(held, store, "stocks") is None
    options = feed_for(held, store, "options")
    assert options is not None and options.base == "wss://socket.massive.com"
    assert options.channels == ("T",)
    delayed = state(massive_api_key=KEY, stocks_timeliness="delayed")
    stocks = feed_for(delayed, store, "stocks")
    assert stocks is not None and stocks.base == "wss://delayed.massive.com"
    assert stocks.channels == ("T",)
    real = feed_for(state(massive_api_key=KEY, stocks_timeliness="real_time"), store, "stocks")
    assert real is not None and real.channels == ("T", "Q")


def test_an_unset_plan_is_delayed() -> None:
    config = config_from({"stocks_timeliness": "real_time"}, unset=["stocks_timeliness"])
    assert config.stocks == "delayed" and config.options == "delayed"
    assert config_from({"stocks_timeliness": "weekly"}).stocks == "delayed"
    assert config_from({"sample_data": True}).timeliness("options") == "real_time"


@pytest.mark.parametrize("page", sorted(PAGES))
def test_each_preview_page_renders(page: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["preview", page]) == 0
    shown = capsys.readouterr().out
    assert "<h1>Massive</h1>" in shown and KEY not in shown

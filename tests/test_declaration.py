"""The version's declaration as the upload reads it (contract v25): printed
from the plugin's root, as `meridian-declaration` prints it in the built
image, it carries the catalogue, the settings (the key secret), the storage,
the interface -- its pages and the tools their routes make -- and each
section of docs/ by its digest and size."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from meridian.declaration import main

from massive import declaration as version
from massive.declared import CATALOGUE, DELAY_NS
from massive.lake import US_EQUITY_CALENDAR
from massive.settings import SETTINGS

ROOT = Path(__file__).resolve().parents[1]
NAMED = "massive.declaration:DECLARATION"
SECTIONS = ["overview", "setup", "troubleshooting", "terms", "pages"]


def printed(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> dict:  # type: ignore[type-arg]
    monkeypatch.chdir(ROOT)
    assert main([NAMED]) == 0
    declared = json.loads(capsys.readouterr().out)
    assert isinstance(declared, dict)
    return declared


def test_the_declaration_carries_its_pages_tools_and_documentation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    declared = printed(monkeypatch, capsys)
    assert [page["title"] for page in declared["interface"]["pages"]] == [
        "Connection",
        "Datasets",
    ]
    # The pages' read tools, and the storage's restore (the SDK's).
    assert {tool["name"] for tool in declared["tools"]} == {
        "read_connection",
        "read_datasets",
        "restore_unit",
    }
    sections = declared["documentation"]
    assert sorted(section["name"] for section in sections) == sorted(SECTIONS)
    for section in sections:
        held = (ROOT / "docs" / f"{section['name']}.md").read_bytes()
        assert section["digest"] == f"sha256:{hashlib.sha256(held).hexdigest()}"
        assert section["bytes"] == len(held)
    assert version.PORT == 8000


def test_the_catalogue_one_dataset_per_timeliness() -> None:
    by_key = {d.key: d for d in CATALOGUE}
    assert list(by_key) == [
        "daily",
        "live",
        "live_delayed",
        "trades",
        "trades_delayed",
        "options",
        "options_delayed",
    ]
    for key, dataset in by_key.items():
        assert dataset.declared_delay_ns == (DELAY_NS if key.endswith("_delayed") else 0)
        assert dataset.aggregator == "Massive"
        assert dataset.vendor == (
            "OPRA" if key.startswith("options") else "US equity SIPs (CTA, UTP)"
        )
        assert dataset.day_time_zone == "America/New_York" and dataset.day_end_minute == 960
    assert "meridian.v1.Quote" in by_key["options"].data_types
    assert "meridian.v1.Quote" not in by_key["options_delayed"].data_types
    assert by_key["live_delayed"].data_types == ("meridian.v1.Price",)
    assert {by_key[k].calendar_id for k in ("daily", "options", "options_delayed")} == {
        US_EQUITY_CALENDAR
    }


def test_each_datasets_terms_and_pricing() -> None:
    # Contract 30 (ruling 1032): Massive's terms in words, its source's first,
    # never a licence; how a gap in each is recovered.
    by_key = {d.key: d for d in CATALOGUE}
    for dataset in CATALOGUE:
        assert "individual plans" in dataset.terms_and_pricing
        assert "not affiliated" in dataset.terms_and_pricing
        assert len(dataset.terms_and_pricing) <= 2000
        assert not dataset.sessions_calendar_id
        assert ("Delayed 15 minutes" in dataset.terms_and_pricing) == dataset.key.endswith(
            "_delayed"
        )
    assert by_key["options"].terms_and_pricing.startswith("OPRA")
    assert {k: d.gap_recovery for k, d in by_key.items()} == {
        "daily": "backfill",
        "live": "stream_only",
        "live_delayed": "none",
        "trades": "backfill",
        "trades_delayed": "backfill",
        "options": "backfill",
        "options_delayed": "backfill",
    }


def test_the_settings_in_the_data_roles_order() -> None:
    assert [s.name for s in SETTINGS] == [
        "massive_api_key",
        "stocks_timeliness",
        "options_timeliness",
        "sample_data",
    ]
    key, stocks, options, sample = SETTINGS
    assert key.secret and key.required
    assert stocks.default == options.default == "delayed"
    assert [c.value for c in stocks.choices] == ["real_time", "delayed", "end_of_day"]
    assert sample.developer

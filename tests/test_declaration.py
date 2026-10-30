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


def test_each_datasets_default_terms_are_an_individual_plans() -> None:
    for dataset in CATALOGUE:
        licence = dataset.licence_default
        assert licence is not None
        assert licence.personal_use and licence.display
        assert licence.derived_use_terms == "internal_only"
        assert licence.redistribution == "not_allowed"
        assert "not affiliated" in licence.not_affiliated_notice
        # Not declared until core's declaration parser carries it.
        assert licence.eligibility is None
    kept = {d.key: (d.licence_default.kept, d.licence_default.retention_days) for d in CATALOGUE}  # type: ignore[union-attr]
    assert kept["daily"] == (True, 0) and kept["live"] == (False, 0)
    assert kept["trades"] == (True, 2) and kept["options"] == (True, 0)


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

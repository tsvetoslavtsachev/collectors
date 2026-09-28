"""Wiring checks: countries.py, register_catalog.ENTRIES, fetch_imf and
config.yaml agree with each other -- no key drifts silently out of sync.

Run: python -m pytest collectors/cbgold/tests/test_wiring.py
"""
from pathlib import Path
import yaml

from collectors.cbgold.countries import COUNTRY_NAMES
from collectors.cbgold.register_catalog import ENTRIES
from collectors.cbgold import fetch_imf

HERE = Path(__file__).resolve().parent.parent


def _cfg():
    return yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))


def test_catalog_has_one_series_per_country():
    assert len(ENTRIES) == len(COUNTRY_NAMES) == 88
    for code in COUNTRY_NAMES:
        assert f"cb_gold_{code.lower()}_tonnes" in ENTRIES


def test_config_prefix_suffix_match_fetch_imf():
    cfg = _cfg()
    assert cfg["imf"]["series_prefix"] == fetch_imf.SERIES_PREFIX
    assert cfg["imf"]["series_suffix"] == fetch_imf.SERIES_SUFFIX
    assert cfg["imf"]["url"] == fetch_imf.URL


def test_every_entry_has_the_required_catalog_fields():
    required = {"description", "source", "manual_source", "license", "basis",
               "frequency", "window", "unit", "schema_version"}
    for sid, e in ENTRIES.items():
        missing = required - set(e)
        assert not missing, f"{sid} missing {missing}"
        assert e["schema_version"] == 1
        assert e["unit"] == "tonnes"
        assert e["frequency"] == "monthly"


def test_the_five_gate_countries_are_present():
    for code in ("USA", "CHN", "POL", "TUR", "IND"):
        assert f"cb_gold_{code.lower()}_tonnes" in ENTRIES

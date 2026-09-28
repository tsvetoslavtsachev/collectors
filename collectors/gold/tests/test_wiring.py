"""Wiring checks: config.yaml, register_catalog.ENTRIES and fetch_* agree on the
same five series_id's -- no key drifts silently out of sync.

Run: python -m pytest collectors/gold/tests/test_wiring.py
"""
from pathlib import Path
import yaml

from collectors.gold import fetch_lbma, fetch_gld, fetch_gldm, fetch_silver
from collectors.gold.register_catalog import ENTRIES

HERE = Path(__file__).resolve().parent.parent


def _cfg():
    return yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))


def test_catalog_has_ten_series():
    assert len(ENTRIES) == 10
    assert set(ENTRIES) == {
        "mkt_gold_usd", "mkt_gold_gbp", "mkt_gold_eur",
        "etf_gld_tonnes", "etf_gld_oz",
        "etf_gldm_tonnes", "etf_gldm_oz",
        "mkt_silver_usd", "mkt_silver_gbp", "mkt_silver_eur",
    }


def test_config_lbma_series_match_fetch_index():
    cfg = _cfg()
    assert cfg["lbma"]["series"] == fetch_lbma.CCY_INDEX
    assert set(cfg["lbma"]["series"]) <= set(ENTRIES)


def test_config_gld_series_match_fetch_columns():
    cfg = _cfg()
    assert cfg["gld"]["series"] == fetch_gld.COLUMNS
    assert set(cfg["gld"]["series"]) <= set(ENTRIES)


def test_config_gldm_series_match_fetch_columns():
    cfg = _cfg()
    assert cfg["gldm"]["series"] == fetch_gldm.COLUMNS
    assert set(cfg["gldm"]["series"]) <= set(ENTRIES)


def test_config_silver_series_match_fetch_index():
    cfg = _cfg()
    assert cfg["silver"]["series"] == fetch_silver.CCY_INDEX
    assert set(cfg["silver"]["series"]) <= set(ENTRIES)


def test_control_series_are_labelled_as_controls():
    for sid in ("etf_gldm_tonnes", "etf_gldm_oz"):
        assert ENTRIES[sid].get("control_of") == "etf_gld_tonnes"


def test_every_entry_has_the_required_catalog_fields():
    required = {"description", "source", "manual_source", "license", "basis",
               "frequency", "window", "unit", "schema_version"}
    for sid, e in ENTRIES.items():
        missing = required - set(e)
        assert not missing, f"{sid} missing {missing}"
        assert e["schema_version"] == 1

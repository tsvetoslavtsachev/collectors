"""Offline test for the LBMA silver parser -- no network, mirrors
test_fetch_lbma.py. Null pattern differs from gold (checked live 28.09.2026:
USD has 19 nulls, GBP has zero, EUR pre-1999) -- these tests exercise the
generic per-currency omission, not gold's specific gap shape.

Run: python -m pytest collectors/gold/tests/test_fetch_silver.py
"""
from collectors.gold import fetch_silver as fs


def _row(d, usd, gbp, eur):
    return {"is_cms_locked": 0, "d": d, "v": [usd, gbp, eur]}


def test_three_currencies_split_into_three_series():
    rows = [_row("2020-01-02", 18.0, 13.8, 16.1)]
    out = fs.to_records(rows)
    assert set(out) == {"mkt_silver_usd", "mkt_silver_gbp", "mkt_silver_eur"}
    for sid in out:
        assert out[sid]["ok"] is True
    assert out["mkt_silver_usd"]["records"] == [
        {"as_of": "2020-01-02", "value": 18.0, "source": "LBMA Silver Price"}]


def test_pre_euro_eur_is_omitted_not_null():
    rows = [_row("1968-01-02", 2.173, 0.904, None)]
    out = fs.to_records(rows)
    assert out["mkt_silver_usd"]["records"]
    assert out["mkt_silver_gbp"]["records"]
    assert out["mkt_silver_eur"]["records"] == []


def test_usd_gap_day_is_omitted_gbp_kept():
    # silver's null pattern is USD-side gaps (unlike gold's GBP-side gaps) --
    # this test would fail if the parser assumed gold's shape instead of
    # reading whichever currency is actually null that day
    rows = [_row("1970-05-01", None, 0.98, 1.10)]
    out = fs.to_records(rows)
    assert out["mkt_silver_usd"]["records"] == []
    assert out["mkt_silver_gbp"]["records"]
    assert out["mkt_silver_eur"]["records"]


def test_never_writes_a_null_value():
    rows = [_row("1968-01-02", 2.173, 0.904, None),
            _row("1970-05-01", None, 0.98, 1.10)]
    out = fs.to_records(rows)
    for sid in out:
        assert all(r["value"] is not None for r in out[sid]["records"])

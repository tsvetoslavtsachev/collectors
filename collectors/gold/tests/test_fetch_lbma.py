"""Offline test for the LBMA PM parser -- no network.

Run: python -m pytest collectors/gold/tests/test_fetch_lbma.py
"""
from collectors.gold import fetch_lbma as fl


def _row(d, usd, gbp, eur):
    return {"is_cms_locked": 0, "d": d, "v": [usd, gbp, eur]}


def test_three_currencies_split_into_three_series():
    rows = [_row("2020-01-02", 1520.0, 1160.0, 1355.0)]
    out = fl.to_records(rows)
    assert set(out) == {"mkt_gold_usd", "mkt_gold_gbp", "mkt_gold_eur"}
    for sid in out:
        assert out[sid]["ok"] is True
    assert out["mkt_gold_usd"]["records"] == [
        {"as_of": "2020-01-02", "value": 1520.0, "source": "LBMA PM"}]
    assert out["mkt_gold_eur"]["records"][0]["value"] == 1355.0


def test_pre_euro_eur_is_omitted_not_null():
    # 1968: EUR did not exist yet -- the null is dropped, never written as a value
    rows = [_row("1968-04-01", 37.7, 15.68, None)]
    out = fl.to_records(rows)
    assert out["mkt_gold_usd"]["records"]
    assert out["mkt_gold_gbp"]["records"]
    assert out["mkt_gold_eur"]["records"] == []


def test_gbp_closure_gap_is_omitted():
    # 1971: LBMA gold market closed (Nixon Shock week) -- GBP null that day only
    rows = [_row("1971-08-17", 43.0, None, None)]
    out = fl.to_records(rows)
    assert out["mkt_gold_usd"]["records"]
    assert out["mkt_gold_gbp"]["records"] == []
    assert out["mkt_gold_eur"]["records"] == []


def test_malformed_row_is_skipped_not_crashed():
    rows = [{"d": None, "v": [1, 2, 3]}, {"d": "2020-01-02", "v": [1]},
            _row("2020-01-03", 1500.0, 1100.0, 1300.0)]
    out = fl.to_records(rows)
    assert len(out["mkt_gold_usd"]["records"]) == 1
    assert out["mkt_gold_usd"]["records"][0]["as_of"] == "2020-01-03"


def test_never_writes_a_null_value():
    rows = [_row("1968-04-01", 37.7, 15.68, None),
            _row("1971-08-17", 43.0, None, None)]
    out = fl.to_records(rows)
    for sid in out:
        assert all(r["value"] is not None for r in out[sid]["records"])

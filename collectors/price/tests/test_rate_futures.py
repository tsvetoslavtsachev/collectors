"""ПЪТ1: the rate-futures strip -- derived from the date, rolls by itself, writes through P1.

    python -m pytest -q collectors/price/tests/test_rate_futures.py
"""
import json
from datetime import date

import pytest

from collectors.price import rate_futures as RF

SPEC = {
    "ZQ": {"name": "30-Day Fed Funds future", "underlying": "avg EFFR", "exchange": "CBT",
           "cycle": "monthly", "months_ahead": 3},
    "SR3": {"name": "3-Month SOFR future", "underlying": "SOFR", "exchange": "CME",
            "cycle": "quarterly", "quarters_ahead": 3, "period": "5d", "history": False},
}


def syms(on, root):
    return [c["symbol"] for c in RF.contracts(on, SPEC) if c["root"] == root]


def test_symbol_and_series_id():
    assert RF.symbol("ZQ", 2026, 12, "CBT") == "ZQZ26.CBT"
    assert RF.series_id("ZQZ26.CBT") == "px_zqz26_cbt_daily"
    assert RF.series_id("SR3U28.CME") == "px_sr3u28_cme_daily"
    assert RF.parse_symbol("SR3H27.CME") == {"root": "SR3", "year": 2027, "month": 3, "exch": "CME"}
    with pytest.raises(ValueError):
        RF.parse_symbol("SPY.XX")


def test_g4_month_switch_rolls_the_strip():
    # G4: two dates in different months -> the list moves by one, the expired month drops out
    nov, dec = syms(date(2026, 11, 30), "ZQ"), syms(date(2026, 12, 1), "ZQ")
    assert nov == ["ZQX26.CBT", "ZQZ26.CBT", "ZQF27.CBT", "ZQG27.CBT"]
    assert dec == ["ZQZ26.CBT", "ZQF27.CBT", "ZQG27.CBT", "ZQH27.CBT"]
    assert "ZQX26.CBT" not in dec
    # year boundary
    assert syms(date(2027, 1, 2), "ZQ")[0] == "ZQF27.CBT"


def test_sr3_quarter_start():
    # a quarter contract trades until its reference quarter ends (~3 months after its month)
    assert syms(date(2026, 11, 22), "SR3") == ["SR3U26.CME", "SR3Z26.CME", "SR3H27.CME"]
    assert syms(date(2027, 1, 5), "SR3")[0] == "SR3Z26.CME"
    assert syms(date(2027, 2, 1), "SR3")[0] == "SR3Z26.CME"
    assert syms(date(2027, 5, 1), "SR3")[0] == "SR3H27.CME"


def _bar(d, px):
    return {"as_of": d, "value": px, "open": px, "high": px, "low": px, "close": px, "value_tr": px,
            "volume": 1, "split_factor": 1.0, "dividend": 0.0, "source": "yfinance"}


def fake_fetch(prices):
    def f(sym, *, period, round_dp):
        if sym not in prices:
            raise RuntimeError(f"no yfinance data for {sym}")
        recs = [_bar(d, p) for d, p in prices[sym]]
        recs[-1]["provisional"] = True
        return recs
    return f


def test_sr3_keeps_only_a_fresh_tip():
    on = date(2026, 11, 22)
    strip = [c for c in RF.contracts(on, SPEC) if c["root"] == "SR3"]
    prices = {"SR3U26.CME": [("2020-01-24", 98.5), ("2026-11-20", 96.0)],   # junk fragment + tip
              "SR3Z26.CME": [("2026-10-01", 95.0)]}                         # stale tip
    res = RF.fetch(strip, "5d", 6, on=on, spec=SPEC, fetch_one=fake_fetch(prices))
    assert [r["as_of"] for r in res["got"]["px_sr3u26_cme_daily"]] == ["2026-11-20"]
    miss = dict(res["missing"])
    assert "SR3Z26.CME" in miss and "not within" in miss["SR3Z26.CME"]
    assert "SR3H27.CME" in miss                                             # not listed -> named


@pytest.fixture
def root(tmp_path):
    (tmp_path / "catalog").mkdir()
    (tmp_path / "catalog" / "catalog.json").write_text(
        json.dumps({"series": {"px_spy_daily": {"symbol": "SPY"}}}), encoding="utf-8")
    return tmp_path


def test_run_registers_and_appends_and_expired_stays(root):
    cfg = {"rate_futures": SPEC, "settings": {"round_dp": 6, "daily_period": "1mo"}}
    nov = {"ZQX26.CBT": [("2026-11-19", 96.1), ("2026-11-20", 96.12)],
           "ZQZ26.CBT": [("2026-11-20", 95.96)],
           "SR3U26.CME": [("2026-11-20", 96.07)]}
    out = RF.run(date(2026, 11, 22), "1mo", root, cfg=cfg, fetch_one=fake_fetch(nov), log=lambda *a: None)
    cat = json.loads((root / "catalog" / "catalog.json").read_text(encoding="utf-8"))["series"]
    assert cat["px_spy_daily"] == {"symbol": "SPY"}                        # never rewritten
    assert cat["px_zqx26_cbt_daily"]["family"] == "future"
    assert cat["px_zqx26_cbt_daily"]["contract_month"] == "2026-11"
    assert "px_zqf27_cbt_daily" not in cat                                 # not listed -> not registered
    assert out["holes"] == []
    # December: ZQX26 has expired (Yahoo 404) -- its folder and last bar stay untouched
    dec = {"ZQZ26.CBT": [("2026-12-01", 95.97)], "ZQF27.CBT": [("2026-12-01", 95.9)]}
    RF.run(date(2026, 12, 1), "1mo", root, cfg=cfg, fetch_one=fake_fetch(dec), log=lambda *a: None)
    x = (root / "archive" / "px_zqx26_cbt_daily" / "2026.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(l)["as_of"] for l in x] == ["2026-11-19", "2026-11-20"]
    cat = json.loads((root / "catalog" / "catalog.json").read_text(encoding="utf-8"))["series"]
    assert "px_zqf27_cbt_daily" in cat                                     # the new one appeared by itself


def test_hole_inside_strip_is_named(root):
    cfg = {"rate_futures": SPEC, "settings": {"round_dp": 6}}
    p = {"ZQX26.CBT": [("2026-11-20", 96.1)], "ZQF27.CBT": [("2026-11-20", 95.9)]}   # ZQZ26 missing
    out = RF.run(date(2026, 11, 22), "1mo", root, cfg=cfg, fetch_one=fake_fetch(p), log=lambda *a: None)
    assert out["holes"] == ["ZQZ26.CBT"]

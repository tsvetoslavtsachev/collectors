"""ЗЛТ8 — главата от Treasury пред лихвите на FRED (DGS10, DFII10).

Болестта: H.15 на FRED дава точката за ден D чак на следващия работен ден около 15:15
CT, затова съботният пуск никога нямаше петъка. Treasury публикува същите числа в CSV
същата вечер. Гейтът: точките след последната дата на FRED се добавят само ако общите
дни съвпадат; при разминаване или грешка серията остава точно каквато е от FRED.

Run: python -m pytest collectors/vrm/tests/test_treasury_head.py
Live (мрежа, FRED ключ): ZLT8_LIVE=1 python -m pytest collectors/vrm/tests/test_treasury_head.py
"""
import datetime as dt
import os

import pytest

from collectors.vrm import fetch_fred, fetch_treasury as ft

CSV = ('Date,"1 Mo","10 Yr","30 Yr"\n'
       "11/20/2026,4.1,4.92,5.30\n"
       "11/19/2026,4.1,5.04,5.41\n"
       "11/18/2026,4.1,5.07,5.44\n"
       "11/17/2026,4.1,5.10,5.47\n"
       "11/16/2026,4.1,5.12,5.49\n"
       "11/13/2026,4.1,5.15,5.50\n"
       "11/12/2026,4.1,,5.48\n")
FRED = [("2026-11-12", 5.10), ("2026-11-13", 5.15), ("2026-11-16", 5.12),
        ("2026-11-17", 5.10), ("2026-11-18", 5.07), ("2026-11-19", 5.04)]


def test_parse_csv_case_insensitive_skips_blanks_ascending():
    rows = ft.parse_csv(CSV, "10 YR")
    assert rows[0] == ("2026-11-13", 5.15) and rows[-1] == ("2026-11-20", 4.92)
    assert "2026-11-12" not in dict(rows)          # празната клетка не е нула


def test_parse_csv_missing_column_raises():
    with pytest.raises(KeyError):
        ft.parse_csv(CSV, "7 Yr")


def test_extend_appends_only_newer_when_overlap_agrees():
    out, note = ft.extend(list(FRED), ft.parse_csv(CSV, "10 Yr"))
    assert out[:len(FRED)] == FRED                  # историята на FRED не се пипа
    assert out[len(FRED):] == [("2026-11-20", 4.92)]
    assert "+1 after 2026-11-19" in note


def test_extend_refuses_on_disagreement():
    bad = [(d, v + (0.05 if d == "2026-11-18" else 0)) for d, v in ft.parse_csv(CSV, "10 Yr")]
    out, note = ft.extend(list(FRED), bad)
    assert out == FRED and "REFUSED" in note


def test_extend_refuses_with_too_few_common_dates():
    out, note = ft.extend(list(FRED), [("2026-11-19", 5.04), ("2026-11-20", 4.92)])
    assert out == FRED and "not trusted" in note


def test_mutation_no_overlap_check_would_land_a_wrong_head(monkeypatch):
    # ФАЛШИФИКАТОР: без проверката на общите дни (TOLERANCE огромен) разминаващ се
    # източник минава и добавя главата. Ако това някога не добави ред, тестът за отказа
    # горе не различава пазача.
    monkeypatch.setattr(ft, "TOLERANCE", 99)
    bad = [(d, v + 0.5) for d, v in ft.parse_csv(CSV, "10 Yr")]
    out, _ = ft.extend(list(FRED), bad)
    assert len(out) == len(FRED) + 1


def test_fetch_takes_last_year_too_in_january():
    seen = []
    ft.fetch("par", "10 Yr", today=dt.date(2027, 1, 4),
             getter=lambda u: seen.append(u) or CSV)
    assert len(seen) == 2 and "/2026/" in seen[0] and "/2027/" in seen[1]


def test_fred_series_gets_head_and_label(monkeypatch):
    monkeypatch.setattr(fetch_fred, "fetch_observations", lambda t: list(FRED))
    monkeypatch.setattr(ft, "fetch", lambda c, col: ft.parse_csv(CSV, col))
    cfg = {"settings": {"round_dp": 6}, "fred": {
        "mkt_ust_10y": {"ticker": "DGS10", "transform": "level", "model_freq": "daily",
                        "treasury_head": {"curve": "par", "column": "10 Yr"}},
        "mkt_breakeven_10y": {"ticker": "T10YIE", "transform": "level", "model_freq": "daily"}}}
    out = fetch_fred.fetch_fred(cfg)
    recs = out["mkt_ust_10y"]["model_records"]
    assert recs[-1] == {"as_of": "2026-11-20", "value": 4.92,
                        "source": "US Treasury par yield curve 10 Yr", "resolution": "daily"}
    assert recs[-2]["source"] == "FRED DGS10"
    assert out["mkt_breakeven_10y"]["model_records"][-1]["as_of"] == "2026-11-19"


def test_treasury_failure_leaves_fred_untouched(monkeypatch):
    monkeypatch.setattr(fetch_fred, "fetch_observations", lambda t: list(FRED))
    def boom(c, col):
        raise OSError("treasury down")
    monkeypatch.setattr(ft, "fetch", boom)
    cfg = {"settings": {"round_dp": 6}, "fred": {"mkt_ust_10y": {
        "ticker": "DGS10", "transform": "level", "model_freq": "daily",
        "treasury_head": {"curve": "par", "column": "10 Yr"}}}}
    out = fetch_fred.fetch_fred(cfg)["mkt_ust_10y"]
    assert out["ok"] and [r["as_of"] for r in out["model_records"]][-1] == "2026-11-19"


@pytest.mark.skipif(not os.environ.get("ZLT8_LIVE"), reason="live: set ZLT8_LIVE=1")
@pytest.mark.parametrize("ticker,curve,col", [("DGS10", "par", "10 Yr"),
                                              ("DFII10", "real", "10 YR")])
def test_live_this_year_matches_fred(ticker, curve, col):
    tre = dict(ft.fetch(curve, col))
    fred = dict(fetch_fred.fetch_observations(ticker))
    common = [d for d in tre if d in fred]
    assert len(common) > 150
    assert max(abs(tre[d] - fred[d]) for d in common) <= ft.TOLERANCE

# -*- coding: utf-8 -*-
"""ИЛЮ3 (22.11.2026): mkt_vix и mkt_move дневни от ценовия архив (решението на Ц. Р2).

Заменя тестовете на ЧИС3 за снимката на барометъра: барометърът вече не храни канона.
Гейтът G3 (мутация): празен, липсващ или подменен архив дава not-ok с причина, не зелено;
здравият архив дава дневния път след последния ред от Блумбърг.
"""
import datetime as dt
import json

import pytest

from collectors.vrm import fetch_bridge as fb

CFG = {"yfinance": {"mkt_vix": {"bridge": True}, "mkt_move": {"bridge": True}}, "settings": {}}
TODAY = dt.date(2026, 11, 22)


def _write(root, px_id, rows):
    d = root / "archive" / px_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _bar(as_of, value, rec="2026-11-21", prov=False):
    return {"as_of": as_of, "value": value, "recorded_on": rec, "provisional": prov}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PRICE_ARCHIVE_ROOT", str(tmp_path))
    canon = {"mkt_vix": [{"as_of": "2026-06-18", "value": 16.4, "source": "backfill:vix.csv (Bloomberg)", "resolution": "daily"},
                         {"as_of": "2026-06-19", "value": 16.9, "source": "backfill:vix.csv (Bloomberg)", "resolution": "daily"},
                         {"as_of": "2026-11-13", "value": 19.3, "source": "etf-rr-barometer", "resolution": "daily"}],
             "mkt_move": []}
    monkeypatch.setattr(fb.storage, "read_canonical", lambda sid: list(canon[sid]))
    return tmp_path


def test_healthy_archive_gives_the_daily_path_after_the_backfill(env):
    _write(env, "px_vix_daily", [_bar("2026-06-18", 16.41), _bar("2026-06-22", 17.0),
                                 _bar("2026-11-19", 26.4), _bar("2026-11-20", 35.889999, prov=True)])
    _write(env, "px_move_daily", [_bar("2026-11-19", 101.0), _bar("2026-11-20", 106.309998)])
    out = fb.fetch_bridge(CFG, today=TODAY)
    vix = out["mkt_vix"]["records"]
    assert out["mkt_vix"]["ok"] and out["mkt_move"]["ok"]
    assert [r["as_of"] for r in vix] == ["2026-06-18", "2026-06-19", "2026-06-22", "2026-11-19", "2026-11-20"]
    assert vix[0]["value"] == 16.4                       # Блумбърг остава какъвто е
    assert vix[-1] == {"as_of": "2026-11-20", "value": 35.889999, "resolution": "daily",
                       "source": "price-archive px_vix_daily (yfinance ^VIX)"}
    assert all(r["source"] != "etf-rr-barometer" for r in vix)   # седмичната снимка е заменена


def test_newest_vintage_wins_per_date(env):
    _write(env, "px_vix_daily", [_bar("2026-11-20", 30.0, rec="2026-11-21", prov=True),
                                 _bar("2026-11-20", 35.89, rec="2026-11-24")])
    _write(env, "px_move_daily", [_bar("2026-11-20", 106.3)])
    assert fb.fetch_bridge(CFG, today=TODAY)["mkt_vix"]["records"][-1]["value"] == 35.89


@pytest.mark.parametrize("rows,why", [
    ([], "empty"),
    ([_bar("2026-11-20", 0.0)], "non-positive"),
    ([_bar("2026-11-20", "<html>")], "non-numeric"),
    ([_bar("2026-11-06", 18.0)], "days old"),
])
def test_mutation_bad_archive_is_not_ok_with_reason(env, rows, why):
    if rows:
        _write(env, "px_vix_daily", rows)
    else:
        (env / "archive" / "px_vix_daily").mkdir(parents=True)
        (env / "archive" / "px_vix_daily" / "2026.jsonl").write_text("", encoding="utf-8")
    _write(env, "px_move_daily", [_bar("2026-11-20", 106.3)])
    out = fb.fetch_bridge(CFG, today=TODAY)
    assert out["mkt_vix"]["ok"] is False and why in out["mkt_vix"]["error"]
    assert out["mkt_move"]["ok"] is True                 # другата серия не страда


def test_missing_archive_series_is_not_ok(env):
    _write(env, "px_move_daily", [_bar("2026-11-20", 106.3)])
    out = fb.fetch_bridge(CFG, today=TODAY)
    assert out["mkt_vix"]["ok"] is False and "no archive files" in out["mkt_vix"]["error"]

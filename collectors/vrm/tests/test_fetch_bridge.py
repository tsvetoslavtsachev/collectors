# -*- coding: utf-8 -*-
"""ЧИС3 казуси 1+2 -- fetch_bridge.py чете value_date по серия и пази срещу
застояла стойност от фийда (виж manus-factory/prompts/observatory-atlas/CHISTKA-3.md)."""
from collectors.vrm import fetch_bridge


def _cfg():
    return {"yfinance": {"mkt_vix": {"bridge": True}, "mkt_move": {"bridge": True}},
            "settings": {}}


def _feed(as_of, vix_value_date=None, move_value_date=None, vix=18.5, move=95.0):
    vix_row = {"indicator": "VIX", "value": vix}
    if vix_value_date is not None:
        vix_row["value_date"] = vix_value_date
    move_row = {"indicator": "MOVE", "value": move}
    if move_value_date is not None:
        move_row["value_date"] = move_value_date
    return {"as_of": as_of, "snapshot": [vix_row, move_row]}


def _patch(monkeypatch, feed, existing=None):
    monkeypatch.setattr(fetch_bridge, "_fetch_feed", lambda url: feed)
    monkeypatch.setattr(fetch_bridge.storage, "read_canonical",
                         lambda sid: (existing or {}).get(sid, []))


def test_uses_value_date_when_present(monkeypatch):
    feed = _feed(as_of="2026-09-05", vix_value_date="2026-09-06")
    _patch(monkeypatch, feed)
    out = fetch_bridge.fetch_bridge(_cfg())
    assert out["mkt_vix"]["records"][-1]["as_of"] == "2026-09-06"


def test_falls_back_to_feed_as_of_when_missing(monkeypatch):
    feed = _feed(as_of="2026-09-05")
    _patch(monkeypatch, feed)
    out = fetch_bridge.fetch_bridge(_cfg())
    assert out["mkt_vix"]["records"][-1]["as_of"] == "2026-09-05"
    assert out["mkt_move"]["records"][-1]["as_of"] == "2026-09-05"


def test_value_date_used_even_when_older_than_feed_as_of(monkeypatch):
    # мутация от гейт А: value_date по-стара от as_of -> все пак се пише value_date,
    # не по-новата от двете.
    feed = _feed(as_of="2026-09-08", vix_value_date="2026-09-05")
    _patch(monkeypatch, feed)
    out = fetch_bridge.fetch_bridge(_cfg())
    assert out["mkt_vix"]["records"][-1]["as_of"] == "2026-09-05"


def test_stale_value_is_skipped_not_recorded(monkeypatch, capsys):
    existing = {"mkt_vix": [{"as_of": "2026-09-05", "value": 18.5,
                              "source": "etf-rr-barometer", "resolution": "daily"}]}
    feed = _feed(as_of="2026-09-05", vix_value_date="2026-09-06", vix=18.5001)
    _patch(monkeypatch, feed, existing)
    out = fetch_bridge.fetch_bridge(_cfg())
    assert out["mkt_vix"]["records"] == existing["mkt_vix"]
    assert "застояла стойност" in capsys.readouterr().out


def test_new_value_beyond_deadband_is_recorded(monkeypatch):
    existing = {"mkt_vix": [{"as_of": "2026-09-05", "value": 18.5,
                              "source": "etf-rr-barometer", "resolution": "daily"}]}
    feed = _feed(as_of="2026-09-05", vix_value_date="2026-09-06", vix=19.2)
    _patch(monkeypatch, feed, existing)
    out = fetch_bridge.fetch_bridge(_cfg())
    assert out["mkt_vix"]["records"][-1] == {
        "as_of": "2026-09-06", "value": 19.2,
        "source": "etf-rr-barometer", "resolution": "daily",
    }

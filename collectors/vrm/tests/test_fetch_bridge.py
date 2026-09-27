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


# ── 27.09.2026 · VIX W39: съвпадение срещу рециклиран отпечатък ───────────────
# CBOE: 21.09 = 14,87 и 25.09 = 14,87, между тях 14,21 / 15,18 / 15,67. Пазачът изхвърли
# истинското 25.09. Фийдът вече носи value_since (откога стойността стои същата).

_W39 = {"mkt_vix": [{"as_of": "2026-09-21", "value": 14.87,
                     "source": "etf-rr-barometer", "resolution": "daily"}]}


def _w39_feed(value_since):
    feed = _feed(as_of="2026-09-25", vix_value_date="2026-09-25",
                 move_value_date="2026-09-25", vix=14.87, move=96.0)
    feed["snapshot"][0]["value_since"] = value_since
    return feed


def test_coincidence_with_a_new_print_is_recorded(monkeypatch, capsys):
    _patch(monkeypatch, _w39_feed(value_since="2026-09-25"), _W39)
    out = fetch_bridge.fetch_bridge(_cfg())
    assert out["mkt_vix"]["records"][-1] == {
        "as_of": "2026-09-25", "value": 14.87,
        "source": "etf-rr-barometer", "resolution": "daily"}
    assert "нов отпечатък" in capsys.readouterr().out


def test_flat_since_last_canonical_is_still_skipped(monkeypatch, capsys):
    _patch(monkeypatch, _w39_feed(value_since="2026-09-21"), _W39)
    out = fetch_bridge.fetch_bridge(_cfg())
    assert out["mkt_vix"]["records"] == _W39["mkt_vix"]
    assert "застояла стойност" in capsys.readouterr().out


def test_repeat_of_previous_session_is_still_skipped(monkeypatch):
    # стойността стои от вчера: днешният ред повтаря предния -> може да е рециклиран
    _patch(monkeypatch, _w39_feed(value_since="2026-09-24"), _W39)
    out = fetch_bridge.fetch_bridge(_cfg())
    assert out["mkt_vix"]["records"] == _W39["mkt_vix"]

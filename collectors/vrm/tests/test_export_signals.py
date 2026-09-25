# -*- coding: utf-8 -*-
"""Офлайн гейт за collectors.vrm.export_signals (signals-registry M2): три обекта по схемата 0.1,
без мрежа, без канон. Пуска се с: python -m pytest collectors/vrm/tests/test_export_signals.py
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from collectors.vrm import export_signals as es

NOW = datetime(2026, 9, 26, 5, 40, tzinfo=timezone.utc)


def overlay_rec(as_of, regime, align=3, provisional=False):
    return {
        "as_of": as_of, "regime": regime, "z_method": "expanding-honest",
        "velocity": {"growth": None, "inflation": None},
        "alignment_score": align,
        "alignment_flags": {"RISK_ON_GROWTH": "WATCH", "RISK_ON_CYCLICAL": "UNDER", "COMMODITY_INFLATION": "OVER",
                            "INFLATION_PROTECTION": "WATCH", "DEFENSIVE_EQUITY": "OVER", "DURATION": "UNDER",
                            "CREDIT": "WATCH", "REAL_ESTATE": "OK"},
        "gms": {"score": 3, "max": 8, "tier": "MEDIUM", "signals": {}},
        "kill_switch": {"active": False},
        "cumulative_4w": {"spy_pct": -0.526302, "threshold_pct": -7.5, "margin_pct": 6.973698},
        "provisional": provisional, "provisional_inputs": [], "schema_version": 2,
    }


def ks_rec(as_of, active=False, applicable=True, weeks_in=None, regime="GROWTH", spy=-0.526302):
    return {"as_of": as_of, "active": active, "applicable": applicable, "variant": "A" if active else None,
            "phase": 1 if active else None, "weeks_in": weeks_in, "since": as_of if active else None,
            "activation_price": None, "spy_pct": spy, "regime": regime, "schema_version": 1}


def overlay_series():
    return [overlay_rec("2026-08-14", "REFLATION"), overlay_rec("2026-08-21", "REFLATION"),
            overlay_rec("2026-08-28", "REFLATION"), overlay_rec("2026-09-04", "GROWTH"),
            overlay_rec("2026-09-11", "GROWTH"), overlay_rec("2026-09-18", "GROWTH")]


def test_three_modules_in_order_with_required_fields():
    sigs = es.build_signals(overlay_series(), [ks_rec("2026-09-18")], "2026-09-26T05:40:00Z")
    assert [s["module"] for s in sigs] == ["vrm-core", "vrm-mid", "kill-switch"]
    for s in sigs:
        for field in ("module", "schema_version", "as_of", "generated_at", "state"):
            assert field in s, field
        assert s["schema_version"] == "0.1"
        assert s["generated_at"].endswith("Z")
        assert len(s["notes"]) <= 300


def test_vrm_core_state_and_persistence():
    core = es.build_signals(overlay_series(), [ks_rec("2026-09-18")], "2026-09-26T05:40:00Z")[0]
    assert core["state"] == "GROWTH"
    assert core["as_of"] == "2026-09-18"
    assert core["persistence"] == 3           # 04.09, 11.09, 18.09
    assert "Растеж" in core["notes"] and "3 поредни" in core["notes"]


def test_vrm_mid_carries_alignment_as_score_and_n_of_8_state():
    mid = es.build_signals(overlay_series(), [ks_rec("2026-09-18")], "2026-09-26T05:40:00Z")[1]
    assert mid["score"] == 3 and mid["scale"] == [0, 8]
    assert mid["state"] == "3/8"
    assert mid["alignment_flags"]["REAL_ESTATE"] == "OK"
    assert "1 съвпадат" in mid["notes"] and "3 гранични" in mid["notes"]


def test_kill_switch_off_and_on():
    off = es.build_signals(overlay_series(), [ks_rec("2026-09-18")], "2026-09-26T05:40:00Z")[2]
    assert off["state"] == "OFF" and off["persistence"] is None and off["applicable"] is True
    assert "неактивен" in off["notes"] and "-7,50%" in off["notes"]
    on = es.build_signals(overlay_series(), [ks_rec("2026-09-18", active=True, weeks_in=2, spy=-8.1)],
                          "2026-09-26T05:40:00Z")[2]
    assert on["state"] == "ON" and on["persistence"] == 2 and on["variant"] == "A"
    assert "активен" in on["notes"] and "неактивен" not in on["notes"]


def test_kill_switch_not_applicable_in_crisis_says_so():
    ks = es.build_signals(overlay_series(), [ks_rec("2026-09-18", applicable=False, regime="CRISIS")],
                          "2026-09-26T05:40:00Z")[2]
    assert ks["state"] == "OFF" and ks["applicable"] is False
    assert "не се прилага" in ks["notes"]


def test_unknown_regime_is_refused_not_exported():
    bad = overlay_series()
    bad[-1]["regime"] = "GOLDILOCKS"
    with pytest.raises(ValueError):
        es.build_signals(bad, [ks_rec("2026-09-18")], "2026-09-26T05:40:00Z")


def test_trailing_run_counts_only_the_last_streak():
    assert es.trailing_run(overlay_series(), "regime") == 3
    assert es.trailing_run(overlay_series()[:3], "regime") == 3
    assert es.trailing_run([], "regime") == 0


def test_export_writes_file_under_root(tmp_path):
    root = tmp_path / "dc"
    (root / "data" / "state").mkdir(parents=True)
    (root / "data" / "state" / "vrm_overlay.json").write_text(json.dumps(overlay_series()), encoding="utf-8")
    (root / "data" / "state" / "vrm_ks_state.json").write_text(json.dumps([ks_rec("2026-09-18")]), encoding="utf-8")
    target = es.export(root, now=NOW)
    assert target == root / "signals" / "current.json"
    data = json.loads(target.read_text(encoding="utf-8"))
    assert [s["module"] for s in data] == ["vrm-core", "vrm-mid", "kill-switch"]
    assert data[0]["generated_at"] == "2026-09-26T05:40:00Z"


def test_main_never_fails_the_food_run_unless_strict(tmp_path):
    assert es.main(["--root", str(tmp_path / "nope")]) == 0
    assert es.main(["--root", str(tmp_path / "nope"), "--strict"]) == 1
    assert es.main(["--strict"]) == 1 or es.main([]) == 0

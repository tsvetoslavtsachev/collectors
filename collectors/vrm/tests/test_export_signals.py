# -*- coding: utf-8 -*-
"""Офлайн гейт за collectors.vrm.export_signals (signals-registry M2 + SR1): четири обекта по
схемата 0.1, без мрежа, без канон. Пуска се с: python -m pytest collectors/vrm/tests/test_export_signals.py
"""
import json
import math
from datetime import date, datetime, timedelta, timezone
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


def fridays_back(n, last="2026-09-18"):
    end = date.fromisoformat(last)
    return [(end - timedelta(weeks=n - 1 - i)).isoformat() for i in range(n)]


# ---------------------------------------------------------------- VRM обектите

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


# ---------------------------------------------------------------- MID: Б2р

def test_tercile_label_uses_fixed_bins_when_history_is_short():
    mid = es.build_signals(overlay_series(), [ks_rec("2026-09-18")], "2026-09-26T05:40:00Z")[1]
    assert mid["tercile_label"] == "смесено" and mid["tercile_cuts"] is None
    assert "фиксирани" in mid["tercile_rule"]
    assert es.bin_fixed(2) == "противоречи" and es.bin_fixed(5) == "смесено" and es.bin_fixed(6) == "потвърждава"


def test_tertile_cuts_match_the_sr1_definition():
    past = [3] * 10 + [5] * 10 + [7] * 10
    assert es.tertile_cuts(past) == (3, 6)
    # изроден случай (всички седмици 5): долният праг пада на 0 (никоя стойност не е близо до
    # долната третина), горният на 6; същото дава numpy-версията на SR1 (първият минимум при равенство)
    assert es.tertile_cuts([5] * 30) == (0, 6)


def test_tercile_label_from_past_weeks_of_the_same_regime_only():
    dates = fridays_back(41)
    overlay = [overlay_rec(d, "GROWTH", align=3 if i % 3 == 0 else (5 if i % 3 == 1 else 7)) for i, d in enumerate(dates[:30])]
    overlay += [overlay_rec(d, "CRISIS", align=8) for d in dates[30:40]]       # друг режим, не влиза в праговете
    overlay.append(overlay_rec(dates[40], "GROWTH", align=6))
    mid = es.build_signals(overlay, [ks_rec(dates[40])], "2026-09-26T05:40:00Z")[1]
    assert mid["tercile_cuts"] == [3, 6]
    assert mid["tercile_label"] == "потвърждава"
    assert "30 минали седмици" in mid["tercile_rule"]
    overlay[-1]["alignment_score"] = 3
    assert es.tercile_label(overlay)["tercile_label"] == "противоречи"
    overlay[-1]["alignment_score"] = 4
    assert es.tercile_label(overlay)["tercile_label"] == "смесено"


# ---------------------------------------------------------------- доларът

def write_price_archive(root: Path, dxy_vals, spy_vals, days, provisional_last=None):
    for rel, vals in ((es.PA_DXY_REL, dxy_vals), (es.PA_SPY_REL, spy_vals)):
        d = root / rel
        d.mkdir(parents=True, exist_ok=True)
        rows = []
        for day, v in zip(days, vals):
            rows.append({"as_of": day.isoformat(), "value": v, "provisional": False,
                         "recorded_on": day.isoformat(), "series_id": rel.name})
        if provisional_last is not None:
            rows.append({"as_of": provisional_last[0].isoformat(), "value": provisional_last[1],
                         "provisional": True, "recorded_on": provisional_last[0].isoformat(), "series_id": rel.name})
        (d / "2026.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def business_days(n, end=date(2026, 9, 18)):
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def test_dollar_signal_perfect_negative_correlation(tmp_path):
    days = business_days(400)
    eps = [math.sin(i * 0.37) * 0.01 + (0.004 if i % 7 == 0 else -0.002) for i in range(len(days))]
    spy, dxy, level = [], [], 0.0
    for e in eps:
        level += e
        spy.append(100 * math.exp(level))
        dxy.append(100 * math.exp(-level))
    write_price_archive(tmp_path, dxy, spy, days, provisional_last=(date(2026, 9, 21), 9999.0))
    sig = es.dollar_signal(tmp_path, "2026-09-26T05:40:00Z")
    assert sig["module"] == "dollar-correlation" and sig["scale"] == [-1, 1]
    assert sig["state"] == "УБЕЖИЩЕ" and sig["raw_state"] == "УБЕЖИЩЕ"
    assert sig["score"] == pytest.approx(-1.0, abs=1e-6)
    assert sig["corr_60d_daily"] == pytest.approx(-1.0, abs=1e-6)
    assert sig["as_of"] == "2026-09-18" and sig["last_price_day"] == "2026-09-18"   # provisional редът не влиза
    assert sig["persistence"] >= 4 and sig["since"] <= sig["as_of"]
    assert date.fromisoformat(sig["as_of"]).weekday() == 4
    assert len(sig["notes"]) <= 300 and "убежище" in sig["notes"]


def test_dollar_signal_positive_correlation_is_risk_asset(tmp_path):
    days = business_days(300)
    eps = [math.cos(i * 0.5) * 0.008 for i in range(len(days))]
    level, spy, dxy = 0.0, [], []
    for e in eps:
        level += e
        spy.append(50 * math.exp(level))
        dxy.append(90 * math.exp(0.5 * level))
    write_price_archive(tmp_path, dxy, spy, days)
    sig = es.dollar_signal(tmp_path, "2026-09-26T05:40:00Z")
    assert sig["state"] == "РИСКОВ АКТИВ" and sig["score"] == pytest.approx(1.0, abs=1e-6)


def test_raw_state_thresholds_and_persistence_rule():
    assert es.raw_state(-0.21) == "УБЕЖИЩЕ" and es.raw_state(-0.2) == "НЕУТРАЛНО"
    assert es.raw_state(0.21) == "РИСКОВ АКТИВ" and es.raw_state(0.0) == "НЕУТРАЛНО"
    N, S = "НЕУТРАЛНО", "УБЕЖИЩЕ"
    assert es.apply_persist_4w([N, S, S, S, S, S]) == [N, N, N, N, S, S]
    assert es.apply_persist_4w([N, S, S, S, N, S, S, S]) == [N, N, N, N, N, N, N, N]


def test_to_friday_leaves_stale_weeks_empty():
    import pandas as pd
    idx = pd.to_datetime(["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-25"])
    s = pd.Series([0.1, 0.2, 0.3, 0.4], index=idx)
    fridays = pd.date_range("2026-08-07", "2026-08-28", freq="W-FRI")
    out = es.to_friday(s, fridays)
    assert out.loc["2026-08-07"] == 0.3
    assert math.isnan(out.loc["2026-08-14"]) and math.isnan(out.loc["2026-08-21"])
    assert out.loc["2026-08-28"] == 0.4


def test_export_without_price_archive_writes_three_objects_and_warns(tmp_path):
    root = tmp_path / "dc"
    (root / "data" / "state").mkdir(parents=True)
    (root / "data" / "state" / "vrm_overlay.json").write_text(json.dumps(overlay_series()), encoding="utf-8")
    (root / "data" / "state" / "vrm_ks_state.json").write_text(json.dumps([ks_rec("2026-09-18")]), encoding="utf-8")
    target, warnings = es.export(root, now=NOW, price_archive=tmp_path / "no-such-archive")
    data = json.loads(target.read_text(encoding="utf-8"))
    assert [s["module"] for s in data] == ["vrm-core", "vrm-mid", "kill-switch"]
    assert warnings and "dollar-correlation пропуснат" in warnings[0]
    target2, warnings2 = es.export(root, now=NOW)
    assert len(json.loads(target2.read_text(encoding="utf-8"))) == 3 and warnings2


def test_main_never_fails_the_food_run_unless_strict(tmp_path):
    assert es.main(["--root", str(tmp_path / "nope")]) == 0
    assert es.main(["--root", str(tmp_path / "nope"), "--strict"]) == 1
    root = tmp_path / "dc"
    (root / "data" / "state").mkdir(parents=True)
    (root / "data" / "state" / "vrm_overlay.json").write_text(json.dumps(overlay_series()), encoding="utf-8")
    (root / "data" / "state" / "vrm_ks_state.json").write_text(json.dumps([ks_rec("2026-09-18")]), encoding="utf-8")
    assert es.main(["--root", str(root), "--price-archive", str(tmp_path / "nope")]) == 0
    assert es.main(["--root", str(root), "--price-archive", str(tmp_path / "nope"), "--strict"]) == 1

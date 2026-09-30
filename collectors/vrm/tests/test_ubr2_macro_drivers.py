"""УБР2 Г3/Г4: макро двигателите на Uber са извън кохортата на режима.

Капанът: carry_forward взима в кохортата всеки ред с model_freq == "monthly". Само
frontier_anchor:false не стига: редът остава в множеството за запълване и carry_forward
измисля месеци до фронтира (ENPLANE изостава ~4 месеца). Затова трите месечни серии са с
model_freq: monthly_native, а седмичният бензин с weekly: нито едно не е "monthly".

Тестовете са върху синтетични данни (без мрежа, без запис в data-core). Мутационният тест
доказва, че пазачът не е сянка: с махната защита фронтирът и запълването се сменят.

Run: python -m pytest collectors/vrm/tests/test_ubr2_macro_drivers.py
"""
import copy
from pathlib import Path

import yaml

from collectors.vrm import carry_forward as cf
from collectors.vrm import fetch_fred
from collectors.vrm import run as vrm_run

CFG = yaml.safe_load((Path(vrm_run.__file__).parent / "config.yaml")
                     .read_text(encoding="utf-8"))

NEW = {"macro_real_dpi": "DSPIC96", "mkt_gasoline_retail": "GASREGW",
       "macro_retail_food_services": "RSFSDP", "macro_air_enplanements": "ENPLANE"}
NEW_MONTHLY = ["macro_real_dpi", "macro_retail_food_services", "macro_air_enplanements"]

# кохортата и котвите ПРЕДИ УБР2 (сверено на 29.09.2026: 14 и 7); всяка промяна е решение
COHORT_BEFORE = {"macro_awh_total_private", "macro_u6", "macro_continued_claims",
                 "macro_retail_sales", "macro_core_pce", "macro_ppi_commodity",
                 "macro_shelter_cpi", "macro_core_cpi", "liq_tga_level", "liq_anfci",
                 "macro_pce_nowcast", "macro_ism_mfg", "macro_ism_services",
                 "macro_mn_ore_cny"}
ANCHORS_BEFORE = {"macro_awh_total_private", "macro_u6", "macro_continued_claims",
                  "macro_retail_sales", "macro_core_pce", "macro_ppi_commodity",
                  "macro_shelter_cpi"}


def test_four_series_are_wired():
    exp = vrm_run.expected_series(CFG)
    for sid, tkr in NEW.items():
        m = CFG["fred"][sid]
        assert m["ticker"] == tkr and m["transform"] == "level" and not m.get("computed")
        assert sid in exp
    assert CFG["fred"]["mkt_gasoline_retail"]["model_freq"] == "weekly"
    assert len(exp) == 62   # +1 УБР5б (mkt_pce_transport_services), +1 УЛМ1 (mkt_consumer_sentiment)


def test_cohort_and_anchors_are_unchanged_by_the_four():
    assert set(cf._cohort_ids(CFG)) == COHORT_BEFORE
    assert set(cf._anchor_ids(CFG)) == ANCHORS_BEFORE
    for sid in NEW:
        assert sid not in cf._cohort_ids(CFG) and sid not in cf._anchor_ids(CFG)
        assert sid not in cf._fill_ids(CFG)


def _month_ends(first, last):
    out, (y, m) = [], (int(first[:4]), int(first[5:7]))
    while f"{y:04d}-{m:02d}" <= last:
        out.append(cf._month_end(f"{y:04d}-{m:02d}"))
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    return out


def _synthetic_raw(cfg):
    """Кохортата до 2026-08; новите: DPI до 2026-09 (пред фронтира), ENPLANE до 2026-04 (изостава)."""
    raw = {}
    for sid in cf._cohort_ids(cfg):
        raw[sid] = {"ok": True, "records": [
            {"as_of": d, "value": 1.0, "source": "t", "resolution": "monthly"}
            for d in _month_ends("2026-01", "2026-08")]}
    last = {"macro_real_dpi": "2026-09", "macro_retail_food_services": "2026-08",
            "macro_air_enplanements": "2026-04"}
    for sid, lm in last.items():
        raw[sid] = {"ok": True, "records": [
            {"as_of": d[:7] + "-01", "value": 2.0, "source": "t", "resolution": "monthly_native"}
            for d in _month_ends("2026-01", lm)]}
    return raw


def test_frontier_does_not_move_and_no_month_is_invented():
    raw = _synthetic_raw(CFG)
    before = copy.deepcopy(raw)
    report = cf.carry_forward_macro(raw, CFG)
    for sid in NEW_MONTHLY:                       # ни един ред не е добавен, не е запълнен
        assert raw[sid]["records"] == before[sid]["records"], sid
    assert not any(r.get("filled") for sid in NEW_MONTHLY for r in raw[sid]["records"])
    assert max(r["as_of"][:7] for sid in cf._anchor_ids(CFG)
               for r in raw[sid]["records"]) == "2026-08"
    assert all(sid not in NEW for sid, _ in report)


def test_mutation_without_the_guard_the_frontier_moves_and_months_are_invented():
    """Махнеш защитата (model_freq: monthly) -> фронтирът и запълването се сменят."""
    bad = copy.deepcopy(CFG)
    for sid in NEW_MONTHLY:
        bad["fred"][sid]["model_freq"] = "monthly"
    assert set(cf._cohort_ids(bad)) == COHORT_BEFORE | set(NEW_MONTHLY)
    assert set(cf._anchor_ids(bad)) == ANCHORS_BEFORE | set(NEW_MONTHLY)
    raw = _synthetic_raw(bad)
    cf.carry_forward_macro(raw, bad)
    front = max(r["as_of"][:7] for sid in cf._anchor_ids(bad) for r in raw[sid]["records"])
    assert front == "2026-09"                                     # фронтирът мръдна
    assert any(r.get("filled") == "carry_forward"
               for r in raw["macro_air_enplanements"]["records"])  # измислени месеци


def test_frontier_anchor_false_alone_still_invents_months():
    """Защо не е достатъчно само frontier_anchor:false (записано като урок в config.yaml)."""
    half = copy.deepcopy(CFG)
    for sid in NEW_MONTHLY:
        half["fred"][sid]["model_freq"] = "monthly"
        half["fred"][sid]["frontier_anchor"] = False
    assert set(cf._anchor_ids(half)) == ANCHORS_BEFORE            # фронтирът е пазен
    raw = _synthetic_raw(half)
    cf.carry_forward_macro(raw, half)
    enp = raw["macro_air_enplanements"]["records"]
    assert sum(1 for r in enp if r.get("filled") == "carry_forward") == 4   # ..., но редове се измислят


def test_native_dates_are_kept_for_monthly_native_and_weekly():
    obs_m = [("2026-05-01", 84693.0), ("2026-06-01", 1.0)]
    out = fetch_fred._to_model_freq(obs_m, CFG["fred"]["macro_air_enplanements"], 6)
    assert out == [("2026-05-01", 84693.0), ("2026-06-01", 1.0)]  # без month-end, без отрязване
    obs_w = [("2026-09-14", 4.4), ("2026-09-21", 4.478)]
    out = fetch_fred._to_model_freq(obs_w, CFG["fred"]["mkt_gasoline_retail"], 6)
    assert out == [("2026-09-14", 4.4), ("2026-09-21", 4.478)]

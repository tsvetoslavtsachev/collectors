"""УЛМ1 Г3/Г4: потребителското доверие (Мичиган) е извън кохортата на режима.

UMCSENT е месечна, но не е вход на режима. model_freq: monthly_native (като УБР2) не е
"monthly", значи carry_forward не я вижда: без кохорта, без котва, без запълване, с родните
дати на FRED (първо число на месеца). Мутационният тест доказва, че пазачът не е сянка: с
model_freq: monthly серията влиза в кохортата, мести фронтира или получава измислени месеци.

Тестовете са върху синтетични данни (без мрежа, без запис в data-core).

Run: python -m pytest collectors/vrm/tests/test_ulm1_consumer_sentiment.py
"""
import copy
from pathlib import Path

import yaml

from collectors.vrm import carry_forward as cf
from collectors.vrm import fetch_fred
from collectors.vrm import run as vrm_run
from collectors.vrm.tests.test_ubr2_macro_drivers import ANCHORS_BEFORE, COHORT_BEFORE, _month_ends

CFG = yaml.safe_load((Path(vrm_run.__file__).parent / "config.yaml")
                     .read_text(encoding="utf-8"))
SID = "mkt_consumer_sentiment"


def test_series_is_wired():
    m = CFG["fred"][SID]
    assert m["ticker"] == "UMCSENT" and m["transform"] == "level" and not m.get("computed")
    assert m["model_freq"] == "monthly_native" and m["source_freq"] == "monthly"
    exp = vrm_run.expected_series(CFG)
    assert SID in exp and len(exp) == 62


def test_cohort_and_anchors_are_unchanged():
    assert set(cf._cohort_ids(CFG)) == COHORT_BEFORE
    assert set(cf._anchor_ids(CFG)) == ANCHORS_BEFORE
    assert SID not in cf._fill_ids(CFG)


def test_native_month_dates_are_kept():
    obs = [("2026-07-01", 55.2), ("2026-08-01", 51.7)]
    assert fetch_fred._to_model_freq(obs, CFG["fred"][SID], 6) == obs   # без month-end, без отрязване


def _raw(cfg):
    """Кохортата до 2026-07; доверието с 2026-08, пред фронтира."""
    raw = {sid: {"ok": True, "records": [
        {"as_of": d, "value": 1.0, "source": "t", "resolution": "monthly"}
        for d in _month_ends("2026-01", "2026-07")]} for sid in cf._cohort_ids(cfg)}
    obs = [("2026-06-01", 49.5), ("2026-07-01", 55.2), ("2026-08-01", 51.7)]
    lv = fetch_fred._to_model_freq(obs, cfg["fred"][SID], 6)
    raw[SID] = {"ok": True, "records": [
        {"as_of": d, "value": v, "source": "t", "resolution": cfg["fred"][SID]["model_freq"]} for d, v in lv]}
    return raw


def test_frontier_does_not_move_and_nothing_is_filled():
    raw = _raw(CFG)
    before = copy.deepcopy(raw[SID]["records"])
    cf.carry_forward_macro(raw, CFG)
    assert raw[SID]["records"] == before
    assert max(r["as_of"][:7] for sid in cf._anchor_ids(CFG) for r in raw[sid]["records"]) == "2026-07"


def test_mutation_monthly_moves_the_frontier_or_invents_months():
    bad = copy.deepcopy(CFG)
    bad["fred"][SID]["model_freq"] = "monthly"
    assert set(cf._cohort_ids(bad)) == COHORT_BEFORE | {SID}
    raw = _raw(bad)
    cf.carry_forward_macro(raw, bad)
    front = max(r["as_of"][:7] for sid in cf._anchor_ids(bad) for r in raw[sid]["records"])
    filled = sum(1 for sid in raw for r in raw[sid]["records"] if r.get("filled") == "carry_forward")
    assert front != "2026-07" or filled > 0                      # пазачът има какво да пази

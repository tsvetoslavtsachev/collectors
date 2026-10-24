"""s9_tech О2: кредитният спред Moody's Baa минус 10г (BAA10Y) е дневна серия извън режима.

BAA10Y има пълна история във FRED (от 1986), значи НЕ е подвижен прозорец: къс отговор се
отказва, главата не се пренася (както DGS10). model_freq: daily = извън месечната кохорта,
без котва, без запълване. Мутационният тест доказва, че пазачът не е сянка: с model_freq:
monthly серията влиза в кохортата.

Тестовете са върху конфигурацията (без мрежа, без запис в data-core).

Run: python -m pytest collectors/vrm/tests/test_baa10y_credit_spread.py
"""
import copy
from pathlib import Path

import yaml

from collectors.vrm import carry_forward as cf
from collectors.vrm import run as vrm_run
from collectors.vrm import to_datacore as td
from collectors.vrm.tests.test_ubr2_macro_drivers import ANCHORS_BEFORE, COHORT_BEFORE

CFG = yaml.safe_load((Path(vrm_run.__file__).parent / "config.yaml")
                     .read_text(encoding="utf-8"))
SID = "mkt_baa_10y_spread"


def test_series_is_wired():
    m = CFG["fred"][SID]
    assert m["ticker"] == "BAA10Y" and m["transform"] == "level" and not m.get("computed")
    assert m["model_freq"] == "daily" and m["source_freq"] == "daily"
    exp = vrm_run.expected_series(CFG)
    assert SID in exp and len(exp) == 63


def test_full_history_not_a_rolling_window():
    assert not CFG["fred"][SID].get("rolling_window")
    assert SID not in td.rolling_window_series()


def test_cohort_and_anchors_are_unchanged():
    assert set(cf._cohort_ids(CFG)) == COHORT_BEFORE
    assert set(cf._anchor_ids(CFG)) == ANCHORS_BEFORE
    assert SID not in cf._fill_ids(CFG)


def test_mutation_monthly_enters_the_cohort():
    bad = copy.deepcopy(CFG)
    bad["fred"][SID]["model_freq"] = "monthly"
    assert set(cf._cohort_ids(bad)) == COHORT_BEFORE | {SID}   # пазачът има какво да пази

"""КРЪГ1п (25.11.2026): четирите серии, които седмичният анализ на W47 не намери.

mkt_ig_oas (BAMLC0A0CM, ICE във FRED: подвижен прозорец от 3 години, главата се пренася
като при HY), mkt_mortgage_30y (MORTGAGE30US, седмична), mkt_fed_funds_eff (EFFR, работни дни),
macro_headline_cpi (CPIAUCSL, monthly_native). Нито една не е вход на режима: кохортата,
котвите и множеството за запълване остават същите. Мутациите доказват, че пазачите не са
сенки: с model_freq: monthly общият CPI влиза в кохортата; без rolling_window IG губи
пренасянето на главата.

Тестовете са върху конфигурацията (без мрежа, без запис в data-core).

Run: python -m pytest collectors/vrm/tests/test_krag1p_credit_rates_cpi.py
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
NEW = {
    "mkt_ig_oas": ("BAMLC0A0CM", "daily"),
    "mkt_mortgage_30y": ("MORTGAGE30US", "weekly"),
    "mkt_fed_funds_eff": ("EFFR", "daily"),
    "macro_headline_cpi": ("CPIAUCSL", "monthly_native"),
}


def test_series_are_wired():
    exp = vrm_run.expected_series(CFG)
    for sid, (tkr, freq) in NEW.items():
        m = CFG["fred"][sid]
        assert m["ticker"] == tkr and m["transform"] == "level" and not m.get("computed")
        assert m["model_freq"] == freq
        assert sid in exp
    assert len(exp) == 72


def test_only_ig_is_a_rolling_window():
    rolling = td.rolling_window_series(CFG)
    assert "mkt_ig_oas" in rolling
    for sid in ("mkt_mortgage_30y", "mkt_fed_funds_eff", "macro_headline_cpi"):
        assert sid not in rolling


def test_cohort_and_anchors_are_unchanged():
    assert set(cf._cohort_ids(CFG)) == COHORT_BEFORE
    assert set(cf._anchor_ids(CFG)) == ANCHORS_BEFORE
    for sid in NEW:
        assert sid not in cf._fill_ids(CFG)


def test_mutation_monthly_headline_cpi_enters_the_cohort():
    bad = copy.deepcopy(CFG)
    bad["fred"]["macro_headline_cpi"]["model_freq"] = "monthly"
    assert set(cf._cohort_ids(bad)) == COHORT_BEFORE | {"macro_headline_cpi"}


def test_mutation_ig_without_rolling_window_loses_head_carry():
    bad = copy.deepcopy(CFG)
    del bad["fred"]["mkt_ig_oas"]["rolling_window"]
    assert "mkt_ig_oas" not in td.rolling_window_series(bad)

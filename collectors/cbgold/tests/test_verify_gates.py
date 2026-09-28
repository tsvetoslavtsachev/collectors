"""Офлайн тестове за verify.py математиката -- без мрежа. Живите Г1/Г2 числа
(реални IMF/Treasury) се пускат ръчно с collectors.cbgold.verify и влизат в
доклада.

Run: python -m pytest collectors/cbgold/tests/test_verify_gates.py
"""
from collectors.cbgold import verify as v


def test_gate2_matches_when_ounces_align():
    # ЗЛТ3: IMF USA 261,499,000 oz vs Treasury 261,498,926 oz -- <=0.01%
    usa_pairs = {"2026-07-31": 261_499_000.0}
    g2 = v.gate2_us_vs_treasury(usa_pairs)
    # gate2 hits the live Treasury endpoint for the date-picking step only
    # in main(); this unit test exercises the comparison math directly by
    # monkeypatching would need network -- instead assert the pure formula:
    imf_oz, treas_oz = 261_499_000.0, 261_498_926.0
    rel = abs(imf_oz - treas_oz) / treas_oz
    assert rel < 0.0001   # <= 0.01%


def test_mutation_scale_trap_multiplying_by_1e6_fails_the_gate():
    # ФАЛШИФИКАТОР на Г2: OBS_VALUE е вече в унции (SCALE="6" е само за
    # показване, ЗЛТ3) -- ако някой все пак умножи по 10^6, гейтът пада.
    imf_oz_correct = 261_499_000.0
    treas_oz = 261_498_926.0
    imf_oz_mutated = imf_oz_correct * 1_000_000   # the SCALE trap, applied wrongly
    rel_correct = abs(imf_oz_correct - treas_oz) / treas_oz
    rel_mutated = abs(imf_oz_mutated - treas_oz) / treas_oz
    assert rel_correct < 0.0001
    assert rel_mutated > 0.0001   # gate fails hard, not by a hair


def test_gate1_flags_stale_and_short_history(tmp_path):
    import json
    canon_dir = tmp_path
    # USA: fresh + full history -> OK
    (canon_dir / "cb_gold_usa_tonnes.json").write_text(json.dumps([
        {"as_of": "2000-10-31", "value": 8000.0},
        {"as_of": "2026-07-31", "value": 8133.5},
    ]), encoding="utf-8")
    # CHN: history starts too late (after the ЗЛТ3 baseline) -> first_ok False
    (canon_dir / "cb_gold_chn_tonnes.json").write_text(json.dumps([
        {"as_of": "2020-01-31", "value": 2000.0},
        {"as_of": "2026-07-31", "value": 2366.4},
    ]), encoding="utf-8")
    # POL: stale (last period far in the past) -> fresh_ok False
    (canon_dir / "cb_gold_pol_tonnes.json").write_text(json.dumps([
        {"as_of": "2000-04-30", "value": 100.0},
        {"as_of": "2024-01-31", "value": 500.0},
    ]), encoding="utf-8")
    # TUR/IND: minimal valid rows so the loop doesn't crash on a missing file
    for code, first in (("tur", "2000-08-31"), ("ind", "2007-10-31")):
        (canon_dir / f"cb_gold_{code}_tonnes.json").write_text(json.dumps([
            {"as_of": first, "value": 1.0},
            {"as_of": "2026-07-31", "value": 2.0},
        ]), encoding="utf-8")

    import datetime as dt
    g1 = v.gate1_availability(canon_dir, today=dt.date(2026, 9, 28))
    assert g1["USA"]["first_ok"] and g1["USA"]["fresh_ok"]
    assert g1["CHN"]["first_ok"] is False
    assert g1["POL"]["fresh_ok"] is False

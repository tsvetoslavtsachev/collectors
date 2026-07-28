# -*- coding: utf-8 -*-
"""Мандат №44 — тестове на пазача на ценовия слой (collectors/price_guard.py)."""
import json

from collectors import price_guard as pg


def _row(as_of, value, value_tr=None):
    r = {"as_of": as_of, "value": value, "source": "yfinance"}
    if value_tr is not None:
        r["value_tr"] = value_tr
    return r


def test_round_kills_epsilon_churn():
    # реалният случай: 21.399103 срещу 21.399094 (5e-6 дрейф между два пуска)
    a = pg.round_records([_row("2005-01-07", 100.0, 21.399103)])
    b = pg.round_records([_row("2005-01-07", 100.0, 21.399094)])
    assert a == b
    assert a[0]["value_tr"] == 21.3991


def test_round_keeps_real_dividend_rescale_visible():
    # истински рескейл (~1.2% фактор) остава различим след закръглянето
    a = pg.round_records([_row("2005-01-07", 100.0, 21.399103)])
    b = pg.round_records([_row("2005-01-07", 100.0, 21.145)])
    assert a != b


def test_no_declaration_when_nothing_changed():
    ex = pg.round_records([_row("2026-07-17", 82.49), _row("2026-07-24", 88.86)])
    assert pg.declare_revisions(ex, ex) is None
    assert pg.declare_revisions([], ex) is None
    assert pg.declare_revisions(ex, []) is None


def test_frontier_settling_is_declared_as_settled():
    # случаят VNQ: петъчният бар се доуточнява между два пуска (в SETTLE_DAYS)
    ex = [_row("2026-07-17", 99.0), _row("2026-07-24", 100.81)]
    new = pg.round_records([_row("2026-07-17", 99.0), _row("2026-07-24", 98.69)])
    d = pg.declare_revisions(ex, new)
    assert d["settled_n"] == 1
    assert d["settled"][0] == {"as_of": "2026-07-24", "field": "value",
                              "old": 100.81, "new": 98.69}
    assert "historical_value" not in d


def test_historical_value_revision_is_named():
    # ревизия на суров факт ИЗВЪН settle прозореца -> поименна декларация
    ex = [_row("2026-01-09", 59.12), _row("2026-07-24", 88.86)]
    new = pg.round_records([_row("2026-01-09", 59.55), _row("2026-07-24", 88.86)])
    d = pg.declare_revisions(ex, new)
    assert d["historical_value_n"] == 1
    assert d["historical_value"][0]["as_of"] == "2026-01-09"
    assert "split_suspect" not in d


def test_split_suspect_on_mass_historical_value_change():
    ex = [_row("2025-{:02d}-{:02d}".format(m, d), 100.0 + m)
          for m in range(1, 6) for d in (3, 10, 17, 24)] + [_row("2026-07-24", 88.86)]
    new = pg.round_records(
        [{**r, "value": r["value"] / 2} for r in ex[:-1]] + [ex[-1]])
    d = pg.declare_revisions(ex, new)
    assert d["historical_value_n"] == 20
    assert d["split_suspect"] is True


def test_recomputed_series_summarized_not_named():
    # COT персентил (жив пуск 28.07): плъзгащият прозорец мести цялата история
    # с ~0.1-0.2 п.п. всяка седмица -> обобщение {n, max_abs}, БЕЗ поименен
    # списък и БЕЗ split-suspect; фронтиерът (settle прозорецът) остава поименен
    hist = [_row("2025-{:02d}-{:02d}".format(m, d), 10.0 + m)
            for m in range(1, 6) for d in (3, 10, 17, 24)]
    ex = hist + [_row("2026-07-17", 48.0), _row("2026-07-21", 50.0)]
    new = pg.round_records(
        [{**r, "value": r["value"] + 0.2} for r in hist]
        + [_row("2026-07-17", 48.3), _row("2026-07-21", 50.0)])
    d = pg.declare_revisions(ex, new, recomputed=True)
    assert d["value_recomputed"]["n"] == 20
    assert abs(d["value_recomputed"]["max_abs"] - 0.2) < 1e-6
    assert "historical_value" not in d and "split_suspect" not in d
    assert d["settled_n"] == 1
    line = pg.summarize(d)
    assert "recomputed n=20" in line and "SPLIT-SUSPECT" not in line


def test_recomputed_real_revision_shows_as_outsized_max_abs():
    # реална историческа ревизия при recomputed серия личи по извънреден max_abs
    ex = [_row("2025-03-07", 12.0), _row("2025-06-06", 40.0),
          _row("2026-07-21", 50.0)]
    new = pg.round_records([_row("2025-03-07", 12.1), _row("2025-06-06", 25.0),
                            _row("2026-07-21", 50.0)])
    d = pg.declare_revisions(ex, new, recomputed=True)
    assert d["value_recomputed"]["n"] == 2
    assert abs(d["value_recomputed"]["max_abs"] - 15.0) < 1e-6


def test_recomputed_flag_is_per_series_default_stays_named():
    # без флага (другите серии) историческата ревизия остава поименна + split-suspect
    ex = [_row("2025-{:02d}-{:02d}".format(m, d), 100.0 + m)
          for m in range(1, 6) for d in (3, 10, 17, 24)] + [_row("2026-07-24", 88.86)]
    new = pg.round_records(
        [{**r, "value": r["value"] / 2} for r in ex[:-1]] + [ex[-1]])
    d = pg.declare_revisions(ex, new)
    assert d["historical_value_n"] == 20
    assert d["split_suspect"] is True
    assert "value_recomputed" not in d


def test_value_tr_rescale_is_summarized_not_named():
    ex = [_row("2025-0{}-03".format(m), 100.0, 20.0) for m in range(1, 8)] + \
         [_row("2026-07-24", 88.86, 88.86)]
    new = pg.round_records(
        [{**r, "value_tr": r["value_tr"] * 0.99} for r in ex[:-1]] + [ex[-1]])
    d = pg.declare_revisions(ex, new)
    assert d["value_tr_rescale"]["n"] == 7
    assert abs(d["value_tr_rescale"]["max_rel"] - 0.01) < 1e-6
    assert "historical_value" not in d and "settled" not in d


def test_migration_compares_at_write_precision():
    # старият канон с 6 знака, новият пуск закръглен: еднаквата цена НЕ е ревизия
    ex = [_row("2026-07-17", 82.490002, 82.490002)]
    new = pg.round_records([_row("2026-07-17", 82.489996, 82.489996)])
    assert pg.declare_revisions(ex, new) is None


def test_deadband_absorbs_source_jitter():
    # джитър на границата на закръгляне: 21.39905 <-> 21.39915 пляска при 4dp;
    # deadband-ът пази старата стойност
    ex = [_row("2005-01-07", 100.0, 21.3991)]
    new = pg.apply_deadband(ex, pg.round_records([_row("2005-01-07", 100.0, 21.39915)]))
    assert new[0]["value_tr"] == 21.3991
    assert pg.declare_revisions(ex, new) is None


def test_deadband_lets_real_moves_through():
    # сетълване -2.1% (VNQ) и дивидентен рескейл ~1% минават невредими
    ex = [_row("2026-07-24", 100.81, 100.81)]
    new = pg.apply_deadband(ex, pg.round_records([_row("2026-07-24", 98.69, 98.69)]))
    assert new[0]["value"] == 98.69
    ex2 = [_row("2005-01-07", 100.0, 21.3991)]
    new2 = pg.apply_deadband(ex2, pg.round_records([_row("2005-01-07", 100.0, 21.145)]))
    assert new2[0]["value_tr"] == 21.145


def test_deadband_small_values_use_abs_floor():
    # oil спредове около нулата: 0.01 -> 0.02 е РЕАЛНА промяна (мин. праг 1e-4 abs)
    ex = [_row("2026-07-10", 0.01)]
    new = pg.apply_deadband(ex, pg.round_records([_row("2026-07-10", 0.02)]))
    assert new[0]["value"] == 0.02


def test_determinism_same_inputs_same_declaration():
    ex = [_row("2026-01-09", 59.12), _row("2026-07-24", 100.81)]
    new = pg.round_records([_row("2026-01-09", 59.55), _row("2026-07-24", 98.69)])
    assert pg.declare_revisions(ex, new) == pg.declare_revisions(ex, new)


def test_ledger_write_and_empty_declaration(tmp_path):
    p = pg.write_ledger("vrm", {}, base=tmp_path)
    assert p.name == "price_revisions_vrm.json"
    assert json.load(open(p, encoding="utf-8")) == {}
    decl = {"etf_vnq": {"tip": "2026-07-24", "settle_window_from": "2026-07-17",
                        "settled": [], "settled_n": 1}}
    p2 = pg.write_ledger("vrm", decl, base=tmp_path)
    assert json.load(open(p2, encoding="utf-8")) == decl

"""Гейт: правдоподобност на тонажа (29.09.2026, след ЗЛТ4 О2) -- repair()
поправя само доказаните дефекти на източника, gate()/check() отказва
всичко останало преди записа. Офлайн, без мрежа, без data-core.

Фикстурата е с реалната форма на 29.09.2026 отговора: BRA x1000 от 2026-03,
AGO x1000 през цялата история, DNK 0 между два ненулеви месеца, САЩ на
книжна цена ($42.22/oz) -- числата са от живия payload, закръглени.

МУТАЦИИ (гейтът трябва да падне): инжектирана x1000 стойност (голяма страна
-> над САЩ; малка страна -> x1000 стъпка) и инжектирана нула между ненулеви.

Run: python -m pytest collectors/cbgold/tests/test_plausibility.py
"""
import pytest

from collectors.cbgold import fetch_imf, plausibility as pl, run, to_datacore as td

MONTHS = ["2025-12-31", "2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"]
PRICE = {"2025-12-31": 4300.0, "2026-01-31": 4900.0, "2026-02-28": 5200.0,
         "2026-03-31": 4600.0, "2026-04-30": 4500.0}   # $/oz, market-like
US_OZ = 261_499_000.0
BRA_OZ = 5_544_278.72


def _oz_usd():
    """(oz, usd) in parse() shape: {code: [(as_of, value)]}."""
    oz = {
        "USA": [(d, US_OZ) for d in MONTHS],
        "POL": [(d, 17_000_000.0) for d in MONTHS],
        "IND": [(d, 28_000_000.0) for d in MONTHS],
        "CHN": [(d, 74_000_000.0) for d in MONTHS],
        "BRA": [(d, BRA_OZ * (1000 if d >= "2026-03-31" else 1)) for d in MONTHS],
        "AGO": [(d, 592_900_000.0) for d in MONTHS],
        "DNK": [(d, 0.0 if d == "2026-02-28" else 2_140_000.0) for d in MONTHS],
        "MLT": [(d, 0.0 if d in ("2026-01-31", "2026-02-28") else 10_000.0) for d in MONTHS],
        "G163": [(d, 346_000_000.0) for d in MONTHS],   # Euro Area: above the US, legit
    }
    usd = {}
    for code, pairs in oz.items():
        if code == "USA":
            usd[code] = [(d, US_OZ * 42.2222) for d, _ in pairs]      # book value
        elif code == "BRA":
            usd[code] = [(d, BRA_OZ * PRICE[d]) for d, _ in pairs]   # true ounces x price
        elif code == "AGO":
            usd[code] = [(d, 592_900.0 * PRICE[d]) for d, _ in pairs]
        else:
            usd[code] = [(d, v * PRICE[d]) for d, v in pairs]
    return oz, usd


def _raw(parsed):
    return fetch_imf.to_records(parsed)


def _sid(code):
    return f"cb_gold_{code.lower()}_tonnes"


# ---------- repair ----------

def test_repair_rescales_the_confirmed_x1000_slips_and_drops_the_sandwiched_zero():
    oz, usd = _oz_usd()
    fixed, notes = pl.repair(oz, usd)
    assert [v for _, v in fixed["BRA"]] == pytest.approx([BRA_OZ] * 5)
    assert [v for _, v in fixed["AGO"]] == pytest.approx([592_900.0] * 5)
    assert "2026-02-28" not in dict(fixed["DNK"])            # gap, not a zero
    assert len(fixed["DNK"]) == 4
    assert fixed["USA"] == oz["USA"]                         # book value is not a slip
    assert fixed["G163"] == oz["G163"]                       # aggregate: never rescaled
    assert len(fixed["MLT"]) == 5                            # 2-zero run left alone
    assert any(n.startswith("BRA 2026-03..2026-04: /1,000 over 2") for n in notes)
    assert any(n.startswith("AGO 2025-12..2026-04: /1,000 over 5") for n in notes)
    assert any(n.startswith("DNK 2026-02: 0 between") for n in notes)


def test_repair_needs_evidence_no_usd_field_means_no_rescale():
    oz, _ = _oz_usd()
    fixed, _ = pl.repair(oz, {})
    assert dict(fixed["BRA"])["2026-03-31"] == BRA_OZ * 1000
    assert dict(fixed["AGO"])["2025-12-31"] == 592_900_000.0


def test_repair_refuses_a_rescale_the_usd_field_contradicts():
    oz, usd = _oz_usd()
    # USD value consistent with the HUGE ounces (i.e. not a slip): /1000 would
    # put the implied price at 1000x market -> no repair.
    usd["AGO"] = [(d, 592_900_000.0 * PRICE[d]) for d in MONTHS]
    fixed, _ = pl.repair(oz, usd)
    assert dict(fixed["AGO"])["2025-12-31"] == 592_900_000.0


# ---------- gate ----------

def _clean_raw():
    oz, usd = _oz_usd()
    return _raw(pl.repair(oz, usd)[0])


def test_gate_green_on_the_repaired_batch():
    assert pl.gate(_clean_raw()) == {}


def test_gate_red_on_the_unrepaired_source_batch():
    oz, _ = _oz_usd()
    red = pl.gate(_raw(oz))
    assert set(red) == {_sid("BRA"), _sid("AGO"), _sid("DNK")}
    assert any("above the US level" in v for v in red[_sid("AGO")])
    assert any("x1,000 step" in v for v in red[_sid("BRA")])
    assert any("zero(s) between non-zero" in v for v in red[_sid("DNK")])


def test_MUTATION_injected_x1000_on_a_big_country_goes_red():
    raw = _clean_raw()
    recs = raw[_sid("POL")]["records"]
    recs[2] = {**recs[2], "value": recs[2]["value"] * 1000}
    red = pl.gate(raw)
    assert list(red) == [_sid("POL")]
    assert any("above the US level" in v for v in red[_sid("POL")])
    assert any("x1,000 step" in v for v in red[_sid("POL")])


def test_MUTATION_injected_x1000_on_a_tiny_country_goes_red_below_the_ceiling():
    # 0.311 t -> 311 t stays under the US; only the step signature catches it.
    raw = _clean_raw()
    recs = raw[_sid("MLT")]["records"]
    recs[-1] = {**recs[-1], "value": recs[-1]["value"] * 1000}
    red = pl.gate(raw)
    assert list(red) == [_sid("MLT")]
    assert red[_sid("MLT")] == [f"1 x1,000 step(s) (first at {recs[-1]['as_of']})"]


def test_MUTATION_injected_sandwiched_zero_goes_red():
    raw = _clean_raw()
    recs = raw[_sid("CHN")]["records"]
    recs[2] = {**recs[2], "value": 0.0}
    red = pl.gate(raw)
    assert list(red) == [_sid("CHN")]
    assert red[_sid("CHN")] == [f"1 zero(s) between non-zero months ({recs[2]['as_of']})"]


def test_gate_leaves_edge_zeros_and_zero_runs_alone():
    # A country that sold out (trailing zeros) or a 2+ zero run is not a gap.
    recs = [{"as_of": d, "value": v} for d, v in
            zip(MONTHS, [5.0, 0.0, 0.0, 3.0, 0.0])]
    assert pl.check(_sid("NOR"), recs, ceiling=8000.0) == []


def test_gate_without_a_us_level_refuses_countries_not_aggregates():
    raw = _clean_raw()
    del raw[_sid("USA")]
    red = pl.gate(raw)
    assert _sid("POL") in red and _sid("G163") not in red


# ---------- wiring: push refuses, run goes red ----------

class _Store:
    def __init__(self):
        self.writes = {}

    def read(self, series_id):
        return []

    def write(self, series_id, records, schema_version):
        self.writes[series_id] = records
        return {"series_id": series_id, "rows": len(records),
                "as_of": max(r["as_of"] for r in records), "source": "IMF"}


def test_push_refuses_the_implausible_series_and_writes_the_rest(monkeypatch):
    store = _Store()
    monkeypatch.setattr(td.storage, "read_canonical", store.read)
    monkeypatch.setattr(td.datacore, "write", store.write)
    raw = _clean_raw()
    recs = raw[_sid("POL")]["records"]
    recs[1] = {**recs[1], "value": recs[1]["value"] * 1000}
    res = {r["series_id"]: r for r in td.push(raw)}
    assert _sid("POL") not in store.writes
    assert res[_sid("POL")]["skipped"].startswith("refused: implausible")
    assert set(store.writes) == set(raw) - {_sid("POL")}


def test_run_exits_red_when_the_gate_refused_a_series(monkeypatch, capsys):
    raw = _clean_raw()
    recs = raw[_sid("CHN")]["records"]
    recs[2] = {**recs[2], "value": 0.0}
    store = _Store()
    monkeypatch.setattr(td.storage, "read_canonical", store.read)
    monkeypatch.setattr(td.datacore, "write", store.write)
    monkeypatch.setattr(run, "assemble", lambda: (raw, []))
    monkeypatch.setattr(run, "freshness_verdict", lambda pushed: (0, "fresh (stub)"))
    monkeypatch.setattr(run.sys, "argv", ["run"])
    assert run.main() == 1
    assert "plausibility gate refused 1 series" in capsys.readouterr().out

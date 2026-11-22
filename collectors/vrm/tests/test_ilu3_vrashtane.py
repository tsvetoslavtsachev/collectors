# -*- coding: utf-8 -*-
"""ИЛЮ3, връщането от ИЛЮД1 (22.11.2026).

В5: отхвърлената или паднала глава от Министерството и застоялият архив за VIX/MOVE правят
хода ЧЕРВЕН (run.main връща 3, vrm.yml червенее в края, след комита), не само „not-ok“.
В10: точката от Министерството за ден D се заменя от FRED на следващия ход, щом FRED я има.
"""
import datetime as dt

import pytest

from collectors.vrm import fetch_fred, fetch_treasury as ft, run, to_datacore as td
from collectors.vrm.tests.test_treasury_head import CSV, FRED

CFG10 = {"settings": {"round_dp": 6}, "fred": {"mkt_ust_10y": {
    "ticker": "DGS10", "transform": "level", "model_freq": "daily",
    "treasury_head": {"curve": "par", "column": "10 Yr"}}}}


@pytest.fixture(autouse=True)
def clean_loud(monkeypatch):
    monkeypatch.setattr(fetch_fred, "LOUD", [])


def _main_with(monkeypatch, raw_fn):
    monkeypatch.setattr(run.to_datacore, "assert_safe_root", lambda: None)
    monkeypatch.setattr(run.to_datacore, "push",
                        lambda raw: [{"series_id": k, "rows": 1, "as_of": "2026-11-20"} for k in raw])
    monkeypatch.setattr(run, "assemble", raw_fn)
    monkeypatch.setattr(run.sys, "argv", ["run"])
    return run.main()


def test_healthy_run_is_green(monkeypatch):
    assert _main_with(monkeypatch, lambda cfg: {"mkt_vix": {"ok": True, "records": []},
                                                "mkt_move": {"ok": True, "records": []}}) == 0


def test_mutation_stale_vix_archive_makes_the_run_red(monkeypatch, capsys):
    raw = {"mkt_vix": {"ok": False, "error": "px_vix_daily: tip 2026-11-06 is 16 days old"},
           "mkt_move": {"ok": True, "records": []}}
    assert _main_with(monkeypatch, lambda cfg: raw) == run.LOUD_RC == 3
    assert "::error::" in capsys.readouterr().out


def test_mutation_html_from_treasury_makes_the_run_red(monkeypatch):
    monkeypatch.setattr(fetch_fred, "fetch_observations", lambda t: list(FRED))

    def html(c, col):
        raise ValueError("treasury answered HTML, not CSV")
    monkeypatch.setattr(ft, "fetch", html)

    def assemble(cfg):
        out = fetch_fred.fetch_fred(CFG10)          # серията остава от FRED ...
        assert out["mkt_ust_10y"]["ok"]
        return {"mkt_ust_10y": out["mkt_ust_10y"], "mkt_vix": {"ok": True}, "mkt_move": {"ok": True}}
    assert _main_with(monkeypatch, assemble) == 3      # ... но ходът е червен


def test_mutation_refused_head_makes_the_run_red(monkeypatch):
    monkeypatch.setattr(fetch_fred, "fetch_observations", lambda t: list(FRED))
    monkeypatch.setattr(ft, "fetch", lambda c, col: ft.parse_csv(CSV.replace("5.04", "5.40"), col))
    fetch_fred.fetch_fred(CFG10)
    assert any("REFUSED" in x for x in fetch_fred.LOUD)
    assert run.loud_failures({}) == fetch_fred.LOUD


def test_accepted_head_is_not_loud(monkeypatch):
    monkeypatch.setattr(fetch_fred, "fetch_observations", lambda t: list(FRED))
    monkeypatch.setattr(ft, "fetch", lambda c, col: ft.parse_csv(CSV, col))
    fetch_fred.fetch_fred(CFG10)
    assert fetch_fred.LOUD == []


# ── В10 ──
class Store:
    def __init__(self):
        self.records = []

    def read(self, sid):
        return list(self.records)

    def write(self, sid, records, schema_version):
        self.records = [dict(r) for r in records]
        return {"series_id": sid, "rows": len(records), "as_of": max(r["as_of"] for r in records)}


def test_treasury_point_is_replaced_by_fred_on_the_next_run(monkeypatch):
    store = Store()
    monkeypatch.setattr(td, "assert_safe_root", lambda: None)
    monkeypatch.setattr(td.storage, "read_canonical", store.read)
    monkeypatch.setattr(td.datacore, "write", store.write)
    monkeypatch.setattr(td.price_guard, "write_ledger", lambda *a, **k: "ledger")
    monkeypatch.setattr(ft, "fetch", lambda c, col: ft.parse_csv(CSV, col))

    # събота: FRED свършва в четвъртък 19.11, петъкът идва от Министерството
    monkeypatch.setattr(fetch_fred, "fetch_observations", lambda t: list(FRED))
    rec = fetch_fred.fetch_fred(CFG10)["mkt_ust_10y"]
    td.push({"mkt_ust_10y": {"ok": True, "records": rec["model_records"]}})
    fri = [r for r in store.records if r["as_of"] == "2026-11-20"]
    assert fri and fri[0]["source"].startswith("US Treasury") and fri[0]["value"] == 4.92

    # следващият ход: FRED вече има 20.11 (H.15 дава същото число; кой печели казва източникът)
    monkeypatch.setattr(fetch_fred, "fetch_observations", lambda t: list(FRED) + [("2026-11-20", 4.92)])
    rec = fetch_fred.fetch_fred(CFG10)["mkt_ust_10y"]
    td.push({"mkt_ust_10y": {"ok": True, "records": rec["model_records"]}})
    fri = [r for r in store.records if r["as_of"] == "2026-11-20"]
    assert len(fri) == 1 and fri[0]["source"] == "FRED DGS10" and fri[0]["value"] == 4.92
    assert fetch_fred.LOUD == []                      # общият ден съвпада: нищо червено

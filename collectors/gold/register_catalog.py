"""Declare the gold canonical series in the data-core catalog (identity guard).

code-map §6 step 3: declare every series in the catalog FIRST -- the writer's
identity gate refuses any series_id it does not find here.

Run once against a data-core checkout:
    DATACORE_ROOT=C:\\Projects\\data-core \\
    PYTHONPATH=C:\\Projects\\collectors python -m collectors.gold.register_catalog

Upserts ONLY its own five keys, field-wise (never `series[sid] = entry(...)`
outright): a blind overwrite would drop a foreign field another session added to
one of these entries later -- the cot registrar's KMW-2 lesson (2026-09-04).
"""
from __future__ import annotations
import json
import os
from pathlib import Path

ENTRIES = {
    "mkt_gold_usd": {
        "description": "LBMA Gold Price PM fix, USD per troy ounce (ЗЛТ2, 28.09.2026) "
                       "-- the observatory's first independent gold spot series "
                       "(previously only the GLD fund price via yfinance).",
        "source": "LBMA",
        "manual_source": "none",
        "license": "LBMA Gold Price JSON feed - free, no login",
        "basis": "LBMA Gold Price PM auction fix",
        "frequency": "daily",
        "window": "open",
        "unit": "USD/oz",
        "schema_version": 1,
        "vrm_role": ["market-context"],
        "provisional": False,
        "source_kind": "automated",
        "note": "AM fix (gold_am.json) not collected -- PM is LBMA's own headline "
               "fix, a scope decision, not a gap. Full history from 1968-04-01.",
    },
    "mkt_gold_gbp": {
        "description": "LBMA Gold Price PM fix, GBP per troy ounce (ЗЛТ2, 28.09.2026).",
        "source": "LBMA",
        "manual_source": "none",
        "license": "LBMA Gold Price JSON feed - free, no login",
        "basis": "LBMA Gold Price PM auction fix",
        "frequency": "daily",
        "window": "open",
        "unit": "GBP/oz",
        "schema_version": 1,
        "vrm_role": ["market-context"],
        "provisional": False,
        "source_kind": "automated",
        "note": "11 historical closure-day gaps (e.g. the week of the Nixon Shock, "
               "Aug 1971) are omitted, never written as null. Full history from 1968-04-01.",
    },
    "mkt_gold_eur": {
        "description": "LBMA Gold Price PM fix, EUR per troy ounce (ЗЛТ2, 28.09.2026).",
        "source": "LBMA",
        "manual_source": "none",
        "license": "LBMA Gold Price JSON feed - free, no login",
        "basis": "LBMA Gold Price PM auction fix",
        "frequency": "daily",
        "window": "open",
        "unit": "EUR/oz",
        "schema_version": 1,
        "vrm_role": ["market-context"],
        "provisional": False,
        "source_kind": "automated",
        "note": "Null before 1999-01-04 (pre-euro) -- history starts there, not 1968.",
    },
    "etf_gld_tonnes": {
        "description": "Tonnes of gold held in the SPDR Gold Shares (GLD) trust "
                       "(ЗЛТ2, 28.09.2026) -- separates 'rates push gold' from "
                       "'ETF holders are leaving' (gaps_map.csv ред 36).",
        "source": "SPDR (State Street)",
        "manual_source": "none",
        "license": "SPDR historical archive - public, no login",
        "basis": "Daily trust holdings, tonnes",
        "frequency": "daily",
        "window": "open",
        "unit": "tonnes",
        "schema_version": 1,
        "vrm_role": ["market-context", "behavioral"],
        "provisional": False,
        "source_kind": "automated",
        "note": "IAU not collected -- iShares serves no machine-readable row without "
               "a browser (checked 28.09.2026). Full history from 2004-11-18.",
    },
    "etf_gld_oz": {
        "description": "Total troy ounces of gold held in the SPDR GLD trust "
                       "(ЗЛТ2, 28.09.2026) -- same trust as etf_gld_tonnes, SPDR's "
                       "own unit (1 tonne = 32150.7465 troy oz).",
        "source": "SPDR (State Street)",
        "manual_source": "none",
        "license": "SPDR historical archive - public, no login",
        "basis": "Daily trust holdings, troy ounces",
        "frequency": "daily",
        "window": "open",
        "unit": "troy oz",
        "schema_version": 1,
        "vrm_role": ["market-context"],
        "provisional": False,
        "source_kind": "automated",
        "note": "Full history from 2004-11-18.",
    },
    "etf_gldm_tonnes": {
        "description": "Tonnes of gold held in the SPDR Gold MiniShares (GLDM) "
                       "trust (ЗЛТ5а, 28.09.2026) -- CONTROL series for "
                       "etf_gld_tonnes, not the main line (Ц. 28.09: GLD stays "
                       "primary; GLDM catches a fund-switch 'rabbit' -- GLD "
                       "outflow paired with a GLDM inflow means fee-driven "
                       "switching, not a real exit from gold).",
        "source": "SPDR (State Street)",
        "manual_source": "none",
        "license": "SPDR historical archive - public, no login",
        "basis": "Daily trust holdings, tonnes (lower-fee sibling of GLD, 0.10% "
                "vs 0.40%)",
        "frequency": "daily",
        "window": "open",
        "unit": "tonnes",
        "schema_version": 1,
        "vrm_role": ["market-context", "behavioral"],
        "role": "control",
        "control_of": "etf_gld_tonnes",
        "provisional": False,
        "source_kind": "automated",
        "note": "Full history from 2018-06-26. Reading only in ЗЛТ5а -- the "
               "GLD-vs-GLDM verdict (fee-switch or not) is ЗЛТ4's job, not this "
               "collector's.",
    },
    "etf_gldm_oz": {
        "description": "Total troy ounces of gold held in the SPDR GLDM trust "
                       "(ЗЛТ5а, 28.09.2026) -- same trust as etf_gldm_tonnes, "
                       "SPDR's own unit.",
        "source": "SPDR (State Street)",
        "manual_source": "none",
        "license": "SPDR historical archive - public, no login",
        "basis": "Daily trust holdings, troy ounces",
        "frequency": "daily",
        "window": "open",
        "unit": "troy oz",
        "schema_version": 1,
        "vrm_role": ["market-context"],
        "role": "control",
        "control_of": "etf_gld_tonnes",
        "provisional": False,
        "source_kind": "automated",
        "note": "Full history from 2018-06-26.",
    },
    "mkt_silver_usd": {
        "description": "LBMA Silver Price, USD per troy ounce (ЗЛТ5а, "
                       "28.09.2026) -- control-group context for the gold "
                       "observatory: a decoupling from gold spot flags a "
                       "metals-wide vs. gold-specific move.",
        "source": "LBMA",
        "manual_source": "none",
        "license": "LBMA Silver Price JSON feed - free, no login",
        "basis": "LBMA Silver Price daily auction fix",
        "frequency": "daily",
        "window": "open",
        "unit": "USD/oz",
        "schema_version": 1,
        "vrm_role": ["market-context"],
        "provisional": False,
        "source_kind": "automated",
        "note": "19 historical null days omitted, never written as null. Full "
               "history from 1968-01-02.",
    },
    "mkt_silver_gbp": {
        "description": "LBMA Silver Price, GBP per troy ounce (ЗЛТ5а, 28.09.2026).",
        "source": "LBMA",
        "manual_source": "none",
        "license": "LBMA Silver Price JSON feed - free, no login",
        "basis": "LBMA Silver Price daily auction fix",
        "frequency": "daily",
        "window": "open",
        "unit": "GBP/oz",
        "schema_version": 1,
        "vrm_role": ["market-context"],
        "provisional": False,
        "source_kind": "automated",
        "note": "No historical null days (unlike mkt_gold_gbp's 11 closure "
               "gaps). Full history from 1968-01-02.",
    },
    "mkt_silver_eur": {
        "description": "LBMA Silver Price, EUR per troy ounce (ЗЛТ5а, 28.09.2026).",
        "source": "LBMA",
        "manual_source": "none",
        "license": "LBMA Silver Price JSON feed - free, no login",
        "basis": "LBMA Silver Price daily auction fix",
        "frequency": "daily",
        "window": "open",
        "unit": "EUR/oz",
        "schema_version": 1,
        "vrm_role": ["market-context"],
        "provisional": False,
        "source_kind": "automated",
        "note": "Null before 1999-01-04 (pre-euro) -- history starts there, "
               "not 1968, same cutoff as mkt_gold_eur.",
    },
}


def main() -> int:
    root = Path(os.environ.get("DATACORE_ROOT", "."))
    path = root / "catalog" / "catalog.json"
    cat = json.loads(path.read_text(encoding="utf-8"))
    series = cat["series"]

    added, updated = [], []
    for sid, fresh in ENTRIES.items():
        (updated if sid in series else added).append(sid)
        existing = dict(series.get(sid, {}))
        existing.update(fresh)
        series[sid] = existing

    path.write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    print(f"gold catalog: {len(added)} added, {len(updated)} updated")
    print(f"catalog now: {len(series)} series")
    for sid in added:
        print("  +", sid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

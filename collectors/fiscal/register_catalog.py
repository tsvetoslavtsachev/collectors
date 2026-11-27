"""Declare the three fiscal canonical series in the data-core catalog (identity guard).

Run once against a data-core checkout:
    DATACORE_ROOT=<data-core> PYTHONPATH=<collectors> python -m collectors.fiscal.register_catalog

Upserts ONLY its own keys, field-wise (the KMW-2 lesson: never overwrite a whole entry).
The coupon auctions are not a canonical series (one row per auction, not one number per
date); they live in data/auctions/ust_coupon_auctions.json, written by collectors.fiscal.auctions.
"""
from __future__ import annotations
import json
import os
from pathlib import Path

_COMMON = {"manual_source": "none", "window": "open", "schema_version": 1,
           "vrm_role": ["market-context"], "provisional": False, "source_kind": "automated"}

ENTRIES = {
    "fisc_avg_rate_marketable": dict(_COMMON, **{
        "description": "Average interest rate on US marketable Treasury debt, month end "
                       "(ПРОЛЕТБ2, 27.11.2026); the rate the state pays on its market debt.",
        "source": "US Treasury Fiscal Data, avg_interest_rates, security_desc Total Marketable",
        "license": "public domain (US government), keyless API",
        "basis": "weighted average rate on outstanding marketable securities",
        "frequency": "monthly",
        "unit": "percent",
        "note": "From 2001-01, published about the 4th business day of the next month. "
                "Total Interest-bearing Debt differs before 2023 (12.2014: +1.4 vs +1.7 over "
                "CPI); only Total Marketable is kept. Reader: migrations/m_dalg (path Г).",
    }),
    "fed_soma_ust": dict(_COMMON, **{
        "description": "The Fed's System Open Market Account holdings of US Treasury "
                       "securities, USD bn, weekly (ПРОЛЕТБ2, 27.11.2026).",
        "source": "NY Fed Markets API, soma/summary.json",
        "license": "public, keyless API",
        "basis": "par: bills + notes/bonds + TIPS + FRN, plus TIPS inflation compensation",
        "frequency": "weekly",
        "unit": "USD bn",
        "note": "From 2003-07-09, Wednesday as-of dates, published Thursday. Agencies and MBS "
                "excluded. Over debt held by the public (fisc_debt_held_public) it is the Fed's "
                "share in m_dalg (path В); vs the quarterly m_money gov_hold_cb the mean "
                "difference 01.2010-05.2026 is -0.71 pt (range -3.18 to +2.99).",
    }),
    "fisc_debt_held_public": dict(_COMMON, **{
        "description": "US federal debt held by the public, USD bn, daily (ПРОЛЕТБ2, 27.11.2026).",
        "source": "US Treasury Fiscal Data, debt_to_penny, debt_held_public_amt",
        "license": "public domain (US government), keyless API",
        "basis": "Debt to the Penny, debt held by the public (includes the Fed's holdings)",
        "frequency": "daily",
        "unit": "USD bn",
        "note": "Debt held by the public from 1997-09-30 (earlier rows carry only the total), "
                "business days, one day behind. Reader: m_dalg (path В).",
    }),
}
ACTIVE = tuple(ENTRIES)


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
    path.write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"fiscal catalog: {len(added)} added, {len(updated)} updated; catalog now {len(series)}")
    for sid in added:
        print("  +", sid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

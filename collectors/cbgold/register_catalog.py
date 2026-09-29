"""Declare the 88 central-bank gold reserve series in the data-core catalog
(identity guard) -- one per IMF COUNTRY/aggregate the IRFCL dataflow serves
for this indicator (ЗЛТ5а, 28.09.2026). See collectors/gold/register_catalog.py
for the same upsert convention this mirrors.

Run once against a data-core checkout:
    DATACORE_ROOT=C:\\Projects\\data-core \\
    PYTHONPATH=C:\\Projects\\collectors python -m collectors.cbgold.register_catalog

Upserts ONLY its own 88 keys, field-wise (same reasoning as collectors.gold:
a blind overwrite would drop a foreign field another session added later).
"""
from __future__ import annotations
import json
import os
from pathlib import Path

from .countries import COUNTRY_NAMES
from .fetch_imf import SERIES_PREFIX, SERIES_SUFFIX

AGGREGATES = {"EZB", "G163"}


def _entry(code: str, name: str) -> dict:
    kind = "aggregate" if code in AGGREGATES else "country"
    return {
        "description": f"Official gold reserves, {name} ({code}) -- IMF "
                       "International Reserves and Foreign Currency Liquidity "
                       "(IRFCL), sector S1XS1311 monetary gold (ЗЛТ5а, 28.09.2026).",
        "source": "IMF (IRFCL, api.imf.org)",
        "manual_source": "none",
        "license": "IMF SDMX 2.1 API - free, no login",
        "basis": "IRFCL indicator IRFCLDT1_IRFCL56V_FTO, sector S1XS1311 "
                "(monetary gold, official reserves)",
        "frequency": "monthly",
        "window": "open",
        "unit": "tonnes",
        "schema_version": 1,
        "vrm_role": ["market-context"],
        "provisional": False,
        "source_kind": "automated",
        "geo_kind": kind,
        "note": "Reserve STOCK (includes gold on deposit/swap), not purchase "
               "flows -- the month-over-month change is the closest proxy for "
               "buying/selling. Reported with a 2-3 month lag and subject to "
               "revision. Raw troy ounces kept only for the Г2 Treasury "
               "cross-check (USA), never written to canonical.",
    }


ENTRIES = {f"{SERIES_PREFIX}{code.lower()}{SERIES_SUFFIX}": _entry(code, name)
           for code, name in COUNTRY_NAMES.items()}


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
    print(f"cbgold catalog: {len(added)} added, {len(updated)} updated")
    print(f"catalog now: {len(series)} series")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

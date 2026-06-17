"""Declare the COT canonical series in the data-core catalog (identity guard).

code-map §6 step 3: declare every series in the catalog FIRST — the writer's
identity gate refuses any series_id it does not find here. This helper is the
deterministic registrar: it reads the market registry and writes one catalog
entry per migrated market, recording the STABLE cftc_code as the durable identity
(the audit's lesson — the contract code, not the mutable name, IS the identity).

It UPSERTS only the `cot_*` namespace (its own), so oil + VRM declarations stay
byte-identical while re-running always reflects markets.py.

Run once against a data-core checkout:
    DATACORE_ROOT=C:\\Projects\\data-core \\
    PYTHONPATH=C:\\Projects\\collectors python -m collectors.cot.register_catalog
"""
from __future__ import annotations
import json
import os
from pathlib import Path

from . import markets

COHORT_LABEL = {markets.MM: "managed money", markets.LEV: "leveraged funds"}


def entry(m: dict) -> dict:
    cohort = COHORT_LABEL[m["cohort"]]
    e = {
        "description": f"{m['title']} COT {cohort} net positioning "
                       f"({m['family']}); raw weekly spec net (contracts)",
        "source": "cftc",
        "license": "CFTC publicreporting - public",
        "basis": f"{cohort} net = long - short, weekly report",
        "frequency": "weekly",
        "window": "open",
        "unit": "contracts_net",
        "schema_version": 1,
    }
    # The stable contract code is the durable identity (survives renames). Recorded
    # here so the catalog — not a mutable LIKE name — anchors what the series IS.
    if m.get("cftc_code"):
        e["cftc_code"] = m["cftc_code"]
    # Declared identity hazards -> recorded in the catalog (the durable scar).
    quality = []
    if m.get("cftc_code"):
        quality.append(f"cftc_code_pinned {m['cftc_code']}: stable contract; name "
                       "changes within it are benign rebrands, full history kept")
    if m.get("splice_watch"):
        quality.append("contract_splice_watch: contract renamed ~2022; "
                       "percentile view restricts to current segment")
    if m.get("satellite"):
        quality.append(f"satellite: {m['satellite']}")
    if m.get("name_break_2022"):
        quality.append("name_break_2022: LIKE query matches post-2022 only")
    if quality:
        e["quality"] = quality
    return e


def main() -> int:
    root = Path(os.environ.get("DATACORE_ROOT", "."))
    path = root / "catalog" / "catalog.json"
    cat = json.loads(path.read_text(encoding="utf-8"))
    series = cat["series"]

    added, updated = [], []
    for m in markets.migrated():
        sid = m["canonical"]
        (updated if sid in series else added).append(sid)
        series[sid] = entry(m)            # upsert — cot_* namespace only

    path.write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    print(f"cot catalog: {len(added)} added, {len(updated)} updated")
    print(f"catalog now: {len(series)} series")
    for sid in added:
        print("  +", sid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

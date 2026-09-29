"""Central-bank gold citizen -- IMF IRFCL tonnage -> data-core canonical.

Write-time guard: identical shape and reasoning to collectors.gold.to_datacore
(itself mirroring collectors.vrm.to_datacore) -- "upsert, не презапис". The
IMF dataflow serves each country's FULL history on every pull (no rolling
window declared here either), so the anti-truncation floor is measured
unconditionally on every existing row, every run: a short answer (an API
hiccup that returns only the latest few months) is refused, never absorbed.

Plausibility gate (29.09.2026, after ЗЛТ4 О2): the records that survive the
floor are checked last by plausibility.check() -- no country level above the
US, no 0 between non-zero months, no x10^3/x10^6 month-to-month step. Any
violation refuses the whole series ("refused: implausible ..."); the canon
keeps its previous file. run.py turns such a refusal into a red exit.
"""
from __future__ import annotations
import datacore
from datacore.schema import SCHEMA_VERSION
from datacore import storage

from . import plausibility

# Same floor as collectors.gold.to_datacore.MIN_RETAIN_RATIO.
MIN_RETAIN_RATIO = 0.9


def _window_start(existing: list):
    return min((r["as_of"] for r in existing), default=None) if existing else None


def _edge_warnings(existing: list, records: list) -> list:
    ex = sorted(r["as_of"] for r in existing)
    nw = sorted(r["as_of"] for r in records)
    if not ex or not nw:
        return []
    w = []
    if nw[0] > ex[0]:
        w.append(f"head shorter ({ex[0]} -> {nw[0]})")
    if nw[-1] < ex[-1]:
        w.append(f"tail regressed ({ex[-1]} -> {nw[-1]})")
    new_set = set(nw)
    gaps = [d for d in ex if nw[0] <= d <= nw[-1] and d not in new_set]
    if gaps:
        w.append(f"{len(gaps)} interior gap(s): {gaps[:3]}{'...' if len(gaps) > 3 else ''}")
    return w


def push(raw: dict) -> list[dict]:
    """raw: {series_id: {"ok": bool, "records": [{as_of, value, source}], "error": str}}."""
    results = []
    ceiling = plausibility.us_ceiling(raw)
    for series_id in sorted(raw):
        block = raw[series_id]
        records = block.get("records") if block.get("ok") else None
        if not records:
            results.append({"series_id": series_id,
                            "skipped": block.get("error", "no data")})
            continue
        warnings = []
        existing = storage.read_canonical(series_id)
        if existing:
            start = _window_start(existing)
            records = [r for r in records if r["as_of"] >= start]   # forward-only
            if len(records) < len(existing) * MIN_RETAIN_RATIO:
                results.append({"series_id": series_id, "skipped":
                                f"refused: would truncate {len(existing)}->{len(records)} rows"})
                continue
            warnings = _edge_warnings(existing, records)
        bad = plausibility.check(series_id, records, ceiling)
        if bad:
            results.append({"series_id": series_id, "implausible": bad,
                            "skipped": "refused: implausible -- " + "; ".join(bad)})
            continue
        try:
            res = datacore.write(series_id, records, SCHEMA_VERSION)
            if warnings:
                res["warnings"] = warnings
            results.append(res)
        except datacore.WriteRejected as e:
            results.append({"series_id": series_id, "skipped": f"rejected: {e}"})
    return results

"""Gold citizen -- LBMA spot (USD/EUR/GBP) + SPDR GLD tonnage -> data-core canonical.

Write-time guard (ЗЛТ2 Фаза Б.4 -- "upsert, не презапис"; mirrors the pattern
collectors.vrm.to_datacore carries for ЗЛТ1/Ф.0б): write_canonical overwrites the
whole file, so an anti-truncation floor at the write path is what makes a full
history "upsert" instead of "silent overwrite".

Every gold source here serves its FULL history on every pull (LBMA from 1968/1999,
SPDR from 2004-11-18) -- unlike ICE-in-FRED there is no rolling window to declare
(collectors.vrm.to_datacore.rolling_window_series has no gold entry). So, unlike vrm,
the floor here is ALWAYS measured on every existing row, unconditionally: a short
answer (an API hiccup that returns only the last month) is refused, never carried.
"""
from __future__ import annotations
import datacore
from datacore.schema import SCHEMA_VERSION
from datacore import storage

# Same floor as collectors.vrm.to_datacore.MIN_RETAIN_RATIO -- a healthy full-history
# pull re-returns ~all existing rows plus new ones; a catastrophic short pull does not.
MIN_RETAIN_RATIO = 0.9


def _window_start(existing: list):
    return min((r["as_of"] for r in existing), default=None) if existing else None


def _edge_warnings(existing: list, records: list) -> list:
    """Surface (never silence) a full-replace pull that quietly shrinks the series
    vs. just refreshing it -- same shape as vrm's _edge_warnings, WARNINGS not refusals."""
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
        try:
            res = datacore.write(series_id, records, SCHEMA_VERSION)
            if warnings:
                res["warnings"] = warnings
            results.append(res)
        except datacore.WriteRejected as e:
            results.append({"series_id": series_id, "skipped": f"rejected: {e}"})
    return results

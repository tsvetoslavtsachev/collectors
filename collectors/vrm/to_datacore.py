"""Citizen step — map the assembled VRM raw -> data-core canonical, via the writer.

Every VRM number lands in the guarded base (identity guard + schema_version +
health stamp), one datacore.write per series (write_canonical overwrites the whole
file, so each series' full record list arrives in one call — same as the S6
importer). A dead feed -> empty records -> that series is skipped (never a silent
zero, never aborts the run).

raw shape (assembled in run.py from all four feeds):
    {series_id: {"ok": bool, "records": [ {as_of, value, [value_tr], source, ...} ],
                 "error": str}}

Cardinal rule: this deterministic path is the ONLY writer of VRM numbers.
Gate 1 routes DATACORE_ROOT to a TEMP base — the real canonical is untouched.
"""
from __future__ import annotations
import os
from pathlib import Path
import datacore
from datacore import storage
from datacore.schema import SCHEMA_VERSION

# write_canonical overwrites the whole file; a short live pull would silently
# truncate the frozen 19y history. Refuse a full-replace that would drop the
# target series below this fraction of its existing rows (anti-truncation floor).
# Gate-5 hardening: tightened 0.5 -> 0.9. The VRM series are effectively fixed-length
# (full_replace re-pulls the whole history every run -> a healthy pull returns ~the
# same row count, +1 new period). The only legitimate shrink is small and bounded --
# the awh head erosion (3 flat-98 rows the live FRED AWHAE lacks = ~1% of 246) and a
# one-row not-yet-released tip; both sit far above 0.9. A 0.5 floor let a half-truncated
# pull (a broken fetch returning 49.6% of the rows) land silently; 0.9 catches that
# while never false-refusing a real run.
MIN_RETAIN_RATIO = 0.9


def assert_safe_root() -> None:
    """Enforce the cardinal rule structurally, AT THE WRITE PATH (Gate-5 hardening:
    moved off the run.py entrypoint so any caller reaching push()/write() without
    going through main() is still guarded).

    DATACORE_ROOT defaults to the data-core repo itself when unset (_root.root), so a
    forgotten env var would let even a --mock run overwrite the real frozen canonical.
    Refuse unless an explicit TEMP root is set, or the operator opts into the real base
    with DATACORE_ALLOW_REAL=1 (Gate 5, plus the deployed CI -- whose data-core checkout
    IS the installed package's repo per the editable install, so it sets ALLOW_REAL=1)."""
    real = Path(datacore.__file__).resolve().parent.parent   # the data-core repo
    env = os.environ.get("DATACORE_ROOT")
    allow_real = os.environ.get("DATACORE_ALLOW_REAL") == "1"
    if not env:
        raise SystemExit(
            "REFUSED: DATACORE_ROOT is unset -> would write the real data-core base. "
            "Set a TEMP root (Gate 1-4), or DATACORE_ALLOW_REAL=1 for the real base.")
    if Path(env).resolve() == real and not allow_real:
        raise SystemExit(
            f"REFUSED: DATACORE_ROOT ({Path(env).resolve()}) is the real data-core "
            "repo. Gate 1-4 use a TEMP root; set DATACORE_ALLOW_REAL=1 to override.")


def _window_start(existing: list):
    """The established series' window start = min existing as_of, or None if new.
    full_replace re-pulls fresh values across this window and extends FORWARD; it
    must NEVER extend backward. A live yfinance/FRED 'max' pull reaches inception
    (SPY 1993, PPI 1913) -- writing that would (a) mislabel pre-window price rows
    bloomberg_era=true (never in the MID/Bloomberg paste) and (b) hand the macro
    regime_engine a longer expanding-Z window than the frozen one, changing the
    231 founding labels. Preserving the start keeps the live canonical shape-equal
    to the frozen one, so the engines reproduce. New series (mkt_*) have no window
    yet -> full history is written."""
    return min((r["as_of"] for r in existing), default=None) if existing else None


def _edge_warnings(existing: list, records: list) -> list:
    """Surface (never silence) the ways a full_replace pull can quietly shrink an
    established series vs. just refresh it. The row-count floor alone misses these:
    a 1-row tip regression or a single interior hole passes the ratio but breaks the
    regime engine (one interior NaN nulls ~6 months via rolling(3).diff(3)). These
    are WARNINGS, not refusals -- some are legitimate (AWH's head genuinely starts
    later in FRED than the frozen Bloomberg paste; a not-yet-released FRED tip) and
    the fill policy is a Gate-4 decision; the point is they must be visible, not
    vanish into canonical."""
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


def push(raw: dict) -> list:
    """Write each ok series; return per-series results (written / skipped / warned).

    Write-time history guards (the cardinal rule must not rest on the fetch layer's
    good behavior): window preservation (no backward extension), the anti-truncation
    floor (hard refuse on catastrophic shrink), and edge/gap detection (loud warn on
    head erosion, tail regression, or interior holes a live source gap introduces)."""
    assert_safe_root()   # structural cardinal-rule guard at the write path itself
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

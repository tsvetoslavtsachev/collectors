"""Derive layer for the COT citizen — the splice-aware percentile machinery.

This is where the audit's missing piece lives: a contract-identity / splice
detector. The cot-monitor computed percentile over the WHOLE accumulated history
with no window label and no segment check, so WTI's NYMEX(2016-2022)+ICE(2022-)
seam poisoned the rank (published 27.62 vs ICE-only 64.2 — inverted).

Two principles, both from S13 decisions:

  1. percentile_<window> is PARAMETRIZED. The window is always an explicit
     argument, never a baked 525/226. Consumers ask for the window they mean.

  2. mark-don't-clean (hippocampus). When a key's history spans two contracts we
     do NOT delete the old segment. We record the seam (`contract_splice` flag)
     and compute the percentile on the CURRENT clean segment only, so the signal
     is honest while the scar stays visible.

The raw net series written to the base keeps EVERY row (full history). Only the
percentile *view* restricts to the clean segment.
"""
from __future__ import annotations
from statistics import mean, pstdev
from typing import Optional

MIN_HISTORY_OBS = 8  # below this, percentile/zscore are flagged, not faked


# ── identity / splice ───────────────────────────────────────────────────────

def distinct_identities(rows: list[dict]) -> list[str]:
    """Distinct, non-empty contract identities present in a market's rows."""
    seen = []
    for r in rows:
        name = (r.get("market_name") or "").strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def detect_splice(rows: list[dict]) -> Optional[dict]:
    """Return the seam if a market's history spans >1 contract identity, else None.

    rows must be sorted ascending by date. The seam is the first date at which
    the identity changes from the previous row. Both names + the boundary date
    are reported so the flag is self-describing (the scar, not a cleanup).
    """
    prev_name = None
    prev_date = None
    for r in rows:
        name = (r.get("market_name") or "").strip()
        if not name:
            continue
        if prev_name is not None and name != prev_name:
            return {
                "flag": "contract_splice",
                "seam_date": (r.get("date") or "")[:10],
                "from_identity": prev_name,
                "to_identity": name,
                "last_clean_date": (prev_date or "")[:10],
            }
        prev_name, prev_date = name, r.get("date")
    return None


def clean_segment(rows: list[dict], splice: Optional[dict]) -> list[dict]:
    """Rows belonging to the CURRENT contract identity (post-seam).

    Used only for the percentile view of a GENUINE contract_splice (two CODES
    under one key). The base series still stores every row. With no splice this
    is the whole history unchanged.

    NB: a benign `name_rebrand` (one stable CFTC code, cosmetic rename) is NOT a
    real discontinuity — consumers must pass splice=None for it so the percentile
    uses the WHOLE continuous history (restricting would re-truncate, e.g. copper
    20y -> 4y). Only pass a seam here when flag == "contract_splice".
    """
    if not splice:
        return rows
    current = splice["to_identity"]
    return [r for r in rows if (r.get("market_name") or "").strip() == current]


def detect_history_gap(rows: list[dict], max_gap_days: int = 60) -> Optional[dict]:
    """Flag a large date discontinuity (e.g. russell's 2008-2017 CME->ICE->CME
    round-trip leaves a multi-year hole under one code).

    Weekly COT rows are ~7 days apart; a gap > max_gap_days marks a real
    discontinuity worth recording. Defensive: dates that do not parse as ISO
    (synthetic test rows) are skipped, so this never fires on mock data.
    """
    from datetime import date
    ds = []
    for r in rows:
        d = (r.get("date") or "")[:10]
        try:
            ds.append(date.fromisoformat(d))
        except ValueError:
            continue
    ds.sort()
    if len(ds) < 2:
        return None
    biggest = max(((ds[i] - ds[i - 1]).days, ds[i - 1], ds[i])
                  for i in range(1, len(ds)))
    days, lo, hi = biggest
    if days <= max_gap_days:
        return None
    return {"flag": "history_gap", "gap_days": days,
            "from_date": lo.isoformat(), "to_date": hi.isoformat()}


# ── percentile / zscore (parametrized window) ───────────────────────────────

def percentile(values: list[float], current: float) -> Optional[float]:
    """Rank percentile of `current` within `values` (inclusive ≤), 0..100.

    Ported from cot-monitor derive_metrics: below/len. Returns None when there
    is too little history or zero dispersion (flagged, never a fake 0/50/100).
    """
    vals = [v for v in values if v is not None]
    if len(vals) < MIN_HISTORY_OBS:
        return None
    if pstdev(vals) == 0:
        return None
    below = sum(1 for v in vals if v <= current)
    return round(100.0 * below / len(vals), 2)


def zscore(values: list[float], current: float) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if len(vals) < MIN_HISTORY_OBS:
        return None
    sigma = pstdev(vals)
    if sigma == 0:
        return None
    return round((current - mean(vals)) / sigma, 4)


def percentile_window(net_series: list[tuple], window: int,
                      splice: Optional[dict] = None) -> dict:
    """Parametrized percentile of the latest net over the trailing `window` weeks
    of the CLEAN segment.

    net_series : [(date, net), ...] ascending; the full raw history.
    window     : trailing weeks to rank against — EXPLICIT, never hardcoded.
    splice     : seam from detect_splice; restricts the view to the current
                 contract (mark-don't-clean). Pass None for clean markets.

    Returns {window, percentile, zscore, n_obs, asof, segment} — segment names
    the identity the percentile was computed on, so the number is auditable.
    """
    rows = [{"date": d, "net": n, "market_name": mn}
            for (d, n, mn) in _as_named(net_series)]
    seg = clean_segment(rows, splice)
    seg = [r for r in seg if r["net"] is not None]
    if not seg:
        return {"window": window, "percentile": None, "zscore": None,
                "n_obs": 0, "asof": None, "segment": None}
    tail = seg[-window:]
    vals = [r["net"] for r in tail]
    current = vals[-1]
    return {
        "window": window,
        "percentile": percentile(vals, current),
        "zscore": zscore(vals, current),
        "n_obs": len(tail),
        "asof": (tail[-1]["date"] or "")[:10],
        "segment": splice["to_identity"] if splice else "single-contract",
    }


def _as_named(net_series):
    """Accept [(date, net)] or [(date, net, market_name)]; yield 3-tuples."""
    for item in net_series:
        if len(item) == 3:
            yield item
        else:
            d, n = item
            yield (d, n, None)


# ── data_quality (flags, never silent) ──────────────────────────────────────

def data_quality(rows: list[dict], market: dict) -> list[dict]:
    """Quality flags for a market's raw rows — the audit's gap, made explicit.

    Flags (each a dict so it is self-describing in the published face):
      - name_rebrand      : a name change within ONE stable cftc_code — benign,
                            cosmetic; keep WHOLE history (percentile uses all).
      - contract_splice   : a non-pinned key spans TWO contract identities —
                            the real hazard; keep both rows but the percentile
                            view restricts to the current segment.
      - history_gap       : a large date discontinuity (e.g. exchange round-trip).
      - name_break_2022   : declared LIKE name-change → short usable history.
      - satellite         : declared proxy contract (e.g. NYMEX Brent).
      - insufficient_hist : < MIN_HISTORY_OBS rows.
      - zero_dispersion   : all nets identical (flat) → percentile undefined.
      - missing_latest_net: latest net is None.
    """
    flags = []
    splice = detect_splice(rows)
    code_pinned = bool(market.get("cftc_code"))
    if splice:
        if code_pinned:
            # One stable CFTC code spans the rename → cosmetic, keep whole.
            flags.append({**splice, "flag": "name_rebrand"})
        else:
            # Two contract identities under a LIKE key → the real splice hazard.
            flags.append(splice)
    gap = detect_history_gap(rows)
    if gap:
        flags.append(gap)
    if market.get("name_break_2022"):
        flags.append({"flag": "name_break_2022",
                      "detail": "CFTC renamed the contract in 2022; LIKE query "
                                "matches post-2022 only (history starts ~2022)."})
    if market.get("satellite"):
        flags.append({"flag": "satellite", "detail": market["satellite"]})
    nets = [r.get("primary_net") for r in rows if r.get("primary_net") is not None]
    if len(nets) < MIN_HISTORY_OBS:
        flags.append({"flag": "insufficient_history", "n_obs": len(nets)})
    elif pstdev(nets) == 0:
        flags.append({"flag": "zero_dispersion"})
    if rows and rows[-1].get("primary_net") is None:
        flags.append({"flag": "missing_latest_net"})
    return flags

"""COT consumer — the dashboards read positioning FROM the base, not a local fetch.

This is the strangler cut-over (INIT-22 S13, decision 1): cot-monitor and
cot-cta retire their own `fetch_cot.py` pipeline and instead source the weekly
spec-net from data-core's `cot_<key>_net` canonical series. The percentile is
NOT a stored number — it is derived here on read with an EXPLICIT window (kills
the audit's baked-window bug), restricting to the clean segment only for a real
`contract_splice` (every migrated market is code-pinned -> name_rebrand -> the
whole continuous history is used).

Split of labour (P1 classification):
  - lib (here)  : raw net from the base + percentile_<window> + zscore + delta.
  - patch (each dashboard keeps) : regime_label, narratives, price overlay,
                                   secondary cohort, CTA AUM scalars (cot-cta).

The CTA lens (cot-cta) stays a PRICE-based TSMOM model; only its COT cross-
reference net now comes from the base. cot-monitor's positioning view (percentile
/ zscore / crowding) is reproduced here from the base directly.

WTI is special: its clean series is `oil_cot_wti_mm_pctile` (already a percentile,
written by the oil collector). The consumer reads that value as-is — no recompute,
no duplicate (decision 3).

Usage:
    DATACORE_ROOT=C:\\Projects\\data-core \\
    PYTHONPATH=C:\\Projects\\data-core;C:\\Projects\\collectors \\
    python -m collectors.cot.consumer
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Optional

from . import markets, derive

# cot-cta CTA capacity scalars (USD), ported verbatim from cot-cta cta_model.py —
# the "lens" that survives the pipeline retirement (decision 1). Used only for the
# estimated-position cross-reference; the TSMOM price signal stays in cot-cta.
CTA_AUM_SCALAR = {
    "sp500": 35e9, "nasdaq": 15e9, "us10y": 50e9, "gold": 12e9, "wti": 8e9,
    "bitcoin": 3e9, "eurfx": 5e9, "gbpfx": 3e9, "dxy": 4e9, "corn": 2e9, "vix": 1.5e9,
}


def _root() -> Path:
    return Path(os.environ.get("DATACORE_ROOT", ".")).resolve()


def read_base_net(series_id: str) -> list[tuple]:
    """Load a base series as [(as_of, value), ...] ascending. Empty if absent."""
    path = _root() / "data" / "canonical" / f"{series_id}.json"
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    out = [(r.get("as_of"), r.get("value")) for r in rows if r.get("value") is not None]
    return sorted(out, key=lambda x: x[0] or "")


def series_for(key: str) -> Optional[str]:
    """Resolve a market key to its base series_id (canonical, or WTI's reuse)."""
    m = next((x for x in markets.MARKETS if x["key"] == key), None)
    if not m:
        return None
    return m.get("canonical") or m.get("reuse")


def positioning_view(key: str, window: Optional[int] = None) -> dict:
    """cot-monitor's core positioning read, sourced from the base.

    window=None -> full-history percentile (cot-monitor's view); an int -> the
    trailing-window percentile (cot-cta uses 156/520). Returns net, percentile,
    zscore, delta_4w, n_obs, asof — all derived here, nothing baked.
    """
    sid = series_for(key)
    if sid is None:
        return {"key": key, "error": "unknown market"}

    # WTI reuse: the base already stores a percentile, not a raw net.
    if key == "wti":
        ser = read_base_net(sid)
        if not ser:
            return {"key": key, "series_id": sid, "error": "no data"}
        asof, pct = ser[-1]
        return {"key": key, "series_id": sid, "reused": True,
                "percentile": pct, "asof": asof, "n_obs": len(ser)}

    ser = read_base_net(sid)
    if not ser:
        return {"key": key, "series_id": sid, "error": "no data"}

    vals_all = [v for _, v in ser]
    net = vals_all[-1]
    w = window if window is not None else len(ser)
    pv = derive.percentile_window([(d, v) for d, v in ser], window=w, splice=None)
    delta_4w = (net - ser[-5][1]) if len(ser) >= 5 else None
    return {
        "key": key, "series_id": sid, "window": w,
        "net": net, "percentile": pv["percentile"], "zscore": pv["zscore"],
        "delta_4w": delta_4w, "n_obs": pv["n_obs"], "asof": pv["asof"],
    }


def cot_rows(key: str) -> list[dict]:
    """Reproduce a market's full weekly cohort rows FROM THE BASE — the dashboards'
    `markets/<key>.json` `cot` array, sourced from data-core instead of a local CFTC
    fetch (S13c cut-over). Each row mirrors cot-monitor's normalizer output exactly
    (date, market_name, open_interest, primary/secondary/tertiary long/short/net,
    report_family), so derive_metrics / cta_model / index.html run unchanged.

    Returns [] for WTI (reuse: the base holds only the oil percentile, not cohort
    detail) and for any unmigrated / absent series — the caller keeps its own thin
    fetch for those.
    """
    m = next((x for x in markets.MARKETS if x["key"] == key), None)
    if not m or not m.get("canonical"):
        return []  # WTI-reuse or unknown -> caller's thin fetch
    sid = m["canonical"]
    path = _root() / "data" / "canonical" / f"{sid}.json"
    if not path.exists():
        return []
    recs = json.loads(path.read_text(encoding="utf-8"))
    fam = m["family"]
    rows = []
    for r in recs:
        rows.append({
            "date": r.get("as_of"),
            "market_name": r.get("market_name"),
            "open_interest": r.get("open_interest"),
            "primary_long": r.get("primary_long"),
            "primary_short": r.get("primary_short"),
            "primary_net": r.get("primary_net"),
            "secondary_long": r.get("secondary_long"),
            "secondary_short": r.get("secondary_short"),
            "secondary_net": r.get("secondary_net"),
            "tertiary_long": r.get("tertiary_long"),
            "tertiary_short": r.get("tertiary_short"),
            "tertiary_net": r.get("tertiary_net"),
            "report_family": fam,
        })
    return rows


def cta_lens(key: str) -> dict:
    """cot-cta's COT cross-reference, sourced from the base (pipeline retired).

    The estimated dollar position still needs the price-based TSMOM ensemble
    (which stays in cot-cta); here we surface the base net + its percentile +
    the AUM scalar so the lens has its positioning anchor from the guarded base.
    """
    view = positioning_view(key)
    aum = CTA_AUM_SCALAR.get(key)
    view["cta_aum_bn"] = round(aum / 1e9, 1) if aum else None
    return view


def main() -> int:
    rows = []
    for m in markets.MARKETS:
        key = m["key"]
        v = positioning_view(key)
        if "error" in v:
            print(f"  ! {key:12} {v['error']} ({v.get('series_id')})")
            continue
        rows.append(v)
        if v.get("reused"):
            print(f"  ~ {key:12} (reuse {v['series_id']}) pctile={v['percentile']} "
                  f"asof {v['asof']} n={v['n_obs']}")
        else:
            print(f"  + {key:12} net={v['net']:>10.0f} pctile={v['percentile']:>6} "
                  f"z={v['zscore']} n={v['n_obs']:>4} asof {v['asof']}")
    print(f"\ncot consumer: {len(rows)} markets read from base "
          f"(DATACORE_ROOT={_root()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

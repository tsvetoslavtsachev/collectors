"""FEED 3 — computed macro series (derived deterministically from FRED levels).

Two series, both byte-faithful to the catalog basis:
  macro_ahe_yoy     = 12-month YoY % of CES0500000003 (the S9 salary watcher input)
                      — exact port of migrations/s9b_ahe_yoy/fetch_ahe_yoy.yoy()
  macro_pce_nowcast = 0.1252 + 0.5632 * CPI_MoM(core CPI)  (frozen 60m OLS snapshot;
                      catalog macro_pce_nowcast basis) — provisional (D8)

Coefficients are pinned constants (NOT eval of the config string) — the config
formula is documentation; the numbers live here, deterministically. Cardinal rule
holds: this deterministic path writes the numbers, not a model.
"""
from __future__ import annotations

# macro_pce_nowcast — frozen OLS snapshot (catalog basis; rolling reproduction is
# S6c's concern). a + b*CPI_MoM. Verified to flip 0 regime labels vs rolling box.
NOWCAST_A = 0.1252
NOWCAST_B = 0.5632


def _yoy12(levels: list, rdp: int, source: str) -> list:
    """12-month YoY %. Strictly causal: YoY[t] uses level[t] and level[t-12] only."""
    out = []
    for i in range(12, len(levels)):
        d, v = levels[i]
        v0 = levels[i - 12][1]
        if v0:
            out.append({"as_of": d, "value": round((v / v0 - 1.0) * 100.0, rdp),
                        "source": source, "resolution": "monthly"})
    return out


def _pce_nowcast(levels: list, rdp: int) -> list:
    """a + b*CPI_MoM, MoM from contiguous core-CPI levels. Causal: t uses t, t-1."""
    out = []
    for i in range(1, len(levels)):
        d, v = levels[i]
        v0 = levels[i - 1][1]
        if v0:
            cpi_mom = (v / v0 - 1.0) * 100.0
            out.append({"as_of": d,
                        "value": round(NOWCAST_A + NOWCAST_B * cpi_mom, rdp),
                        "source": "computed (0.1252+0.5632*CPI_MoM core CPI)",
                        "resolution": "monthly", "provisional": True})
    return out


def compute_all(fred_raw: dict, cfg: dict) -> dict:
    """{series_id: {ok, records}} for the computed series, from FRED level caches."""
    rdp = int(cfg["settings"].get("round_dp", 6))
    out: dict = {}

    # macro_ahe_yoy — YoY of its own FRED ticker (fetched in fetch_fred, computed here)
    ahe = fred_raw.get("macro_ahe_yoy", {})
    if ahe.get("ok") and ahe.get("levels"):
        tkr = cfg["fred"]["macro_ahe_yoy"]["ticker"]
        recs = _yoy12(ahe["levels"], rdp, f"FRED {tkr} 12m YoY % (computed)")
        out["macro_ahe_yoy"] = {"ok": bool(recs), "records": recs,
                                "error": None if recs else "too few levels for YoY"}
    else:
        out["macro_ahe_yoy"] = {"ok": False,
                                "error": "macro_ahe_yoy FRED levels unavailable"}

    # macro_pce_nowcast — from the fetched core-CPI levels (cross-series derive)
    cc = fred_raw.get("macro_core_cpi", {})
    if cc.get("ok") and cc.get("levels"):
        recs = _pce_nowcast(cc["levels"], rdp)
        out["macro_pce_nowcast"] = {"ok": bool(recs), "records": recs,
                                    "error": None if recs else "too few CPI levels"}
    else:
        out["macro_pce_nowcast"] = {"ok": False,
                                    "error": "macro_core_cpi levels unavailable"}
    return out

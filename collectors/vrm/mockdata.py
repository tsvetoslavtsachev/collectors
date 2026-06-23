"""Offline synthetic raw for end-to-end smoke (no network).

Returns the citizen shape {series_id: {ok, records}} for ALL 51 VRM series, with
the exact record shape each feed produces (incl. resolution, dual basis), so
run.py --mock exercises the full wiring: every VRM series_id passes the identity
guard and lands in canonical — without a network call and (with DATACORE_ROOT=temp)
without touching the real base. Values are deterministic placeholders, never used
for analysis. Resolution mirrors config so live/mock cadence agree.
"""
from __future__ import annotations

# fixed deterministic calendars (literal strings — no Date.now in collectors)
_FRIDAYS = ["2026-04-24", "2026-05-01", "2026-05-08", "2026-05-15",
            "2026-05-22", "2026-05-29", "2026-06-05", "2026-06-12"]
_DAILY = ["2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12"]
_MONTHS = ["2025-12-31", "2026-01-31", "2026-02-28",
           "2026-03-31", "2026-04-30", "2026-05-31"]
_CUT = "2026-06-12"


def _price_records(i: int, dual_basis: bool, forward_only: bool, resolution: str) -> list:
    dates = _DAILY if resolution == "daily" else _FRIDAYS
    recs = []
    for j, d in enumerate(dates):
        base = 100.0 + i + j * 0.5
        recs.append({"as_of": d, "value": round(base, 6),
                     "value_tr": round(base * 1.02 if dual_basis else base, 6),
                     "source": "yfinance", "resolution": resolution,
                     "bloomberg_era": bool(resolution == "weekly"
                                           and not forward_only and d <= _CUT)})
    return recs


def _level_records(i: int, source: str, resolution: str, provisional: bool = False) -> list:
    dates = _DAILY if resolution == "daily" else _MONTHS
    recs = []
    for j, d in enumerate(dates):
        rec = {"as_of": d, "value": round(50.0 + i + j * 0.3, 6),
               "source": source, "resolution": resolution}
        if provisional:
            rec["provisional"] = True
        recs.append(rec)
    return recs


def raw(cfg: dict) -> dict:
    out: dict = {}

    for i, (sid, m) in enumerate(cfg["yfinance"].items()):
        out[sid] = {"ok": True,
                    "records": _price_records(i, bool(m.get("dual_basis")),
                                              bool(m.get("forward_only")),
                                              m.get("resolution", "weekly"))}

    for i, (sid, m) in enumerate(cfg["fred"].items()):
        res = m.get("model_freq", "monthly")
        if m.get("computed"):
            out[sid] = {"ok": True,
                        "records": _level_records(i, "FRED CES0500000003 12m YoY % (computed)", res)}
        else:
            out[sid] = {"ok": True,
                        "records": _level_records(i, f"FRED {m['ticker']}", res)}

    for i, sid in enumerate(cfg["computed"]):
        out[sid] = {"ok": True,
                    "records": _level_records(i, "computed (0.1252+0.5632*CPI_MoM core CPI)",
                                              "monthly", provisional=True)}

    for i, sid in enumerate(cfg["manual"]["series"]):
        out[sid] = {"ok": True,
                    "records": _level_records(i, "manual_bloomberg", "monthly", provisional=True)}

    return out

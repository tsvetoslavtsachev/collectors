"""Synthetic gold raw data for --mock / offline CI (no network)."""
from __future__ import annotations
import datetime as dt

from .register_catalog import ACTIVE

SERIES = list(ACTIVE)
BASE = {"mkt_gold_usd": 2650.0,
        "etf_gld_tonnes": 880.0, "etf_gld_oz": 28_300_000.0,
        "etf_gldm_tonnes": 230.0, "etf_gldm_oz": 7_400_000.0}


def _bdays(n: int, end: dt.date | None = None) -> list[str]:
    end = end or dt.date.today()
    out = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return list(reversed(out))


def raw(n: int = 30) -> dict:
    dates = _bdays(n)
    out = {}
    for sid in SERIES:
        recs = [{"as_of": d, "value": round(BASE[sid] * (1 + 0.0003 * i), 2),
                "source": "mock"} for i, d in enumerate(dates)]
        out[sid] = {"ok": True, "records": recs}
    return out

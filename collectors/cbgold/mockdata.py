"""Synthetic cbgold raw data for --mock / offline CI (no network) -- 24
monthly points per series, all 88 series_id's from ENTRIES."""
from __future__ import annotations
import calendar
import datetime as dt

from .register_catalog import ENTRIES

SERIES = list(ENTRIES)
BASE = 500.0   # tonnes, arbitrary but > 0 for every series


def _month_ends(n: int, end: dt.date | None = None) -> list[str]:
    end = end or dt.date.today()
    y, m = end.year, end.month
    out = []
    for _ in range(n):
        last_day = calendar.monthrange(y, m)[1]
        out.append(dt.date(y, m, last_day).isoformat())
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


def raw(n: int = 24) -> dict:
    dates = _month_ends(n)
    out = {}
    for i, sid in enumerate(SERIES):
        base = BASE + 10.0 * (i % 7)
        recs = [{"as_of": d, "value": round(base * (1 + 0.001 * j), 6),
                "source": "mock"} for j, d in enumerate(dates)]
        out[sid] = {"ok": True, "records": recs}
    return out

"""Offline record-shape fixtures for run.py --mock (Gate 1 -- proves the
fetch->push->archive wiring with NO network).

Mirrors the fetch_prices output contract exactly: per series a list of daily bars in
the P1 archive record shape, spanning TWO calendar years so the year-partition path
is exercised, with the tip marked ``provisional`` (P1 freezes it on the next run).
Values are deterministic and arbitrary -- NOT real prices.
"""
from __future__ import annotations

# Two calendar years -> exercises year-partitioning offline.
_DATES = ["2024-12-30", "2024-12-31", "2025-01-02"]


def _bar(as_of: str, value: float, source: str) -> dict:
    return {"as_of": as_of, "value": value, "open": value, "high": value,
            "low": value, "close": value, "value_tr": value, "volume": 1000,
            "split_factor": 1.0, "dividend": 0.0, "source": source}


def records_for(index: int, source: str = "mock") -> list[dict]:
    """Deterministic 3-bar series; ``index`` separates one series from the next."""
    base = 100.0 + index
    recs = [_bar(d, round(base + j * 0.5, 6), source) for j, d in enumerate(_DATES)]
    recs[-1]["provisional"] = True       # tip only
    return recs


def raw(cfg: dict, *, only: list[str] | None = None) -> dict:
    """{series_id: {"ok": True, "records": [...]}} for every configured price series
    (or the ``only`` subset). Source tag is 'mock' so a mock run is never mistaken
    for real prices in the archive."""
    out: dict = {}
    for i, (sid, _m) in enumerate(cfg["price"].items()):
        if only is not None and sid not in only:
            continue
        out[sid] = {"ok": True, "records": records_for(i)}
    return out

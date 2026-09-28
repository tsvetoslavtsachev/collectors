"""Fetch the LBMA Silver Price (USD/GBP/EUR) -> per-currency records.

Source: https://prices.lbma.org.uk/json/silver.json -- same shape as
fetch_lbma.py's gold_pm.json ({"d": "YYYY-MM-DD", "v": [USD, GBP, EUR]}), one
row per LBMA business day (verified 28.09.2026, no login: 14,854 rows,
1968-01-02 -> 2026-09-25). Unlike gold there is only one LBMA silver fix per
day (no AM/PM split), so "silver" here needs no scope decision the way
fetch_lbma's PM-not-AM choice did.

Null pattern differs from gold's (checked before writing this, not assumed):
USD carries 19 historical nulls (LBMA market-closure days), GBP carries ZERO,
EUR is null before 1999-01-04 (pre-euro, same cutoff as gold). All three are
simply OMITTED per currency, same rule as fetch_lbma -- no null ever reaches
canonical.
"""
from __future__ import annotations
import requests

URL = "https://prices.lbma.org.uk/json/silver.json"
SOURCE = "LBMA Silver Price"
# canonical series_id -> index into each row's "v" array
CCY_INDEX = {"mkt_silver_usd": 0, "mkt_silver_gbp": 1, "mkt_silver_eur": 2}


def fetch_prices(url: str = URL, timeout: int = 30) -> list[dict]:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def to_records(rows: list[dict], ccy_index: dict = CCY_INDEX) -> dict:
    out = {sid: [] for sid in ccy_index}
    for row in rows:
        as_of = row.get("d")
        v = row.get("v") or []
        if not as_of or len(v) < 3:
            continue
        for sid, idx in ccy_index.items():
            val = v[idx]
            if val is None:
                continue
            out[sid].append({"as_of": as_of, "value": val, "source": SOURCE})
    return {sid: {"ok": True, "records": recs} for sid, recs in out.items()}


def fetch(url: str = URL, ccy_index: dict = CCY_INDEX) -> dict:
    return to_records(fetch_prices(url), ccy_index)

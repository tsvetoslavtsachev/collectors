"""Fetch the LBMA Gold Price PM fix (USD/GBP/EUR) -> per-currency records.

Source: https://prices.lbma.org.uk/json/gold_pm.json -- a JSON array of
{"d": "YYYY-MM-DD", "v": [USD, GBP, EUR]}, one row per LBMA business day, from
1968-04-01 (verified 28.09.2026, no login). EUR is null before 1999-01-04 (no
euro yet); GBP carries 11 historical nulls (LBMA market-closure days, e.g. the
week of the Nixon Shock, Aug 1971). Both are simply OMITTED from that
currency's records -- data-core's schema has no null value, and a currency
whose fix did not exist that day is not "the value is zero".

AM fix (gold_am.json, from 1968-01-02) is NOT fetched. PM is LBMA's own
headline fix; one canonical spot series per currency is enough (brief:
"AM по желание" -- by desire, not required). Recorded in the collector
README as a scope decision, mirroring the IAU exclusion.
"""
from __future__ import annotations
import requests

PM_URL = "https://prices.lbma.org.uk/json/gold_pm.json"
SOURCE = "LBMA PM"
# canonical series_id -> index into each row's "v" array
CCY_INDEX = {"mkt_gold_usd": 0, "mkt_gold_gbp": 1, "mkt_gold_eur": 2}


def fetch_pm(url: str = PM_URL, timeout: int = 30) -> list[dict]:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def to_records(rows: list[dict], ccy_index: dict = CCY_INDEX) -> dict:
    """Raw LBMA rows -> {series_id: {"ok": True, "records": [...]}} per currency."""
    out = {sid: [] for sid in ccy_index}
    for row in rows:
        as_of = row.get("d")
        v = row.get("v") or []
        if not as_of or len(v) < 3:
            continue
        for sid, idx in ccy_index.items():
            val = v[idx]
            if val is None:                     # pre-euro EUR / closure-day gap
                continue
            out[sid].append({"as_of": as_of, "value": val, "source": SOURCE})
    return {sid: {"ok": True, "records": recs} for sid, recs in out.items()}


def fetch(url: str = PM_URL, ccy_index: dict = CCY_INDEX) -> dict:
    return to_records(fetch_pm(url), ccy_index)

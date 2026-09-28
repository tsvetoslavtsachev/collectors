"""Fetch the SPDR GLDM historical archive (.xlsx) -> tonnage/ounces records.

Same shape as GLD (columns compared 28.09.2026 before touching this parser:
identical names except the premium/discount column, which mentions "GLDM"
instead of "GLD" and is unused either way; holiday rows carry the same literal
"US Holiday" marker -- 83 found in the GLDM archive). Reuses
collectors.gold.fetch_gld.parse_archive/fetch_bytes rather than duplicating
the trap-handling; only `to_records` is local, because it stamps a different
SOURCE string and a different pair of series_id's.

Source: https://api.spdrgoldshares.com/api/v1/historical-archive?product=gldm&
exchange=NYSE&lang=en (verified 28.09.2026, no login), sheet
"US GLDM Historical Archive", one row per NYSE trading day from 2018-06-26.
GLDM is SPDR's lower-fee gold ETF (0.10% vs GLD's 0.40%) -- part of ЗЛТ5а's
control group for GLD: a GLD outflow paired with a GLDM inflow would say
"holders moved fund for the fee", not "holders left gold" (brief: "зайчето
от шапката на фокусника"). Reading only, no verdict here -- that is ЗЛТ4's job.
"""
from __future__ import annotations
from . import fetch_gld

URL = ("https://api.spdrgoldshares.com/api/v1/historical-archive"
       "?product=gldm&exchange=NYSE&lang=en")
SHEET = "US GLDM Historical Archive"
SOURCE = "SPDR GLDM archive"
COLUMNS = {
    "etf_gldm_tonnes": "Tonnes of Gold",
    "etf_gldm_oz": "Total Ounces of Gold in the Trust",
}
# kept for a Г4-style cross-check only, never written to canonical (no
# declared consumer) -- same scope decision as fetch_gld.NAV_COLUMN/CLOSE_COLUMN
NAV_COLUMN = fetch_gld.NAV_COLUMN
CLOSE_COLUMN = fetch_gld.CLOSE_COLUMN


def fetch_bytes(url: str = URL, timeout: int = 60) -> bytes:
    return fetch_gld.fetch_bytes(url, timeout)


def parse_archive(xlsx_bytes: bytes, sheet: str = SHEET):
    return fetch_gld.parse_archive(xlsx_bytes, sheet)


def to_records(df, columns: dict = COLUMNS) -> dict:
    out = {}
    for sid, col in columns.items():
        sub = df[["as_of", col]].dropna()
        out[sid] = {"ok": True, "records": [
            {"as_of": d, "value": float(v), "source": SOURCE}
            for d, v in zip(sub["as_of"], sub[col])
        ]}
    return out


def fetch(url: str = URL) -> dict:
    return to_records(parse_archive(fetch_bytes(url)))

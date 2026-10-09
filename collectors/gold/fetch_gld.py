"""Fetch the SPDR GLD historical archive (.xlsx) -> tonnage/ounces records.

Source: https://api.spdrgoldshares.com/api/v1/historical-archive?product=gld&
exchange=NYSE&lang=en -- an .xlsx (the old CSV link now redirects elsewhere,
verified 28.09.2026, no login), sheet "US GLD Historical Archive", one row per
NYSE trading day from 2004-11-18. Holiday rows carry the literal string
"US Holiday" in every numeric column and are dropped.

IAU (iShares) is NOT collected: iShares serves no machine-readable row without
a browser (checked 28.09.2026) -- a scope decision, recorded in the collector
README, not a silent gap.
"""
from __future__ import annotations
import io
import requests
import pandas as pd

URL = ("https://api.spdrgoldshares.com/api/v1/historical-archive"
       "?product=gld&exchange=NYSE&lang=en")
SHEET = "US GLD Historical Archive"
SOURCE = "SPDR GLD archive"
DATE_COL = "Date"
HOLIDAY_MARK = "US Holiday"
# canonical series_id -> source column
COLUMNS = {
    "etf_gld_tonnes": "Tonnes of Gold",
    "etf_gld_oz": "Total Ounces of Gold in the Trust",
}
# kept for the Г3 cross-check only (tonnes/oz x LBMA price vs the trust's own NAV);
# never written to canonical -- no declared consumer for a raw NAV series
NAV_COLUMN = "Total Net Asset Value in the Trust"
CLOSE_COLUMN = "Closing Price"
# the trust values its gold at the LBMA Gold Price PM, so NAV/Share / oz-per-Share
# IS that fix (2630/2630 days exact to the cent 2016-01..2026-09, ЗЛТ6 10.10.2026);
# read by fallback_usd only, when LBMA itself refuses
NAV_SHARE_COLUMN = "NAV/Share at 10:30am NYT"
OZ_SHARE_COLUMN = "Ounces of Gold per Share"


def fetch_bytes(url: str = URL, timeout: int = 60) -> bytes:
    # 28.09.2026 SPDR 403'd a bare-UA request and a browser UA was sent; on 10.10.2026
    # (ЗЛТ6) the endpoint answers 200 to python-requests and to this honest bot UA, so
    # no browser is impersonated -- the fallback_usd path must not rest on one.
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "collectors-bot/1.0"})
    r.raise_for_status()
    return r.content


def parse_archive(xlsx_bytes: bytes, sheet: str = SHEET) -> pd.DataFrame:
    """xlsx bytes -> DataFrame with a clean `as_of` (ISO) column and numeric
    tonnes/oz/NAV/close columns; holiday rows dropped."""
    df = pd.read_excel(io.BytesIO(xlsx_bytes), sheet_name=sheet, engine="openpyxl")
    df = df[df[list(COLUMNS.values())[0]].astype(str) != HOLIDAY_MARK].copy()
    df["as_of"] = pd.to_datetime(df[DATE_COL], format="%d-%b-%Y").dt.strftime("%Y-%m-%d")
    for col in list(COLUMNS.values()) + [NAV_COLUMN, CLOSE_COLUMN,
                                         NAV_SHARE_COLUMN, OZ_SHARE_COLUMN]:
        if col in df:                       # GLDM shares the parser; never KeyError on it
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=list(COLUMNS.values()))
    return df.sort_values("as_of").reset_index(drop=True)


def to_records(df: pd.DataFrame, columns: dict = COLUMNS) -> dict:
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

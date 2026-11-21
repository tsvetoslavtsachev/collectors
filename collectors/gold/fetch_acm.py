"""10y Treasury term premium, Adrian-Crump-Moench (NY Fed), daily (ЗЛТ8, 21.11.2026).

    GET newyorkfed.org/medialibrary/media/research/data_indicators/ACMTermPremium.xls
    -> legacy .xls (xlrd), sheet "ACM Daily": DATE ("19-Nov-2026"), ..., ACMTP10

Full history from 14.06.1961 on every pull (about 10 MB), one business day behind.
A second model next to the Kim-Wright series mkt_term_premium_10y (FRED THREEFYTP10,
about 6 days behind), which stays as it is: two models, two series, never blended.
"""
from __future__ import annotations

import datetime as dt
import io

import requests

SERIES = "mkt_term_premium_acm_10y"
URL = ("https://www.newyorkfed.org/medialibrary/media/research/data_indicators/"
       "ACMTermPremium.xls")
SHEET = "ACM Daily"
COLUMN = "ACMTP10"
DATE_FMT = "%d-%b-%Y"
SOURCE = "NY Fed ACM term premium ACMTP10"
MIN_ROWS = 10_000                  # 16 323 rows on 21.11.2026; far fewer = broken file
SANE = 20.0                        # percent; |term premium| beyond this is not data


class FormatError(ValueError):
    """The ACM workbook no longer has the shape this reader was built on."""


def parse_frame(df) -> list:
    """DataFrame of the daily sheet -> [{as_of, value, source}] ascending."""
    if "DATE" not in df.columns or COLUMN not in df.columns:
        raise FormatError(f"sheet lacks DATE/{COLUMN}: {list(df.columns)[:8]}")
    out = []
    for d, v in zip(df["DATE"], df[COLUMN]):
        if v is None or v != v:                      # blank / NaN cell
            continue
        try:
            day = dt.datetime.strptime(str(d).strip(), DATE_FMT).date()
            v = float(v)
        except (TypeError, ValueError) as e:
            raise FormatError(f"DATE {d!r} / {COLUMN} {v!r} not in the known format") from e
        if abs(v) > SANE:
            raise FormatError(f"implausible {COLUMN} {v} on {day}")
        out.append({"as_of": day.isoformat(), "value": round(v, 6), "source": SOURCE})
    if len(out) < MIN_ROWS:
        raise FormatError(f"only {len(out)} rows (< {MIN_ROWS})")
    return sorted(out, key=lambda r: r["as_of"])


def parse(xls_bytes: bytes) -> list:
    import pandas as pd
    try:
        df = pd.read_excel(io.BytesIO(xls_bytes), sheet_name=SHEET, engine="xlrd",
                           usecols=lambda c: c in ("DATE", COLUMN))
    except ValueError as e:                          # missing sheet
        raise FormatError(f"cannot read sheet {SHEET!r}: {e}") from e
    return parse_frame(df)


def fetch(get=requests.get, timeout: int = 120) -> bytes:
    r = get(URL, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.content

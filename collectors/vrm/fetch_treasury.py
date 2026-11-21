"""Treasury head for the gold rate series (ЗЛТ8, 21.11.2026).

FRED's DGS10 / DFII10 are H.15 series: a day's point appears on FRED on the NEXT
business day around 15:15 CT, so the Saturday run never had Friday (and saw Thursday
only when the run was late enough). The US Treasury publishes the same curve points
the same evening as CSV (keyless):

    Daily Treasury Par Yield Curve   -> "10 Yr"   == FRED DGS10
    Daily Treasury Real Yield Curve  -> "10 YR"   == FRED DFII10

ЗЛТ7 compared both with the canonical store for 217 days of 2026: difference 0.

The Treasury files are per calendar year, so they cannot replace FRED's history (DGS10
from 1962). They only EXTEND it: points dated after FRED's last observation are
appended, and only when the overlap agrees (the last OVERLAP_DAYS common dates within
TOLERANCE). A disagreement or a failed fetch leaves the FRED series untouched -- the
head is best effort, never a reason to fail the FRED series.

Config (collectors/vrm/config.yaml, under the fred series):
    treasury_head: {curve: par|real, column: "10 Yr"}
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import urllib.request

URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
       "daily-treasury-rates.csv/{year}/all?type={type}&field_tdr_date_value={year}"
       "&page&_format=csv")
CURVE_TYPES = {"par": "daily_treasury_yield_curve",
               "real": "daily_treasury_real_yield_curve"}
SOURCE_LABEL = {"par": "US Treasury par yield curve {col}",
                "real": "US Treasury real yield curve {col}"}
OVERLAP_DAYS = 10      # common dates compared before a head is trusted
TOLERANCE = 0.005      # both sources publish 2 decimals; any real gap is >= 0.01


def parse_csv(text: str, column: str) -> list:
    """Treasury CSV text -> [(YYYY-MM-DD, float)] ascending; blanks skipped.
    Column match is case/space-insensitive ("10 Yr" vs "10 YR")."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]
    want = column.strip().lower()
    if want not in header:
        raise KeyError(f"column {column!r} not in Treasury header {rows[0]}")
    ci = header.index(want)
    out = []
    for r in rows[1:]:
        if len(r) <= ci or not r[ci].strip():
            continue
        d = dt.datetime.strptime(r[0].strip(), "%m/%d/%Y").date().isoformat()
        out.append((d, float(r[ci])))
    return sorted(out)


def _get(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8-sig")


def fetch(curve: str, column: str, today: dt.date | None = None, getter=_get) -> list:
    """This year's points (plus last year's in January, so the overlap exists)."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    years = [today.year - 1, today.year] if today.month == 1 else [today.year]
    out: list = []
    for y in years:
        out += parse_csv(getter(URL.format(year=y, type=CURVE_TYPES[curve])), column)
    return sorted(set(out))


def extend(fred_obs: list, treasury_obs: list, rdp: int = 6) -> tuple:
    """(extended obs, note). Appends Treasury points newer than FRED's last date when
    the last OVERLAP_DAYS common dates agree within TOLERANCE; otherwise returns the
    FRED obs unchanged and the note says why."""
    if not fred_obs or not treasury_obs:
        return fred_obs, "treasury head: nothing to compare"
    fred = dict(fred_obs)
    common = sorted(d for d, _ in treasury_obs if d in fred)[-OVERLAP_DAYS:]
    if len(common) < min(OVERLAP_DAYS, 3):
        return fred_obs, f"treasury head: only {len(common)} common dates, not trusted"
    tre = dict(treasury_obs)
    bad = [d for d in common if abs(tre[d] - fred[d]) > TOLERANCE]
    if bad:
        return fred_obs, (f"treasury head REFUSED: {len(bad)} of {len(common)} common "
                          f"dates differ, e.g. {bad[-1]} FRED {fred[bad[-1]]} vs "
                          f"Treasury {tre[bad[-1]]}")
    last = fred_obs[-1][0]
    head = [(d, round(v, rdp)) for d, v in treasury_obs if d > last]
    return fred_obs + head, (f"treasury head: +{len(head)} after {last} "
                             f"({len(common)} common dates agree)")

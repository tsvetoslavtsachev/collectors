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
    treasury_head: {curve: par, column: "10 Yr", minus: {curve: real, column: "10 YR"}}

ИЛЮ3 (22.11.2026): the head covers every daily yield the Friday needs, not only the
gold pair -- 2y, 30y, 3m, 5y, 5y real -- and the two FRED spreads that are plain
differences of curve points: T10Y2Y = DGS10 - DGS2 and T10YIE = DGS10 - DFII10
(`minus`). WHICH SOURCE WINS on a date both have: FRED, always. The Treasury point is
appended only AFTER FRED's last date; the next run's FRED observation for that date
replaces it (to_datacore full-replaces the series from this run's list), and the
overlap check refuses the whole head if the two sources disagree anywhere in the last
OVERLAP_DAYS common dates. The row's `source` names which one it came from.
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


_TEXT_CACHE: dict = {}   # url -> CSV text, one download per curve per run (default getter only)


def _cached_get(url: str) -> str:
    if url not in _TEXT_CACHE:
        _TEXT_CACHE[url] = _get(url)
    return _TEXT_CACHE[url]


def fetch(curve: str, column: str, today: dt.date | None = None, getter=None) -> list:
    """This year's points (plus last year's in January, so the overlap exists)."""
    getter = getter or _cached_get
    today = today or dt.datetime.now(dt.timezone.utc).date()
    years = [today.year - 1, today.year] if today.month == 1 else [today.year]
    out: list = []
    for y in years:
        text = getter(URL.format(year=y, type=CURVE_TYPES[curve]))
        if text.lstrip()[:1] == "<":   # an HTML page instead of CSV is a failure, not data
            raise ValueError(f"Treasury {curve} {y}: HTML instead of CSV")
        out += parse_csv(text, column)
    return sorted(set(out))


def fetch_head(th: dict, today: dt.date | None = None, getter=None) -> list:
    """The head for one config entry: a single curve column, or `column - minus.column`
    on the dates both have (2 decimals, like FRED's spreads)."""
    kw = {k: v for k, v in (("today", today), ("getter", getter)) if v is not None}
    obs = fetch(th["curve"], th["column"], **kw)
    m = th.get("minus")
    if not m:
        return obs
    sub = dict(fetch(m["curve"], m["column"], **kw))
    return [(d, round(v - sub[d], 2)) for d, v in obs if d in sub]


def label(th: dict) -> str:
    base = SOURCE_LABEL[th["curve"]].format(col=th["column"])
    m = th.get("minus")
    if not m:
        return base
    return f"{base} minus {SOURCE_LABEL[m['curve']].format(col=m['column'])}"


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

"""mkt_gold_usd fallback when LBMA refuses -- the PM fix read back out of SPDR GLD (ЗЛТ6).

Why: from 03.10.2026 prices.lbma.org.uk answers 403 (Cloudflare "Sorry, you have been
blocked", from GitHub Actions and from a home line alike), and LBMA's own page now
says a licence from IBA is required to obtain, use or redistribute the benchmark
data. Nothing here touches LBMA or its bot wall.

What: the SPDR GLD trust values its bullion at the LBMA Gold Price PM, and its public
archive (already pulled by fetch_gld) carries NAV/Share and Ounces of Gold per Share.
Their ratio, rounded to the cent, equals the canonical LBMA PM USD on 2630/2630 days
2016-01..2026-09 (ЗЛТ6 cross-check, 10.10.2026). Same price, same 15:00 London clock.

Where it differs from LBMA, and how that is handled:
  * UK bank holidays: no LBMA fix, but GLD (NYSE open) carries the previous price ->
    dropped via the gov.uk England-and-Wales calendar (keyless, OGL).
  * Half days (last business day before 25 Dec / 1 Jan): LBMA runs only the AM
    auction and GLD values at it -> dropped, the PM series has no row there.
  * US holidays: LBMA fixes, GLD is closed -> no row (a known gap, ~6 days/year).
With those two drops, 2019-01..2026-09 shows 0 extra rows vs canonical.

Only rows AFTER the newest canonical row are appended; the LBMA history already in
data-core is carried unchanged. USD only -- GBP/EUR would need an FX rate at another
clock, and silver has no keyless equivalent (both left to Ц., ZLT6 report).
"""
from __future__ import annotations
import datetime as dt

import requests

from . import fetch_gld

SERIES = "mkt_gold_usd"
SOURCE = "LBMA PM via SPDR GLD NAV"
HOLIDAYS_URL = "https://www.gov.uk/bank-holidays.json"
HOLIDAYS_DIVISION = "england-and-wales"


def fetch_uk_holidays(url: str = HOLIDAYS_URL, timeout: int = 30) -> set[str]:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return {e["date"] for e in r.json()[HOLIDAYS_DIVISION]["events"]}


def _last_business_day_before(target: dt.date, holidays: set[str]) -> dt.date:
    d = target - dt.timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in holidays:
        d -= dt.timedelta(days=1)
    return d


def is_half_day(day: dt.date, holidays: set[str]) -> bool:
    """LBMA AM-only day: the last business day before 25 Dec or before 1 Jan."""
    return day in (_last_business_day_before(dt.date(day.year, 12, 25), holidays),
                   _last_business_day_before(dt.date(day.year + 1, 1, 1), holidays))


def implied_rows(df, holidays: set[str]) -> list[dict]:
    """GLD archive DataFrame (fetch_gld.parse_archive) -> LBMA-PM-shaped USD rows."""
    sub = df[["as_of", fetch_gld.NAV_SHARE_COLUMN, fetch_gld.OZ_SHARE_COLUMN]].dropna()
    out = []
    for as_of, nav, oz in zip(sub["as_of"], sub[fetch_gld.NAV_SHARE_COLUMN],
                              sub[fetch_gld.OZ_SHARE_COLUMN]):
        if not oz or as_of in holidays or is_half_day(dt.date.fromisoformat(as_of), holidays):
            continue
        out.append({"as_of": as_of, "value": round(float(nav) / float(oz), 2),
                    "source": SOURCE})
    return out


def extend(existing: list[dict], df, holidays: set[str]) -> dict:
    """Canonical rows (carried as-is) + GLD-implied rows strictly after the newest one."""
    if not existing:
        return {"ok": False, "error": "fallback: no canonical LBMA history to extend"}
    carried = [{"as_of": r["as_of"], "value": r["value"], "source": r["source"]}
               for r in existing]
    last = max(r["as_of"] for r in carried)
    new = [r for r in implied_rows(df, holidays) if r["as_of"] > last]
    return {"ok": True, "records": carried + new,
            "note": f"fallback {SOURCE}: +{len(new)} row(s) after {last}"}

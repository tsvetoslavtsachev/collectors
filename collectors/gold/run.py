"""Gold collector -- LBMA spot (USD/EUR/GBP) + SPDR GLD tonnage, third citizen (ЗЛТ2).

Run: python -m collectors.gold.run [--mock]

Flow: fetch LBMA PM + SPDR GLD archive (each isolated) -> WRITE each series' full
history through the data-core gate (identity + schema + health + anti-truncation
floor) -> report. Mirrors collectors.cot.run / collectors.oil.run.
"""
from __future__ import annotations
import datetime as dt
import sys

from . import fetch_lbma, fetch_gld, to_datacore
from .register_catalog import ENTRIES

# LBMA/SPDR are business-day sources publishing same-day or next-day; a full
# business week missed (long weekend + a skipped run) is the outer bound before
# this is a real miss, not a normal Sat-run lag on a Fri source.
STALE_DAYS = 5


def assemble() -> dict:
    raw = {}
    raw.update(fetch_lbma.fetch())
    raw.update(fetch_gld.fetch())
    return raw


def _base_frontier(pushed: list) -> str | None:
    dates = [r["as_of"] for r in pushed
             if r.get("rows") is not None and r.get("as_of")]
    return max(dates) if dates else None


def freshness_verdict(pushed: list, today: dt.date | None = None,
                      stale_days: int = STALE_DAYS) -> tuple[int, str]:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    frontier = _base_frontier(pushed)
    if frontier is None:
        return 1, ("no series wrote a canonical row this run (total fetch failure) "
                   "-- cannot confirm gold base freshness")
    last = dt.date.fromisoformat(frontier[:10])
    age = (today - last).days
    if age > stale_days:
        return 1, (f"gold base STALE: newest canonical as_of {frontier} is {age}d "
                   f"old (> {stale_days}d)")
    return 0, (f"gold base fresh: newest canonical as_of {frontier} is {age}d old "
               f"(<= {stale_days}d)")


def main() -> int:
    exp = list(ENTRIES)
    if "--mock" in sys.argv:
        from . import mockdata
        raw = mockdata.raw()
    else:
        raw = assemble()

    pushed = to_datacore.push(raw)

    wrote = [r for r in pushed if r.get("rows") is not None]
    skipped = [r for r in pushed if r.get("rows") is None]
    print(f"gold citizen: {len(wrote)} written, {len(skipped)} skipped "
          f"(of {len(exp)} expected)")
    for r in wrote:
        warn = f"  [WARN: {'; '.join(r['warnings'])}]" if r.get("warnings") else ""
        print(f"  + {r['series_id']}: {r['rows']} rows, as_of {r['as_of']}{warn}")
    for r in skipped:
        print(f"  - {r['series_id']}: SKIP ({r.get('skipped')})")

    missing = sorted(set(exp) - {r["series_id"] for r in pushed})
    if missing:
        print(f"  ! MISSING from raw (not even attempted): {missing}")

    code, msg = freshness_verdict(pushed)
    print(("FAIL: " if code else "OK: ") + msg)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

"""Central-bank gold reserves (IMF IRFCL) collector -- fifth citizen (ЗЛТ5а).

Run: python -m collectors.cbgold.run [--mock]

Flow: fetch the IMF dataflow (one HTTP call, 88 country/aggregate series) ->
WRITE each series' full history through the data-core gate -> report. Mirrors
collectors.gold.run / collectors.cot.run.
"""
from __future__ import annotations
import datetime as dt
import sys

from . import fetch_imf, to_datacore
from .register_catalog import ENTRIES

# IMF IRFCL is monthly with a 2-3 month reporting lag (ЗЛТ3) -- unlike gold's
# daily sources, "stale" here has to mean "the fetch stopped working", not
# "this month isn't out yet". 120 days (~4 months) clears normal lag with
# room, so this only fires on a real outage.
STALE_DAYS = 120


def assemble() -> dict:
    return fetch_imf.fetch()


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
                   "-- cannot confirm cbgold base freshness")
    last = dt.date.fromisoformat(frontier[:10])
    age = (today - last).days
    if age > stale_days:
        return 1, (f"cbgold base STALE: newest canonical as_of {frontier} is {age}d "
                   f"old (> {stale_days}d)")
    return 0, (f"cbgold base fresh: newest canonical as_of {frontier} is {age}d old "
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
    print(f"cbgold citizen: {len(wrote)} written, {len(skipped)} skipped "
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

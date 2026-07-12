"""COT positioning collector — second citizen of data-core (INIT-22 S13).

Run:  python -m collectors.cot.run [--mock]

Flow: fetch per market (each isolated) -> WRITE each market's raw spec-net through
the data-core gate (identity + schema + health) -> report. The numbers live in
data-core; this repo holds only the fetch logic and the identity/percentile lib.
Percentiles are derived on the clean segment with explicit windows, never baked.
"""
from __future__ import annotations
import datetime as dt
import sys
from pathlib import Path
import yaml

from . import to_datacore, derive, markets

HERE = Path(__file__).resolve().parent

# Symmetric with cot-monitor/scripts/check_freshness.py -- see that docstring for
# the Tuesday-report / Friday-publish (+3d) / holiday-shift (<=+3d) calibration.
# Beyond 11 days a full weekly reporting cycle was missed.
STALE_DAYS = 11


def _base_frontier(pushed: list) -> str | None:
    """Newest as_of across the series actually written this run (the base frontier)."""
    dates = [r["as_of"] for r in pushed
             if r.get("rows") is not None and r.get("as_of")]
    return max(dates) if dates else None


def freshness_verdict(pushed: list, today: dt.date | None = None,
                      stale_days: int = STALE_DAYS) -> tuple[int, str]:
    """Fail-loud guard symmetric to the dashboard's check_freshness.py.

    Returns (exit_code, message). RED (1) when the COT base did not advance past a
    full weekly reporting cycle, so frozen positioning can never quietly ship on a
    green run. The prices/manifest churn every run, so silence is the default
    failure mode this makes loud.

    NOT fired on the normal "run before publication" case: those runs still write
    the full cohort with a frontier <= stale_days old, so age stays within budget.
    Only a genuinely missed cycle (or a total fetch failure that wrote nothing)
    trips it.
    """
    today = today or dt.datetime.now(dt.timezone.utc).date()
    frontier = _base_frontier(pushed)
    if frontier is None:
        return 1, ("no market wrote a canonical row this run (total fetch failure) "
                   "-- cannot confirm COT base freshness")
    try:
        last = dt.date.fromisoformat(frontier[:10])
    except ValueError:
        return 1, f"unparseable base frontier date {frontier!r}"
    age = (today - last).days
    if age > stale_days:
        return 1, (f"COT base STALE: newest canonical as_of {frontier} is {age}d old "
                   f"(> {stale_days}d) -- a weekly reporting cycle was missed")
    return 0, (f"COT base fresh: newest canonical as_of {frontier} is {age}d old "
               f"(<= {stale_days}d)")


def main() -> int:
    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))

    if "--mock" in sys.argv:
        from . import mockdata
        raw = mockdata.raw(cfg)
    else:
        from .fetch_cot import fetch_cot
        raw = fetch_cot(cfg)

    # --- citizen step: every market's net goes through the data-core gate ---
    pushed = to_datacore.push(raw)

    wrote = [r for r in pushed if r.get("rows") is not None]
    skipped = [r for r in pushed if r.get("rows") is None]
    print(f"cot citizen: {len(wrote)} written, {len(skipped)} skipped "
          f"(of {len(markets.migrated())} migrated markets)")
    for r in wrote:
        flags = r.get("quality_flags")
        mark = ""
        if flags:
            mark = "  [" + ", ".join(f["flag"] for f in flags) + "]"
        # percentile report (clean segment, explicit windows) for written series
        print(f"  + {r['series_id']}: {r['rows']} rows, as_of {r['as_of']}{mark}")
    for r in skipped:
        print(f"  - {r['series_id']}: SKIP ({r.get('skipped')})")

    # fail-loud freshness guard (before the CI commit step): a missed reporting
    # cycle must go RED, never silently green.
    code, msg = freshness_verdict(pushed)
    print(("FAIL: " if code else "OK: ") + msg)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

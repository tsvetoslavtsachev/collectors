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
from collectors import fresh_guard

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


def source_last(cfg: dict, get=None) -> str | None:
    """Newest report date CFTC has across the configured datasets (one tiny query
    each). None when every query failed."""
    import requests
    get = get or requests.get
    f = cfg.get("fetch", {})
    best = None
    for url in cfg["endpoints"].values():
        try:
            r = get(url, params={"$select": "max(report_date_as_yyyy_mm_dd) AS d"},
                    headers={"User-Agent": f.get("user_agent", "cot-citizen/1.0")},
                    timeout=f.get("timeout", 30))
            r.raise_for_status()
            d = str((r.json() or [{}])[0].get("d") or "")[:10]
        except Exception as e:  # noqa: BLE001 -- one dataset down is not a verdict
            print(f"  probe {url}: {type(e).__name__}: {e}")
            continue
        if d and (best is None or d > best):
            best = d
    return best


def if_newer(cfg: dict, get=None, read=None) -> tuple:
    """ЗЛТ8 daily path: run the full fetch only if CFTC has a newer report date than
    the newest canonical COT as_of."""
    from datacore import storage
    read = read or storage.read_canonical
    src = source_last(cfg, get)
    go, msg = fresh_guard.decide(
        src, fresh_guard.canonical_last([m["canonical"] for m in markets.migrated()], read),
        "cot")
    if src is None:
        fresh_guard.warn("cot source down", msg)
    return go, msg


def main() -> int:
    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))

    if "--mock" in sys.argv:
        from . import mockdata
        raw = mockdata.raw(cfg)
    else:
        if fresh_guard.wants_if_newer():  # ЗЛТ8 daily path: a 2-query probe first
            go, msg = if_newer(cfg)
            print(msg)
            if not go:
                return 0
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

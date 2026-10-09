"""Gold collector -- gold spot (USD) + SPDR GLD/GLDM tonnage, third citizen (ЗЛТ2, ЗЛТ6).

Run: python -m collectors.gold.run [--mock]

Flow: fetch the SPDR GLD archive (tonnage + the LBMA PM fix read out of its NAV,
gld_fix) and the GLDM archive, each isolated -> WRITE each series' full history
through the data-core gate (identity + schema + health + anti-truncation floor) ->
report. Mirrors collectors.cot.run / collectors.oil.run. LBMA itself is no longer
called (ЗЛТ6): its GBP/EUR gold and silver series are frozen, register_catalog.RETIRED.
"""
from __future__ import annotations
import datetime as dt
import os
import sys

from datacore import storage

from . import fetch_gld, fetch_gldm, gld_fix, to_datacore
from .register_catalog import ACTIVE, RETIRED

# SPDR is a business-day source publishing same-day or next-day; a full business
# week missed (long weekend + a skipped run) is the outer bound before this is a
# real miss, not a normal Sat-run lag on a Fri source.
STALE_DAYS = 5


def _guarded(name: str, fn, series_ids) -> dict:
    """One source down must not take the run down (ЗЛТ6: LBMA's 403 on 03.10.2026
    killed GLD/GLDM with it). Its series come back ok=False, reported as SKIP."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 -- isolate per source
        err = f"{name}: {type(e).__name__}: {e}"
        return {sid: {"ok": False, "error": err} for sid in series_ids}


def _gld(df) -> dict:
    out = fetch_gld.to_records(df)
    out.update(_guarded("GLD fix", lambda: {gld_fix.SERIES: gld_fix.extend(
        storage.read_canonical(gld_fix.SERIES), df, gld_fix.fetch_uk_holidays())},
        [gld_fix.SERIES]))         # gov.uk down must not take the tonnage down
    return out


def assemble() -> dict:
    raw = {}
    gld_ids = [*fetch_gld.COLUMNS, gld_fix.SERIES]
    raw.update(_guarded("SPDR GLD", lambda: _gld(fetch_gld.parse_archive(
        fetch_gld.fetch_bytes())), gld_ids))
    raw.update(_guarded("SPDR GLDM", fetch_gldm.fetch, fetch_gldm.COLUMNS))
    return raw


def _annotate(msg: str) -> None:
    """Surface a degraded series on the Actions run page, not only in the log."""
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::warning title=gold source down::{msg}")


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
    exp = list(ACTIVE)
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
        note = raw.get(r["series_id"], {}).get("note")
        print(f"  + {r['series_id']}: {r['rows']} rows, as_of {r['as_of']}{warn}"
              + (f"  [{note}]" if note else ""))
    for r in skipped:
        print(f"  - {r['series_id']}: SKIP ({r.get('skipped')})")
        _annotate(f"{r['series_id']}: SKIP ({r.get('skipped')})")

    print(f"  = frozen at 2026-09-25, not collected (ЗЛТ6): {', '.join(RETIRED)}")

    missing = sorted(set(exp) - {r["series_id"] for r in pushed})
    if missing:
        print(f"  ! MISSING from raw (not even attempted): {missing}")

    code, msg = freshness_verdict(pushed)
    print(("FAIL: " if code else "OK: ") + msg)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

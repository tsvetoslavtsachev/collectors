"""Gold collector -- LBMA spot (USD/EUR/GBP) + SPDR GLD tonnage, third citizen (ЗЛТ2).

Run: python -m collectors.gold.run [--mock]

Flow: fetch LBMA PM + SPDR GLD archive (each isolated) -> WRITE each series' full
history through the data-core gate (identity + schema + health + anti-truncation
floor) -> report. Mirrors collectors.cot.run / collectors.oil.run.
"""
from __future__ import annotations
import datetime as dt
import os
import sys

from datacore import storage

from . import fallback_usd, fetch_lbma, fetch_gld, fetch_gldm, fetch_silver, to_datacore
from .register_catalog import ENTRIES

# LBMA/SPDR are business-day sources publishing same-day or next-day; a full
# business week missed (long weekend + a skipped run) is the outer bound before
# this is a real miss, not a normal Sat-run lag on a Fri source.
STALE_DAYS = 5


def _guarded(name: str, fn, series_ids) -> dict:
    """One source down must not take the run down (ЗЛТ6: LBMA's 403 on 03.10.2026
    killed GLD/GLDM with it). Its series come back ok=False, reported as SKIP."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 -- isolate per source
        err = f"{name}: {type(e).__name__}: {e}"
        return {sid: {"ok": False, "error": err} for sid in series_ids}


def assemble() -> dict:
    raw = {}
    raw.update(_guarded("LBMA PM", fetch_lbma.fetch, fetch_lbma.CCY_INDEX))
    gld_df = None
    try:
        gld_df = fetch_gld.parse_archive(fetch_gld.fetch_bytes())
        raw.update(fetch_gld.to_records(gld_df))
    except Exception as e:  # noqa: BLE001
        raw.update({sid: {"ok": False, "error": f"SPDR GLD: {type(e).__name__}: {e}"}
                    for sid in fetch_gld.COLUMNS})
    raw.update(_guarded("SPDR GLDM", fetch_gldm.fetch, fetch_gldm.COLUMNS))
    raw.update(_guarded("LBMA Silver", fetch_silver.fetch, fetch_silver.CCY_INDEX))

    sid = fallback_usd.SERIES
    if not raw[sid].get("ok") and gld_df is not None:
        primary = raw[sid]["error"]
        try:
            fb = fallback_usd.extend(storage.read_canonical(sid), gld_df,
                                     fallback_usd.fetch_uk_holidays())
        except Exception as e:  # noqa: BLE001
            fb = {"ok": False, "error": f"fallback: {type(e).__name__}: {e}"}
        key = "note" if fb.get("ok") else "error"
        fb[key] = f"{fb[key]} (primary down -- {primary})"
        raw[sid] = fb
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
        note = raw.get(r["series_id"], {}).get("note")
        print(f"  + {r['series_id']}: {r['rows']} rows, as_of {r['as_of']}{warn}"
              + (f"  [{note}]" if note else ""))
        if note:
            _annotate(f"{r['series_id']}: {note}")
    for r in skipped:
        print(f"  - {r['series_id']}: SKIP ({r.get('skipped')})")
        _annotate(f"{r['series_id']}: SKIP ({r.get('skipped')})")

    missing = sorted(set(exp) - {r["series_id"] for r in pushed})
    if missing:
        print(f"  ! MISSING from raw (not even attempted): {missing}")

    code, msg = freshness_verdict(pushed)
    print(("FAIL: " if code else "OK: ") + msg)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

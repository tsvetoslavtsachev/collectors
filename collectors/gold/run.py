"""Gold collector -- gold spot (USD) + SPDR GLD/GLDM tonnage, third citizen (ЗЛТ2, ЗЛТ6).

Run: python -m collectors.gold.run [--mock]

Flow (ЗЛТ8 adds Swissquote spot, Yahoo GC=F and NY Fed ACM, each isolated):
fetch the SPDR GLD archive (tonnage + the LBMA PM fix read out of its NAV,
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
from collectors import fresh_guard

from . import fetch_acm, fetch_fut, fetch_gld, fetch_gldm, fetch_spot, gld_fix, to_datacore
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
    # ЗЛТ8 (21.11.2026): spot, futures and the ACM term premium, each isolated
    raw.update(_guarded("Swissquote spot", lambda: {fetch_spot.SERIES: fetch_spot.extend(
        storage.read_canonical(fetch_spot.SERIES), fetch_spot.parse(fetch_spot.fetch()))},
        [fetch_spot.SERIES]))
    raw.update(_guarded("Yahoo GC=F", lambda: {fetch_fut.SERIES: fetch_fut.upsert(
        storage.read_canonical(fetch_fut.SERIES), fetch_fut.parse(fetch_fut.fetch()))},
        [fetch_fut.SERIES]))
    raw.update(_guarded("NY Fed ACM", lambda: {fetch_acm.SERIES: {
        "ok": True, "records": fetch_acm.parse(fetch_acm.fetch())}}, [fetch_acm.SERIES]))
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


# ЗЛТ8 daily path: each source is judged on its own -- SPDR being quiet must not stop
# a new spot quote, and a new ACM day must not drag the SPDR series into a rewrite.
GROUPS = {
    "SPDR": (*fetch_gld.COLUMNS, gld_fix.SERIES, *fetch_gldm.COLUMNS),
    "spot": (fetch_spot.SERIES,),
    "futures": (fetch_fut.SERIES,),
    "acm": (fetch_acm.SERIES,),
}


def _key(r: dict) -> str:
    """as_of, plus quote_ts where the row has one: a later quote for the same
    trading day is newer (that is how Friday's close replaces Friday's early quote)."""
    d = str(r.get("as_of") or "")[:10]
    q = r.get("quote_ts")
    return f"{d}|{q}" if d and q else d


def _newest(rows) -> str | None:
    keys = [k for k in (_key(r) for r in rows or []) if k]
    return max(keys) if keys else None


def newer_subset(raw: dict, read=None) -> tuple:
    """(the part of raw whose source is ahead of canonical, one message per source)."""
    read = read or storage.read_canonical
    keep, msgs = {}, []
    for name, sids in GROUPS.items():
        present = [s for s in sids if s in raw]
        if not present:
            continue
        src = _newest([r for s in present if raw[s].get("ok")
                       for r in raw[s].get("records") or []])
        canon = None
        for s in present:
            try:
                k = _newest(read(s))
            except Exception:  # noqa: BLE001 -- a missing series is just "no date"
                k = None
            if k and (canon is None or k > canon):
                canon = k
        go, msg = fresh_guard.decide(src, canon, f"gold/{name}")
        if src is None:
            fresh_guard.warn("gold source down", msg)
        msgs.append(msg)
        if go:
            keep.update({s: raw[s] for s in present})
    return keep, msgs


def if_newer(raw: dict, read=None) -> tuple:
    keep, msgs = newer_subset(raw, read)
    return bool(keep), "; ".join(msgs)


# ЗЛТ8 червената лампа (21.11.2026): the runs above only WARN when a source is down,
# so a broken unofficial feed (Yahoo, Swissquote) could sit frozen for weeks behind a
# green run. After the commit, the workflow runs `--lamp`: every active series is aged
# against its own limit (calendar days from today, UTC, to its newest canonical as_of)
# and the run goes red when one is past it. The limits sit above each source's normal
# gap plus a holiday, so a one-night hiccup stays a warning, never a red light.
LAMP_DAYS = {
    "SPDR": 6,     # business-day source, next-day rows; Christmas/Easter clusters reach 5
    "spot": 3,     # a quote every run, stamped today or later: 3 = two missed nights
    "futures": 5,  # completed COMEX sessions; Thu -> Mon over a CME holiday is 4
    "acm": 10,     # one business day behind; cadence over holidays unmeasured, wide on purpose
}


def lamp(today: dt.date | None = None, read=None) -> tuple[int, list]:
    """(exit code, one line per active series). Missing series = stale."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    read = read or storage.read_canonical
    code, lines = 0, []
    for name, sids in GROUPS.items():
        limit = LAMP_DAYS[name]
        for sid in sids:
            last = fresh_guard.canonical_last([sid], read)
            age = (today - dt.date.fromisoformat(last)).days if last else None
            stale = age is None or age > limit
            lines.append(f"{'STALE' if stale else 'ok   '} {sid}: newest {last}, "
                         f"{'-' if age is None else age}d (limit {limit}d, {name})")
            if stale:
                code = 1
                if os.environ.get("GITHUB_ACTIONS"):
                    print(f"::error title=gold series stale::{sid} newest {last}, "
                          f"limit {limit}d ({name})")
    return code, lines


def main() -> int:
    if "--lamp" in sys.argv:              # ЗЛТ8: after the commit, no fetch
        code, lines = lamp()
        print("\n".join(lines))
        print(("FAIL: a gold series is past its limit" if code
               else "OK: every gold series within its limit"))
        return code

    exp = list(ACTIVE)
    if "--mock" in sys.argv:
        from . import mockdata
        raw = mockdata.raw()
    else:
        raw = assemble()

    if fresh_guard.wants_if_newer():      # ЗЛТ8 daily path
        raw, msgs = newer_subset(raw)
        for m in msgs:
            print(m)
        if not raw:
            return 0
        exp = sorted(raw)
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

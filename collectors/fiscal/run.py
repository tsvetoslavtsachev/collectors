"""Fiscal citizen (ПРОЛЕТБ2, 27.11.2026).

Run: python -m collectors.fiscal.run           fetch -> write canonical + auctions -> report
     python -m collectors.fiscal.run --lamp    after the commit: every series within its limit

Each source is isolated: one down does not take the others with it. The canonical write
goes through the data-core gate via collectors.gold.to_datacore.push (identity, schema,
health, anti-truncation floor), the same path as the gold and oil citizens.
"""
from __future__ import annotations
import datetime as dt
import os
import sys

from datacore import storage

from collectors.gold.to_datacore import push
from . import auctions, sources
from .register_catalog import ACTIVE

# calendar days from today to the newest date in the series; above each source's normal
# gap plus a holiday, so one quiet night is not red
LAMP_DAYS = {
    sources.AVG_RATE: 45,      # month end, published ~4th business day of the next month
    sources.SOMA_UST: 10,      # Wednesday as-of, published Thursday
    sources.DEBT_PUBLIC: 7,    # business days, one day behind
    "ust_coupon_auctions": 21,  # coupon auctions every week or two; quiet weeks around holidays
}


def _guarded(name, fn, ids):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 -- isolate per source
        return {sid: {"ok": False, "error": f"{name}: {type(e).__name__}: {e}"} for sid in ids}


def assemble() -> dict:
    raw = {}
    raw.update(_guarded("avg_rate", lambda: {sources.AVG_RATE: {
        "ok": True, "records": sources.parse_avg_rate(sources.fetch_avg_rate())}}, [sources.AVG_RATE]))
    raw.update(_guarded("soma", lambda: {sources.SOMA_UST: {
        "ok": True, "records": sources.parse_soma(sources.fetch_soma())}}, [sources.SOMA_UST]))
    raw.update(_guarded("debt", lambda: {sources.DEBT_PUBLIC: {
        "ok": True, "records": sources.parse_debt(sources.fetch_debt())}}, [sources.DEBT_PUBLIC]))
    return raw


def lamp(today: dt.date | None = None, read=None, read_auctions=None) -> tuple[int, list]:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    read = read or storage.read_canonical
    read_auctions = read_auctions or auctions.read
    code, lines = 0, []
    for sid, limit in LAMP_DAYS.items():
        if sid == "ust_coupon_auctions":
            rows = read_auctions()
            last = rows[-1]["auction_date"] if rows else None
        else:
            rows = read(sid) or []
            last = max((r["as_of"] for r in rows), default=None)
        age = (today - dt.date.fromisoformat(last)).days if last else None
        stale = age is None or age > limit
        code |= int(stale)
        lines.append(f"{'STALE' if stale else 'ok   '} {sid}: newest {last}, "
                     f"{'-' if age is None else age}d (limit {limit}d)")
        if stale and os.environ.get("GITHUB_ACTIONS"):
            print(f"::error title=fiscal series stale::{sid} newest {last}, limit {limit}d")
    return code, lines


def main() -> int:
    if "--lamp" in sys.argv:
        code, lines = lamp()
        print("\n".join(lines))
        print("FAIL: a fiscal series is past its limit" if code else "OK: every fiscal series within its limit")
        return code

    raw = assemble()
    pushed = push(raw)
    bad = 0
    print(f"fiscal citizen: {sum(r.get('rows') is not None for r in pushed)} canonical written "
          f"(of {len(ACTIVE)} expected)")
    for r in pushed:
        if r.get("rows") is not None:
            w = f"  [WARN: {'; '.join(r['warnings'])}]" if r.get("warnings") else ""
            print(f"  + {r['series_id']}: {r['rows']} rows, as_of {r['as_of']}{w}")
        else:
            bad += 1
            print(f"  - {r['series_id']}: SKIP ({r.get('skipped')})")
    try:
        res = auctions.write(sources.parse_auctions(sources.fetch_auctions()))
    except Exception as e:  # noqa: BLE001
        res = {"skipped": f"{type(e).__name__}: {e}"}
    if "rows" in res:
        w = f"  [WARN: {'; '.join(res['warnings'])}]" if res.get("warnings") else ""
        print(f"  + ust_coupon_auctions: {res['rows']} rows, last {res['last']}{w}")
    else:
        bad += 1
        print(f"  - ust_coupon_auctions: SKIP ({res['skipped']})")
    print("FAIL: a fiscal source was skipped" if bad else "OK: all fiscal sources written")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

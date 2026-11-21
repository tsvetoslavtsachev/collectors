"""COMEX gold futures GC=F daily close from Yahoo's chart API (ЗЛТ8, 21.11.2026).

A FUTURES series (continuous front month, CMX), kept apart from the spot on purpose:
it carries contango and roll jumps, so it is never mixed into mkt_gold_usd or
mkt_gold_spot_usd.

    GET query1.finance.yahoo.com/v8/finance/chart/GC=F?range=10y&interval=1d
    -> chart.result[0]: meta (instrumentType FUTURE, exchangeName CMX),
       timestamp[] (00:00 America/New_York of the trading day), indicators.quote[0].close[]

Only completed sessions are written: a bar for day D counts once D 17:00 ET has
passed (the weekday run at ~01-05 UTC otherwise sees the half-built bar of the new
day). The pull is a rolling 10-year window, so it is UPSERTED onto the canonical
history: rows older than the window are carried, never dropped.
"""
from __future__ import annotations

import datetime as dt
import zoneinfo

import requests

SERIES = "mkt_gold_fut_usd"
URL = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
PARAMS = {"range": "10y", "interval": "1d"}
SOURCE = "Yahoo GC=F (COMEX gold futures, front month) close"
NY = zoneinfo.ZoneInfo("America/New_York")
SESSION_END = dt.time(17, 0)
SANE = (100.0, 100_000.0)


class FormatError(ValueError):
    """The Yahoo chart JSON no longer has the shape this reader was built on."""


def parse(payload, now: dt.datetime | None = None) -> list:
    """Payload -> [{as_of, value, source}] of completed sessions, ascending."""
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        res = payload["chart"]["result"][0]
        meta, ts = res["meta"], res["timestamp"]
        close = res["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError) as e:
        raise FormatError(f"chart JSON shape changed: {e!r}") from e
    if meta.get("instrumentType") != "FUTURE" or meta.get("exchangeName") != "CMX":
        raise FormatError(f"not the COMEX future: {meta.get('instrumentType')} "
                          f"{meta.get('exchangeName')}")
    if len(ts) != len(close):
        raise FormatError(f"timestamp/close length mismatch {len(ts)} != {len(close)}")
    out = {}
    for t, v in zip(ts, close):
        if v is None:
            continue
        day = dt.datetime.fromtimestamp(int(t), NY).date()
        if dt.datetime.combine(day, SESSION_END, NY) > now:
            continue                                   # session not finished yet
        v = float(v)
        if not (SANE[0] < v < SANE[1]):
            raise FormatError(f"implausible close {v} on {day}")
        out[day.isoformat()] = {"as_of": day.isoformat(), "value": round(v, 2),
                                "source": SOURCE}
    if not out:
        raise FormatError("no completed sessions in the payload")
    return [out[d] for d in sorted(out)]


def upsert(existing: list, fresh: list) -> dict:
    rows = {r["as_of"]: {k: v for k, v in r.items()
                         if k not in ("series_id", "schema_version")}
            for r in existing or []}
    last = max(rows) if rows else None
    rows.update({r["as_of"]: r for r in fresh})
    new = sum(1 for r in fresh if last is None or r["as_of"] > last)
    return {"ok": True, "records": [rows[d] for d in sorted(rows)],
            "note": f"GC=F: +{new} session(s) after {last}"}


def fetch(get=requests.get, timeout: int = 30):
    r = get(URL, params=PARAMS, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.json()

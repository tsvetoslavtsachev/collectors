"""FEED 5 — market-context bridge from the ETF-rr barometer (mkt_vix, mkt_move).

DECISION (Цветослав, 2026-06-22): VIX/MOVE come from the ETF-rr barometer feed,
NOT a direct yfinance pull — one source shared with the barometer (D2 intent;
catalog source = etf-rr-barometer, migration_status = switch).

The barometer publishes its 10 indicators (VIX + MOVE among them) as a JSON feed
at <Pages>/barometer_feed.json (behavioral-tracker reads the same docs/ file). The
feed is a CURRENT SNAPSHOT — one reading per indicator, dated `as_of`, with `value`
holding the raw index level (even where the barometer's own `kind` is robust_z).
So the bridge FORWARD-ACCUMULATES one record per run into the canonical history
(the S15 shares_history pattern): read the existing series, add today's reading,
return the merged list for to_datacore's full_replace to write. Re-running the same
as_of is idempotent (overwrites by date, never duplicates). A missing feed /
indicator -> that series not-ok, never a silent zero, and the rest of the run
proceeds (mkt_vix/mkt_move are not on the regime/overlay critical path).
"""
from __future__ import annotations
import json
import urllib.request

from datacore import storage

# barometer indicator key -> VRM canonical series_id
BRIDGE_MAP = {"VIX": "mkt_vix", "MOVE": "mkt_move"}
DEFAULT_FEED_URL = "https://tsvetoslavtsachev.github.io/ETF-rotationradar/barometer_feed.json"


def _fetch_feed(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _accumulate(series_id: str, rec: dict) -> list:
    """Existing canonical history + the new snapshot reading, sorted, de-duplicated
    by as_of (a same-day re-run overwrites that date, never appends a duplicate)."""
    by = {r["as_of"]: r for r in storage.read_canonical(series_id)}
    by[rec["as_of"]] = rec
    return [by[d] for d in sorted(by)]


def fetch_bridge(cfg: dict) -> dict:
    """{series_id: {ok, records}} for the bridge series from the barometer feed."""
    bridges = {sid: m for sid, m in cfg["yfinance"].items() if m.get("bridge")}
    out: dict = {}
    if not bridges:
        return out
    url = cfg["settings"].get("barometer_feed_url", DEFAULT_FEED_URL)
    try:
        feed = _fetch_feed(url)
    except Exception as e:  # noqa: BLE001 — feed down -> all bridge series pending
        for sid in bridges:
            out[sid] = {"ok": False, "error": f"barometer feed unreachable: {type(e).__name__}"}
        return out

    as_of = feed.get("as_of")
    snap = {row.get("indicator"): row for row in feed.get("snapshot", [])}
    for ind, sid in BRIDGE_MAP.items():
        if sid not in bridges:
            continue
        row = snap.get(ind)
        try:
            val = float(row.get("value")) if row else None
        except (TypeError, ValueError):
            val = None
        # VIX/MOVE are index levels -> always > 0; reject missing/non-positive so a
        # feed glitch (None, 'n/a', 0.0) shows not-ok in Health, never a silent zero.
        # The parse is guarded per-indicator so one bad value can't abort the run.
        if not as_of or val is None or val <= 0:
            out[sid] = {"ok": False,
                        "error": f"{ind}: missing/non-positive barometer value ({row.get('value') if row else None!r})"}
            continue
        rec = {"as_of": as_of, "value": round(val, 6),
               "source": "etf-rr-barometer", "resolution": "daily"}
        out[sid] = {"ok": True, "records": _accumulate(sid, rec)}
    return out

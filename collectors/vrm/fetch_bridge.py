"""FEED 5 -- mkt_vix and mkt_move, DAILY from the price archive (ИЛЮ3, 22.11.2026).

DECISION (Цветослав, 22.11.2026, ИЛЮ6 Р2 „Дневни + етикет“): VIX and MOVE are daily
series in the price archive (px_vix_daily = ^VIX, px_move_daily = ^MOVE, collectors/price),
and the canonical mkt_vix / mkt_move are built from them. Before this the bridge took ONE
snapshot per weekly run from the ETF-rr barometer feed (decision of 22.06), so after the
Bloomberg backfill (to 2026-06-19) the canon held 26 weekly points labelled "daily".
The barometer pulls the same ^VIX/^MOVE from yfinance, so the levels are the same source.

THE MERGE. Rows whose source is the Bloomberg backfill ("backfill:...") are kept as they
are (the archive matches them on 99.8% of 6482 common days, measured 22.11.2026); every
date after the last backfill row comes from the archive, which replaces the weekly
barometer snapshots with the full daily path. The newest bar of each date wins
(bitemporal), the archive's provisional tip included (an index close; the next price
run finalises it).

LOUD, NOT SILENT. An archive that is missing, empty, holds a non-positive or non-numeric
level, or has not moved for STALE_DAYS calendar days is a not-ok series with the reason
(Health shows SKIP and the canon keeps its last good rows); it is never a silent green.
"""
from __future__ import annotations
import datetime as dt
import json
import os
from pathlib import Path

from datacore import storage

ARCHIVE_MAP = {"mkt_vix": "px_vix_daily", "mkt_move": "px_move_daily"}
SYMBOL = {"mkt_vix": "^VIX", "mkt_move": "^MOVE"}
STALE_DAYS = 6          # a Friday bar read on the next Thursday is still fine; older is not
_DEFAULT_ROOTS = (Path(__file__).resolve().parents[3] / "price-archive",
                  Path(r"C:\Projects\price-archive"))


def _archive_root() -> Path | None:
    env = os.environ.get("PRICE_ARCHIVE_ROOT")
    if env:
        return Path(env)
    return next((p for p in _DEFAULT_ROOTS if (p / "archive").is_dir()), None)


def read_archive(root: Path, px_id: str) -> list:
    """[(as_of, value)] ascending, newest vintage per date, provisional tip included.
    Raises ValueError on a missing/empty series or a non-positive/non-numeric level."""
    files = sorted((root / "archive" / px_id).glob("*.jsonl"))
    if not files:
        raise ValueError(f"{px_id}: no archive files under {root}")
    best: dict = {}
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            key = (r.get("recorded_on") or "", )
            if r["as_of"] not in best or key >= best[r["as_of"]][0]:
                best[r["as_of"]] = (key, r.get("value"))
    if not best:
        raise ValueError(f"{px_id}: archive files are empty")
    out = []
    for d in sorted(best):
        v = best[d][1]
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{px_id} {d}: non-numeric level {v!r}") from None
        if v <= 0:
            raise ValueError(f"{px_id} {d}: non-positive level {v}")
        out.append((d, v))
    return out


def merge(existing: list, archive: list, sid: str) -> list:
    """Bloomberg backfill rows kept; every date after the last of them from the archive."""
    kept = [r for r in existing if str(r.get("source", "")).startswith("backfill:")]
    cut = max((r["as_of"] for r in kept), default="")
    src = f"price-archive {ARCHIVE_MAP[sid]} (yfinance {SYMBOL[sid]})"
    tail = [{"as_of": d, "value": round(v, 6), "source": src, "resolution": "daily"}
            for d, v in archive if d > cut]
    return sorted(kept, key=lambda r: r["as_of"]) + tail


def fetch_bridge(cfg: dict, today: dt.date | None = None) -> dict:
    """{series_id: {ok, records}} for mkt_vix / mkt_move from the price archive."""
    bridges = {sid: m for sid, m in cfg["yfinance"].items() if m.get("bridge")}
    out: dict = {}
    if not bridges:
        return out
    root = _archive_root()
    today = today or dt.datetime.now(dt.timezone.utc).date()
    for sid in bridges:
        px_id = ARCHIVE_MAP.get(sid)
        if not px_id:
            out[sid] = {"ok": False, "error": f"{sid}: no archive series mapped"}
            continue
        if root is None:
            out[sid] = {"ok": False, "error": "price archive not found (set PRICE_ARCHIVE_ROOT)"}
            continue
        try:
            arch = read_archive(root, px_id)
        except ValueError as e:
            out[sid] = {"ok": False, "error": str(e)}
            continue
        tip = dt.date.fromisoformat(arch[-1][0])
        if (today - tip).days > STALE_DAYS:
            out[sid] = {"ok": False, "error": f"{px_id}: archive tip {tip} is {(today - tip).days} "
                                              f"days old (> {STALE_DAYS}); canon keeps its last rows"}
            continue
        out[sid] = {"ok": True, "records": merge(storage.read_canonical(sid), arch, sid)}
    return out

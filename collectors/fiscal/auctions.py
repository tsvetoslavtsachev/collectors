"""Write the coupon-auction table into data-core: data/auctions/ust_coupon_auctions.json.

Not a canonical series (one row per auction). Same guards as the canonical writer in
spirit: a pull that would shrink the table below 90% of what is there is refused, a
tail that goes backwards is a warning, and the file carries its source and newest date.
"""
from __future__ import annotations
import datetime as dt
import json
import os
from pathlib import Path

REL = Path("data") / "auctions" / "ust_coupon_auctions.json"
MIN_RETAIN_RATIO = 0.9


def path(root: str | os.PathLike | None = None) -> Path:
    return Path(root or os.environ.get("DATACORE_ROOT", ".")) / REL


def read(root=None) -> list:
    p = path(root)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))["rows"]


def write(rows: list, root=None, now: dt.datetime | None = None) -> dict:
    existing = read(root)
    if existing and len(rows) < len(existing) * MIN_RETAIN_RATIO:
        return {"skipped": f"refused: would truncate {len(existing)}->{len(rows)} rows"}
    warnings = []
    if existing and rows and rows[-1]["auction_date"] < existing[-1]["auction_date"]:
        warnings.append(f"tail regressed ({existing[-1]['auction_date']} -> {rows[-1]['auction_date']})")
    now = now or dt.datetime.now(dt.timezone.utc)
    doc = {"series_id": "ust_coupon_auctions",
           "source": rows[0]["source"] if rows else None,
           "written_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "first_auction": rows[0]["auction_date"] if rows else None,
           "last_auction": rows[-1]["auction_date"] if rows else None,
           "n": len(rows),
           "rows": rows}
    p = path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"rows": len(rows), "last": doc["last_auction"], "warnings": warnings}

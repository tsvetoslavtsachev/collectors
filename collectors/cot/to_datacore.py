"""Map the COT raw fetch -> data-core canonical series, through the writer gate.

This is what makes cot a *citizen*: every weekly spec-net lands in the guarded
base (identity + schema + health), one series per market, never a local file.

Identity enforcement happens HERE, before the write, and is the audit's missing
guard made real:

  - A NON-pinned market whose rows carry >1 contract identity is REFUSED (the WTI
    NYMEX+ICE-under-one-key LIKE case → skipped, never written). No silently-mixed
    series ever reaches the base.
  - A `cftc_code`-pinned market is identity-anchored by the STABLE contract code,
    so the fetch returns ONE contract; any name change within it is a benign
    `name_rebrand` (cosmetic, e.g. "UST 2Y NOTE" was "2-YEAR U.S. TREASURY NOTES")
    → marked, written WHOLE with full history (the consumer percentile uses ALL
    rows — no re-truncation).
  - A declared `satellite` (NYMEX Brent) is marked and written whole.
  - A dead source -> empty points -> the series is SKIPPED (never a silent zero).
"""
from __future__ import annotations
import datacore
from datacore.schema import SCHEMA_VERSION

from . import markets, derive


def push(raw_markets: dict) -> list[dict]:
    """raw_markets: {key: {"ok": bool, "rows": [normalized rows], "error": str}}."""
    results = []
    for m in markets.migrated():
        sid = m["canonical"]
        block = raw_markets.get(m["key"], {})
        if not block.get("ok"):
            results.append({"series_id": sid, "skipped": block.get("error", "no data")})
            continue

        rows = [r for r in block.get("rows", []) if r.get("primary_net") is not None]
        if not rows:
            results.append({"series_id": sid, "skipped": "no usable rows"})
            continue

        # --- identity guard: refuse silently-mixed contract identity ---
        # A pinned cftc_code anchors identity to one stable contract (the fetch
        # queries by code, so the rows cannot span two codes); a name change
        # within it is a benign rebrand. Only a NON-pinned LIKE key that spans
        # >1 contract name is the real hazard the audit exposed → refuse it.
        ids = derive.distinct_identities(rows)
        declared = bool(
            m.get("cftc_code") or m.get("splice_watch")
            or m.get("satellite") or m.get("name_break_2022")
        )
        if len(ids) > 1 and not declared:
            results.append({"series_id": sid,
                            "skipped": f"identity_rejected: {len(ids)} contracts "
                                       f"under one key {ids}"})
            continue

        # --- mark-don't-clean: keep all rows, record the seam ---
        flags = derive.data_quality(rows, m)
        records = [{"as_of": (r["date"] or "")[:10],
                    "value": r["primary_net"], "source": "cftc"} for r in rows]
        try:
            res = datacore.write(sid, records, schema_version=SCHEMA_VERSION)
            if flags:
                res["quality_flags"] = flags
            results.append(res)
        except datacore.WriteRejected as e:
            results.append({"series_id": sid, "skipped": f"rejected: {e}"})
    return results

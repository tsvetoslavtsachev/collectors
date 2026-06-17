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
        # Full cohort row (S13c expansion): `value` stays primary_net so every
        # analytical consumer that reads `value` is byte-stable; the named cohort
        # fields are additive and let the dashboards reproduce their full markets/
        # <key>.json shape from the base (retiring their own CFTC fetch). market_name
        # is kept per row so the rebrand/splice scar stays visible at row level.
        records = [{"as_of": (r["date"] or "")[:10],
                    "value": r["primary_net"], "source": "cftc",
                    "primary_long": r.get("primary_long"),
                    "primary_short": r.get("primary_short"),
                    "primary_net": r.get("primary_net"),
                    "secondary_long": r.get("secondary_long"),
                    "secondary_short": r.get("secondary_short"),
                    "secondary_net": r.get("secondary_net"),
                    "tertiary_long": r.get("tertiary_long"),
                    "tertiary_short": r.get("tertiary_short"),
                    "tertiary_net": r.get("tertiary_net"),
                    "open_interest": r.get("open_interest"),
                    "market_name": r.get("market_name")} for r in rows]
        try:
            res = datacore.write(sid, records, schema_version=SCHEMA_VERSION)
            if flags:
                res["quality_flags"] = flags
            results.append(res)
        except datacore.WriteRejected as e:
            results.append({"series_id": sid, "skipped": f"rejected: {e}"})
    return results

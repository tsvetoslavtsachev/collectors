"""Map oil's raw fetch output -> data-core canonical series, via the writer lib.

This is what makes oil a *citizen* of data-core: every number it produces lands in
the guarded base (identity guard + schema_version + health stamp), not in a local
file. A dead source -> empty points -> that series is skipped (never a silent zero).
"""
from __future__ import annotations
import datacore
from datacore.schema import SCHEMA_VERSION

# (data-core series_id, raw block, source tag, field holding [(date, value), ...])
SERIES = [
    ("oil_brent_m1_m2",         "prices", "yfinance",      "spread_series"),
    ("oil_brent_wti_spread",    "prices", "yfinance",      "bw_spread_series"),
    ("oil_wti_close",           "prices", "yfinance",      "wti_closes"),
    ("oil_hormuz_transit_pct",  "hormuz", "imf_portwatch", "weekly_pct"),
    ("oil_eia_crude_deviation", "eia",    "eia",           "deviations_mbbl"),
    ("oil_cot_wti_mm_pctile",   "cot",    "cftc",          "pctile_series"),
]


def push(raw: dict) -> list[dict]:
    results = []
    for series_id, block_key, source, field in SERIES:
        block = raw.get(block_key, {})
        points = block.get(field, []) if block.get("ok") else []
        if not points:
            results.append({"series_id": series_id,
                            "skipped": block.get("error", "no data")})
            continue
        records = [{"as_of": d, "value": v, "source": source} for d, v in points]
        try:
            results.append(
                datacore.write(series_id, records, schema_version=SCHEMA_VERSION)
            )
        except datacore.WriteRejected as e:
            results.append({"series_id": series_id, "skipped": f"rejected: {e}"})
    return results

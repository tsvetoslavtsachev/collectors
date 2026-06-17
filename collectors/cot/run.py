"""COT positioning collector — second citizen of data-core (INIT-22 S13).

Run:  python -m collectors.cot.run [--mock]

Flow: fetch per market (each isolated) -> WRITE each market's raw spec-net through
the data-core gate (identity + schema + health) -> report. The numbers live in
data-core; this repo holds only the fetch logic and the identity/percentile lib.
Percentiles are derived on the clean segment with explicit windows, never baked.
"""
from __future__ import annotations
import sys
from pathlib import Path
import yaml

from . import to_datacore, derive, markets

HERE = Path(__file__).resolve().parent


def main() -> int:
    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))

    if "--mock" in sys.argv:
        from . import mockdata
        raw = mockdata.raw(cfg)
    else:
        from .fetch_cot import fetch_cot
        raw = fetch_cot(cfg)

    # --- citizen step: every market's net goes through the data-core gate ---
    pushed = to_datacore.push(raw)

    wrote = [r for r in pushed if r.get("rows") is not None]
    skipped = [r for r in pushed if r.get("rows") is None]
    print(f"cot citizen: {len(wrote)} written, {len(skipped)} skipped "
          f"(of {len(markets.migrated())} migrated markets)")
    for r in wrote:
        flags = r.get("quality_flags")
        mark = ""
        if flags:
            mark = "  [" + ", ".join(f["flag"] for f in flags) + "]"
        # percentile report (clean segment, explicit windows) for written series
        print(f"  + {r['series_id']}: {r['rows']} rows, as_of {r['as_of']}{mark}")
    for r in skipped:
        print(f"  - {r['series_id']}: SKIP ({r.get('skipped')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Targeted backfill of NEW FRED series through the FEED 2 code path (ЗЛТ1).

Run:  python -m collectors.vrm.backfill mkt_ust_10y mkt_real_10y mkt_term_premium_10y

A new series in config.yaml `fred:` is written with its full FRED history by the very
first run of the weekly collector (no established window -> nothing to preserve; that is
how mkt_breakeven_10y got 2003). This does the same first run for the named series only,
without waiting for the Saturday CI and without rewriting the other ~50 series:
fetch_fred.fetch_fred (same fetch, same downsample rules) -> to_datacore.push (same guards).
The ledger face of the last full weekly run is left alone (push ledger=False).

Same cardinal-rule guard as run.py: needs DATACORE_ROOT (TEMP) or DATACORE_ALLOW_REAL=1.
FRED_API_KEY from env only; nothing about the key is printed.
"""
from __future__ import annotations
import sys
from pathlib import Path
import yaml

from . import fetch_fred, to_datacore

HERE = Path(__file__).resolve().parent


def main(argv: list) -> int:
    ids = [a for a in argv if not a.startswith("-")]
    if not ids:
        print("usage: python -m collectors.vrm.backfill <series_id> [<series_id> ...]")
        return 2
    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))
    unknown = [i for i in ids if i not in cfg["fred"] or cfg["fred"][i].get("computed")]
    if unknown:
        print(f"not a plain FRED series in config.yaml `fred:` : {unknown}")
        return 2
    to_datacore.assert_safe_root()
    sub = {**cfg, "fred": {i: cfg["fred"][i] for i in ids}}
    fred_raw = fetch_fred.fetch_fred(sub)
    raw = {}
    for sid, blk in fred_raw.items():
        if blk.get("ok"):
            raw[sid] = {"ok": True, "records": blk["model_records"]}
        else:
            raw[sid] = {"ok": False, "error": blk.get("error")}
    results = to_datacore.push(raw, ledger=False)
    rc = 0
    for r in results:
        if r.get("rows") is None:
            rc = 1
            print(f"  - {r['series_id']}: SKIP ({r.get('skipped')})")
        else:
            kept = f" [kept head: {r['retained_head']}]" if r.get("retained_head") else ""
            warn = f" [WARN: {'; '.join(r['warnings'])}]" if r.get("warnings") else ""
            first = min(x["as_of"] for x in raw[r["series_id"]]["records"])
            print(f"  + {r['series_id']}: {r['rows']} rows, {first} .. {r['as_of']}{kept}{warn}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

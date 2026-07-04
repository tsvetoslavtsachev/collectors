# -*- coding: utf-8 -*-
"""INIT-22 M1 -- ALFRED vintage runner: fetch_alfred -> datacore.vintage.append.

Pulls the full vintage history of the 7 FRED regime series (fetch_alfred) and
writes it to the data-core PIT layer (datacore.vintage), append-only. ISM is not
touched (licensed manual holdout, no ALFRED vintages). Mirrors run.py's shape.

Cardinal rule: this deterministic path is the only writer into vintage/. The
vintage.append safe-root guard refuses the real base without DATACORE_ALLOW_REAL=1
(CI sets it, as vrm.yml does; local runs write a TEMP root).

    python -m collectors.vrm.run_alfred                 # writes DATACORE_ROOT/vintage
    python -m collectors.vrm.run_alfred --cache DIR     # offline replay from raw cache
"""
from __future__ import annotations
import argparse
import sys

from collectors.vrm.fetch_alfred import fetch_alfred

try:
    from datacore import vintage as V
except Exception as e:  # noqa: BLE001
    V = None
    _IMPORT_ERR = e


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ALFRED vintage -> data-core PIT layer")
    ap.add_argument("--cache", default=None,
                    help="raw-rows cache dir (offline determinism replay)")
    ap.add_argument("--round-dp", type=int, default=6)
    args = ap.parse_args(argv)

    if V is None:
        print(f"FATAL: cannot import datacore.vintage: {_IMPORT_ERR}", file=sys.stderr)
        return 2

    res = fetch_alfred(rdp=args.round_dp, cache_dir=args.cache)
    total = {"appended": 0, "skipped": 0}
    rc = 0
    for sid, r in res.items():
        if not r.get("ok"):
            print(f"  {sid:26} FETCH-FAIL {r.get('error')}", file=sys.stderr)
            rc = 1
            continue
        s = V.append(sid, r["vintage_records"])   # root from DATACORE_ROOT env
        total["appended"] += s["appended"]
        total["skipped"] += s["skipped"]
        mm = r["meta"]
        print(f"  {sid:26} vints={mm['n_vintages']:>4} "
              f"earliest={mm['earliest_vintage']} "
              f"appended={s['appended']:>5} skipped={s['skipped']:>5}")
    print(f"TOTAL appended={total['appended']} skipped={total['skipped']}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

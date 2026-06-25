"""Price collector -- canonical daily ETF price citizen (INIT-22 P3).

Run:
    python -m collectors.price.run --mock              # offline wiring (Gate 1)
    python -m collectors.price.run --spot SPY,QQQ      # live, a few ETFs (Gate 2)
    python -m collectors.price.run                     # live, full universe (P4/P5)

Flow: fetch per symbol (each isolated) -> push every bar through the P1 archive
primitive into the SEPARATE price-archive store (append-only, year-partitioned,
bitemporal) -> report. The numbers live in the archive; this repo holds only the
fetch logic. ZERO prices touch the main data-core.

DATACORE_ROOT must point at the price-archive checkout (push() reads it and passes
it EXPLICITLY to every append -- the load-bearing convention). With it unset, the
P1 cardinal guard refuses the write (SystemExit) before any bar is written.
"""
from __future__ import annotations
import sys
from pathlib import Path

import yaml

from . import to_datacore
from .fetch_prices import fetch_prices

HERE = Path(__file__).resolve().parent


def _arg(args: list[str], flag: str) -> str | None:
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def _sids_for_symbols(cfg: dict, symbols: list[str]) -> list[str]:
    bysym = {m["symbol"].upper(): sid for sid, m in cfg["price"].items()}
    out, missing = [], []
    for s in symbols:
        sid = bysym.get(s.strip().upper())
        (out if sid else missing).append(sid or s)
    if missing:
        print(f"  ! unknown symbols ignored: {missing}")
    return [s for s in out if s]


def main() -> int:
    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))
    args = sys.argv[1:]

    if "--mock" in args:
        from . import mockdata
        raw = mockdata.raw(cfg)
        mode = "mock"
    else:
        only = None
        period = None
        spot = _arg(args, "--spot")
        if spot:
            only = _sids_for_symbols(cfg, spot.split(","))
            period = cfg["settings"].get("spot_check_period")
        raw = fetch_prices(cfg, period=period, only=only)
        mode = f"live ({'spot ' + spot if spot else 'full universe'})"

    value_tol = float(cfg["settings"].get("value_tol", to_datacore.DEFAULT_VALUE_TOL))
    pushed = to_datacore.push(raw, value_tol=value_tol)

    wrote = [r for r in pushed if r.get("ok")]
    skipped = [r for r in pushed if not r.get("ok")]
    # "accepted" = append did not error; "changed" = bytes actually moved (files_touched).
    # On a daily re-run (P5) most series are idempotent no-ops -- surface that so the
    # headline is an honest provenance signal, not a flat "N written".
    changed = [r for r in wrote if r.get("files_touched")]
    print(f"price citizen [{mode}]: {len(wrote)} accepted "
          f"({len(changed)} changed, {len(wrote) - len(changed)} idempotent), "
          f"{len(skipped)} skipped (of {len(raw)} series)")
    for r in wrote[:12]:
        ft = r.get("files_touched") or []
        print(f"  + {r['series_id']}: appended={r.get('appended', 0)} "
              f"restated={r.get('restated', 0)} revised={r.get('revised', 0)} "
              f"frozen={r.get('frozen', 0)} skipped={r.get('skipped', 0)} "
              f"files={ft}")
    if len(wrote) > 12:
        print(f"  ... (+{len(wrote) - 12} more written)")
    for r in skipped[:12]:
        print(f"  - {r['series_id']}: SKIP ({r.get('skip_reason')})")
    if len(skipped) > 12:
        print(f"  ... (+{len(skipped) - 12} more skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""VRM weekly collector — third citizen of data-core (INIT-22 E4/E5).

Run:  python -m collectors.vrm.run [--mock]

Flow: fetch (yfinance + FRED) -> compute (ahe YoY, pce nowcast) -> manual (ISM) ->
WRITE each series through the data-core gate (identity + schema + health) -> report.
The numbers live in data-core; this repo holds only the fetch/compute logic.

51 VRM series un-frozen: 34 yfinance (32 ETF/idx dual-basis + ^VIX/^MOVE) + 13 FRED
levels + 2 computed (ahe_yoy, pce_nowcast) + 2 manual ISM.

Cardinal rule: a model never writes numbers here. Point DATACORE_ROOT at a TEMP
base for Gate 1 — the real canonical is never touched until acceptance + sign-off.
"""
from __future__ import annotations
import os
import sys
import datetime as dt
from pathlib import Path
import yaml

from . import to_datacore

HERE = Path(__file__).resolve().parent

# Cardinal-rule guard moved to to_datacore.assert_safe_root (Gate-5 hardening: it
# now fires at the write path, not just here). main() still calls it early for
# fast-fail (refuse before a ~60s live fetch), and push() enforces it structurally.


def _safe(fn, *args) -> dict:
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        return {"__error__": f"{type(e).__name__}: {e}"}


def expected_series(cfg: dict) -> list:
    """The 51 series_ids the collector is responsible for (completeness check)."""
    ids = list(cfg["yfinance"])
    ids += [s for s, m in cfg["fred"].items() if not m.get("computed")]
    ids += [s for s, m in cfg["fred"].items() if m.get("computed")]  # ahe -> compute
    ids += list(cfg["computed"])
    ids += list(cfg["manual"]["series"])
    return ids


def assemble(cfg: dict) -> dict:
    """Live assembly of the {series_id: {ok, records}} raw from all four feeds."""
    from . import (fetch_prices, fetch_fred, compute, manual_ism, fetch_bridge,
                   carry_forward)

    raw: dict = {}
    raw.update(fetch_prices.fetch_prices(cfg))       # yfinance (skips bridge:true)
    raw.update(fetch_bridge.fetch_bridge(cfg))       # mkt_vix/mkt_move via barometer

    fred_raw = fetch_fred.fetch_fred(cfg)
    for sid, blk in fred_raw.items():
        if blk.get("ok") and blk.get("model_records") is not None:
            raw[sid] = {"ok": True, "records": blk["model_records"]}
        elif not blk.get("ok"):
            raw[sid] = {"ok": False, "error": blk.get("error")}
    raw.update(compute.compute_all(fred_raw, cfg))   # ahe_yoy, pce_nowcast
    raw.update(manual_ism.load_ism(cfg))

    # Carry-forward fill the month-end macro cohort (Gate-4 decision: carry-forward)
    # so a live FRED source gap (Oct-2025 BLS delay, lagging PCE tip) does not null
    # the recent regime. Filled cells are flagged (filled=carry_forward, provisional).
    for sid, months in carry_forward.carry_forward_macro(raw, cfg):
        print(f"  carry-forward {sid}: filled {months}")
    return raw


def main() -> int:
    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))
    to_datacore.assert_safe_root()   # cardinal rule: never the real base without opt-in
    root = os.environ.get("DATACORE_ROOT", "(data-core repo default)")
    exp = expected_series(cfg)
    print(f"VRM collector -> DATACORE_ROOT = {root}")
    print(f"expected series: {len(exp)}  (wiring check: {'OK' if len(exp) == 51 else 'MISMATCH'})")

    if "--mock" in sys.argv:
        from . import mockdata
        raw = mockdata.raw(cfg)
    else:
        raw = assemble(cfg)

    pushed = to_datacore.push(raw)

    wrote = [r for r in pushed if r.get("rows") is not None]
    skipped = [r for r in pushed if r.get("rows") is None]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"[{stamp}] vrm citizen: {len(wrote)} written, {len(skipped)} skipped "
          f"(of {len(exp)} expected)")
    for r in wrote:
        warn = f"  [WARN: {'; '.join(r['warnings'])}]" if r.get("warnings") else ""
        print(f"  + {r['series_id']}: {r['rows']} rows, as_of {r['as_of']}{warn}")
    for r in skipped:
        print(f"  - {r['series_id']}: SKIP ({r.get('skipped')})")
    warned = [r for r in wrote if r.get("warnings")]
    if warned:
        print(f"  ! {len(warned)} series written WITH edge/gap warnings (see [WARN] above): "
              f"a live source gap shrank them vs the established base; fill policy = Gate-4 decision")

    missing = sorted(set(exp) - {r["series_id"] for r in pushed})
    if missing:
        print(f"  ! MISSING from raw (not even attempted): {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

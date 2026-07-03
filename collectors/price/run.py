"""Price collector -- canonical daily ETF price citizen (INIT-22 P3).

Run:
    python -m collectors.price.run --mock              # offline wiring (Gate 1)
    python -m collectors.price.run --spot SPY,QQQ      # live, a few ETFs (Gate 2)
    python -m collectors.price.run --daily             # live, full universe, SHORT window (P5 daily CI)
    python -m collectors.price.run                     # live, full universe, "max" window (manual full pull)
    python -m collectors.price.run --period 10d        # live, full universe, explicit window override

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
from . import split_heal
from . import reconcile
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


def _family_sids(cfg: dict, families: list[str]) -> list[str]:
    """series_ids whose family is in ``families`` (an ETF entry omits ``family`` ->
    defaults to 'etf'). This is the P8a daily FAMILY-SCOPE guard (program Капан 1): the
    SHORT-window ``--daily`` run appends only families that are split-heal-ready, so the
    moment stocks are registered in the catalog they do NOT silently start appending
    through the daily path (a short window strands pre-split bars at the old scale -- a
    ~Nx TA cliff, e.g. NVDA 10:1). Stocks join the daily run only via an explicit
    ``--family stock`` (or being added to settings.daily_families) once P8b's split-heal
    lands. A FULL manual pull (no ``--daily``) is unscoped -- it re-pulls every bar, so it
    carries no cliff risk."""
    fams = set(families)
    # RETIRED constituents (merged/delisted, symbol will not return -- e.g. CTRA->DVN merger,
    # 2026-07-03) are dropped from fetch scope: their config entry stays for provenance, but the
    # daily run must not keep probing a dead symbol. Distinct from _QUARANTINE_DEAD_OK (temporary
    # upstream outage, symbol returns UNCHANGED) -- retire is permanent.
    return [sid for sid, m in cfg["price"].items()
            if m.get("family", "etf") in fams and not m.get("retired")]


def _daily_ready_scope(cfg: dict, sids: list[str], *, root=None, log=print) -> list[str]:
    """P8b: restrict a stock-INCLUSIVE daily run to series that are READY to write --
    REGISTERED in the archive catalog AND (for stocks) carrying a stable_id.

      * an UNREGISTERED family (e.g. STOXX before P8c registers it) is dropped BEFORE
        fetch -- never fetched-from-Yahoo-then-skipped (saves a wasted foreign pull/day);
      * a registered-but-UNSTAMPED stock is refused (F5 fail-closed: never a silent
        unstamped write -- a delisted-but-still-configured stock whose ticker lost its
        active identity epoch).

    Both are surfaced LOUDLY (count + sample), never silent. ETF-only daily never calls
    this (the caller gates on a non-etf family), so the live ETF path stays byte-identical;
    even if it did, all ETFs are registered and carry no stable_id -> the set is unchanged.
    """
    arch_root = to_datacore._resolve_root(root)
    cat = to_datacore.load_catalog(arch_root)            # root=None -> env (the daily CI sets it)
    if not (cat and cat.get("series")):
        # No catalog to classify against (no/blank DATACORE_ROOT). Do NOT silently drop the
        # whole universe -- leave the scope unchanged; the push cardinal guard fails loud.
        return sids
    series = cat["series"]
    ready, unregistered, unstamped = [], [], []
    for sid in sids:
        fam = cfg["price"].get(sid, {}).get("family", "etf")
        entry = series.get(sid)
        if entry is None:
            unregistered.append(sid)
        elif fam == "stock" and not entry.get("stable_id"):
            unstamped.append(sid)
        else:
            ready.append(sid)
    if unregistered:
        log(f"  daily-scope: skipping {len(unregistered)} unregistered series "
            f"(not yet in the catalog -- e.g. {unregistered[:4]})")
    if unstamped:
        log(f"  F5 fail-closed: refusing {len(unstamped)} registered-but-unstamped stock(s) "
            f"(no stable_id; identity-map gap -- e.g. {unstamped[:4]})")
    return ready


def main() -> int:
    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))
    args = sys.argv[1:]

    if "--mock" in args:
        from . import mockdata
        raw = mockdata.raw(cfg)
        mode = "mock"
    else:
        only = None
        # Window precedence: explicit --period wins; then --spot / --daily presets; else
        # None -> fetch_prices falls back to settings.history_period_prices ("max").
        # --daily is the routine P5 CI entry: a SHORT settings.daily_period window that
        # appends the new bar, freezes the prior provisional tip, and BOUNDS the value_tr
        # dividend cascade to the window (a "max" daily pull would restate ALL history).
        # SCOPE precedence (P8a): --spot (explicit symbols) > --family (explicit family) >
        # --daily (settings.daily_families guard, default ETF-only) > full universe.
        period = _arg(args, "--period")
        spot = _arg(args, "--spot")
        fam = _arg(args, "--family")
        daily = "--daily" in args
        if spot:
            only = _sids_for_symbols(cfg, spot.split(","))
            period = period or cfg["settings"].get("spot_check_period")
            scope = "spot " + spot
        elif fam:
            only = _family_sids(cfg, [fam])              # explicit family scope (P8a pilot / P8b heal)
            if daily:
                period = period or cfg["settings"].get("daily_period", "1mo")
            scope = f"family={fam}" + (" daily" if daily else "")
        elif daily:
            period = period or cfg["settings"].get("daily_period", "1mo")
            fams = cfg["settings"].get("daily_families", ["etf"])  # Капан-1 guard: ready families only
            only = _family_sids(cfg, fams)
            scope = "daily (families=%s)" % ",".join(fams)
        else:
            scope = "full universe"                       # unscoped manual pull -- no cliff risk
        # P8b: ANY --daily run that pulls a non-ETF family is restricted to series that are
        # registered + stamped -- this covers BOTH the bare `--daily` (daily_families) and the
        # explicit `--family X --daily` paths (C5 fix; the latter previously bypassed the
        # guard). Drops STOXX before P8c registers it, and is the early/loud half of F5 (the
        # push require_stamp backstop below is the write-time half). A pure ETF-only --daily,
        # --spot, and a full manual pull are NOT scoped -> the live ETF daily is byte-identical.
        if daily and only is not None and any(
                cfg["price"].get(s, {}).get("family", "etf") != "etf" for s in only):
            only = _daily_ready_scope(cfg, only)
        raw = fetch_prices(cfg, period=period, only=only)
        mode = f"live ({scope}{', period=' + period if period else ''})"

    # P8c CURRENCY RECONCILE (F3): a multi-currency (STOXX) series whose LIVE yfinance currency
    # contradicts its catalog quote_basis -- a GBX<->GBP/USD re-denomination -> a silent 100x in the
    # RAW store -- is DROPPED loud BEFORE the write; an unreachable fast_info is a soft WARN (the
    # bar is still split-adjusted-correct, self-heals next run). Gated on a LIVE run with a
    # currency-bearing series actually in scope, so an ETF/SP500-only daily makes ZERO fast_info
    # calls and the live ETF path is byte-identical.
    if mode != "mock" and isinstance(raw, dict):
        # Only reconcile series that actually FETCHED ok: skip an already-dead fetch (no wasted
        # fast_info call, and no currency reason overwriting the original fetch-error reason -- the
        # series is dropped either way). Ungated, an ok=False STOXX would still hit the network.
        ccy_sids = [s for s in raw
                    if isinstance(raw.get(s), dict) and raw[s].get("ok")
                    and cfg["price"].get(s, {}).get("quote_basis")]
        if ccy_sids:
            mism, _unverified = reconcile.reconcile(cfg, ccy_sids)
            if mism:
                reconcile.neutralize(raw, mism)

    value_tol = float(cfg["settings"].get("value_tol", to_datacore.DEFAULT_VALUE_TOL))
    # F5 (P8b): a run that includes the STOCK family enforces the stamp at write time -- a
    # registered-but-unstamped stock is refused, never silently written. The seed/backfill
    # driver pushes with the permissive default, so the one-time seed flow is unchanged.
    require_stamp = (mode != "mock") and any(
        cfg["price"].get(s, {}).get("family") == "stock"
        for s in (raw if isinstance(raw, dict) else ()))
    pushed = to_datacore.push(raw, value_tol=value_tol, require_stamp=require_stamp)

    # P8b SPLIT-HEAL (the blocker). A STOCK whose SHORT daily window shows a split
    # (split_factor != 1.0) has pre-window bars stranded at the OLD scale -> a coverage-
    # complete full-depth re-pull restates the whole stored range to the new scale (zero ~Nx
    # TA cliff). Gated on a STOCK actually being in scope, so the live ETF-only daily runs
    # ZERO split_heal code (C4); and on a live run (a --mock run's synthetic factors must not
    # trigger a network pull).
    if require_stamp:
        split_sids = split_heal.detect_split_symbols(cfg, raw)
        if split_sids:
            print(f"  split-heal: {len(split_sids)} stock(s) split in-window "
                  f"-> coverage-complete full-depth re-pull: {split_sids}")
            split_heal.heal(cfg, split_sids, value_tol=value_tol, require_stamp=True)

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

"""P4 ETF backfill driver -- the one-time inception seed of the price archive.

A THIN orchestrator over the FROZEN P3 citizen (``fetch_prices`` + ``to_datacore.push``
+ ``load_catalog``). It does NOT modify the citizen or the P1 primitive -- it only
sequences them: batched, fail-loud-per-symbol, resumable, and two-key-guarded for the
real write.

WHY a separate driver (run.py is not enough). ``run.py`` fetches ALL 132 symbols into
memory and pushes once at the end -- a crash at symbol 100 persists nothing and a
re-run starts over. For a 132 x full-history seed that is fragile. This driver:
  * BATCHES fetch->push (default 25/batch) so partial progress lands per batch;
  * is RESUMABLE -- a re-run is an idempotent no-op for already-written symbols (the
    P1 append skips identical bars), so a throttled/failed batch is just re-run;
  * inherits PER-SYMBOL ISOLATION from the citizen (a dead/empty symbol is marked
    not-ok and skipped; the rest of the universe still lands -- never a silent zero);
  * spaces batches with a small sleep to be gentle on yfinance.

CARDINAL RULE (program R7/R8). The REAL price-archive is written ONLY with the TWO KEYS
``--real --confirm BACKFILL-REAL``, never on a scheduled path. There are TWO real-write
paths, both two-key-gated AND both gated by the verify_backfill commit gate (which is
HARD on depth + full coverage, so a partial/truncated seed cannot promote):

  * canonical LOCAL seed -- ``--real --confirm ... --from <verified_temp>``: a REPLAY
    (no network) of an already-verified temp archive into the real root. This is the
    OPERATING PROCEDURE used for the inception seed: rehearse into a temp root, verify,
    get sign-off, then promote the exact verified bytes. No re-fetch, no drift.
  * CI path -- ``--real --confirm ...`` WITHOUT ``--from``: a direct live fetch into the
    archive checkout (the workflow_dispatch runner). Legitimate, but note it is NOT a
    replay -- it is guarded by the two keys AND the verify gate that runs before the
    commit, NOT by a "must have a verified temp" code invariant.

``assert_safe_root`` (inside ``archive.append``, vendored from VRM) fires structurally on
EVERY path: it refuses the real data-core base; the price-archive checkout is a DIFFERENT
path, so writes proceed WITHOUT ``DATACORE_ALLOW_REAL`` (which ``push`` independently
refuses anyway).

Usage:
    # 1) dress rehearsal into a TEMP root (network, batched) -- no keys needed:
    DATACORE_ROOT=<temp> PYTHONPATH=<data-core>;<collectors> \
        python -m collectors.price.backfill --root <temp>

    # 2) real LOCAL seed (two keys + replay of the verified temp), after sign-off:
    python -m collectors.price.backfill --root C:/Projects/price-archive \
        --real --confirm BACKFILL-REAL --from <temp>

    # (the workflow_dispatch CI path fetches directly: --real --confirm <input>, no --from)

Daily increments are P5 (NOT here). Same-day re-pull / value_tr-cascade calibration
are P5 concerns too (this is a one-time, single-vintage seed -> restatement-free).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

from . import to_datacore
from .fetch_prices import fetch_prices

HERE = Path(__file__).resolve().parent

# The SECOND key. The first key is the explicit --real flag; this exact string is the
# confirm that authorizes a for-keeps write (mirrors the workflow_dispatch confirm input).
REAL_CONFIRM_TOKEN = "BACKFILL-REAL"


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def guard_real_write(real: bool, confirm: str) -> None:
    """Two-key guard. A REAL seed needs BOTH the ``--real`` flag AND the exact confirm
    token. A confirm WITHOUT ``--real`` is a likely foot-slip -> also refused loudly.
    Raises SystemExit (a BaseException -- never swallowed by per-series isolation)."""
    if real and confirm != REAL_CONFIRM_TOKEN:
        raise SystemExit(
            f"REFUSED: --real needs --confirm {REAL_CONFIRM_TOKEN} (the second key); "
            f"got {confirm!r}. The real price-archive is never seeded with one key.")
    if confirm and not real:
        raise SystemExit(
            "REFUSED: --confirm was given without --real. Pass --real to authorize a "
            "real seed, or drop --confirm for a temp dress rehearsal.")


def backfill(cfg: dict, root, *, only=None, period=None, batch_size=25,
             batch_sleep=3.0, recorded_on=None, catalog=None,
             fetch=fetch_prices, push=to_datacore.push, log=print) -> dict:
    """Batched fetch->push into the archive at ``root``.

    Resumable: a re-run is an idempotent no-op for already-written symbols (P1 append
    skips identical bars), so a throttled batch is simply re-run. ``fetch``/``push`` are
    injectable so offline tests can drive the same control flow with mock data.

    Returns {series_id: per-series result dict} (the push summary, ok/skip_reason).
    """
    root = str(root)
    sids = [s for s in cfg["price"] if (only is None or s in only)]
    # Load the archive catalog ONCE here too (the catalog-once contract, program R6),
    # and pass the SAME dict to every batch's push so it is never re-parsed per series.
    cat = catalog if catalog is not None else to_datacore.load_catalog(root)
    results: dict = {}
    nbatch = (len(sids) + batch_size - 1) // batch_size if sids else 0
    for bi, batch in enumerate(_chunks(sids, batch_size), 1):
        log(f"  batch {bi}/{nbatch}: fetching {len(batch)} symbols ...")
        raw = fetch(cfg, period=period, only=batch)          # network, per-symbol isolated
        pushed = push(raw, root=root, catalog=cat, recorded_on=recorded_on)
        for r in pushed:
            results[r["series_id"]] = r
        ok = sum(1 for r in pushed if r.get("ok"))
        appended = sum(r.get("appended", 0) for r in pushed if r.get("ok"))
        log(f"    -> {ok}/{len(batch)} ok, {appended} bars appended"
            f"{'' if ok == len(batch) else '  (' + str(len(batch) - ok) + ' skipped)'}")
        if batch_sleep and bi < nbatch:
            time.sleep(batch_sleep)
    return results


def promote(from_root, to_root, *, only=None, recorded_on=None, catalog=None,
            push=to_datacore.push, log=print) -> dict:
    """Replay an already-verified archive (``from_root``) into ``to_root`` through the
    citizen push path -- re-validating identity (``to_root``'s catalog), the safe-root
    guard, and the schema/shape gates. NO network, NO drift: the real bytes are the
    bytes we verified in the temp root.

    Valid ONLY for a restatement-free seed (a single ``recorded_on`` vintage per
    ``as_of``). The first seed is exactly that (verify gate: restated==0), so
    ``archive.read`` (current view) returns every bar with nothing collapsed. For
    ongoing daily writes (P5) the path is a direct append/restate to the real root,
    NOT a replay -- promote is a SEED-only mechanism.
    """
    from datacore import archive  # data-core on PYTHONPATH
    from_root, to_root = str(from_root), str(to_root)
    cat = catalog if catalog is not None else to_datacore.load_catalog(to_root)
    arch = Path(from_root) / "archive"
    sids = sorted(
        p.name for p in arch.glob("*")
        if p.is_dir() and p.name.startswith("px_") and (only is None or p.name in only)
    ) if arch.exists() else []
    if not sids:
        raise SystemExit(
            f"REFUSED: --from {from_root} has no px_* archive to promote -- run the "
            f"temp rehearsal + verify first.")
    results: dict = {}
    for sid in sids:
        recs = archive.read(sid, root=from_root)   # current view (single vintage on a clean seed)
        # Carry each bar's own recorded_on from the verified temp (it is the genuine
        # first-recording date); recorded_on= is the fallback only for bars lacking one.
        raw = {sid: {"ok": bool(recs), "records": recs,
                     "error": "empty series in temp -- nothing to promote"}}
        pushed = push(raw, root=to_root, catalog=cat, recorded_on=recorded_on)
        results[sid] = pushed[0]
        s = pushed[0]
        log(f"  promote {sid}: appended={s.get('appended', 0)} "
            f"skipped={s.get('skipped', 0)} ok={s.get('ok')}")
    return results


def _report(results: dict, log=print) -> dict:
    """Per-symbol roll-up: written vs dead, total bars. Depth/coverage detail is the
    verify script's job (it reads the archive independently)."""
    wrote = {k: v for k, v in results.items() if v.get("ok")}
    dead = {k: v for k, v in results.items() if not v.get("ok")}
    bars = sum(v.get("appended", 0) for v in wrote.values())
    log("")
    log(f"backfill report: {len(wrote)} series written ({bars} bars appended), "
        f"{len(dead)} dead/skipped, {len(results)} attempted")
    for sid, v in sorted(dead.items()):
        log(f"  - DEAD {sid}: {v.get('skip_reason')}")
    return {"written": len(wrote), "dead": sorted(dead), "bars": bars,
            "attempted": len(results)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="INIT-22 P4 ETF backfill driver")
    ap.add_argument("--root", required=True,
                    help="destination archive root (price-archive checkout, or a TEMP "
                         "dir for the dress rehearsal)")
    ap.add_argument("--real", action="store_true",
                    help="authorize a REAL seed of --root (requires --confirm)")
    ap.add_argument("--confirm", default="",
                    help=f"the second key; must equal {REAL_CONFIRM_TOKEN} when --real is set")
    ap.add_argument("--from", dest="from_root", default=None,
                    help="promote (REPLAY) an already-verified temp archive into --root "
                         "instead of fetching (the real LOCAL write); requires --real")
    ap.add_argument("--only", default=None, help="comma-separated series_ids subset")
    ap.add_argument("--batch-size", type=int, default=25)
    ap.add_argument("--batch-sleep", type=float, default=3.0)
    ap.add_argument("--recorded-on", default=None,
                    help="pin recorded_on (default: today) -- determinism for the seed")
    args = ap.parse_args(argv)

    guard_real_write(args.real, args.confirm)
    if args.from_root and not args.real:
        raise SystemExit("REFUSED: --from (promote/replay) requires --real --confirm "
                         f"{REAL_CONFIRM_TOKEN}.")

    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))
    only = [s.strip() for s in args.only.split(",")] if args.only else None

    if args.from_root:
        print(f"PROMOTE (replay, no network) {args.from_root} -> {args.root}  [REAL]")
        results = promote(args.from_root, args.root, only=only, recorded_on=args.recorded_on)
    else:
        tag = "REAL" if args.real else "rehearsal (temp)"
        print(f"BACKFILL fetch -> {args.root}  [{tag}]  batch={args.batch_size} "
              f"sleep={args.batch_sleep}s")
        results = backfill(cfg, args.root, only=only, batch_size=args.batch_size,
                           batch_sleep=args.batch_sleep, recorded_on=args.recorded_on)

    _report(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

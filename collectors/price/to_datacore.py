"""Map the raw daily fetch -> price-archive, through the P1 archive primitive.

This is what makes price a *citizen*: every ETF's daily bar lands in the guarded,
append-only, year-partitioned archive (P2 location) via ``datacore.archive.append``
(P1), one series per symbol, never a local file -- and never the canonical data-core
price layer (there is none; prices live ONLY in the archive).

THREE things this push does that the oil/cot template never had to:

  1. EXPLICIT root on every append (the load-bearing convention from
     price-archive/scripts/gate.py): forces the root-aware identity branch
     (archive.py reads <root>/catalog/catalog.json) and the safe-root guard to
     validate the EFFECTIVE archive target. A caller that omitted root would
     silently fall back to data-core's PRODUCTION catalog/base -- never here.

  2. CATALOG CACHED ONCE per run, passed as ``catalog=<dict>`` to every append.
     Without it the identity gate re-parses the whole catalog on EVERY series ->
     O(N^2) at 132 (and 1,200 later) series (program R6). The P1 ``catalog=`` param
     dissolves it with zero new code.

  3. The P1 archive.append carries the value-conflict / provisional / append-only
     machinery itself -- push() just feeds it the records the fetcher shaped.

CARDINAL RULE, structurally enforced: ``archive.append`` calls ``assert_safe_root``
FIRST. With DATACORE_ROOT unset (or pointed at the real data-core repo without
DATACORE_ALLOW_REAL=1) it raises SystemExit -> the run aborts LOUDLY. We do NOT
catch SystemExit per series (it is BaseException, not Exception), so a cardinal
violation can never be swallowed by the per-series isolation below.

Per-series isolation: a dead fetch (ok=False) or a per-series append error
(UnknownSeries / ArchiveError) is recorded and SKIPPED -- the run continues for the
rest of the universe; never a silent zero, never a whole-run abort for one symbol.
"""
from __future__ import annotations
import json
import os
from pathlib import Path

# P1 default; tightest-safe (captures every restatement). TWO daily-RE-PULL hazards
# belong to P4/P5, NOT P3 (P3 writes a TEMP root ONCE, so neither arises here):
#  (1) value_tr (Adj Close) is dividend-back-adjusted and IS in P1's value-diff set, so
#      every future ex-dividend nudges ALL prior bars' value_tr a few bps -> a re-pull
#      would cascade-restate the whole history. P4/P5 calibration: loosen value_tol, or
#      exclude value_tr from the conflict diff (conflict only on as-traded close*factor).
#  (2) SAME-calendar-day restatement collision: the citizen does not pass recorded_on, so
#      P1 defaults to date.today(). A finalized bar whose close changes on a SECOND run the
#      SAME day cannot advance recorded_on -> P1 raises ArchiveError -> caught as a LOUD
#      per-series skip (the stale bar is dropped for that run, the symbol reported skipped).
#      P5 (one run/day) avoids it; a same-day re-run would thread an explicit recorded_on.
# Named here so P5 inherits these, not discovers them.
DEFAULT_VALUE_TOL = 1e-6


def _resolve_root(root) -> str | None:
    if root is not None:
        return str(root)
    return os.environ.get("DATACORE_ROOT")


def load_catalog(root) -> dict | None:
    """Load + VALIDATE the archive's dedicated catalog ONCE; the dict is passed as
    ``catalog=`` to every append (the catalog-once contract).

    Fails CLOSED and LOUD on a missing or mis-shaped catalog: a None / list /
    series-less dict would otherwise let the P1 identity gate fall OPEN
    (``catalog.get('series', catalog)`` would treat the whole dict as the registry) or
    silently degrade catalog-once back to per-series disk reads. ``root=None`` -> None
    (when no root is set it is the CARDINAL guard, not the catalog, that should fire)."""
    if root is None:
        return None
    path = Path(root) / "catalog" / "catalog.json"
    if not path.exists():
        raise FileNotFoundError(
            f"no catalog at {path} -- run `python -m collectors.price.register_catalog` "
            f"against the archive root first")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not (isinstance(data, dict) and isinstance(data.get("series"), dict)):
        raise ValueError(
            f"malformed catalog at {path}: expected a dict with a top-level 'series' dict")
    return data


def push(raw: dict, *, root=None, catalog=None,
         value_tol: float = DEFAULT_VALUE_TOL, recorded_on=None) -> list[dict]:
    """raw: {series_id: {"ok": bool, "records": [...], "error": str}}.

    Writes each ok series into the archive at <root> through P1. Returns a list of
    per-series result dicts ({"ok": True, **P1 summary} on success, or
    {"ok": False, "skip_reason": reason} on a dead/refused series). ``recorded_on``
    (None -> P1 uses date.today()) is threaded through for deterministic tests/backfill.
    The cardinal-rule SystemExit is intentionally NOT caught.
    """
    from datacore import archive  # data-core on PYTHONPATH

    # Defense in depth (program R8): the price citizen has NO legitimate use for the
    # real-base override -- prices live ONLY in the archive, never data-core. VRM
    # legitimately sets DATACORE_ALLOW_REAL=1 in its CI (collectors/.github/workflows/
    # vrm.yml); a price CI (P5) copy-pasting that step would silently DISARM the cardinal
    # guard. Refuse it here so that footgun can never arm.
    if os.environ.get("DATACORE_ALLOW_REAL") == "1":
        raise SystemExit(
            "REFUSED: collectors/price must NEVER run with DATACORE_ALLOW_REAL=1 -- "
            "prices live only in the archive, never the real data-core base.")

    arch_root = _resolve_root(root)
    # Load the catalog ONCE (None only when no root -- the first append then hits the
    # cardinal guard and aborts before any identity check anyway).
    cat = catalog if catalog is not None else load_catalog(arch_root)

    results: list[dict] = []
    for sid, block in raw.items():
        # ``ok`` is the written/dead discriminator -- the P1 summary itself carries a
        # ``skipped`` row-count, so a separate ``skip_reason`` keeps the two apart.
        # A non-dict block is isolated too (never aborts the whole universe run).
        if not isinstance(block, dict):
            results.append({"series_id": sid, "ok": False, "skip_reason": "malformed block"})
            continue
        if not block.get("ok"):
            results.append({"series_id": sid, "ok": False,
                            "skip_reason": block.get("error", "no data")})
            continue
        recs = block.get("records") or []
        if not recs:
            results.append({"series_id": sid, "ok": False, "skip_reason": "no records"})
            continue
        try:
            summary = archive.append(sid, recs, root=arch_root, catalog=cat,
                                     value_tol=value_tol, recorded_on=recorded_on)
            results.append({"series_id": sid, "ok": True, **summary})
        except Exception as e:  # noqa: BLE001 -- isolate per series (NOT SystemExit)
            results.append({"series_id": sid, "ok": False,
                            "skip_reason": f"{type(e).__name__}: {e}"})
    return results

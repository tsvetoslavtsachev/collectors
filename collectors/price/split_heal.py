# -*- coding: utf-8 -*-
"""INIT-22 P8b -- split-aware re-heal for the STOCK daily path (THE BLOCKER).

THE HAZARD (config.yaml:37-52, verified). ``run --daily`` pulls a SHORT window
(settings.daily_period = "1mo"). ``archive.append`` restates ONLY the as_ofs in the
incoming batch, so on a stock split the post-split window bars land at the new
(split-adjusted) scale while every pre-WINDOW bar STRANDS at the OLD scale -- a ~Nx
discontinuity (e.g. NVDA 10:1) in ``close``/``value``, the EXACT series TA reads
(50/200-DMA, RSI, MACD). ETFs effectively never split, so this was dormant; the STOCK
family (P7a) makes it live. Stock daily CI must not turn on until this is closed.

MECHANISM (mandate P8b step 2, option A -- reactive per-symbol). A full-depth re-pull
re-fetches EVERY bar at the CURRENT scale. Pushed through the citizen path, each changed
pre-window bar becomes a bitemporal RESTATEMENT (``archive.append``: new ``recorded_on``,
the prior line retained byte-for-byte), so the stored current view lands wholly on the
new scale -- ZERO cliff. ``value_tr`` is re-healed across history as a bonus (the
documented daily ``value_tr`` drift vanishes for healed symbols). The opposite option a
scheduled MONTHLY full re-pull of every stock) was rejected: a heavy all-universe pull
from a cloud runner risks Yahoo's IP block (the P4 lesson), whereas this re-pulls only the
handful of symbols that actually split on a given day -- network-light and automatic.

DETECTION. In a split-free window every bar anchors at ``split_factor == 1.0`` (the factor
is the product of splits STRICTLY AFTER a bar's date; with no split in the window all are
1.0, and the tip is always 1.0). The moment a split's ex-date falls inside the window the
in-window pre-ex bars carry the ratio (!= 1.0). So a STOCK series whose freshly-fetched
daily window holds ANY bar with ``split_factor != 1.0`` split recently -> it needs a full
re-heal. This catches EVERY split provided the daily runs at least once while the ex-date is
still inside the ~1mo window (it runs ~5x/week, Tue-Sat). The pathological case -- a CI
outage LONGER than the window -- is the documented manual fallback (a one-off local
``run --period max <sym>``); verify_backfill --daily v2c already surfaces >4d laggards.
NOT a silent cap: heal() logs every symbol it heals, and this boundary is stated here.

DEPTH + COVERAGE-COMPLETENESS. The heal re-pulls ``period="max"`` then TRIMS to the series'
stored earliest as_of, so it appends nothing older -- the 6y stock depth contract
(settings.history_period_stock) stays uniform. CRUCIALLY it does NOT assume the re-pull is a
SUPERSET of the stored as_ofs: archive.append restates only as_ofs in the incoming batch, so
a stored interior date the vendor happens to OMIT from a later "max" pull would otherwise
strand at the old scale (a residual cliff). So any stored bar MISSING from the re-pull is
forced onto the new scale via the as-traded IMMUTABILITY invariant -- as-traded =
close*split_factor is constant, so close_new = close_old * (old_factor / new_factor), with
new_factor read from the re-pull's cumulative split_factor at the nearest covered date
AT-OR-BEFORE the missing one (split_factor is constant between ex-dates; looking backward is
correct even when the omitted bar abuts the split ex-date). The whole stored range lands on
the new scale even when the vendor drops a date.

SAME-DAY SAFETY. heal() runs in the SAME ``run --daily`` invocation as the daily push,
both defaulting ``recorded_on`` to today. No vintage collision (archive.py:327): the
pre-window bars the heal restates carry the OLD backfill ``recorded_on`` (< today, so the
restatement advances cleanly), and the in-window bars the daily push just wrote today are
byte-identical to the heal's re-pull for the same day -> SKIP, never a same-day restate. A
genuinely-divergent intraday vendor value would raise ArchiveError -> caught as a LOUD
per-series skip (never silent), self-healing on the next day's run.

SCOPE. STOCK family only (mandate scope; keeps the live ETF daily byte-identical). An ETF
split would heal through the identical mechanism -- left off so the ETF daily path is
provably unchanged; flip the family filter in detect_split_symbols to extend it.
"""
from __future__ import annotations

import bisect

from . import to_datacore
from .fetch_prices import fetch_prices

_SPLIT_EPS = 1e-9   # matches verify_backfill's split_factor != 1.0 test
_PRICE_FIELDS = ("value", "open", "high", "low", "close", "value_tr")


def _has_window_split(records) -> bool:
    """True if any record in a fetched window carries split_factor != 1.0 (a split's
    ex-date fell inside the window -> the pre-ex bars carry the ratio)."""
    for r in records:
        try:
            if abs(float(r.get("split_factor", 1.0)) - 1.0) > _SPLIT_EPS:
                return True
        except (TypeError, ValueError):
            continue
    return False


def detect_split_symbols(cfg: dict, raw: dict) -> list[str]:
    """STOCK series in a daily fetch result whose window holds a split (split_factor !=
    1.0). Stock-scoped (the live ETF daily stays byte-identical). Sorted, deterministic.

    ``raw`` is the {series_id: {"ok", "records", "error"}} shape from fetch_prices. A dead
    (ok=False) or non-stock series is ignored.
    """
    price = cfg["price"]
    out = []
    for sid, block in raw.items():
        if price.get(sid, {}).get("family") != "stock":
            continue
        if not (isinstance(block, dict) and block.get("ok")):
            continue
        if _has_window_split(block.get("records") or []):
            out.append(sid)
    return sorted(out)


def _new_factor_for(asof: str, covered: list, cov_asofs: list) -> float:
    """The NEW cumulative split_factor for a stored as_of MISSING from the re-pull, taken from
    the nearest covered (re-pulled) bar with as_of <= the missing date (BACKWARD).

    WHY BACKWARD (the C2-round2 fix). split_factor = product of splits STRICTLY AFTER a bar,
    a step function that changes only at split ex-dates. For a missing date d, the last covered
    bar AT-OR-BEFORE d shares d's factor UNLESS a split ex-date fell in that (covered, d] gap --
    but a covered bar between two split levels means no ex-date sits between it and d (the
    ex-date bar would itself be a covered bar nearer to d). Looking FORWARD instead was WRONG
    exactly when the omitted bar ABUTS the ex-date: the nearest later covered bar is then the
    ex-date bar carrying the POST-split factor (e.g. 1.0), so a pre-split missing bar would not
    be rescaled and would strand a cliff. The only residual is a split ex-date whose ENTIRE bar
    neighborhood (the ex-date bar AND every bar back to the last covered one) was dropped -- a
    degenerate persistent vendor drop; each daily re-heal re-FETCHES, so a transient drop self-
    corrects within the window, and the >1mo bound applies beyond it. Falls back to the nearest
    covered bar AFTER the date when none precedes it; 1.0 if the series has no covered bar."""
    i = bisect.bisect_right(cov_asofs, asof) - 1     # nearest covered with as_of <= asof
    if i >= 0:
        return covered[i][1]
    return covered[0][1] if covered else 1.0         # no earlier covered bar -> nearest after


def _rescale_to_new_scale(bar: dict, new_factor: float, round_dp: int):
    """Force a stored bar onto the new split scale via the as-traded immutability invariant:
    as-traded = close*split_factor is constant, so close_new = close_old * (old_factor /
    new_factor). Price fields scale by that ratio; volume inversely; split_factor becomes
    new_factor. value_tr's split component is rescaled too (its dividend back-adjustment
    stays at the stored vintage -- the documented daily value_tr trade-off; close/value, what
    TA reads, is exact). Returns None if this bar's scale did not change (already correct)."""
    old_factor = float(bar.get("split_factor", 1.0))
    if new_factor <= 0 or old_factor <= 0 or abs(new_factor - old_factor) <= _SPLIT_EPS:
        return None
    scale = old_factor / new_factor
    out = {k: v for k, v in bar.items()
           if k not in ("recorded_on", "series_id", "schema_version", "provisional")}
    for k in _PRICE_FIELDS:
        v = bar.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = round(v * scale, round_dp)
    # VOLUME stays RAW -- deliberately NOT scaled. yfinance auto_adjust=False reports raw
    # (unadjusted) historical volume, identical on every pull (the consumer.py contract:
    # "Volume == raw volume"). Scaling it here would (a) give a rescaled-missing bar a volume
    # inconsistent with its covered (vendor-raw) siblings, and (b) make the bar diverge from
    # the vendor's raw value when the dropped date is later re-included -> a PHANTOM bitemporal
    # restatement (volume is not in archive._NON_VALUE_KEYS). The carried-over stored volume
    # is already the raw value. (value_tr IS rescaled above -- its split component must move so
    # the total-return series has no cliff; a value_tr restatement on re-inclusion is the
    # normal, accepted dividend-cascade behavior, unlike volume which must never restate.)
    out["split_factor"] = round(new_factor, 8)
    return out


def heal(cfg: dict, sids: list[str], *, root=None, catalog=None, recorded_on=None,
         value_tol: float = to_datacore.DEFAULT_VALUE_TOL, require_stamp: bool = False,
         fetch=fetch_prices, push=to_datacore.push, read=None, log=print) -> dict:
    """Coverage-complete full-depth re-pull -> restate the WHOLE stored range to the new
    split scale (zero residual cliff). Returns {series_id: push_result_dict}.

    For each split symbol: re-pull "max" (current scale) trimmed to the stored earliest, then
    for any stored as_of the re-pull OMITTED, re-emit it rescaled onto the new scale via the
    immutability invariant (so a vendor date-drop cannot strand a bar -- the P8b C2 fix). A
    no-op for an empty ``sids`` (the common split-free day). ``fetch``/``push``/``read`` are
    injectable so the synthetic replay test drives this control flow offline.
    """
    if not sids:
        return {}
    if read is None:
        from datacore import archive
        read = archive.read
    arch_root = to_datacore._resolve_root(root)
    cat = catalog if catalog is not None else to_datacore.load_catalog(arch_root)
    round_dp = int(cfg.get("settings", {}).get("round_dp", 6))

    results: dict = {}
    for sid in sids:
        stored = read(sid, root=arch_root)
        raw = fetch(cfg, period="max", only=[sid])     # full inception at the CURRENT scale
        block = raw.get(sid) or {"ok": False, "error": "heal re-pull returned nothing"}
        rescaled_n = 0
        if block.get("ok") and stored:
            earliest = stored[0]["as_of"]
            repull = [r for r in (block.get("records") or []) if r["as_of"] >= earliest]
            repull_asofs = {r["as_of"] for r in repull}
            covered = sorted((r["as_of"], float(r.get("split_factor", 1.0))) for r in repull)
            cov_asofs = [c[0] for c in covered]
            # COVERAGE-COMPLETE (C2): force every stored bar the re-pull omitted onto the new
            # scale, so a vendor date-drop never leaves a residual ~Nx cliff in close/value.
            rescaled = []
            for s in stored:
                if s["as_of"] in repull_asofs:
                    continue
                rb = _rescale_to_new_scale(s, _new_factor_for(s["as_of"], covered, cov_asofs),
                                           round_dp)
                if rb is not None:
                    rescaled.append(rb)
            rescaled_n = len(rescaled)
            block = {**block, "records": repull + rescaled}
        pushed = push({sid: block}, root=arch_root, catalog=cat,
                      value_tol=value_tol, recorded_on=recorded_on, require_stamp=require_stamp)
        results[sid] = pushed[0]
        s = pushed[0]
        log(f"  heal {sid}: restated={s.get('restated', 0)} appended={s.get('appended', 0)} "
            f"rescaled_missing={rescaled_n} skipped={s.get('skipped', 0)} ok={s.get('ok')}"
            + ("" if s.get("ok") else f" SKIP({s.get('skip_reason')})"))
    return results

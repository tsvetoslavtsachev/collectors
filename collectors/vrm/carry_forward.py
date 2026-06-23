"""Carry-forward fill for monthly macro source gaps.

DECISION (Цветослав, Gate-4, 2026-06-23): option = CARRY-FORWARD.

A live FRED source gap leaves holes the frozen Bloomberg paste had filled:
  * an INTERIOR missing month — Oct-2025 BLS release delay -> '.' in U6RATE / CPILFESL
    / CUSR0000SAH1 (jobs + CPI reports both blacked out), and
  * a not-yet-released latest month — May PCE, which lags the rest of the cohort.
One interior NaN nulls ~6 regime months through regime_engine.classify()'s
rolling(3).mean().diff(3) velocity, so the model would lose its CURRENT reading. The
fix: carry the last known value forward into every missing month up to the cohort
frontier (the latest month any cohort series reached), FLAGGING each filled record
(filled='carry_forward', provisional=True). It is deterministic (a rule, not a model
number -> cardinal-rule clean) and SELF-HEALING: when FRED publishes the real value,
full_replace overwrites the fill on the next run.

Scope: the month-END monthly macro/liquidity cohort (the FRED-sourced regime inputs +
liq + computed pce_nowcast) AND the two manual ISM series. Prices (weekly), daily
mkt_*, and the month-START macro_ahe_yoy are untouched.

ISM is special, and the leads/lags asymmetry matters. ISM PMI is an EARLY release
(1st-3rd business day) so it normally LEADS the FRED cohort, and it is hand-entered,
so it can also operationally LAG (a print not yet typed into the slot). Either way ISM
must NOT define how far the regime reaches -- that bound is the FRED release calendar.
So the cohort FRONTIER is computed from the FRED/computed anchor series only; ISM (and
every cohort series) is then carried forward UP TO that frontier when it lags, while
real ISM months BEYOND the frontier (the lead case) are preserved untouched. Without
this, a slot entered only through April would null the May regime: a missing ISM month
-> NaN GROWTH -> rolling(3).diff(3) kills the velocity -> no label that month.
"""
from __future__ import annotations
import calendar


def _month_end(ym: str) -> str:
    y, mo = int(ym[:4]), int(ym[5:7])
    return "{}-{:02d}".format(ym, calendar.monthrange(y, mo)[1])


def _months(ym0: str, ym1: str) -> list:
    """Inclusive 'YYYY-MM' sequence from ym0 to ym1."""
    y, m = int(ym0[:4]), int(ym0[5:7])
    y1, m1 = int(ym1[:4]), int(ym1[5:7])
    out = []
    while (y, m) <= (y1, m1):
        out.append("{:04d}-{:02d}".format(y, m))
        m = m + 1 if m < 12 else 1
        y = y if m != 1 else y + 1
    return out


def _cohort_ids(cfg: dict) -> list:
    """The full month-END monthly cohort that gets carried forward (the FILL set):
    every FRED monthly (non-computed) series + the computed month-end series
    (pce_nowcast) + the two manual ISM series. macro_ahe_yoy (computed, FRED
    month-START) and the daily mkt_* are excluded by construction."""
    ids = [sid for sid, m in cfg["fred"].items()
           if m.get("model_freq", "monthly") == "monthly" and not m.get("computed")]
    ids += list(cfg.get("computed", {}))
    ids += list(cfg.get("manual", {}).get("series", {}))
    return ids


def _anchor_ids(cfg: dict) -> list:
    """The FRONTIER-defining SUBSET = the FRED regime inputs regime_engine reads,
    i.e. the macro release calendar that bounds how far the regime may HONESTLY reach.
    A cohort series carries frontier_anchor:false in config when it completes a month
    EARLIER than the regime macro (liq_tga/liq_anfci on a daily/weekly source ->
    mean_of_month; core_cpi + pce_nowcast on the CPI calendar) -> those are still
    FILLED but must NOT push the frontier, or an early-month run would phantom-extend
    the regime by a carried month (Gate-5 hardening). macro_ahe_yoy (computed, FRED
    month-start) and daily mkt_* are excluded by construction; ISM is excluded too (it
    LEADS on release, so it must not push the frontier either -- see module docstring)."""
    ids = [sid for sid, m in cfg["fred"].items()
           if m.get("model_freq", "monthly") == "monthly"
           and not m.get("computed")
           and m.get("frontier_anchor", True)]
    ids += [sid for sid, m in cfg.get("computed", {}).items()
            if m.get("frontier_anchor", True)]
    return ids


def _fill_ids(cfg: dict) -> list:
    """The carry-forward (fill) set = the whole month-end cohort, DECOUPLED from the
    anchor set (Gate-5 hardening): a frontier_anchor:false series is filled up to the
    frontier but never defines it. ISM is filled (when it lags) but never anchors."""
    return _cohort_ids(cfg)


def carry_forward_macro(raw: dict, cfg: dict) -> list:
    """Fill interior gaps + tail-align the month-end macro cohort by carry-forward.
    Mutates raw in place; each filled record carries filled='carry_forward' +
    provisional=True. Returns [(series_id, [filled month-ends])] for the run report.

    The FRONTIER is the latest month any ANCHOR (FRED/computed) series reached this
    run; ISM does not move it. Each fill series is then carried forward to the
    frontier when it lags, while real months beyond the frontier (ISM's lead case)
    are kept verbatim -- never filled past, never truncated."""
    anchor = [sid for sid in _anchor_ids(cfg)
              if raw.get(sid, {}).get("ok") and raw[sid].get("records")]
    if not anchor:
        return []   # no anchor cohort this run -> nothing to align the fill to
    frontier_ym = max(r["as_of"][:7] for sid in anchor for r in raw[sid]["records"])
    ids = [sid for sid in _fill_ids(cfg)
           if raw.get(sid, {}).get("ok") and raw[sid].get("records")]
    report = []
    for sid in ids:
        by_ym = {r["as_of"][:7]: r for r in raw[sid]["records"]}
        # extend the walk to cover real months beyond the frontier (ISM lead case),
        # but only CARRY (fill) up to the frontier -- never fabricate past it.
        end_ym = max(frontier_ym, max(by_ym))
        out, filled, last = [], [], None
        for ym in _months(min(by_ym), end_ym):
            if ym in by_ym:
                last = by_ym[ym]
                out.append(last)
            elif last is not None and ym <= frontier_ym:   # carry into a hole <= frontier
                out.append({**last, "as_of": _month_end(ym),
                            "filled": "carry_forward", "provisional": True})
                filled.append(_month_end(ym))
        raw[sid]["records"] = out
        if filled:
            report.append((sid, filled))
    return report

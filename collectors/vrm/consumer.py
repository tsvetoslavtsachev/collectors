# -*- coding: utf-8 -*-
"""collectors.vrm.consumer -- base-first canonical MARKET-CONTEXT reader (ATL4 P10).

The READ side of the market-context citizen, and the twin of ``collectors/price/consumer.py``:
consumers read a canonical series FROM data-core instead of each pulling the same FRED ticker
on their own. ONE reader, shared by every consumer -- no per-repo copy.

WHY IT EXISTS (ATL4 P10, Tsvetoslav 2026-09-03). The HY OAS spread and the 10y breakeven flowed
by TWO paths: data-core pulled FRED BAMLH0A0HYM2 / T10YIE into ``mkt_hy_oas`` /
``mkt_breakeven_10y``, and the ETF-rr barometer pulled the SAME two tickers itself. The two
histories were verified identical before the switch (784 and 5919 shared dates, ZERO value
mismatches), so the canon became the single well and the second pull went away.

NOT IN SCOPE, deliberately: ``mkt_vix`` / ``mkt_move``. Those already have ONE well and it runs
the OTHER way -- the barometer publishes them and ``collectors/vrm/fetch_bridge.py`` (FEED 5)
writes the canon. Making the barometer read the canon for them would close a cycle. Their short
history (one snapshot per bridge run, not a series) is a documented property of that bridge, not
a duplication to remove.

ROOT RESOLUTION deliberately IGNORES ``DATACORE_ROOT``. That variable is already spoken for:
the ETF-rr workflow sets it to the PRICE-ARCHIVE checkout, because that is where
``datacore.archive.read`` must resolve its data. Honouring it here would send this reader looking
for ``mkt_hy_oas`` inside the price archive, find nothing, and fall back silently -- exactly the
failure the P10 guard exists to catch. So the root is the checkout that CONTAINS the importable
``datacore`` package (``.data-core`` in CI, the repo itself locally); an explicit ``root`` wins,
and ``DATACORE_BASE_ROOT`` is the dedicated override. For the same reason the canonical file is
read directly rather than through ``datacore.storage``, whose CANONICAL path is bound to
``DATACORE_ROOT`` at import time.

FRESHNESS (Tsvetoslav 2026-09-03, "base-first with a threshold and a red CI"). One well does not
mean a stale well. Measured the same day: the market-context collect runs WEEKLY (Saturdays),
the barometer runs DAILY, and a five-business-day-old value lands the indicator in a different
zone on 11.9% of days (HY) and 9.8% (breakeven). So the canon serves the HISTORY and the
consumer's own pull serves ONLY the tail after the canon's last point -- see
``load_series_base_first``. The threshold now governs how long that tail may get before the well
is declared broken, and the CI guard fails RED on a broken well, an unreachable one, or a
disagreement between the two paths on a shared date (a re-split).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

SRC_BASE = "base"
SRC_BASE_TAIL = "base+tail"
SRC_FETCH = "fetch"
SRC_CONFLICT = "conflict"

# How far the canon may trail the consumer's run before the well is declared BROKEN. The
# market-context collect is WEEKLY (Saturdays), so a tail of up to five business days is the
# NORMAL state, not a fault; ten business days means two missed collects and the guard goes red.
DEFAULT_MAX_TAIL_BDAYS = 10
# Both paths pull the same FRED ticker; anything above float noise on a shared date is a
# definition change, not a rounding difference.
OVERLAP_TOL = 1e-9


def resolve_root(root=None):
    """Explicit ``root`` -> ``DATACORE_BASE_ROOT`` -> the checkout holding ``datacore``.

    ``DATACORE_ROOT`` is NOT consulted: the ETF-rr workflow points it at the price archive."""
    if root is not None:
        return Path(root)
    env = os.environ.get("DATACORE_BASE_ROOT")
    if env:
        return Path(env)
    try:
        import datacore
    except ImportError:
        return None
    return Path(datacore.__file__).resolve().parent.parent


def read_series(series_id, root=None):
    """One canonical series as a ``pd.Series`` (DatetimeIndex -> float), sorted, de-duplicated.

    Returns an EMPTY series when the base is unreachable or the series is absent, so the caller
    falls back instead of raising. Production must not stop because the base is missing."""
    base = resolve_root(root)
    if base is None:
        return pd.Series(dtype="float64")
    path = base / "data" / "canonical" / "{}.json".format(series_id)
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return pd.Series(dtype="float64")
    if not isinstance(records, list) or not records:
        return pd.Series(dtype="float64")
    idx, vals = [], []
    for r in records:
        try:
            vals.append(float(r["value"]))
            idx.append(pd.Timestamp(r["as_of"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not idx:
        return pd.Series(dtype="float64")
    s = pd.Series(vals, index=pd.DatetimeIndex(idx)).sort_index()
    return s[~s.index.duplicated(keep="last")]


def stale_bdays(series, as_of=None):
    """Business days between the series' last point and ``as_of`` (default: today)."""
    if series is None or series.empty:
        return None
    end = pd.Timestamp(as_of).normalize() if as_of is not None else pd.Timestamp.today().normalize()
    last = pd.Timestamp(series.index[-1]).normalize()
    if last >= end:
        return 0
    return int(np.busday_count(last.date(), end.date()))


def load_series_base_first(series_map, *, fetch_fallback, as_of=None, root=None,
                           max_tail_bdays=DEFAULT_MAX_TAIL_BDAYS, overlap_tol=OVERLAP_TOL):
    """Base-first read of several canonical series, with a FRESH TAIL on top (ATL4 P10).

    WHY A TAIL AND NOT A PLAIN FALLBACK. Measured 2026-09-03: data-core's market-context
    collector runs WEEKLY (Saturdays: 08-01, 08-08, 08-15, 08-22, 08-29), while the barometer
    runs DAILY. A plain base-first read would hand the barometer a value up to five business days
    old, and on the last three years of canon a five-business-day-old value puts the indicator in
    a DIFFERENT zone on 11.9% of days for HY and 9.8% for the breakeven -- one day in eight
    showing the wrong zone. So the canon is the well for its whole HISTORY and the consumer's own
    pull supplies ONLY the days AFTER the canon's last point. The duplicated path shrinks from a
    full parallel history to a few-day overlap, and when the collector goes daily the tail shrinks
    to nothing on its own, with no code change here.

    ``series_map``     -- {consumer key: canonical series_id}
    ``fetch_fallback`` -- called as ``fetch_fallback(key)`` -> pd.Series (or None). Injected so
                          this module stays consumer-agnostic and testable without network.

    Returns ``(series_by_key, source_map, detail)``. ``source_map`` values:
      * ``base``        -- the canon already covered everything; nothing was fetched
      * ``base+tail``   -- the canon carried the history, the fetch carried only the tail
      * ``fetch``       -- the canon was unreachable or empty; the OLD path served alone
      * ``conflict``    -- the canon and the fetch DISAGREE on a shared date -> a re-split; the
                          CI guard fails RED on this and the canon is kept (never silently mixed)

    Production NEVER stops: an unreachable base routes the key through the fallback alone.
    """
    series_by_key, source_map, detail = {}, {}, {}
    for key, sid in series_map.items():
        base = read_series(sid, root=root)
        fetched = fetch_fallback(key)
        if fetched is None:
            fetched = pd.Series(dtype="float64")
        fetched = fetched.dropna()

        info = {"series_id": sid, "base_rows": int(len(base)), "tail_rows": 0,
                "base_last": base.index[-1].strftime("%Y-%m-%d") if not base.empty else None,
                "fetch_last": fetched.index[-1].strftime("%Y-%m-%d") if not fetched.empty else None,
                "overlap": 0, "overlap_mismatches": 0, "worst_abs_diff": 0.0,
                "tail_bdays": None, "max_tail_bdays": max_tail_bdays}

        if base.empty:
            series_by_key[key] = fetched
            source_map[key] = SRC_FETCH
            info["why"] = "base empty or unreachable"
            detail[key] = info
            continue

        # ГЕЙТЪТ СРЕЩУ ПОВТОРНО РАЗЦЕПВАНЕ: двата пътя трябва да СЪВПАДАТ там, където се
        # застъпват. Разминаване значи, че някой от тях е сменил определението или тикера --
        # находка, не шум, и НЕ се замазва чрез сливане.
        shared = base.index.intersection(fetched.index)
        if len(shared):
            diff = (base.reindex(shared) - fetched.reindex(shared)).abs()
            info["overlap"] = int(len(shared))
            info["worst_abs_diff"] = float(diff.max())
            info["overlap_mismatches"] = int((diff > overlap_tol).sum())

        if info["overlap_mismatches"]:
            series_by_key[key] = base
            source_map[key] = SRC_CONFLICT
            info["why"] = "canon and fetch disagree on {} of {} shared dates (worst {:.6g})".format(
                info["overlap_mismatches"], info["overlap"], info["worst_abs_diff"])
            detail[key] = info
            continue

        last_base = pd.Timestamp(base.index[-1])
        tail = fetched[fetched.index > last_base]
        info["tail_rows"] = int(len(tail))
        end = pd.Timestamp(as_of).normalize() if as_of is not None else pd.Timestamp.today().normalize()
        info["tail_bdays"] = int(np.busday_count(last_base.date(), end.date()))             if last_base.normalize() < end else 0
        info["why"] = None if info["tail_bdays"] <= max_tail_bdays else             "canon behind by {} business days > {} -- the weekly collect is missing runs".format(
                info["tail_bdays"], max_tail_bdays)

        merged = pd.concat([base, tail]).sort_index() if len(tail) else base
        series_by_key[key] = merged[~merged.index.duplicated(keep="first")]
        source_map[key] = SRC_BASE_TAIL if len(tail) else SRC_BASE
        detail[key] = info
    return series_by_key, source_map, detail

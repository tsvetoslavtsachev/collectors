# -*- coding: utf-8 -*-
"""collectors.price.consumer -- base-first canonical price READER for ETF consumers (INIT-22 P6).

The READ side of the price citizen. Consumers (ETF-rr first, then macro-satellite, then the
stock radars) read split-adjusted OHLCV + total-return close FROM the canonical price-archive
(P2) through the P1 primitive (``datacore.archive.read``) instead of each pulling yfinance and
discarding the raw bars. ONE canonical reader, shared by every consumer -- no per-repo copy.

SHAPES MATCH ETF-rr ``src/prices.py`` EXACTLY so a consumer swaps the fetch for the base read
with zero downstream change:

  * ``read_base_close(tickers, ...)``  -> a flat DataFrame (DatetimeIndex x tickers) of the
    total-return close. Drop-in for ``prices.download_prices`` (auto_adjust=True Close).
  * ``read_base_ohlcv(tickers, ...)``  -> dict{Open,High,Low,Close,Volume}, each a flat
    DataFrame. Drop-in for ``prices.download_ohlcv`` (auto_adjust=True).

TOTAL-RETURN RECONSTRUCTION (why it is bar-for-bar identical to auto_adjust=True). yfinance
``auto_adjust=True`` multiplies EVERY OHLC field by the same per-day ratio (Adj Close / Close)
and leaves Volume raw. The archive stores split-adjusted OHLC (``open/high/low/close``), the
fully-adjusted close (``value_tr`` == Adj Close), real ``volume``, and the split/dividend
ingredients. So:

    factor   = value_tr / close          # the auto_adjust ratio, per day, per symbol
    Close    = value_tr                   # == auto_adjust=True Close
    O/H/L    = open/high/low * factor     # == auto_adjust=True O/H/L
    Volume   = volume                     # auto_adjust never touches volume

BASIS DECISION (Tsvetoslav, 2026-06-26): ``value_tr`` direct -- the field ETF-rr already uses.
It is bar-for-bar identical to today's fetch. ``value_tr`` carries a BOUNDED staleness for bars
older than the daily 1mo window (drifts ~the dividend yield/yr; see collectors/price/config.yaml
+ price-archive/price-daily.yml). The split-adjusted ``close`` is drift-free; a consumer needing
an EXACT total-return series can recompute from close+dividend, or a periodic local
``run --period max`` re-heals value_tr across history. P1 ``_NON_VALUE_KEYS`` stays untouched.
NOTE the two error scales are different: value_tr is RECONSTRUCTIBLE from close+dividend to
~0.03%, but the RANK drift if the re-heal lapses is the *uncorrected* dividend accumulation --
it grows ~the yield/yr and is multi-percentile-point for high-yield ETFs (TLT/SCHD/HYG) whose
12-1 momentum spans a year of un-applied distributions. Hence a re-heal cadence (or a vintage-age
WARN) is a before-ACTIVATION operational guarantee, not optional housekeeping (INIT-22 RIV-2).

P6 MECHANISM = checkout via read PAT (Tsvetoslav, 2026-06-26). The archive data lives in the
PRIVATE price-archive repo; a consumer's CI checks it out (fine-grained read PAT) and points
``DATACORE_ROOT`` at it (local dev: a local price-archive checkout). ``datacore.archive.read``
is read-only (no safe-root guard), so pointing a reader at the archive is safe. Root resolution
order: explicit ``root`` arg -> ``DATACORE_ROOT`` env -> NONE (base unavailable -> the caller's
CLOSED fallback takes over; we NEVER read with root=None, which would resolve to the data-core
base -- where prices do not live -- and silently return empty).

STRANGLER. This module is the base read ONLY. The CLOSED fallback (the consumer's OLD yfinance
fetch) is injected into ``load_ohlcv_base_first`` so production never stops when a symbol is
missing from the archive or the archive is not checked out. Every symbol is stamped with its
provenance (``base`` / ``fetch`` / ``unmapped``) so the consumer can write a source map and the
``assert_base_sourced`` guard can fail RED on any symbol that did not come from the base.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

# Source provenance tags stamped per symbol.
SRC_BASE = "base"          # served from the canonical archive
SRC_FETCH = "fetch"        # CLOSED fallback to the old yfinance pull
SRC_UNMAPPED = "unmapped"  # ticker has no px_* series (not in the price universe)

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


@lru_cache(maxsize=1)
def symbol_to_series() -> dict[str, str]:
    """{TICKER (upper) -> px_<key>_daily} from config.yaml's ``price`` block.

    The config is the SAME authoritative universe the citizen writes (one source of truth);
    we invert it rather than hard-coding ``px_{ticker}_daily`` so a future rename or an
    irregular id (e.g. DX-Y.NYB -> px_dxy_daily) stays correct automatically.
    """
    data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    price = data.get("price", {}) or {}
    out: dict[str, str] = {}
    for series_id, meta in price.items():
        sym = (meta or {}).get("symbol")
        if sym:
            out[str(sym).upper()] = series_id
    return out


def resolve_root(root=None):
    """Explicit ``root`` -> ``DATACORE_ROOT`` env -> None. Returns a str path or None.

    None means the archive is NOT reachable -> the caller must fall back. We deliberately do
    NOT let it default to the data-core base (``datacore.archive.read`` would, via _root): the
    data-core base holds no prices, so that would be a silent all-empty read."""
    if root is not None:
        return str(root)
    return os.environ.get("DATACORE_ROOT") or None


# --------------------------------------------------------------------------- #
# Period / window
# --------------------------------------------------------------------------- #
def _period_start(period, end_ts):
    """Map a yfinance-style ``period`` ('2y','3y','6mo','30d','max') to a start Timestamp
    (or None for 'max'/unknown). ``end_ts`` is the right edge (latest bar or explicit end)."""
    if not period or period == "max":
        return None
    p = str(period).strip().lower()
    try:
        if p.endswith("mo"):
            return end_ts - pd.DateOffset(months=int(p[:-2]))
        if p.endswith("y"):
            return end_ts - pd.DateOffset(years=int(p[:-1]))
        if p.endswith("d"):
            return end_ts - pd.Timedelta(days=int(p[:-1]))
    except ValueError:
        return None
    return None


# --------------------------------------------------------------------------- #
# Core read
# --------------------------------------------------------------------------- #
def _read_one(archive, series_id, root, as_of_vintage):
    """Read one px_* series from the archive -> a per-field dict of pandas Series indexed by a
    normalized DatetimeIndex, total-return reconstructed. Empty dict if the series has no bars.

    Returns {"Open":S,"High":S,"Low":S,"Close":S,"Volume":S}. ``Close`` == value_tr;
    O/H/L scaled by the per-day auto_adjust ratio (value_tr/close); Volume is the raw volume.
    The ``archive`` module is passed in so the import (and its failure -> fallback) is handled
    ONCE by the caller, not per series."""
    recs = archive.read(series_id, root=root, as_of_vintage=as_of_vintage)
    if not recs:
        return {}
    idx, o, h, l, c, v = [], [], [], [], [], []
    for r in recs:
        close = r.get("close", r.get("value"))
        vtr = r.get("value_tr", close)
        if close is None or vtr is None:
            continue
        # Drop a non-positive close (a corrupt/non-price bar): the auto_adjust ratio is undefined
        # and an emitted O/H/L-vs-Close would be internally inconsistent (matches fetch_prices +
        # the None branch above). A real price bar is always > 0.
        if not (isinstance(close, (int, float)) and close > 0):
            continue
        factor = vtr / close  # the per-day auto_adjust ratio (Adj Close / Close)
        idx.append(r["as_of"])
        c.append(float(vtr))
        o.append(float(r["open"]) * factor if r.get("open") is not None else float("nan"))
        h.append(float(r["high"]) * factor if r.get("high") is not None else float("nan"))
        l.append(float(r["low"]) * factor if r.get("low") is not None else float("nan"))
        v.append(float(r["volume"]) if r.get("volume") is not None else float("nan"))
    if not idx:
        return {}
    di = pd.to_datetime(idx).normalize()
    return {
        "Open": pd.Series(o, index=di),
        "High": pd.Series(h, index=di),
        "Low": pd.Series(l, index=di),
        "Close": pd.Series(c, index=di),
        "Volume": pd.Series(v, index=di),
    }


_OHLCV_FIELDS = ("Open", "High", "Low", "Close", "Volume")


def read_base_ohlcv(tickers, *, root=None, period="2y", start=None, end=None,
                    as_of_vintage=None):
    """Read OHLCV for ``tickers`` from the canonical archive. PURE base read (no fallback).

    Returns ``(ohlcv, source_map)`` where:
      * ``ohlcv``      = dict{field -> flat DataFrame (DatetimeIndex x tickers)}, total-return
                         reconstructed (see module docstring). Columns ONLY for served symbols.
      * ``source_map`` = {ticker -> SRC_BASE | SRC_UNMAPPED}. A mapped-but-empty series is
                         OMITTED from source_map here (it is neither base-served nor unmapped);
                         the orchestrator treats any ticker absent from ``base`` columns as a
                         fallback candidate. ``SRC_UNMAPPED`` is reported so a caller can tell a
                         no-px-series ticker (e.g. ^VIX) from a transient archive miss.

    If the archive root cannot be resolved, returns empty frames + an empty source_map (every
    ticker becomes a fallback candidate) -- never a root=None read against the data-core base.
    """
    eff_root = resolve_root(root)
    cols: dict[str, dict[str, pd.Series]] = {f: {} for f in _OHLCV_FIELDS}
    source_map: dict[str, str] = {}

    if eff_root is None:
        return ({f: pd.DataFrame() for f in _OHLCV_FIELDS}, source_map)
    try:
        from datacore import archive  # data-core on PYTHONPATH
    except ImportError:
        # The reader code is not importable (no data-core checkout) -> the archive is
        # unreachable. Return empty so the caller's CLOSED fallback takes over the WHOLE
        # universe -- degrade, never crash (the strangler promise: production never stops).
        return ({f: pd.DataFrame() for f in _OHLCV_FIELDS}, source_map)

    sym_map = symbol_to_series()
    # Right edge for the period window: explicit end, else "today" (read returns to latest).
    end_ts = pd.Timestamp(end).normalize() if end is not None else pd.Timestamp.today().normalize()
    if start is not None:
        start_ts = pd.Timestamp(start).normalize()
    else:
        start_ts = _period_start(period, end_ts)

    for t in tickers:
        sid = sym_map.get(str(t).upper())
        if sid is None:
            source_map[t] = SRC_UNMAPPED
            continue
        fields = _read_one(archive, sid, eff_root, as_of_vintage)
        if not fields:
            continue  # mapped but empty -> fallback candidate (left out of source_map)
        clipped = {}
        for f in _OHLCV_FIELDS:
            s = fields[f]
            if start_ts is not None:
                s = s[s.index >= start_ts]
            if end is not None:
                s = s[s.index <= end_ts]
            clipped[f] = s
        # Base-served ONLY if the requested window actually contains bars. A series that stopped
        # updating before the window (a long P5 gap) clips to EMPTY here; stamping it SRC_BASE
        # would feed an all-NaN column (silently dropped from the ranks) AND suppress the
        # fallback. Leave it a fallback candidate instead (same as the mapped-but-empty branch).
        if clipped["Close"].dropna().empty:
            continue
        for f in _OHLCV_FIELDS:
            cols[f][t] = clipped[f]
        source_map[t] = SRC_BASE

    ohlcv = {f: (pd.DataFrame(cols[f]).sort_index() if cols[f] else pd.DataFrame())
             for f in _OHLCV_FIELDS}
    return ohlcv, source_map


def read_base_close(tickers, *, root=None, period="2y", start=None, end=None,
                    as_of_vintage=None):
    """Total-return Close only. ``(close_df, source_map)``; drop-in for download_prices."""
    ohlcv, source_map = read_base_ohlcv(
        tickers, root=root, period=period, start=start, end=end, as_of_vintage=as_of_vintage)
    return ohlcv.get("Close", pd.DataFrame()), source_map


# --------------------------------------------------------------------------- #
# Strangler orchestrator: base-first + CLOSED fallback
# --------------------------------------------------------------------------- #
def _merge_field(base_df, fb_df):
    """Column-union two flat field frames (base wins on a collision -- never happens since the
    two sets are disjoint by construction), outer-aligned on the date index, sorted."""
    if base_df is None or base_df.empty:
        return fb_df.sort_index() if (fb_df is not None and not fb_df.empty) else pd.DataFrame()
    if fb_df is None or fb_df.empty:
        return base_df.sort_index()
    add = [c for c in fb_df.columns if c not in base_df.columns]
    merged = pd.concat([base_df, fb_df[add]], axis=1) if add else base_df
    return merged.sort_index()


def load_ohlcv_base_first(tickers, *, fetch_fallback, root=None, period="2y",
                          start=None, end=None):
    """Base-first OHLCV with a CLOSED fallback to the consumer's OLD fetch (strangler).

    ``fetch_fallback`` is the consumer's existing downloader, called as
    ``fetch_fallback(missing_tickers, period=period)`` and expected to return a
    dict{field -> flat DataFrame} (i.e. ETF-rr ``src.prices.download_ohlcv``). Injected so this
    module stays consumer-agnostic and unit-testable without yfinance.

    Returns ``(ohlcv, source_map)`` covering EVERY requested ticker:
      * served from the archive            -> SRC_BASE
      * missing/unmapped/no-root, fetched  -> SRC_FETCH
      * fetched but the fallback also had no data -> left out of both frames + source_map
        (a genuinely dead symbol; the consumer's own empty-frame health check still fires).

    Production NEVER stops: an unreachable archive (no root / no checkout) routes the WHOLE
    universe through the fallback -- exactly the pre-cutover behavior.
    """
    base_ohlcv, source_map = read_base_ohlcv(
        tickers, root=root, period=period, start=start, end=end)

    served = {t for t, s in source_map.items() if s == SRC_BASE}
    missing = [t for t in tickers if t not in served]

    if not missing:
        return base_ohlcv, source_map

    fb = fetch_fallback(missing, period=period) or {}
    ohlcv = {}
    for f in _OHLCV_FIELDS:
        ohlcv[f] = _merge_field(base_ohlcv.get(f, pd.DataFrame()), fb.get(f, pd.DataFrame()))

    # Stamp provenance for the fallback set: SRC_FETCH only where the fallback actually returned
    # the column (otherwise the symbol is genuinely dead -- recorded in neither frame).
    fb_close = fb.get("Close", pd.DataFrame())
    fb_cols = set(fb_close.columns) if not fb_close.empty else set()
    for t in missing:
        if t in fb_cols:
            source_map[t] = SRC_FETCH
        else:
            source_map.pop(t, None)
    return ohlcv, source_map

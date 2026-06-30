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

TOTAL-RETURN RECONSTRUCTION (bar-for-bar identical to auto_adjust=True, AND drift-proof -- INIT-22
RIV-2 capstone). yfinance ``auto_adjust=True`` multiplies EVERY OHLC field by the same per-day ratio
(Adj Close / Close) and leaves Volume raw. The archive stores split-adjusted OHLC
(``open/high/low/close``), the fully-adjusted close (``value_tr`` == Adj Close at the bar's FREEZE
vintage), real ``volume``, and the split/dividend ingredients. So:

    factor   = Close / close              # the auto_adjust ratio, per day, per symbol
    Close    = total-return close          # see _total_return_close -- == auto_adjust=True Close
    O/H/L    = open/high/low * factor      # == auto_adjust=True O/H/L
    Volume   = volume                      # auto_adjust never touches volume

DRIFT-PROOF BASIS (Tsvetoslav sign-off 2026-06-30; supersedes the 2026-06-26 value_tr-direct basis).
The stored ``value_tr`` goes STALE: the daily 1mo re-heal window never re-touches a bar older than
~1 month, so a frozen bar never absorbs dividends paid AFTER it froze and its total-return level
drifts ~the dividend yield/yr -- VERIFIED 2.5-5.5% on the 12-1 momentum denominator for high-yield
names (HYG 5.5%, TLT 4.2%, SCHD 3.7%), enough to swap adjacent ranks. So ``Close`` is no longer
``value_tr`` direct; ``_total_return_close`` REBUILDS it from the split-adjusted ``close`` + the
FORWARD ``dividend`` stream:

    tr[t] = close[t] * PROD_{ex-date i > t} (1 - div[i]/close[i-1])

which is window-self-contained (depends only on dividends AFTER a bar, each stored on its own fresh
ex-date bar) -> drift-proof BY CONSTRUCTION and == auto_adjust=True to ~1e-6, with NO re-heal cadence.
The split-adjusted ``close`` is itself drift-free (split_heal keeps it current); ``close`` and the
stored ``dividend`` share one split space (verified vs live yfinance on multi-split names). PRICE-ONLY
EXCEPTION: Yahoo's adjclose for some venues (London ``.L`` names) IGNORES dividends -- value_tr there
is price-only, does NOT drift, and the dividend formula would inject an adjustment Yahoo never applies
(silently changing ranks). ``_is_dividend_adjusted`` detects this PER SERIES (a drift-immune probe)
and keeps value_tr for the price-only class. UNFAITHFUL EXCEPTION: a curated set of continental-EU
names (``_UNFAITHFUL_SERIES``) where Yahoo's HISTORICAL ex-date factor differs from the textbook
``1-div/close_prev`` (so recon would regress vs the bar-for-bar-correct value_tr) is pinned to
value_tr; a fresh-cohort belt (``_belt_diverges``) backstops gross/unpinned cases. P1
``_NON_VALUE_KEYS`` stays untouched -- this is a READER change only; nothing is rewritten in the
archive.

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

CURRENCY NORMALIZATION (INIT-22 P8c, ``normalize_currency=``). The archive stores prices RAW per
decision 4a: a London ``.L`` name quoted in GBX (pence) sits in the archive as pence (HSBA.L
~5000), 100x its GBP value -- ``/100 -> GBP`` is a CONSUMER step, deliberately NOT baked into the
archive (same spirit as split_factor; the archive stays the single raw source). The readers below
take an OPT-IN ``normalize_currency`` flag (default False -> raw, byte-identical to today's ETF
consumers and to yfinance ``auto_adjust=True`` which ALSO returns pence for a .L name). With it
True, every price field (Open/High/Low/Close) of a ``quote_basis=="GBX"`` series is divided by 100
to its major currency unit (EUR/USD/CHF/... are untouched; VOLUME is a share count, never a price,
so never divided). CRITICAL placement: normalization is applied to the FULL, already-MERGED frame
(base + CLOSED fallback) -- never inside the raw base read -- because the strangler fallback
(yfinance) returns the SAME raw pence for a .L name, so normalizing only the base would leave
base-served and fetch-served bars in MIXED units in one frame (a silent, provenance-dependent
100x). A STOXX/multi-currency consumer (P9) passes ``normalize_currency=True``; forgetting it is
the documented footgun this step exists to make explicit.
"""
from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

# Source provenance tags stamped per symbol.
SRC_BASE = "base"          # served from the canonical archive
SRC_FETCH = "fetch"        # CLOSED fallback to the old yfinance pull
SRC_UNMAPPED = "unmapped"  # ticker has no px_* series (not in the price universe)

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

# quote_basis -> divisor that converts a RAW stored price to its major CURRENCY unit (P8c).
# GBX (London pence) -> 100 -> GBP is the only minor-unit basis in the universe today; a future
# minor-unit venue (e.g. ZAc South African cents) is a one-line addition here, not a scattered
# special-case. A major-unit basis (EUR/USD/CHF/SEK/...) or an absent basis -> 1.0 (untouched).
_BASIS_DIVISOR = {"GBX": 100.0}

# Price fields normalized by the currency divisor. VOLUME is a SHARE COUNT, never a price -> it is
# deliberately absent here (a GBX->GBP /100 must never touch volume).
_PRICE_FIELDS_FX = ("Open", "High", "Low", "Close")


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


@lru_cache(maxsize=1)
def quote_basis_map() -> dict[str, str]:
    """{TICKER (upper) -> quote_basis} from config.yaml's ``price`` block (P8c).

    The SAME authoritative universe the citizen writes (one source of truth), inverted by
    SYMBOL so a consumer can normalize a raw GBX series to GBP without re-reading the catalog
    (the catalog carries the same currency/quote_basis, but the consumer already reads config
    for symbol_to_series -- one file, one parse). A series with no quote_basis (ETF / SP500
    single-currency USD) is simply absent -> the divisor defaults to 1.0 (major units)."""
    data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    price = data.get("price", {}) or {}
    out: dict[str, str] = {}
    for meta in price.values():
        sym = (meta or {}).get("symbol")
        qb = (meta or {}).get("quote_basis")
        if sym and qb:
            out[str(sym).upper()] = str(qb)
    return out


def quote_basis_divisor(quote_basis) -> float:
    """Divisor converting a raw ``quote_basis`` price to its major currency unit (P8c).

    GBX (London pence) -> 100.0 (-> GBP). A major-unit basis (EUR/USD/CHF/SEK/...) or an
    unknown/missing basis -> 1.0 (untouched)."""
    return _BASIS_DIVISOR.get((quote_basis or "").upper(), 1.0)


def normalize_to_currency(ohlcv):
    """Convert raw quote-basis price fields to each series' major CURRENCY unit, per ticker (P8c).

    For every column (ticker) of every PRICE field (Open/High/Low/Close), divide by the ticker's
    quote_basis divisor: GBX -> /100 (-> GBP); EUR/USD/CHF/... -> /1 (untouched). VOLUME is a share
    count -> never divided. A ticker absent from the quote_basis map (ETF, SP500 USD, ^VIX) -> 1.0.

    Apply this to the FULL, already-MERGED OHLCV (base + CLOSED fallback) -- NOT inside the raw base
    read -- so base-served and yfinance-fallback bars (both raw pence for a .L name) end up in the
    SAME unit. Non-mutating: returns a new dict; the caller's frames are untouched."""
    qb = quote_basis_map()
    out = {}
    for field, df in ohlcv.items():
        if df is None or df.empty or field not in _PRICE_FIELDS_FX:
            out[field] = df            # Volume + empty frames pass through unscaled
            continue
        scaled = df.copy()
        for col in scaled.columns:
            div = quote_basis_divisor(qb.get(str(col).upper()))
            if div != 1.0:
                scaled[col] = scaled[col] / div
        out[field] = scaled
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
# Total-return reconstruction (INIT-22 RIV-2 capstone) -- see module docstring
# --------------------------------------------------------------------------- #
# Drift-proof, self-calibrating total-return Close. Threshold biased toward the SAFE value_tr
# fallback (Tsvetoslav sign-off 2026-06-30: 0.7) -- the dangerous mis-class is price-only -> blind
# reconstruction (would inject dividends Yahoo never applies); the harmless one is the reverse.
_TR_DISCRIMINATOR_THRESHOLD = 0.7   # score >= -> Yahoo dividend-adjusts (reconstruct); < -> value_tr
_TR_MIN_DIV_FRACTION = 0.001        # an ex-date's div/close must exceed this to be a clean probe
_TR_BELT_FRESH_DAYS = 60            # belt: bars recorded within N days of the latest recorded_on are
                                    # "fresh" (value_tr == live yfinance there) -> recon must match
_TR_BELT_TOL = 0.02                 # >2% recon-vs-value_tr on the fresh cohort -> broken/unfaithful

# Series where the TEXTBOOK dividend reconstruction is NOT faithful to Yahoo's own historical
# adjusted close, so the stored value_tr (== live yfinance auto_adjust=True) is kept instead.
# MECHANISM: Yahoo's per-ex-date back-adjustment factor for these names differs from the textbook
# (1 - div/close_prev) -- a Yahoo data convention that varies by venue/era. Verified vs live
# yfinance: recon diverges up to ~8.7% (EQNR.OL) while value_tr matches yfinance to ~0%. The
# single-ex-date discriminator CANNOT catch them (their MOST-RECENT ex-date matches textbook; the
# error lives in OLDER ex-dates), so they are pinned here. ALL are continental-European / cross-
# listed names -> NONE are in the US consumers' universes (ETF-rr, macro-satellite,
# SP500-rotationradar); only stoxx600 reads them.
# REGENERATE (collectors/price/tests fixtures or an offline sweep): a series is unfaithful iff
# recon-vs-live-yfinance > 0.3% AND value_tr-vs-live-yfinance < 0.1% (value_tr is the correct one;
# this excludes US ETFs whose recon merely HEALS a transiently stale archive tip). 14 names as of
# 2026-06-30; re-run after large universe additions.
_UNFAITHFUL_SERIES = frozenset({
    "px_eqnr_ol_daily", "px_sren_sw_daily", "px_ubsg_sw_daily", "px_eng_mc_daily",
    "px_shell_as_daily", "px_ten_mi_daily", "px_cpg_l_daily", "px_ihg_l_daily",
    "px_qia_de_daily", "px_tte_pa_daily", "px_mt_as_daily", "px_grf_mc_daily",
    "px_stmmi_mi_daily", "px_fntn_de_daily",
})


def _is_dividend_adjusted(close, vtr, div) -> bool:
    """Drift-immune per-series probe: does Yahoo back-adjust this series for dividends?

    On the MOST-RECENT ex-date i carrying a meaningful dividend, measure how value_tr moved across
    the ex-date relative to price, and compare to the dividend's own factor:

        measured = (vtr[i-1]/close[i-1]) / (vtr[i]/close[i])   # ~theo if the div was added back
        theo     = 1 - div[i]/close[i-1]                        # the dividend back-adjust factor
        score    = (measured - 1) / (theo - 1)                  # ~1.0 div-adjusted, ~0.0 price-only

    DRIFT-IMMUNE: the most-recent ex-date has NO later dividend inside its freeze window, so the
    adjacent-bar (i-1, i) value_tr ratio isolates exactly this event's factor regardless of how
    stale the shared future-dividend tail is (the two adjacent bars share a freeze vintage). Verified
    score 1.000 (dividend-adjusted: US ETFs/stocks, continental EU) vs 0.010 (London price-only),
    identical fresh and stale.

    Returns False when there is no usable ex-date (a no-dividend series, where the reconstruction
    equals value_tr anyway, so keeping value_tr loses nothing) -- the safe default.
    """
    n = len(close)
    for i in range(n - 1, 0, -1):
        d, cp, ci = div[i], close[i - 1], close[i]
        if not (d and d > 0 and cp and cp > 0 and ci and ci > 0):
            continue
        vp, vi = vtr[i - 1], vtr[i]
        if not (vp and vp > 0 and vi and vi > 0):
            continue
        if d / cp < _TR_MIN_DIV_FRACTION:        # too small to discriminate cleanly -> older ex-date
            continue
        theo = 1.0 - d / cp
        if abs(theo - 1.0) < 1e-12:
            continue
        measured = (vp / cp) / (vi / ci)
        score = (measured - 1.0) / (theo - 1.0)
        return score >= _TR_DISCRIMINATOR_THRESHOLD
    return False


def _belt_diverges(recon, vtr, recorded_on) -> bool:
    """Best-effort backstop: on the FRESH cohort (bars whose recorded_on is within _TR_BELT_FRESH_DAYS
    of the latest recorded_on -- where value_tr == live yfinance), the reconstruction must track
    value_tr. A gap > _TR_BELT_TOL there means broken dividend data or an unfaithful series not yet
    pinned in _UNFAITHFUL_SERIES -> fall back to value_tr.

    NOT a complete newcomer-catcher: a series whose textbook error lives only in OLD ex-dates looks
    fine on the fresh cohort (that is exactly why _UNFAITHFUL_SERIES is a curated list refreshed by an
    offline faithfulness sweep). This catches gross / recent divergence only. The 2% tol is deliberately
    above the ~0.4% a legitimate drift correction (FAN-style transiently-stale archive tip) shows, so
    the belt never undoes a real correction. Disabled (False) when recorded_on is unavailable."""
    if not recorded_on or len(recorded_on) != len(recon):
        return False
    parsed = []
    for ro in recorded_on:
        try:
            parsed.append(date.fromisoformat(ro) if ro else None)
        except (ValueError, TypeError):
            parsed.append(None)
    valid = [p for p in parsed if p is not None]
    if not valid:
        return False
    latest = max(valid)
    for j in range(len(recon)):
        p = parsed[j]
        if p is None or (latest - p).days > _TR_BELT_FRESH_DAYS:
            continue
        b = vtr[j]
        if b and b > 0 and abs(recon[j] - b) / abs(b) > _TR_BELT_TOL:
            return True
    return False


def _total_return_close(close, vtr, div, *, series_id=None, recorded_on=None) -> list:
    """Drift-proof total-return Close for one series (chronological close/value_tr/dividend lists).

    Three safety layers decide between the dividend reconstruction and the stored value_tr:
      1. PRICE-ONLY / no usable dividend (Yahoo's adjclose ignores dividends -- London .L) ->
         keep value_tr (_is_dividend_adjusted is False; no drift exists for a price-only series).
      2. KNOWN-UNFAITHFUL series (_UNFAITHFUL_SERIES: Yahoo's historical factor != textbook for a
         curated set of continental-EU names) -> keep value_tr (== live yfinance; recon would be a
         regression). The single-ex-date discriminator cannot see these, hence the pinned list.
      3. FRESH-COHORT belt (_belt_diverges): a gross recon-vs-value_tr gap on recently-recorded bars
         -> keep value_tr (broken data / an unpinned unfaithful newcomer).
    Otherwise rebuild from close + the FORWARD dividend stream
        tr[t] = close[t] * PROD_{ex-date i > t} (1 - div[i]/close[i-1])
    which never reads the stale value_tr level -> drift-proof, == auto_adjust=True to ~1e-6.

    ``series_id`` and ``recorded_on`` are optional so the function stays unit-testable with bare
    arrays; _read_one always supplies both.
    """
    n = len(close)
    recon = [0.0] * n
    cum = 1.0
    for k in range(n - 1, -1, -1):
        recon[k] = close[k] * cum
        d = div[k]
        if d and d > 0 and k - 1 >= 0 and close[k - 1] and close[k - 1] > 0:
            cum *= (1.0 - d / close[k - 1])
    if not _is_dividend_adjusted(close, vtr, div):
        return list(vtr)                          # 1. price-only (London .L) / no usable dividends
    if series_id in _UNFAITHFUL_SERIES:
        return list(vtr)                          # 2. Yahoo's factor != textbook for this name
    if _belt_diverges(recon, vtr, recorded_on):
        return list(vtr)                          # 3. broken / unfaithful on the fresh cohort
    return recon


# --------------------------------------------------------------------------- #
# Core read
# --------------------------------------------------------------------------- #
def _read_one(archive, series_id, root, as_of_vintage):
    """Read one px_* series from the archive -> a per-field dict of pandas Series indexed by a
    normalized DatetimeIndex, total-return reconstructed (INIT-22 RIV-2). Empty dict if the series
    has no usable bars.

    Returns {"Open":S,"High":S,"Low":S,"Close":S,"Volume":S}. ``Close`` is the DRIFT-PROOF
    total-return close (``_total_return_close``: dividend reconstruction for Yahoo-dividend-adjusted
    series, value_tr passthrough for the price-only class); O/H/L scaled by the per-day auto_adjust
    ratio (Close/close); Volume is the raw volume. The ``archive`` module is passed in so the import
    (and its failure -> fallback) is handled ONCE by the caller, not per series."""
    recs = archive.read(series_id, root=root, as_of_vintage=as_of_vintage)
    if not recs:
        return {}
    idx, cl, vt, dv, ro, o, h, l, v = [], [], [], [], [], [], [], [], []
    for r in recs:
        close = r.get("close", r.get("value"))
        # Drop a non-positive/absent close (a corrupt/non-price bar): the auto_adjust ratio is
        # undefined and an emitted O/H/L-vs-Close would be internally inconsistent (matches
        # fetch_prices). A real price bar is always > 0.
        if not (isinstance(close, (int, float)) and not isinstance(close, bool) and close > 0):
            continue
        vtr = r.get("value_tr")
        vtr = float(vtr) if isinstance(vtr, (int, float)) and not isinstance(vtr, bool) else float(close)
        d = r.get("dividend")
        idx.append(r["as_of"])
        cl.append(float(close))
        vt.append(vtr)
        dv.append(float(d) if isinstance(d, (int, float)) and not isinstance(d, bool) else 0.0)
        ro.append(r.get("recorded_on"))
        o.append(float(r["open"]) if r.get("open") is not None else float("nan"))
        h.append(float(r["high"]) if r.get("high") is not None else float("nan"))
        l.append(float(r["low"]) if r.get("low") is not None else float("nan"))
        v.append(float(r["volume"]) if r.get("volume") is not None else float("nan"))
    if not idx:
        return {}
    # archive.read returns records sorted by as_of -> cl/vt/dv/ro are chronological, exactly as the
    # FORWARD dividend reconstruction, the most-recent-ex-date probe, and the fresh-cohort belt require.
    tr = _total_return_close(cl, vt, dv, series_id=series_id, recorded_on=ro)
    c_out, o_out, h_out, l_out = [], [], [], []
    for k in range(len(idx)):
        factor = tr[k] / cl[k]            # cl[k] > 0 (filtered) and tr[k] finite -> factor finite
        c_out.append(tr[k])
        o_out.append(o[k] * factor if o[k] == o[k] else float("nan"))   # o[k]==o[k] => not NaN
        h_out.append(h[k] * factor if h[k] == h[k] else float("nan"))
        l_out.append(l[k] * factor if l[k] == l[k] else float("nan"))
    di = pd.to_datetime(idx).normalize()
    return {
        "Open": pd.Series(o_out, index=di),
        "High": pd.Series(h_out, index=di),
        "Low": pd.Series(l_out, index=di),
        "Close": pd.Series(c_out, index=di),
        "Volume": pd.Series(v, index=di),
    }


_OHLCV_FIELDS = ("Open", "High", "Low", "Close", "Volume")


def read_base_ohlcv(tickers, *, root=None, period="2y", start=None, end=None,
                    as_of_vintage=None, normalize_currency=False):
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

    ``normalize_currency`` (P8c, default False -> raw): when True, GBX (pence) price fields are
    /100 to GBP (see ``normalize_to_currency``). On the PURE base read this is unit-consistent (no
    fallback bars to mix); ``load_ohlcv_base_first`` normalizes AFTER the merge instead.
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
    if normalize_currency:
        ohlcv = normalize_to_currency(ohlcv)
    return ohlcv, source_map


def read_base_close(tickers, *, root=None, period="2y", start=None, end=None,
                    as_of_vintage=None, normalize_currency=False):
    """Total-return Close only. ``(close_df, source_map)``; drop-in for download_prices.

    ``normalize_currency`` (P8c) is passed through -> a GBX Close is /100 to GBP when True."""
    ohlcv, source_map = read_base_ohlcv(
        tickers, root=root, period=period, start=start, end=end, as_of_vintage=as_of_vintage,
        normalize_currency=normalize_currency)
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
                          start=None, end=None, normalize_currency=False):
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

    ``normalize_currency`` (P8c, default False): when True, GBX (pence) price fields are /100 to
    GBP AFTER the base+fallback merge -- uniform across provenance, so a base-served and a
    fetch-served .L name never end up in mixed units (the whole reason it is applied here, not in
    the raw base read).
    """
    base_ohlcv, source_map = read_base_ohlcv(
        tickers, root=root, period=period, start=start, end=end)   # raw base; normalize AFTER merge

    served = {t for t, s in source_map.items() if s == SRC_BASE}
    missing = [t for t in tickers if t not in served]

    if not missing:
        return (normalize_to_currency(base_ohlcv) if normalize_currency else base_ohlcv), source_map

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
    # Normalize the WHOLE merged frame (base + fetch both raw pence for a .L name) in one pass.
    if normalize_currency:
        ohlcv = normalize_to_currency(ohlcv)
    return ohlcv, source_map

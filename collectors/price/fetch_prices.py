"""FEED -- yfinance DAILY OHLCV for the ETF universe (INIT-22 P3).

ONE pull per symbol per day: ``yf.download(interval="1d", auto_adjust=False,
actions=True)`` -> a full daily bar PLUS the split/dividend events. Each record is
the P1 archive record shape (mirrors price-archive/scripts/gate.py::_bar):

    value         = Close       (split-adjusted close; headline == close)
    open/high/low = Open/High/Low (split-adjusted OHLC, auto_adjust=False)
    close         = Close
    value_tr      = Adj Close    (fully adjusted: split + dividend)
    volume        = Volume       (int)
    split_factor  = cumulative split factor on this row, so the genuinely immutable
                    AS-TRADED price = close * split_factor  (program S2/Decision d)
    dividend      = cash dividend on the ex-date, else 0.0
    source        = "yfinance"
    provisional   = True ONLY on the tip (latest) bar -> P1 freezes it next run

WHY split_factor (the honest-immutability mechanism, program R5). yfinance
``auto_adjust=False`` "Close" is STILL split-adjusted: every pre-split historical
close is silently rewritten by the split ratio on the next re-pull. There is no
as-traded close column from yfinance in either mode. So we store the cumulative
split factor per row -- a reverse-cumulative-product of the "Stock Splits" ratios
that occur STRICTLY AFTER that row's date -- and as-traded = close * split_factor is
reconstructible and genuinely immutable. On a future split, BOTH close and
split_factor move on re-pull while as-traded stays put; the P1 value-conflict check
turns that move into an auditable bitemporal restatement (new recorded_on), never a
silent overwrite.

Per-symbol isolation: a dead/empty symbol -> that series marked not-ok (never a
silent zero, never aborts the run) -- the oil/VRM fetcher contract.

NETWORK STEP -- exercised in Gate 2 (live fetch + spot-check + split_factor on a
known split). Gate 1 proves the wiring offline via run.py --mock (mockdata.py
supplies this same record shape).

Cardinal rule: this deterministic path writes the numbers, never a model.
"""
from __future__ import annotations


def _flatten(df):
    """yf.download for a single ticker can return MultiIndex (field, ticker) columns;
    squeeze to flat field columns so df["Close"] is a Series, not a 1-col frame."""
    import pandas as pd
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


def _dedup_index(df):
    """Collapse duplicate index dates (a known yfinance quirk on flaky pulls).

    Without this, _split_factors visits a duplicated date TWICE and double-folds its
    split ratio (pre-split factor 10*10 instead of 10 -> as-traded wrong for the whole
    pre-split history), AND fetch_one emits two records with the same as_of (P1's
    dup-as_of guard then refuses the batch and the symbol is skipped for the day).

    Keep the LAST OHLCV per date (matches yfinance's own convention) but take the MAX of
    the corporate-action columns, so a split/dividend/cap-gain sitting on EITHER
    duplicate row is preserved (a split can land on either row; max keeps it, since the
    non-event value is 0). Guarded by has_duplicates so the normal path is untouched.
    """
    if not df.index.has_duplicates:
        return df
    event_cols = {"Stock Splits", "Dividends", "Capital Gains"}
    agg = {c: ("max" if c in event_cols else "last") for c in df.columns}
    return df.groupby(level=0).agg(agg).sort_index()


def _split_factors(index, splits) -> dict:
    """Reverse cumulative-product of split ratios -> {date: cumulative factor}.

    split_factor[d] reflects every split whose ex-date is STRICTLY AFTER d, so that
    as-traded(d) = close(d) * split_factor[d]. On the ex-date itself the price is
    already at the post-split level, so its factor excludes that split. Iterate
    newest -> oldest, recording the running product BEFORE folding in the current
    row's split. No splits column (an index like DX-Y.NYB) -> all 1.0.
    """
    import pandas as pd
    factors: dict = {}
    cum = 1.0
    for d in reversed(list(index)):
        factors[d] = cum
        if splits is not None:
            sp = splits.loc[d]
            if isinstance(sp, pd.Series):       # duplicate-index guard
                sp = sp.iloc[0]
            if sp is not None and not pd.isna(sp) and float(sp) > 0:
                cum *= float(sp)
    return factors


def fetch_one(symbol: str, *, period: str, round_dp: int,
              source: str = "yfinance") -> list[dict]:
    """Live full-history (or windowed) daily pull for one symbol -> archive records.

    Raises on empty data (fail-loud); the caller isolates the failure per series.
    """
    import yfinance as yf
    import pandas as pd

    df = yf.download(symbol, period=period, interval="1d",
                     auto_adjust=False, actions=True, progress=False)
    if df is None or df.empty:
        raise RuntimeError(f"no yfinance data for {symbol}")
    df = _dedup_index(_flatten(df))     # one row per date BEFORE factors/records
    if "Close" not in df.columns:
        raise RuntimeError(f"no Close column for {symbol}: {list(df.columns)}")

    close = df["Close"]
    adj = df["Adj Close"] if "Adj Close" in df.columns else close
    op = df["Open"] if "Open" in df.columns else close
    hi = df["High"] if "High" in df.columns else close
    lo = df["Low"] if "Low" in df.columns else close
    vol = df["Volume"] if "Volume" in df.columns else None
    splits = df["Stock Splits"] if "Stock Splits" in df.columns else None
    divs = df["Dividends"] if "Dividends" in df.columns else None
    factors = _split_factors(df.index, splits)

    def num(series, d):
        if series is None:
            return None
        v = series.loc[d]
        if isinstance(v, pd.Series):
            v = v.iloc[0]
        return None if pd.isna(v) else float(v)

    records = []
    for d in df.index:
        c = num(close, d)
        if c is None:                      # no close -> not a tradeable bar, drop
            continue
        # Compute each field ONCE (avoid redundant .loc lookups; P4 backfill compounds).
        a = num(adj, d)
        o, h, lw = num(op, d), num(hi, d), num(lo, d)
        dv = num(divs, d) or 0.0
        rec = {
            "as_of": d.date().isoformat(),
            "value": round(c, round_dp),                       # split-adj close (headline)
            "open": round(o if o is not None else c, round_dp),
            "high": round(h if h is not None else c, round_dp),
            "low": round(lw if lw is not None else c, round_dp),
            "close": round(c, round_dp),
            "value_tr": round(a if a is not None else c, round_dp),  # fully adjusted (div+split+cap-gains)
            "volume": int(num(vol, d) or 0),
            "split_factor": round(factors[d], 8),              # as-traded = close*split_factor
            "dividend": round(dv, round_dp),
            "source": source,
        }
        records.append(rec)

    if not records:
        raise RuntimeError(f"no usable daily closes for {symbol}")
    records.sort(key=lambda r: r["as_of"])
    records[-1]["provisional"] = True       # tip only; P1 freezes it on the next run
    return records


def fetch_prices(cfg: dict, *, period: str | None = None,
                 only: list[str] | None = None) -> dict:
    """Pull every configured price series. Per-series isolation: a dead symbol ->
    that series marked not-ok (never a silent zero, never aborts the run).

    ``period`` overrides settings (Gate 2 uses a short window). ``only`` restricts to
    a subset of series_ids (live spot-check on a handful of ETFs).
    """
    s = cfg["settings"]
    per = period or s.get("history_period_prices", "max")
    rdp = int(s.get("round_dp", 6))
    src = s.get("source", "yfinance")

    out: dict = {}
    for sid, m in cfg["price"].items():
        if only is not None and sid not in only:
            continue
        try:
            recs = fetch_one(m["symbol"], period=per, round_dp=rdp, source=src)
            out[sid] = {"ok": True, "records": recs}
        except Exception as e:  # noqa: BLE001 -- isolate per series
            out[sid] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return out

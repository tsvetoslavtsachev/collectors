"""FEED 1 — yfinance prices (32 ETF/idx dual-basis + ^VIX/^MOVE levels).

Live port of migrations/s6_vrm_history/import_vrm_history.py: the W-FRI weekly
logic here is an EXACT mirror of recon_engine.weekly() / the S6 importer
(drop daily NaNs -> resample('W-FRI').last() -> drop empty weeks), so a live pull
reproduces the frozen canonical shape. The S6 importer read a frozen S5 parquet
cache (no network); this collector pulls live yfinance full history so fresh weeks
land each run.

Per series, ONE record list -> ONE datacore.write (write_canonical overwrites the
whole file, so the whole history must be in one call). Each record:
    value         = Close      (PX_LAST; levels / thresholds / KS)        [always]
    value_tr      = Adj Close   (total-return; ratios / Alignment)        [dual_basis]
    source        = "yfinance"
    resolution    = "weekly"
    bloomberg_era = True for as_of <= CUT on the in-MID series, else False

NETWORK STEP — exercised in Gate 2 (live fetch + spot-check). Gate 1 proves the
wiring offline via run.py --mock (mockdata.py supplies this same record shape).

Cardinal rule: this deterministic path writes the numbers, never a model.
"""
from __future__ import annotations
import datetime as dt
import pandas as pd


def _weekly(daily: "pd.Series", today: "dt.date | None" = None) -> "pd.Series":
    """W-FRI weekly close. Parity with S6 import_vrm_history.weekly() on completed
    history, PLUS a SESSION-AWARE completed-week guard the frozen importer never
    needed: only emit a W-FRI bar whose Friday label is strictly BEFORE today.

    A live pull can otherwise land a partial bar with an intraweek close mislabeled
    as the Friday close in three ways: mid-week (label = a future Friday); intraday
    or after-close ON the Friday itself (label == today, last obs == today, so a
    `label > last_obs` test fails to drop it); or a market-holiday Friday. Gating on
    `label < today` drops all three. The just-closed Friday lands on the next run
    (<= 3 days later) -- a lag the weekly model tolerates, vs. leaking a
    non-deterministic look-ahead reading into the overlay's latest KS/momentum row.
    A past holiday-Friday week (Juneteenth 06-19, market closed -> the bar holds
    Thu 06-18's close) is KEPT once today > 06-19: it is a real, completed past bar.
    (oil.yml's sibling CI runs Friday 12:00 ET / mid-session -- exactly the case the
    old `> last_obs` guard mishandled.) `today` is injectable for deterministic tests."""
    s = daily.dropna()
    if not len(s):
        return s
    w = s.resample("W-FRI").last().dropna()
    cutoff = pd.Timestamp(today or dt.date.today())
    return w[w.index < cutoff]   # completed weeks only (label strictly before today)


def _col(df: "pd.DataFrame", name: str) -> "pd.Series":
    """yfinance can return a 1-col DataFrame per field; squeeze to a Series."""
    c = df[name]
    if isinstance(c, pd.DataFrame):
        c = c.iloc[:, 0]
    return c


def fetch_one(symbol: str, dual_basis: bool, forward_only: bool,
              cut: str, period: str, round_dp: int,
              resolution: str = "weekly") -> list[dict]:
    """Live full-history pull for one symbol -> canonical records.

    resolution='weekly' (default, ETF/idx): W-FRI resample, dual basis, bloomberg_era
    on the MID-reconciled history. resolution='daily' (market-context ^VIX/^MOVE):
    raw daily closes, no resample, bloomberg_era=False (not a MID series)."""
    import yfinance as yf

    df = yf.download(symbol, period=period, interval="1d",
                     progress=False, auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError(f"no yfinance data for {symbol}")

    close = _col(df, "Close")
    adj = _col(df, "Adj Close")
    if resolution == "daily":
        px = close.dropna()
        tr = (adj.dropna() if dual_basis else px)
    else:
        px = _weekly(close)
        tr = _weekly(adj) if dual_basis else px
    dates = px.index.intersection(tr.index).sort_values()
    if not len(dates):
        raise RuntimeError(f"no overlapping {resolution} closes for {symbol}")

    records = []
    for d in dates:
        as_of = d.date().isoformat()
        rec = {
            "as_of": as_of,
            "value": round(float(px.loc[d]), round_dp),
            "value_tr": round(float(tr.loc[d]), round_dp),
            "source": "yfinance",
            "resolution": resolution,
            "bloomberg_era": bool(resolution == "weekly"
                                  and not forward_only and as_of <= cut),
        }
        records.append(rec)
    return records


def fetch_prices(cfg: dict) -> dict:
    """Pull every yfinance series in the config map. Per-series isolation: a dead
    symbol -> that series marked not-ok (never a silent zero, never aborts run)."""
    s = cfg["settings"]
    cut = s["bloomberg_era_cut"]
    period = s.get("history_period_prices", "max")
    rdp = int(s.get("round_dp", 6))

    out: dict = {}
    for series_id, m in cfg["yfinance"].items():
        if m.get("bridge"):
            continue            # bridge-sourced (ETF-rr barometer) -> fetch_bridge
        try:
            recs = fetch_one(
                m["symbol"], bool(m.get("dual_basis")), bool(m.get("forward_only")),
                cut, period, rdp, m.get("resolution", "weekly"),
            )
            out[series_id] = {"ok": True, "records": recs}
        except Exception as e:  # noqa: BLE001 — isolate per series
            out[series_id] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return out

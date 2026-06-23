"""FEED 2 — FRED via the official API (14 series).

Live port of migrations/s9b_ahe_yoy/fetch_ahe_yoy.py + s6b_vrm_macro/fred_verify.py:
api.stlouisfed.org/fred/series/observations + FRED_API_KEY (the keyless
fredgraph.csv bot-stalls — see memory init22-no-local-egress). Key is read from
env ONLY, never printed, never written to any artifact (4 retries, UA header).

Responsibility split: this module does network + downsample-to-model-frequency.
The two computed transforms (macro_ahe_yoy = 12m YoY; macro_pce_nowcast = OLS on
CPI MoM) live in compute.py — this returns their raw monthly levels for it.

  transform=level + model_freq=monthly, source monthly -> pass through FRED dates.
  transform=level + source daily/weekly, model monthly -> mean_of_month (VERIFIED
    method, S6b threshold_baseline: VRM2 = mean-of-month for TGA/ANFCI).
  transform=level + model_freq=daily (mkt_*) -> all daily observations.
  computed=true (ahe) -> levels only; compute.py builds the YoY records.

NETWORK STEP — exercised in Gate 2. Gate 1 proves wiring offline via run.py --mock.
"""
from __future__ import annotations
import os
import json
import time
import calendar
import datetime as dt
import urllib.request
from collections import defaultdict

FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"


def _month_end(ym: str) -> str:
    """'YYYY-MM' -> 'YYYY-MM-DD' calendar month-end (leap-correct)."""
    y, mo = int(ym[:4]), int(ym[5:7])
    return "{}-{:02d}".format(ym, calendar.monthrange(y, mo)[1])


def _current_ym() -> str:
    """The current calendar month 'YYYY-MM' (the incomplete-month boundary)."""
    t = dt.date.today()
    return "{:04d}-{:02d}".format(t.year, t.month)


def fetch_observations(ticker: str) -> list:
    """[(YYYY-MM-DD, float)] for a FRED ticker. S9b pattern; key never logged."""
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise RuntimeError("FRED_API_KEY not in env (keyless fredgraph bot-stalls)")
    url = f"{FRED_API_BASE}?series_id={ticker}&file_type=json&api_key={key}"
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            out = []
            for o in data.get("observations", []):
                v = o.get("value")
                if v in (".", "", "NaN", None):
                    continue
                out.append((o["date"][:10], float(v)))
            return out
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"could not fetch {ticker}: {last}")


def _mean_of_month(obs: list, rdp: int) -> dict:
    """Daily/weekly observations -> {ym: monthly mean}. Date label is assigned by
    _to_model_freq (month-end), so the 9 regime macro align on a single timestamp."""
    buckets: dict = defaultdict(list)
    for d, v in obs:
        buckets[d[:7]].append(v)
    return {ym: round(sum(vals) / len(vals), rdp) for ym, vals in buckets.items()}


def _to_model_freq(obs: list, m: dict, rdp: int) -> list:
    """Map raw FRED observations to the series' model frequency + date convention.

    Monthly regime/liquidity series are labeled at the calendar MONTH-END (the
    VRM2/Bloomberg convention the frozen canonical carries) so regime_engine's
    timestamp join aligns all 9 inputs on one date per month; the current
    (incomplete) calendar month is DROPPED (partial-month guard, the monthly
    analogue of fetch_prices' trailing partial-week drop). Computed series
    (macro_ahe_yoy) keep FRED-native month-start dates -- the frozen S9b series is
    month-start and compute.py derives the YoY from these. Daily series (mkt_*)
    keep raw observation dates."""
    freq = m.get("model_freq", "monthly")
    if m.get("computed") or freq != "monthly":
        return [(d, round(v, rdp)) for d, v in obs]
    if m.get("downsample") == "mean_of_month":
        monthly = _mean_of_month(obs, rdp)                      # {ym: mean}
    else:
        monthly = {d[:7]: round(v, rdp) for d, v in obs}       # passthrough: 1/month
    cur = _current_ym()
    return [(_month_end(ym), monthly[ym]) for ym in sorted(monthly) if ym < cur]


def fetch_fred(cfg: dict) -> dict:
    """{series_id: {ok, levels:[(date,val)], model_records:[...]|None, error}}.

    model_records = canonical records for transform=level series (written directly).
    levels = model-frequency observations (computed series use these in compute.py).
    """
    rdp = int(cfg["settings"].get("round_dp", 6))
    out: dict = {}
    for series_id, m in cfg["fred"].items():
        try:
            obs = fetch_observations(m["ticker"])
            levels = _to_model_freq(obs, m, rdp)
            if m.get("computed"):
                model_records = None      # compute.py builds the records (YoY)
            else:
                src = f"FRED {m['ticker']}"
                res = m.get("model_freq", "monthly")
                model_records = [{"as_of": d, "value": v, "source": src,
                                  "resolution": res} for d, v in levels]
            out[series_id] = {"ok": True, "levels": levels,
                              "model_records": model_records}
        except Exception as e:  # noqa: BLE001 — isolate per series
            out[series_id] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return out

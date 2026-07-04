# -*- coding: utf-8 -*-
"""FEED 2b -- ALFRED (Archival FRED) vintage history for the 7 FRED regime series.

INIT-22 M1 (C2.1 bitemporal PIT layer). The sibling ``fetch_fred.py`` pulls only
the CURRENT vintage of each series (the ``observations`` endpoint with no realtime
window -> the "latest known" value per month). This module pulls the FULL vintage
history: every (observation-month, value) pair AS IT WAS KNOWN at each historical
publication date, so the regime engine can be replayed as-lived (look-ahead-free).

THE ONE-CALL TRICK (verified M1 recon 2026-07-04)
-------------------------------------------------
FRED's ``series/observations`` with ``realtime_start=1776-07-04&realtime_end=
9999-12-31`` returns EVERY (date, vintage) pair in a SINGLE response -- each row
carries ``realtime_start``/``realtime_end`` = the window that value was the live
print. So the whole vintage history of a series is ONE API call, not one-per-
vintage (U6RATE = 185 vintages in 1 call; CCSA = 868). This kills the rate-limit
concern the audit flagged (AUDIT-GROUP-A §3.1): 7 calls total for all 7 series.

Mapping to the bitemporal PIT record (datacore/vintage.py):
    row.date          -> as_of        (downsampled to month-end, VRM convention)
    row.value         -> value        (the as-lived level for that vintage)
    row.realtime_start-> recorded_on  (the vintage date the value became known)
one (as_of, recorded_on) pair per (month, vintage) = one as-lived record.

ISM (macro_ism_mfg / macro_ism_services) is DELIBERATELY EXCLUDED here: it is a
licensed Bloomberg hand-paste with NO ALFRED vintages (the blind spot, AUDIT §1).
Its as-lived handling is the "final = as-lived" assumption, validated separately
in the M1 ISM cross-check (mandate R1 / step S3). See _ISM_EXCLUDED below.

Key handling, retries, UA header, BOM/ZWSP strip: byte-identical to fetch_fred.py
(the keyless fredgraph.csv bot-stalls -- memory init22-no-local-egress).

NETWORK STEP. Offline replay uses cached raw JSON (see fetch_all -> cache_dir).
"""
from __future__ import annotations
import os
import json
import time
import calendar
import urllib.request
from collections import defaultdict

ALFRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"

# The 7 FRED regime tickers (config.yaml:91-97). ISM is NOT here -- see below.
# series_id -> {ticker, downsample}. downsample=mean_of_month only for the one
# weekly-source series (CCSA), mirroring config.yaml:93; the other 6 are monthly.
ALFRED_SERIES = {
    "macro_awh_total_private": {"ticker": "AWHAE",        "downsample": None},
    "macro_u6":                {"ticker": "U6RATE",       "downsample": None},
    "macro_continued_claims":  {"ticker": "CCSA",         "downsample": "mean_of_month"},
    "macro_retail_sales":      {"ticker": "RSXFS",        "downsample": None},
    "macro_core_pce":          {"ticker": "PCEPILFE",     "downsample": None},
    "macro_ppi_commodity":     {"ticker": "PPIACO",       "downsample": None},
    "macro_shelter_cpi":       {"ticker": "CUSR0000SAH1", "downsample": None},
}

# Explicitly documented exclusion (mandate step S1: "ISM изрично пропуснат с
# етикет в кода"). ISM has no ALFRED vintages; its PIT handling is the R1
# "final = as-lived" assumption, cross-checked against the Excel hand-record.
_ISM_EXCLUDED = ("macro_ism_mfg", "macro_ism_services")

# Same key-junk strip as fetch_fred.py:36 (BOM/ZWSP that ride a pasted key).
_KEY_JUNK = chr(0xFEFF) + chr(0x200B)


def _api_key() -> str:
    key = os.environ.get("FRED_API_KEY", "").strip().strip(_KEY_JUNK).strip()
    if not key:
        raise RuntimeError("FRED_API_KEY not in env (keyless fredgraph bot-stalls)")
    return key


def _month_end(ym: str) -> str:
    """'YYYY-MM' -> 'YYYY-MM-DD' calendar month-end (leap-correct). Mirrors
    fetch_fred._month_end so the PIT as_of aligns with the canonical as_of."""
    y, mo = int(ym[:4]), int(ym[5:7])
    return "{}-{:02d}".format(ym, calendar.monthrange(y, mo)[1])


def fetch_all_vintages(ticker: str, *, api_key: str | None = None) -> list:
    """[{realtime_start, realtime_end, date, value(float)}] -- the ENTIRE vintage
    history of ``ticker`` in one call. Drops FRED's missing-value sentinels
    (".", "", "NaN", None), exactly as fetch_fred.fetch_observations:66-69."""
    key = api_key or _api_key()
    url = (f"{ALFRED_API_BASE}?series_id={ticker}&file_type=json&api_key={key}"
           f"&realtime_start=1776-07-04&realtime_end=9999-12-31")
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            out = []
            for o in data.get("observations", []):
                v = o.get("value")
                if v in (".", "", "NaN", None):
                    continue
                out.append({
                    "realtime_start": o["realtime_start"],
                    "realtime_end": o["realtime_end"],
                    "date": o["date"][:10],
                    "value": float(v),
                })
            return out
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"could not fetch ALFRED {ticker}: {last}")


def _to_vintage_records(rows: list, downsample, rdp: int, ticker: str) -> list:
    """Raw ALFRED rows -> per-vintage monthly as-lived records.

    Each row is a (observation-month, vintage) pair. We label the observation at
    the calendar MONTH-END (VRM convention, fetch_fred._to_model_freq:96) and set
    recorded_on = realtime_start (the vintage the value became live). For the one
    weekly series (CCSA) we mean-of-month WITHIN each vintage window -- a partial
    month inside a vintage averages only the weeks that vintage knew about, which
    is precisely the as-lived monthly level for that publication date.

    ``source`` = "ALFRED <TICKER>" (the schema gate requires a source; this marks
    the record as vintage-sourced, distinct from the current-vintage "FRED <TICKER>").

    Returns [{as_of, value, source, recorded_on, realtime_end}] sorted by
    (as_of, recorded_on). realtime_end is carried for provenance (not a PIT key)."""
    src = f"ALFRED {ticker}"
    if downsample == "mean_of_month":
        # bucket by (vintage_start, YYYY-MM) -> mean of that vintage's weekly obs
        buckets: dict = defaultdict(list)
        rt_end: dict = {}
        for r in rows:
            k = (r["realtime_start"], r["date"][:7])
            buckets[k].append(r["value"])
            rt_end[k] = r["realtime_end"]  # same within a (vintage, month) window
        recs = []
        for (rstart, ym), vals in buckets.items():
            recs.append({
                "as_of": _month_end(ym),
                "value": round(sum(vals) / len(vals), rdp),
                "source": src,
                "recorded_on": rstart,
                "realtime_end": rt_end[(rstart, ym)],
            })
    else:  # monthly source: 1 obs per (vintage, month) already
        recs = [{
            "as_of": _month_end(r["date"][:7]),
            "value": round(r["value"], rdp),
            "source": src,
            "recorded_on": r["realtime_start"],
            "realtime_end": r["realtime_end"],
        } for r in rows]
    recs.sort(key=lambda x: (x["as_of"], x["recorded_on"]))
    return recs


def fetch_alfred(*, rdp: int = 6, api_key: str | None = None,
                 cache_dir: str | None = None) -> dict:
    """{series_id: {ok, vintage_records:[...], meta:{...}, error}} for the 7 FRED
    regime series' full vintage history.

    meta per series: {ticker, n_rows, n_vintages, earliest_vintage, earliest_obs,
                      latest_obs} -- the coverage the M1 report needs.

    ``cache_dir``: if given, each ticker's raw JSON is read from / written to
    ``<cache_dir>/<TICKER>.json`` so an offline determinism replay never re-hits
    the API (the S1 verify: "повторен pull детерминистично стабилен").
    """
    key = api_key or _api_key()
    out: dict = {}
    for series_id, m in ALFRED_SERIES.items():
        tkr = m["ticker"]
        try:
            rows = _cached_or_fetch(tkr, key, cache_dir)
            recs = _to_vintage_records(rows, m["downsample"], rdp, tkr)
            vints = sorted({r["recorded_on"] for r in recs})
            obs = sorted({r["as_of"] for r in recs})
            out[series_id] = {
                "ok": True,
                "vintage_records": recs,
                "meta": {
                    "ticker": tkr,
                    "n_rows": len(recs),
                    "n_vintages": len(vints),
                    "earliest_vintage": vints[0] if vints else None,
                    "latest_vintage": vints[-1] if vints else None,
                    "earliest_obs": obs[0] if obs else None,
                    "latest_obs": obs[-1] if obs else None,
                },
            }
        except Exception as e:  # noqa: BLE001 - isolate per series
            out[series_id] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        time.sleep(1)  # batching politeness between series (rate-limit discipline)
    return out


def _cached_or_fetch(ticker: str, key: str, cache_dir) -> list:
    """Read raw rows from cache if present, else fetch + write cache. The cache
    stores the SAME row shape fetch_all_vintages returns (already sentinel-free)."""
    if cache_dir:
        path = os.path.join(cache_dir, f"{ticker}.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        rows = fetch_all_vintages(ticker, api_key=key)
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        return rows
    return fetch_all_vintages(ticker, api_key=key)

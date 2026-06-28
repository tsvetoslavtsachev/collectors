# -*- coding: utf-8 -*-
"""collectors.price.figi -- OpenFIGI shareClassFIGI client (INIT-22 P7b rename signal).

shareClassFIGI is the PRIMARY automatic rename signal (decision c'/e', ADDENDUM):
it does NOT change on a corporate action, so a company's old and new tickers share
one shareClassFIGI (FB and META -> one FIGI). OpenFIGI's mapping API is free and the
FIGI is public-domain (MIT/BSD terms -> cacheable + redistributable). identity.py
fetches on first-sight and caches the FIGI into stock_identity.json -> OFFLINE
thereafter. It is the only rename signal that also works for STOXX (no change-log).

GRACEFUL DEGRADATION (cardinal): OpenFIGI unreachable / rate-limited / malformed ->
the affected jobs map to None and the caller falls back to ISIN, then a flagged-only
name hit. This module NEVER raises on a network problem -- a build must not die
because a third-party call failed. An optional OPENFIGI_API_KEY env raises the rate
ceiling; the public (keyless) tier is enough for a one-time 1112-symbol map.

  POST https://api.openfigi.com/v3/mapping
  body : [{"idType": "TICKER", "idValue": "AAPL", "exchCode": "US"}, ...]
  resp : [{"data": [{..., "shareClassFIGI": "BBG001S5N8V8", ...}]}, {"warning": ...}]
  header X-OPENFIGI-APIKEY: <key>   (optional)

This module is collectors-PUBLIC: it holds ZERO secrets. The key is read from the
environment (OPENFIGI_API_KEY), never committed.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

# Conservative per-request job counts. The keyless public tier caps both the jobs
# per request and the requests per minute lower than the keyed tier; we stay small
# and polite so a one-time 1112-symbol map never trips the limiter. (Exact ceilings
# vary by API revision -- we do not hardcode a claimed rate; we just batch small and
# back off on 429.)
_BATCH_NO_KEY = 10
_BATCH_KEY = 100


def _post(jobs: list, api_key, timeout: float):
    body = json.dumps(jobs).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key
    req = urllib.request.Request(OPENFIGI_URL, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def map_share_class_figi(items, *, api_key=None, timeout: float = 20.0,
                         sleep: float = 0.0, max_retries: int = 2) -> dict:
    """items: iterable of (ticker, exch_code). Returns {(ticker, exch_code):
    shareClassFIGI or None}.

    Offline-safe: ANY failure (network, HTTP, 429 rate, parse, partial response) ->
    the affected jobs stay None. Never raises. A 429 is retried with a short backoff
    up to ``max_retries``; if it still fails the chunk stays None (the caller flags).
    """
    api_key = api_key or os.environ.get("OPENFIGI_API_KEY")
    items = list(dict.fromkeys(items))          # de-dup, preserve order
    out = {it: None for it in items}
    batch = _BATCH_KEY if api_key else _BATCH_NO_KEY

    for i in range(0, len(items), batch):
        chunk = items[i:i + batch]
        jobs = [{"idType": "TICKER", "idValue": t, "exchCode": x} for (t, x) in chunk]
        res = None
        for attempt in range(max_retries + 1):
            try:
                res = _post(jobs, api_key, timeout)
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < max_retries:
                    time.sleep(1.0 + attempt)       # polite backoff on rate-limit
                    continue
                break                                # other HTTP error -> leave None
            except Exception:
                break                                # network/parse error -> leave None
        if isinstance(res, list):
            for it, r in zip(chunk, res):
                data = r.get("data") if isinstance(r, dict) else None
                if data:
                    out[it] = data[0].get("shareClassFIGI")
        if sleep:
            time.sleep(sleep)
    return out


def probe(items, *, api_key=None) -> dict:
    """Convenience for the Gate 2 live probe: returns {(ticker, exch): figi|None}
    plus prints a one-line result per item. Used by verify_identity --live."""
    res = map_share_class_figi(items, api_key=api_key)
    for it in items:
        f = res.get((it[0], it[1]))
        print(f"    {it[0]:>10} @ {it[1]:<3} -> {f or '(none)'}")
    return res

"""Fetch weekly COT rows per market from CFTC publicreporting (no file writes).

Returns the citizen shape consumed by to_datacore:
    {key: {"ok": bool, "rows": [normalized rows], "error": str}}

Identity resolution reuses cot-monitor's proven name resolver (LIKE query +
must_contain/must_not_contain filters + highest-OI dedup per date). The NEW guard
is downstream in to_datacore: any key whose rows still carry >1 contract identity
and is not a declared splice is refused. Where a stable `cftc_code` is known the
fetch pins by contract_market_code instead (structurally single identity — the
real fix; oil does this for WTI). Code discovery is a separate hardening step.
"""
from __future__ import annotations
import requests

from .markets import MARKETS

# Normalizers are family-specific: TFF headline net = leveraged funds; the
# disaggregated headline net = managed money (matches oil's WTI series).


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _sub(a, b):
    return None if (a is None or b is None) else a - b


def _normalize_tff(row):
    lev_l, lev_s = _num(row.get("lev_money_positions_long")), _num(row.get("lev_money_positions_short"))
    return {"date": row.get("report_date_as_yyyy_mm_dd"),
            "market_name": row.get("market_and_exchange_names"),
            "open_interest": _num(row.get("open_interest_all")),
            "primary_net": _sub(lev_l, lev_s)}


def _normalize_disagg(row):
    mm_l, mm_s = _num(row.get("m_money_positions_long_all")), _num(row.get("m_money_positions_short_all"))
    return {"date": row.get("report_date_as_yyyy_mm_dd"),
            "market_name": row.get("market_and_exchange_names"),
            "open_interest": _num(row.get("open_interest_all")),
            "primary_net": _sub(mm_l, mm_s)}


def _safe_oi(row):
    try:
        return float(str(row.get("open_interest_all") or 0).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _dedupe_by_date(rows):
    """One raw row per report date — drop MICRO/MINI-SIZED, keep highest OI."""
    from collections import defaultdict
    EXCLUDE = ("MICRO", "MINI-SIZED")
    filtered = [r for r in rows
                if not any(k in str(r.get("market_and_exchange_names", "")).upper() for k in EXCLUDE)]
    if not filtered:
        filtered = rows
    by_date = defaultdict(list)
    for r in filtered:
        by_date[str(r.get("report_date_as_yyyy_mm_dd") or "")[:10]].append(r)
    return [max(group, key=_safe_oi) for group in by_date.values()]


def _apply_filters(rows, market):
    must = market.get("name_must_contain")
    if must:
        terms = [must] if isinstance(must, str) else list(must)
        pinned = [r for r in rows
                  if all(t.upper() in str(r.get("market_and_exchange_names", "")).upper() for t in terms)]
        if pinned:
            rows = pinned
    must_not = market.get("name_must_not_contain")
    if must_not:
        terms = [must_not] if isinstance(must_not, str) else list(must_not)
        rows = [r for r in rows
                if not any(t.upper() in str(r.get("market_and_exchange_names", "")).upper() for t in terms)]
    return rows


def fetch_one(cfg, market) -> dict:
    family = market["family"]
    base = cfg["endpoints"]["tff"] if family == "tff" else cfg["endpoints"]["disaggregated"]
    headers = {"User-Agent": cfg["fetch"]["user_agent"]}

    if market.get("cftc_code"):
        where = f"cftc_contract_market_code = '{market['cftc_code']}'"
    else:
        where = f"upper(market_and_exchange_names) like '%{market['query_name'].upper()}%'"
    params = {"$limit": cfg["fetch"]["limit"],
              "$order": "report_date_as_yyyy_mm_dd DESC", "$where": where}

    r = requests.get(base, params=params, headers=headers, timeout=cfg["fetch"]["timeout"])
    r.raise_for_status()
    raw = r.json()
    if not raw:
        return {"ok": False, "error": "empty CFTC response"}

    if not market.get("cftc_code"):
        raw = _apply_filters(raw, market)
    raw = _dedupe_by_date(raw)
    normalizer = _normalize_tff if family == "tff" else _normalize_disagg
    rows = sorted((normalizer(x) for x in raw), key=lambda r: r.get("date") or "")
    return {"ok": True, "rows": rows}


def fetch_cot(cfg) -> dict:
    """Fetch every migrated market; per-market failures isolate (never tank the run)."""
    out = {}
    for m in MARKETS:
        if not m.get("canonical"):       # WTI-reuse: not fetched here
            continue
        try:
            out[m["key"]] = fetch_one(cfg, m)
        except Exception as e:
            out[m["key"]] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return out

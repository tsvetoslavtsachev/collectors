# -*- coding: utf-8 -*-
"""Shared universe accounting for the offline price gates (not a test; no main).

The gates used to hardcode universe counts (141 ETF, 1117 stock, 1258 registered), which
broke CI every time config.yaml legitimately grew. Counts are now DERIVED from config, and
the protection the constants gave (a silently lost series) is kept two ways:

  1. exact set equality against config where a second artifact exists (catalog, _family_sids,
     seeded identity map) -- a dropped series fails exactly, not by a tolerance;
  2. retire-INCLUSIVE floors per origin family. A config row is never deleted: a merger or
     delisting keeps it as a `retired` tombstone. So each family's retire-inclusive count can
     only grow, and a floor at today's count catches a deleted row without breaking on growth.
     Raise a floor when a family grows; lowering one needs a written reason.

Families (every row must land in exactly one; an unknown family/origin FAILS):
  etf               family etf (default)
  sp500             family stock, no origin, no currency (USD index members)
  stoxx             family stock, no origin, currency (STOXX600, multi-ccy)
  curated-offindex  family stock, origin curated-offindex (P7a-3, ERA.PA)
  ishares-basket    family stock, origin ishares-basket (ЦАБ1 16.09.2026 FXI + EWZ; ЦАБ2 17.09.2026 EWJ + EWY + EWT)
"""
from __future__ import annotations

FLOORS = {
    "etf": 159,               # 137 + 4 F13 (22.08) + 18 later
    "sp500": 508,             # retire-inclusive (3 retired tombstones, CTRA among them)
    "stoxx": 609,
    "curated-offindex": 1,    # ERA.PA, 26.08.2026
    "ishares-basket": 416,    # fxi_us 50 + ewz_us 46 (ЦАБ1) + ewj_us 167 + ewy_us 77 + ewt_us 76 (ЦАБ2)
}
STOCK_FAMILIES = ("sp500", "stoxx", "curated-offindex", "ishares-basket")


def family_of(m: dict) -> str | None:
    fam = m.get("family", "etf")
    origin = m.get("origin")
    if fam == "etf":
        return "etf" if origin is None else None
    if fam != "stock":
        return None
    if origin is None:
        return "stoxx" if m.get("currency") else "sp500"
    if origin == "curated-offindex":
        return origin if m.get("currency") else None
    if origin == "ishares-basket":
        return origin if (m.get("currency") and m.get("basket")) else None
    return None


def partition(cfg: dict) -> tuple[dict[str, list[str]], list[str]]:
    """(retire-inclusive family -> sids, unclassifiable sids)."""
    parts: dict[str, list[str]] = {f: [] for f in FLOORS}
    unknown: list[str] = []
    for sid, m in cfg["price"].items():
        f = family_of(m)
        (parts[f] if f else unknown).append(sid)
    return parts, unknown


def live(cfg: dict, sids=None) -> list[str]:
    sids = cfg["price"].keys() if sids is None else sids
    return [s for s in sids if not cfg["price"][s].get("retired")]


def floor_violations(parts: dict[str, list[str]]) -> dict[str, tuple[int, int]]:
    return {f: (len(parts[f]), FLOORS[f]) for f in FLOORS if len(parts[f]) < FLOORS[f]}


def summary(cfg: dict) -> str:
    parts, unknown = partition(cfg)
    return " ".join("%s=%d/%d" % (f, len(live(cfg, parts[f])), len(parts[f])) for f in FLOORS) \
        + " unknown=%d (live/incl-retired)" % len(unknown)

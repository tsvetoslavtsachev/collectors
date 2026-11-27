"""The four keyless sources of the fiscal citizen (ПРОЛЕТБ2, 27.11.2026).

  avg_rate  Fiscal Data `avg_interest_rates`, "Total Marketable", monthly from 2001-01
  soma      NY Fed `soma/summary.json`, the Fed's Treasury holdings, weekly from 2003-07
  debt      Fiscal Data `debt_to_penny`, debt held by the public, daily from 1997-09-30
  auctions  TreasuryDirect `securities/search?type=Note|Bond`, every coupon auction
            with results from 2008-04 (Fiscal Data `auctions_query` stopped publishing
            results after 15.10.2026; the two agree to the digit on all 1388 nominal
            coupon auctions 05.2008 to 15.10.2026, checked in ПРОЛЕТБ2 phase 0)

Every number field is read through `num`: the sources write a missing value as the
TEXT "null" (Fiscal Data) or "" (TreasuryDirect); either is None, never 0 or a float.
"""
from __future__ import annotations

import datetime as dt

import requests

UA = {"User-Agent": "Mozilla/5.0 (data-collector)"}
FD = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"

AVG_RATE = "fisc_avg_rate_marketable"
SOMA_UST = "fed_soma_ust"
DEBT_PUBLIC = "fisc_debt_held_public"

SRC_AVG = "Fiscal Data avg_interest_rates Total Marketable"
SRC_SOMA = "NY Fed SOMA summary (bills+notesbonds+tips+frn+tipsInflationCompensation)"
SRC_DEBT = "Fiscal Data debt_to_penny debt_held_public_amt"
SRC_AUCT = "TreasuryDirect securities/search"

SOMA_PARTS = ("bills", "notesbonds", "tips", "frn", "tipsInflationCompensation")


class FormatError(ValueError):
    """A source no longer has the shape this reader was built on."""


def num(v):
    """Source cell -> float or None. "null", "", None and whitespace are None."""
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() == "null":
        return None
    try:
        return float(s)
    except ValueError as e:
        raise FormatError(f"not a number: {v!r}") from e


def _get_json(url, get, timeout=120):
    r = get(url, timeout=timeout, headers=UA)
    r.raise_for_status()
    return r.json()


def _fd_all(path, get, extra=""):
    rows, page = [], 1
    while True:
        d = _get_json(f"{FD}/{path}?page[size]=10000&page[number]={page}&sort=record_date{extra}", get)
        rows += d["data"]
        if page >= int(d["meta"]["total-pages"]):
            return rows
        page += 1


# ---------- the average rate on marketable debt ----------
def parse_avg_rate(rows: list) -> list:
    out = []
    for r in rows:
        if r.get("security_desc") != "Total Marketable":
            continue
        v = num(r.get("avg_interest_rate_amt"))
        if v is None:
            continue
        if not 0 <= v <= 20:
            raise FormatError(f"implausible average rate {v} on {r.get('record_date')}")
        out.append({"as_of": r["record_date"], "value": round(v, 3), "source": SRC_AVG})
    if len(out) < 200:
        raise FormatError(f"only {len(out)} monthly rows for Total Marketable")
    return sorted(out, key=lambda x: x["as_of"])


def fetch_avg_rate(get=requests.get) -> list:
    return _fd_all("v2/accounting/od/avg_interest_rates", get,
                   "&filter=security_desc:eq:Total%20Marketable")


# ---------- the Fed's Treasury portfolio ----------
def parse_soma(doc: dict) -> list:
    try:
        rows = doc["soma"]["summary"]
    except (KeyError, TypeError) as e:
        raise FormatError("no soma.summary in the NY Fed document") from e
    out = []
    for s in rows:
        parts = [num(s.get(k)) for k in SOMA_PARTS]
        if all(p is None for p in parts):
            continue
        tot = sum(p or 0 for p in parts) / 1e9
        out.append({"as_of": s["asOfDate"], "value": round(tot, 3), "source": SRC_SOMA})
    if len(out) < 1000:
        raise FormatError(f"only {len(out)} weekly SOMA rows")
    return sorted(out, key=lambda x: x["as_of"])


def fetch_soma(get=requests.get) -> dict:
    return _get_json("https://markets.newyorkfed.org/api/soma/summary.json", get)


# ---------- debt held by the public ----------
def parse_debt(rows: list) -> list:
    out = []
    for r in rows:
        v = num(r.get("debt_held_public_amt"))
        if v is None:
            continue
        out.append({"as_of": r["record_date"], "value": round(v / 1e9, 3), "source": SRC_DEBT})
    if len(out) < 5000:
        raise FormatError(f"only {len(out)} daily debt rows")
    return sorted(out, key=lambda x: x["as_of"])


def fetch_debt(get=requests.get) -> list:
    return _fd_all("v2/accounting/od/debt_to_penny", get)


# ---------- the coupon auctions ----------
def auction_date(x: dict) -> str | None:
    """The auction day from the results file name R_YYYYMMDD_n.xml; None = no result yet."""
    f = (x.get("treasuryAuctionResultsXmlFileName") or "").strip()
    if not f.startswith("R_") or len(f) < 10:
        return None
    try:
        return dt.date(int(f[2:6]), int(f[6:8]), int(f[8:10])).isoformat()
    except ValueError as e:
        raise FormatError(f"results file name {f!r} does not carry a date") from e


def parse_auctions(rows: list) -> list:
    """Nominal coupon auctions (Note, Bond; no TIPS, no FRN) that have results."""
    out = []
    for x in rows:
        if x.get("securityType") not in ("Note", "Bond"):
            continue
        if x.get("inflationProtected") == "Yes" or x.get("floatingRate") == "Yes":
            continue
        d = auction_date(x)
        btc = num(x.get("bidToCoverRatio"))
        pd_, di, ind = (num(x.get(k)) for k in ("primaryDealersCompetitiveParAwarded",
                                                 "directBiddersCompetitiveParAwarded",
                                                 "indirectBiddersCompetitiveParAwarded"))
        if d is None or btc is None or pd_ is None:
            continue                                   # announced, not yet auctioned
        comp = pd_ + (di or 0) + (ind or 0)
        if comp <= 0:
            continue
        out.append({"auction_date": d, "cusip": x.get("cusip"),
                    "security_type": x["securityType"], "term": x.get("securityTerm"),
                    "original_term": x.get("originalSecurityTerm") or x.get("securityTerm"),
                    "reopening": x.get("reopening") == "Yes",
                    "offering_bn": round((num(x.get("offeringAmount")) or 0) / 1e9, 3),
                    "bid_to_cover": btc,
                    "dealers_pct": round(100 * pd_ / comp, 2),
                    "competitive_bn": round(comp / 1e9, 3),
                    "high_yield": num(x.get("highYield")),
                    "source": SRC_AUCT})
    if len(out) < 1000:
        raise FormatError(f"only {len(out)} coupon auctions with results")
    out.sort(key=lambda a: (a["auction_date"], a["cusip"] or ""))
    return out


def fetch_auctions(get=requests.get) -> list:
    rows = []
    for t in ("Note", "Bond"):
        rows += _get_json(f"https://www.treasurydirect.gov/TA_WS/securities/search?format=json&type={t}",
                          get, timeout=180)
    return rows

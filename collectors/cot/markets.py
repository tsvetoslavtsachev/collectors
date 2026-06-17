"""The 38-market COT registry (faithful port of cot-monitor MARKETS) + canonical
keys for data-core.

Each market declares ONE canonical identity. The published cot-monitor used a
LIKE substring query on `market_and_exchange_names`, which silently spanned two
different contracts for WTI (NYMEX until 2022-02, ICE after) — the spliced
percentile that inverted the signal. Here every market carries:

  - `query_name` + filters : the proven name resolver (reused verbatim) — used
                             only as a FALLBACK when no `cftc_code` is pinned.
  - `cftc_code`            : the STABLE contract_market_code (survives renames).
                             Pinning the code is the real identity fix (oil does
                             this for WTI = 067651). fetch prefers code over name.
  - `canonical`            : the data-core series_id for the raw spec net, or
                             None when the market reuses an existing series.
  - `cohort`               : which trader cohort is the headline net —
                             "mm"  (managed money) for disaggregated commodities,
                             "lev" (leveraged funds) for TFF financials.
                             Recorded so the stored net is never ambiguous.

LIVE-DATA LESSON (S13, 2026-06-17): the CFTC 2022 mass-rename (and the 2007
NYBOT->ICE migration) broke the LIKE-by-name resolver for ~17 markets — some were
REJECTED (LIKE matched old+new name -> identity guard refused), others were
silently TRUNCATED to post-rename history (LIKE matched only the new name). The
fix is to pin the STABLE `cftc_contract_market_code`: one code spans the rename,
so the full history returns under one contract identity. A name change WITHIN a
pinned code is a benign `name_rebrand` (cosmetic) -> mark, keep whole history;
only a NON-pinned market spanning two CODES is a real `contract_splice` (restrict
the percentile to the current segment). Codes below were each verified against
CFTC publicreporting (full date span under the code). The 18 markets that already
resolve to a single stable identity via LIKE keep `cftc_code: None` (proven full
history) — pinning their codes is forward-hardening, not a correctness fix.

WTI is NOT migrated here: the clean NYMEX-pinned series already lives in the base
as `oil_cot_wti_mm_pctile` (oil collector, contract 067651). cot reuses it rather
than duplicating the fetch (Цветослав, S13). Documented via `reuse`.
"""
from __future__ import annotations

# cohort constants — which net is the headline, per report family.
MM = "mm"    # managed money (disaggregated / commodities)
LEV = "lev"  # leveraged funds (TFF / financials)

# Markets ported 1:1 from cot-monitor/scripts/fetch_cot.py MARKETS (38 entries).
# Order preserved for diff-ability. `cftc_code` pinned for the rename-affected
# markets (verified live); None where LIKE already resolves a single identity.
MARKETS = [
    # ── Financials (TFF; headline net = leveraged funds) ────────────────────
    {"key": "sp500", "title": "E-mini S&P 500", "subtitle": "US Equities",
     "family": "tff", "cohort": LEV, "query_name": "E-MINI S&P 500",
     "name_must_not_contain": "MICRO", "cftc_code": "13874A",
     # 13874A: "E-MINI S&P 500 STOCK INDEX" -> "E-MINI S&P 500" (2022 rename),
     # one code 2006-2026 -> name_rebrand, full history.
     "canonical": "cot_sp500_net"},
    {"key": "nasdaq", "title": "Nasdaq Mini", "subtitle": "US Equities",
     "family": "tff", "cohort": LEV, "query_name": "NASDAQ-100",
     "name_must_not_contain": "MICRO", "cftc_code": "209742",
     # 209742 = E-mini Nasdaq-100 ("NASDAQ-100 STOCK INDEX (MINI)" -> "NASDAQ
     # MINI"), full 2006-2026, parallel to the sp500 e-mini. CHOSEN over the
     # "NASDAQ-100 Consolidated" code 20974+ (2010+ only, a combined report):
     # the e-mini gives longer history + matches sp500. Flagged for sign-off.
     "canonical": "cot_nasdaq_net"},
    {"key": "russell", "title": "Russell 2000 Mini", "subtitle": "US Equities",
     "family": "tff", "cohort": LEV, "query_name": "RUSSELL",
     "name_must_contain": "E-MINI",
     "name_must_not_contain": ["MICRO", "1000", "DIVIDEND"], "cftc_code": "239742",
     # 239742 CME e-mini Russell. Exchange round-trip: CME (2006-2008) -> ICE
     # (2008-2017, code 23977A, NOT this code) -> CME (2017-) -> a real 2008-2017
     # GAP under this code. Honest: raw keeps all rows, gap visible by date;
     # recent 2017+ segment is clean+contiguous. history_gap noted in derive.
     "canonical": "cot_russell_net"},
    {"key": "us2y", "title": "UST 2Y Note", "subtitle": "Rates",
     "family": "tff", "cohort": LEV, "query_name": "UST 2Y NOTE",
     "cftc_code": "042601", "canonical": "cot_us2y_net"},
    # 042601: "2-YEAR U.S. TREASURY NOTES" -> "UST 2Y NOTE" (2022), full 2006-2026.
    {"key": "us5y", "title": "UST 5Y Note", "subtitle": "Rates",
     "family": "tff", "cohort": LEV, "query_name": "UST 5Y NOTE",
     "cftc_code": "044601", "canonical": "cot_us5y_net"},
    {"key": "us10y", "title": "UST 10Y Note", "subtitle": "Rates",
     "family": "tff", "cohort": LEV, "query_name": "UST 10Y NOTE",
     "cftc_code": "043602", "canonical": "cot_us10y_net"},
    {"key": "us30y", "title": "UST Bond (30Y)", "subtitle": "Rates",
     "family": "tff", "cohort": LEV, "query_name": "UST BOND",
     "name_must_not_contain": "ULTRA", "cftc_code": "020601",
     # 020601: "U.S. TREASURY BONDS" -> "UST BOND" (2022). Ultra bond = 020604
     # (different code), excluded by the pinned code.
     "canonical": "cot_us30y_net"},
    {"key": "usultra10y", "title": "Ultra UST 10Y", "subtitle": "Rates",
     "family": "tff", "cohort": LEV, "query_name": "ULTRA UST 10Y",
     "cftc_code": "043607", "canonical": "cot_usultra10y_net"},
    # 043607: "ULTRA 10-YEAR U.S. T-NOTES" -> "ULTRA UST 10Y" (2022); from 2016.
    {"key": "vix", "title": "VIX Futures", "subtitle": "Volatility",
     "family": "tff", "cohort": LEV, "query_name": "VIX FUTURES",
     "cftc_code": None, "canonical": "cot_vix_net"},
    {"key": "bitcoin", "title": "Bitcoin Futures", "subtitle": "Crypto",
     "family": "tff", "cohort": LEV, "query_name": "BITCOIN",
     "name_must_contain": "CHICAGO MERCANTILE", "cftc_code": None,
     "canonical": "cot_bitcoin_net"},
    # ── FX (TFF; CME-pinned; exclude cross-rates) ───────────────────────────
    {"key": "dxy", "title": "USD Index", "subtitle": "FX",
     "family": "tff", "cohort": LEV, "query_name": "USD INDEX",
     "cftc_code": "098662", "canonical": "cot_dxy_net"},
    # 098662 (ICE): "U.S. DOLLAR INDEX" -> "USD INDEX" (2022). Code-pinning gives
    # the FULL 2006-2026 history (the old LIKE matched post-2022 only -> 227 rows).
    {"key": "eurfx", "title": "Euro FX", "subtitle": "FX",
     "family": "tff", "cohort": LEV, "query_name": "EURO FX",
     "name_must_contain": "CHICAGO MERCANTILE", "name_must_not_contain": "/",
     "cftc_code": None, "canonical": "cot_eurfx_net"},
    {"key": "gbpfx", "title": "British Pound", "subtitle": "FX",
     "family": "tff", "cohort": LEV, "query_name": "BRITISH POUND",
     "name_must_not_contain": "/", "cftc_code": "096742",
     # 096742: "BRITISH POUND STERLING" -> "BRITISH POUND" (2022), full history.
     "canonical": "cot_gbpfx_net"},
    {"key": "jpy", "title": "Japanese Yen", "subtitle": "FX",
     "family": "tff", "cohort": LEV, "query_name": "JAPANESE YEN",
     "name_must_contain": "CHICAGO MERCANTILE", "name_must_not_contain": "/",
     "cftc_code": None, "canonical": "cot_jpy_net"},
    {"key": "chf", "title": "Swiss Franc", "subtitle": "FX",
     "family": "tff", "cohort": LEV, "query_name": "SWISS FRANC",
     "name_must_contain": "CHICAGO MERCANTILE", "name_must_not_contain": "/",
     "cftc_code": None, "canonical": "cot_chf_net"},
    {"key": "cad", "title": "Canadian Dollar", "subtitle": "FX",
     "family": "tff", "cohort": LEV, "query_name": "CANADIAN DOLLAR",
     "name_must_contain": "CHICAGO MERCANTILE", "name_must_not_contain": "/",
     "cftc_code": None, "canonical": "cot_cad_net"},
    {"key": "aud", "title": "Australian Dollar", "subtitle": "FX",
     "family": "tff", "cohort": LEV, "query_name": "AUSTRALIAN DOLLAR",
     "name_must_contain": "CHICAGO MERCANTILE", "name_must_not_contain": "/",
     "cftc_code": None, "canonical": "cot_aud_net"},
    # ── Metals (disaggregated; headline net = managed money) ─────────────────
    {"key": "gold", "title": "Gold", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "GOLD",
     "cftc_code": None, "canonical": "cot_gold_net"},
    {"key": "silver", "title": "Silver", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "SILVER",
     "name_must_not_contain": "MICRO", "cftc_code": None,
     "canonical": "cot_silver_net"},
    {"key": "copper", "title": "Copper", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "COPPER",
     "name_must_not_contain": "MICRO", "cftc_code": "085692",
     # 085692: "COPPER-GRADE #1" -> "COPPER- #1" (2022), full 2006-2026.
     "canonical": "cot_copper_net"},
    {"key": "platinum", "title": "Platinum", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "PLATINUM",
     "cftc_code": None, "canonical": "cot_platinum_net"},
    {"key": "palladium", "title": "Palladium", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "PALLADIUM",
     "cftc_code": None, "canonical": "cot_palladium_net"},
    # ── Energy (disaggregated) ───────────────────────────────────────────────
    {"key": "wti", "title": "WTI Crude", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "CRUDE OIL, LIGHT SWEET",
     "cftc_code": "067651", "canonical": None,
     # REUSE: clean NYMEX series already in base via oil collector. Not migrated
     # here (avoids duplicate fetch + the LIKE splice). Consumer reads the oil one.
     "reuse": "oil_cot_wti_mm_pctile"},
    {"key": "natgas", "title": "Natural Gas", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "NATURAL GAS",
     "cftc_code": "023651", "canonical": "cot_natgas_net"},
    # 023651 = the FLAGSHIP physically-settled NatGas futures ("NATURAL GAS" ->
    # "NAT GAS NYME" 2022 rename), full 2006-2026. SWITCHED from cot-monitor's
    # "HENRY HUB" (code 03565B = the financial swap, a secondary instrument):
    # the flagship futures is the canonical NatGas COT. Flagged for sign-off.
    {"key": "brent", "title": "Brent Crude", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "BRENT LAST DAY",
     "cftc_code": None, "canonical": "cot_brent_net",
     # NB: only the NYMEX "BRENT LAST DAY" satellite exists in CFTC
     # publicreporting (no ICE Brent); history genuinely starts 2022. Labelled
     # satellite (the short history is real, not a truncation).
     "satellite": "NYMEX BRENT LAST DAY (no ICE Brent in CFTC); from 2022"},
    {"key": "rbob", "title": "RBOB Gasoline", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "GASOLINE RBOB",
     "cftc_code": "111659", "canonical": "cot_rbob_net"},
    # 111659: "GASOLINE BLENDSTOCK (RBOB)" -> "GASOLINE RBOB" (2022), full history.
    {"key": "heatingoil", "title": "Heating Oil", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "NY HARBOR ULSD",
     "cftc_code": "022651", "canonical": "cot_heatingoil_net"},
    # 022651: "NO. 2 HEATING OIL, N.Y. HARBOR" -> "#2 HEATING OIL, NY HARBOR-ULSD"
    # -> "#2 HEATING OIL- NY HARBOR-ULSD" -> "NY HARBOR ULSD" (3 renames, one
    # code), full 2006-2026. The old LIKE matched the 2022 name only -> 226 rows.
    # ── Grains (disaggregated) ───────────────────────────────────────────────
    {"key": "corn", "title": "Corn", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "CORN",
     "cftc_code": None, "canonical": "cot_corn_net"},
    {"key": "soybeans", "title": "Soybeans", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "SOYBEANS",
     "name_must_not_contain": "OIL", "cftc_code": None,
     "canonical": "cot_soybeans_net"},
    {"key": "wheat", "title": "Wheat (SRW)", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "WHEAT-SRW",
     "cftc_code": "001602", "canonical": "cot_wheat_net"},
    # 001602: "WHEAT" -> "WHEAT-SRW" (2013 rename), full 2006-2026. Old LIKE
    # matched "WHEAT-SRW" only -> 652 rows from 2013.
    {"key": "soyoil", "title": "Soybean Oil", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "SOYBEAN OIL",
     "cftc_code": None, "canonical": "cot_soyoil_net"},
    {"key": "soymeal", "title": "Soybean Meal", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "SOYBEAN MEAL",
     "cftc_code": None, "canonical": "cot_soymeal_net"},
    # ── Softs (disaggregated; ICE Futures U.S.) ──────────────────────────────
    {"key": "coffee", "title": "Coffee", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "COFFEE C",
     "cftc_code": "083731", "canonical": "cot_coffee_net"},
    # 083731: NYBOT (2006-2007) -> ICE (2007-, the 2007 NYBOT->ICE merger),
    # one code, full history -> name_rebrand (exchange rebrand, not a splice).
    {"key": "sugar", "title": "Sugar", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "SUGAR NO. 11",
     "cftc_code": "080732", "canonical": "cot_sugar_net"},
    {"key": "cocoa", "title": "Cocoa", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "COCOA",
     "name_must_not_contain": "EUROPEAN", "cftc_code": "073732",
     "canonical": "cot_cocoa_net"},
    {"key": "cotton", "title": "Cotton", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "COTTON NO. 2",
     "cftc_code": "033661", "canonical": "cot_cotton_net"},
    # softs 080732/073732/033661: same NYBOT->ICE 2007 rebrand pattern as coffee.
    # ── Livestock (disaggregated) ────────────────────────────────────────────
    {"key": "cattle", "title": "Live Cattle", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "LIVE CATTLE",
     "cftc_code": None, "canonical": "cot_cattle_net"},
    {"key": "hogs", "title": "Lean Hogs", "subtitle": "Commodities",
     "family": "disaggregated", "cohort": MM, "query_name": "LEAN HOGS",
     "cftc_code": None, "canonical": "cot_hogs_net"},
]

# Sanity: every market has a unique key + canonical (or a reuse), so the catalog
# and the writer never see two markets fighting over one series_id.
assert len(MARKETS) == 38, f"expected 38 markets, got {len(MARKETS)}"
_keys = [m["key"] for m in MARKETS]
assert len(_keys) == len(set(_keys)), "duplicate market key"
_canon = [m["canonical"] for m in MARKETS if m.get("canonical")]
assert len(_canon) == len(set(_canon)), "duplicate canonical series_id"
# Pinned codes must be unique too (one CFTC contract -> one series).
_codes = [m["cftc_code"] for m in MARKETS if m.get("cftc_code")]
assert len(_codes) == len(set(_codes)), "duplicate cftc_code"


def migrated():
    """Markets that write a new canonical series (everything except WTI-reuse)."""
    return [m for m in MARKETS if m.get("canonical")]

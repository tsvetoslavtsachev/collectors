# -*- coding: utf-8 -*-
"""P7a-2 verify gate -- the STOXX600 stock family (foreign-exchange) extension.

Offline gates (default run) -- no network, against a TEMPORARY archive root:
  t1 currency/basis -- EVERY STOXX series carries currency + quote_basis (machine-readable)
  t2 GBX rule       -- .L+GBP -> quote_basis "GBX" (pence); USD-on-LSE (IHG.L) -> "USD" not GBX;
                       a EUR name (SAP.DE) -> "EUR" (currency-driven, NOT suffix-driven)
  t3 collisions=0   -- suffix-retained series_id -> 0 dup symbol/series_id across the WHOLE union
                       (132 ETF + 503 SP500 + ~609 STOXX); the cardinal cross-archive risk
  t4 normalization  -- dashed+suffixed (NOVO-B.CO -> px_novo_b_co_daily); intra-STOXX base-dup
                       disambiguation (SAN.PA Sanofi vs SAN.MC Santander -> distinct ids/names)
  t5 entry routing  -- STOXX entry: currency+quote_basis+backtest_valid:false+survivorship;
                       SP500 stock entry: backtest_valid:false but NO currency key (no regression)
  t6 counts         -- families etf / stock; STOXX subset carries currency

Live gate (--live, network):
  t7 fx round trip  -- a suffixed STOXX name (SAP.DE) + a London pence name fetch + append to temp root

Run:
  PYTHONPATH=C:\\Projects\\data-core;C:\\Projects\\collectors \\
  python collectors/price/tests/test_stoxx.py [--live]
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

from datacore import archive
from collectors.price import fetch_prices, to_datacore, register_catalog

PRICE_DIR = Path(register_catalog.__file__).resolve().parent
CFG = yaml.safe_load((PRICE_DIR / "config.yaml").read_text(encoding="utf-8"))
STOCK = {s: m for s, m in CFG["price"].items() if m.get("family") == "stock"}
STOXX = {s: m for s, m in STOCK.items() if m.get("currency")}        # multi-ccy -> STOXX
SP500_STOCK = {s: m for s, m in STOCK.items() if not m.get("currency")}
ETF = {s: m for s, m in CFG["price"].items() if m.get("family", "etf") == "etf"}


class Gate:
    def __init__(self):
        self.total = 0
        self.fails: list[str] = []

    def check(self, name, cond, detail=""):
        self.total += 1
        print(("  [PASS] " if cond else "  [FAIL] ") + name + (f" -- {detail}" if detail else ""))
        if not cond:
            self.fails.append(name)


def _seed_temp_root() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="px_p7a2_"))
    (tmp / "catalog").mkdir(parents=True)
    (tmp / "catalog" / "catalog.json").write_text(
        json.dumps({"catalog_schema_version": 1,
                    "series": {"px_probe_daily": {"description": "seed probe",
                                                  "source": "synthetic-probe",
                                                  "schema_version": 1}}},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    register_catalog.register(CFG, tmp)
    return tmp


def offline(g: Gate, tmp: Path) -> None:
    cat = to_datacore.load_catalog(tmp)["series"]

    # t1 currency + quote_basis on EVERY STOXX series -----------------------------
    g.check("t1a STOXX family present (~609 current members)", len(STOXX) >= 590,
            "stoxx=%d" % len(STOXX))
    missing = [s for s in STOXX
               if not isinstance(cat.get(s, {}).get("currency"), str)
               or not isinstance(cat.get(s, {}).get("quote_basis"), str)]
    g.check("t1b EVERY STOXX series has currency + quote_basis (machine-readable str)",
            not missing, "offenders=%d e.g.%s" % (len(missing), missing[:5]))
    # and they remain survivorship-flagged like all stock
    bad_flag = [s for s in STOXX if cat.get(s, {}).get("backtest_valid") is not False]
    g.check("t1c EVERY STOXX series still backtest_valid:false (survivorship)", not bad_flag,
            "offenders=%d" % len(bad_flag))

    # t2 GBX rule (currency-driven, not suffix-driven) ---------------------------
    def qb(sid):
        return cat.get(sid, {}).get("quote_basis")
    g.check("t2a London .L + GBP -> quote_basis GBX (HSBA.L pence)", qb("px_hsba_l_daily") == "GBX",
            "HSBA=%s" % qb("px_hsba_l_daily"))
    g.check("t2b USD-on-LSE -> quote_basis USD, NOT GBX (IHG.L / CPG.L)",
            qb("px_ihg_l_daily") == "USD" and qb("px_cpg_l_daily") == "USD",
            "IHG=%s CPG=%s" % (qb("px_ihg_l_daily"), qb("px_cpg_l_daily")))
    g.check("t2c non-LSE -> quote_basis == currency (SAP.DE EUR)", qb("px_sap_de_daily") == "EUR",
            "SAP=%s" % qb("px_sap_de_daily"))
    # every GBX series must actually be a .L/GBP name (no false GBX)
    false_gbx = [s for s, m in STOXX.items()
                 if cat.get(s, {}).get("quote_basis") == "GBX"
                 and not (m["symbol"].endswith(".L") and m.get("currency") == "GBP")]
    g.check("t2d no false GBX (every GBX is a .L/GBP name)", not false_gbx,
            "offenders=%s" % false_gbx[:5])

    # t3 collisions = 0 across the WHOLE union (cardinal cross-archive risk) ------
    all_sids = list(CFG["price"].keys())
    all_syms = [m["symbol"].upper() for m in CFG["price"].values()]
    g.check("t3a 0 duplicate series_id across union (132 ETF + 503 SP500 + ~609 STOXX)",
            len(all_sids) == len(set(all_sids)),
            "total=%d unique=%d" % (len(all_sids), len(set(all_sids))))
    g.check("t3b 0 duplicate symbol across union", len(all_syms) == len(set(all_syms)),
            "total=%d unique=%d" % (len(all_syms), len(set(all_syms))))
    # the decisive property: STOXX ids keep a suffix (_xx), bare US ids never do -> disjoint
    bare_us = {s for s in SP500_STOCK} | {s for s in ETF}
    overlap = bare_us & set(STOXX)
    g.check("t3c STOXX series_id namespace disjoint from bare SP500/ETF", not overlap,
            "overlap=%s" % list(overlap)[:5])

    # t4 normalization + intra-STOXX disambiguation ------------------------------
    g.check("t4a dashed+suffixed series_id (NOVO-B.CO -> px_novo_b_co_daily)",
            "px_novo_b_co_daily" in CFG["price"]
            and CFG["price"]["px_novo_b_co_daily"]["symbol"] == "NOVO-B.CO")
    san = {s: CFG["price"][s] for s in ("px_san_pa_daily", "px_san_mc_daily") if s in CFG["price"]}
    g.check("t4b SAN.PA (Sanofi) vs SAN.MC (Santander) -> distinct series_id + names",
            len(san) == 2 and san["px_san_pa_daily"]["name"] != san["px_san_mc_daily"]["name"],
            "%s" % {k: v["name"] for k, v in san.items()})

    # t5 entry() routing: STOXX enriched, SP500 stock unchanged (no regression) --
    es = register_catalog.entry({"symbol": "SAP.DE", "name": "SAP", "category": "Technology",
                                 "currency": "EUR", "quote_basis": "EUR", "family": "stock"})
    eg = register_catalog.entry({"symbol": "HSBA.L", "name": "HSBC", "category": "Financial Services",
                                 "currency": "GBP", "quote_basis": "GBX", "family": "stock"})
    esp = register_catalog.entry({"symbol": "AAPL", "name": "Apple", "category": "Information Technology",
                                  "family": "stock"})  # SP500: no currency
    g.check("t5a entry(STOXX) carries currency+quote_basis+backtest_valid:false",
            es["currency"] == "EUR" and es["quote_basis"] == "EUR" and es["backtest_valid"] is False)
    g.check("t5b entry(GBX) quote_basis=GBX + description notes pence/100", eg["quote_basis"] == "GBX"
            and "/100" in eg["description"])
    g.check("t5c entry(SP500 stock) has NO currency key (no regression)",
            "currency" not in esp and esp["backtest_valid"] is False)

    # t6 counts ------------------------------------------------------------------
    g.check("t6 families: 132 ETF, stock = SP500 + STOXX",
            len(ETF) == 132 and len(STOCK) == len(SP500_STOCK) + len(STOXX),
            "etf=%d stock=%d sp500=%d stoxx=%d" % (len(ETF), len(STOCK), len(SP500_STOCK), len(STOXX)))

    # t8 (P8c) currency=>family defense-in-depth -- a currency-bearing entry MUST be a stock.
    # Without family:stock it would fall to the ETF branch (drop currency, stamp backtest_valid:
    # true) -- a silent multi-currency misfile. 0/609 violate today; the assert keeps it closed.
    def _raises(m):
        try:
            register_catalog.entry(m)
            return False
        except ValueError:
            return True
    g.check("t8a entry(currency, NO family) RAISES (currency=>family assert)",
            _raises({"symbol": "HSBA.L", "name": "HSBC", "category": "Financial Services",
                     "currency": "GBP", "quote_basis": "GBX"}))
    g.check("t8b entry(currency + family:etf) RAISES (no survivorship-clean misfile)",
            _raises({"symbol": "X.L", "name": "X", "category": "C",
                     "currency": "GBP", "quote_basis": "GBX", "family": "etf"}))
    g.check("t8c entry(currency + family:stock) is still ACCEPTED (the legitimate STOXX path)",
            register_catalog.entry({"symbol": "HSBA.L", "name": "HSBC",
                                    "category": "Financial Services", "currency": "GBP",
                                    "quote_basis": "GBX", "family": "stock"})["quote_basis"] == "GBX")


def live(g: Gate, tmp: Path) -> None:
    # t7 foreign-exchange round trip: a suffixed name + a London pence name
    os.environ["DATACORE_ROOT"] = str(tmp)
    try:
        raw = fetch_prices.fetch_prices(CFG, period="1mo",
                                        only=["px_sap_de_daily", "px_hsba_l_daily"])
        pushed = to_datacore.push(raw)
    finally:
        os.environ.pop("DATACORE_ROOT", None)
    ok = [r for r in pushed if r.get("ok")]
    g.check("t7a suffixed STOXX names (SAP.DE, HSBA.L) fetched + appended into temp root",
            len(ok) == 2 and all(r.get("appended", 0) > 0 for r in ok),
            "pushed=%s" % [(r["series_id"], r.get("appended"), r.get("skip_reason")) for r in pushed])
    sap = archive.read("px_sap_de_daily", root=tmp)
    g.check("t7b SAP.DE bars are value-faithful daily records",
            len(sap) > 3 and all({"value", "close", "split_factor"} <= set(b) for b in sap),
            "bars=%d" % len(sap))


def main() -> int:
    do_live = "--live" in sys.argv
    g = Gate()
    tmp = _seed_temp_root()
    print(f"P7a-2 STOXX gate: temp archive root = {tmp}")
    try:
        offline(g, tmp)
        if do_live:
            print("  --- live (network) ---")
            live(g, tmp)
        else:
            print("  (skipping live gates; pass --live for SAP.DE/HSBA.L fetch)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nP7a-2 STOXX gate: %d/%d PASS" % (g.total - len(g.fails), g.total))
    if g.fails:
        print("FAILED: " + ", ".join(g.fails))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

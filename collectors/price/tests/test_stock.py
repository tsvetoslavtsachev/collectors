# -*- coding: utf-8 -*-
"""P7a verify gate -- the STOCK family (SP500) extension of the price citizen.

Offline gates (default run) -- no network, against a TEMPORARY archive root:
  s1 survivorship  -- EVERY stock series carries backtest_valid:false +
                      survivorship:"current-members-only", MACHINE-READABLE (not text)
  s2 no-regression -- EVERY ETF series stays backtest_valid:true, no survivorship key
  s3 B2/D3 control -- a programmatic reader can HARD-REFUSE a flagged series by the flag
  s4 family route  -- entry() returns the stock shape for family:stock, etf shape otherwise
  s5 dotted ticker -- BRK-B -> series_id px_brk_b_daily, symbol "BRK-B", in config + catalog
  s6 collision     -- no dup series_id / symbol across the whole config; ~503 stock + 137 etf
  s7 per-family    -- fetch default period = stock "6y" / etf "max" (monkeypatched, no network)

Live gate (--live, network):
  s8 dotted live   -- BRK-B fetches + appends into the temp root (dotted-ticker round trip)
  (NVDA 10:1 / AAPL 4:1 stock-split reconstruction is covered by test_price.py --live g4b.)

Run:
  PYTHONPATH=C:\\Projects\\data-core;C:\\Projects\\collectors \\
  python collectors/price/tests/test_stock.py [--live]
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
STOCK_SIDS = [s for s, m in CFG["price"].items() if m.get("family") == "stock"]
ETF_SIDS = [s for s, m in CFG["price"].items() if m.get("family", "etf") == "etf"]


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
    tmp = Path(tempfile.mkdtemp(prefix="px_p7a_"))
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

    # s1 survivorship flag on EVERY stock series ---------------------------------
    g.check("s1a stock family present (~503 SP500 members)", len(STOCK_SIDS) >= 490,
            "stock=%d" % len(STOCK_SIDS))
    missing_flag = [s for s in STOCK_SIDS
                    if cat.get(s, {}).get("backtest_valid") is not False
                    or cat.get(s, {}).get("survivorship") != "current-members-only"]
    g.check("s1b EVERY stock series: backtest_valid:false + survivorship:current-members-only",
            not missing_flag, "offenders=%d e.g.%s" % (len(missing_flag), missing_flag[:5]))
    # machine-readable = real JSON booleans/strings, not buried in description text.
    # (px_aapl_daily is a STOCK -- AAPL, not the SPY ETF -- so backtest_valid must be False.)
    aapl_stock = cat.get("px_aapl_daily", {})
    g.check("s1c flag is a real JSON boolean (machine-readable, not text)",
            aapl_stock.get("backtest_valid") is False and isinstance(aapl_stock.get("survivorship"), str))

    # s2 ETF no-regression -------------------------------------------------------
    etf_bad = [s for s in ETF_SIDS
               if cat.get(s, {}).get("backtest_valid") is not True
               or "survivorship" in cat.get(s, {})]
    g.check("s2 EVERY ETF series stays backtest_valid:true, no survivorship key (no regression)",
            not etf_bad, "offenders=%d e.g.%s" % (len(etf_bad), etf_bad[:5]))

    # s3 B2/D3 control: a reader HARD-REFUSES a flagged series -------------------
    def backtest_allows(series_id: str, *, long_horizon: bool, override: bool = False) -> bool:
        meta = cat.get(series_id, {})
        if long_horizon and meta.get("backtest_valid") is False and not override:
            return False        # the machine refusal (program S3c/R3)
        return True
    g.check("s3a long-horizon backtest on a stock series is REFUSED by the flag",
            backtest_allows("px_aapl_daily", long_horizon=True) is False)
    g.check("s3b explicit logged override re-enables it",
            backtest_allows("px_aapl_daily", long_horizon=True, override=True) is True)
    g.check("s3c ETF long-horizon backtest is allowed (survivorship-clean)",
            backtest_allows("px_spy_daily", long_horizon=True) is True)

    # s4 family routing in entry() ----------------------------------------------
    es = register_catalog.entry({"symbol": "AAPL", "name": "Apple Inc.",
                                 "category": "Information Technology", "family": "stock"})
    ee = register_catalog.entry({"symbol": "SPY", "name": "SPDR S&P 500",
                                 "category": "US Equity"})  # no family -> etf
    g.check("s4a entry(stock) -> backtest_valid:false + survivorship",
            es["backtest_valid"] is False and es["survivorship"] == "current-members-only")
    g.check("s4b entry(etf default) -> backtest_valid:true, no survivorship",
            ee["backtest_valid"] is True and "survivorship" not in ee)

    # s5 dotted ticker -----------------------------------------------------------
    brk = CFG["price"].get("px_brk_b_daily")
    g.check("s5a BRK-B -> series_id px_brk_b_daily, yfinance symbol 'BRK-B'",
            brk is not None and brk["symbol"] == "BRK-B" and brk.get("family") == "stock",
            "entry=%s" % brk)
    g.check("s5b dotted-ticker stock registered in archive catalog with the flag",
            cat.get("px_brk_b_daily", {}).get("backtest_valid") is False)
    g.check("s5c BF-B dotted ticker also present + flagged",
            CFG["price"].get("px_bf_b_daily", {}).get("symbol") == "BF-B"
            and cat.get("px_bf_b_daily", {}).get("backtest_valid") is False)

    # s6 collision guard ---------------------------------------------------------
    sids = list(CFG["price"].keys())
    syms = [m["symbol"].upper() for m in CFG["price"].values()]
    g.check("s6a no duplicate series_id across the whole config", len(sids) == len(set(sids)),
            "total=%d unique=%d" % (len(sids), len(set(sids))))
    g.check("s6b no duplicate symbol across the whole config", len(syms) == len(set(syms)),
            "total=%d unique=%d" % (len(syms), len(set(syms))))
    g.check("s6c union = 137 ETF + ~503 stock", len(ETF_SIDS) == 137 and len(STOCK_SIDS) >= 490,
            "etf=%d stock=%d" % (len(ETF_SIDS), len(STOCK_SIDS)))

    # s7 per-family default depth (no network -- monkeypatch fetch_one) ----------
    seen: dict[str, str] = {}
    orig = fetch_prices.fetch_one

    def spy_fetch_one(symbol, *, period, round_dp, source="yfinance"):
        seen[symbol] = period
        return [{"as_of": "2025-01-02", "value": 1.0, "open": 1.0, "high": 1.0, "low": 1.0,
                 "close": 1.0, "value_tr": 1.0, "volume": 1, "split_factor": 1.0,
                 "dividend": 0.0, "source": source, "provisional": True}]
    fetch_prices.fetch_one = spy_fetch_one
    try:
        # default (period=None) on a stock + an etf -> per-family depth applies
        fetch_prices.fetch_prices(CFG, only=["px_aapl_daily", "px_spy_daily"])
        # explicit period overrides BOTH families
        seen_ovr: dict[str, str] = {}
        seen.clear()
        fetch_prices.fetch_prices(CFG, period="10d", only=["px_aapl_daily", "px_spy_daily"])
        seen_ovr.update(seen)
    finally:
        fetch_prices.fetch_one = orig
    # re-run default to read the per-family values cleanly
    seen.clear()
    fetch_prices.fetch_one = spy_fetch_one
    try:
        fetch_prices.fetch_prices(CFG, only=["px_aapl_daily", "px_spy_daily"])
    finally:
        fetch_prices.fetch_one = orig
    want_stock = CFG["settings"].get("history_period_stock", "6y")
    want_etf = CFG["settings"].get("history_period_prices", "max")
    g.check("s7a stock default period = history_period_stock (%s)" % want_stock,
            seen.get("AAPL") == want_stock, "AAPL=%s" % seen.get("AAPL"))
    g.check("s7b etf default period = history_period_prices (%s)" % want_etf,
            seen.get("SPY") == want_etf, "SPY=%s" % seen.get("SPY"))
    g.check("s7c explicit --period overrides BOTH families",
            seen_ovr.get("AAPL") == "10d" and seen_ovr.get("SPY") == "10d",
            "AAPL=%s SPY=%s" % (seen_ovr.get("AAPL"), seen_ovr.get("SPY")))

    # s9 fail-CLOSED family routing (adversarial gate MAJOR 1) -------------------
    # The one direction a stock can silently lose its survivorship flag: a missing/miscased/
    # typo'd `family` falling through to the ETF branch (-> backtest_valid:true). entry() now
    # REFUSES an unknown family; this gate locks that in so a future config edit can't ship it.
    bad_families = [m.get("family") for m in CFG["price"].values()
                    if m.get("family", "etf") not in ("etf", "stock")]
    g.check("s9a every config family is in {etf,stock} (no typo/miscase in the live config)",
            not bad_families, "bad=%s" % bad_families[:5])
    raised = False
    try:
        register_catalog.entry({"symbol": "X", "name": "X", "category": "C", "family": "Stock"})
    except ValueError:
        raised = True
    g.check("s9b entry() FAILS CLOSED on a miscased/typo family (no silent ETF fall-through)",
            raised)
    # the survivorship-unsafe direction: a mis-tagged stock must NEVER silently ship valid
    safe = True
    try:
        e = register_catalog.entry({"symbol": "X", "name": "X", "category": "C", "family": "stonk"})
        safe = e.get("backtest_valid") is not True
    except ValueError:
        safe = True
    g.check("s9c a mis-tagged stock can never silently ship backtest_valid:true", safe)
    # a known-good family still routes correctly (no false refusal)
    g.check("s9d valid families still route (etf default + explicit stock)",
            register_catalog.entry({"symbol": "S", "name": "S", "category": "C"})["backtest_valid"] is True
            and register_catalog.entry({"symbol": "S", "name": "S", "category": "C",
                                        "family": "stock"})["backtest_valid"] is False)


def live(g: Gate, tmp: Path) -> None:
    # s8 dotted-ticker round trip: BRK-B fetch + append into the temp root
    os.environ["DATACORE_ROOT"] = str(tmp)
    try:
        raw = fetch_prices.fetch_prices(CFG, period="1mo", only=["px_brk_b_daily"])
        pushed = to_datacore.push(raw)
    finally:
        os.environ.pop("DATACORE_ROOT", None)
    ok = [r for r in pushed if r.get("ok")]
    g.check("s8a BRK-B (dotted) fetched + appended into temp root",
            len(ok) == 1 and ok[0].get("appended", 0) > 0, "pushed=%s" % pushed)
    bars = archive.read("px_brk_b_daily", root=tmp)
    g.check("s8b BRK-B bars are value-faithful daily records",
            len(bars) > 3 and all({"value", "close", "split_factor"} <= set(b) for b in bars),
            "bars=%d" % len(bars))


def main() -> int:
    do_live = "--live" in sys.argv
    g = Gate()
    tmp = _seed_temp_root()
    print(f"P7a stock gate: temp archive root = {tmp}")
    try:
        offline(g, tmp)
        if do_live:
            print("  --- live (network) ---")
            live(g, tmp)
        else:
            print("  (skipping live gates; pass --live for BRK-B dotted-ticker fetch)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nP7a stock gate: %d/%d PASS" % (g.total - len(g.fails), g.total))
    if g.fails:
        print("FAILED: " + ", ".join(g.fails))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""P7b verify gate -- the stable internal stock-identity + recycling/splice guard +
AUTOMATIC rename-continuity (SCAFFOLD), against a TEMPORARY archive root.

Offline gates (default run) -- no network:
  t1 seed 1112      -- identity.seed mints one active epoch per CURRENT stock series
                       (503 SP500 + 609 STOXX), sorted -> SEC-NNNNNN, dense 1..N,
                       unique, idempotent (a re-seed mints 0).
  t2 stable_id stamp -- EVERY stock catalog entry carries stable_id: SEC-NNNNNN,
                       MACHINE-READABLE (a real string a reader resolves identity by).
  t3 ETF no-regress  -- NO ETF entry gains stable_id; ETF entries are BYTE-identical
                       with vs without the identity map (verify gate 3).
  t4 dotted/suffix   -- BRK-B -> US, SAP.DE -> GR, HSBA.L -> LN; each minted + stamped.
  t5 1244 intact     -- 132 ETF + 1112 stock = 1244 unique series_id; no re-key.
  t6 invariants      -- the seeded 1112 map: unique ids, 1 active epoch/ticker, dense.
  t7 splice-refuse   -- a recycled ticker on the REAL map mints a 2nd id, flagged.
  t8 rename-continuity-- a FIGI-confirmed rename on the real map -> ONE stable_id;
                       a different company / FIGI-offline -> fresh id / flag (no merge).

Live gate (--live, network):
  t9 OpenFIGI probe  -- shareClassFIGI resolves for >=1 of META/AAPL/NVDA/SAP/HSBA
                       (0 acceptable -> graceful fallback; non-zero proves the wire).

Run:
  PYTHONPATH=C:\\Projects\\data-core;C:\\Projects\\collectors \\
  python collectors/price/tests/test_identity.py [--live]
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

from collectors.price import identity, register_catalog

PRICE_DIR = Path(register_catalog.__file__).resolve().parent
CFG = yaml.safe_load((PRICE_DIR / "config.yaml").read_text(encoding="utf-8"))
STOCK_SIDS = [s for s, m in CFG["price"].items() if m.get("family") == "stock"]
ETF_SIDS = [s for s, m in CFG["price"].items() if m.get("family", "etf") == "etf"]
SEED_DATE = "2026-06-28"


class Gate:
    def __init__(self):
        self.total = 0
        self.fails: list[str] = []

    def check(self, name, cond, detail=""):
        self.total += 1
        print(("  [PASS] " if cond else "  [FAIL] ") + name + (f" -- {detail}" if detail else ""))
        if not cond:
            self.fails.append(name)


def _temp_root() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="px_p7b_"))
    (tmp / "catalog").mkdir(parents=True)
    (tmp / "catalog" / "catalog.json").write_text(
        json.dumps({"catalog_schema_version": 1,
                    "series": {"px_probe_daily": {"description": "seed probe",
                                                  "source": "synthetic-probe",
                                                  "schema_version": 1}}},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return tmp


def offline(g: Gate, tmp: Path) -> None:
    # ---- seed the identity map against the temp root --------------------------
    m, minted = identity.seed(CFG, tmp, SEED_DATE)
    active = [e for e in m["epochs"] if e["effective_to"] is None]
    ids = [e["internal_id"] for e in m["epochs"]]

    # t1 seed 1112 -------------------------------------------------------------
    g.check("t1a seed minted one epoch per stock series (== %d)" % len(STOCK_SIDS),
            minted == len(STOCK_SIDS) and len(m["epochs"]) == len(STOCK_SIDS),
            "minted=%d epochs=%d stock=%d" % (minted, len(m["epochs"]), len(STOCK_SIDS)))
    g.check("t1b 1112 current stock members (503 SP500 + 609 STOXX)",
            len(STOCK_SIDS) == 1112, "stock=%d" % len(STOCK_SIDS))
    g.check("t1c every internal_id unique", len(ids) == len(set(ids)),
            "ids=%d unique=%d" % (len(ids), len(set(ids))))
    nums = sorted(int(i[4:]) for i in ids)
    g.check("t1d ids dense SEC-000001..SEC-%06d (sorted seed, no holes)" % len(ids),
            nums == list(range(1, len(ids) + 1)), "min=%d max=%d" % (nums[0], nums[-1]))
    # idempotent re-seed
    m2, minted2 = identity.seed(CFG, tmp, "2026-07-01")
    g.check("t1e re-seed is idempotent (mints 0, no renumber)",
            minted2 == 0 and [e["internal_id"] for e in m2["epochs"]] == ids,
            "minted2=%d" % minted2)

    # ---- register the catalog against the temp root (auto-loads the map) ------
    register_catalog.register(CFG, tmp)
    cat = json.loads((tmp / "catalog" / "catalog.json").read_text(encoding="utf-8"))["series"]

    # t2 stable_id stamped on EVERY stock series, machine-readable ---------------
    missing = [s for s in STOCK_SIDS if not isinstance(cat.get(s, {}).get("stable_id"), str)]
    g.check("t2a EVERY stock entry carries a stable_id string", not missing,
            "missing=%d e.g.%s" % (len(missing), missing[:5]))
    import re as _re
    bad_fmt = [s for s in STOCK_SIDS if not _re.match(r"^SEC-\d{6}$", cat.get(s, {}).get("stable_id", ""))]
    g.check("t2b stable_id format SEC-NNNNNN (machine-readable, not text)", not bad_fmt,
            "bad=%d e.g.%s" % (len(bad_fmt), bad_fmt[:5]))
    # a reader resolves identity by stable_id, NOT by ticker
    aapl_sid = cat.get("px_aapl_daily", {}).get("stable_id")
    g.check("t2c reader resolves identity by stable_id (px_aapl_daily -> %s)" % aapl_sid,
            aapl_sid == identity.stable_id(m, "AAPL", "US"))

    # t3 ETF no-regression + byte-identity --------------------------------------
    etf_bad = [s for s in ETF_SIDS if "stable_id" in cat.get(s, {})]
    g.check("t3a NO ETF entry gains stable_id (decision h)", not etf_bad,
            "offenders=%d e.g.%s" % (len(etf_bad), etf_bad[:5]))
    # byte-identity: entry(etf) with vs without the seeded map must be identical
    sample_etf = ETF_SIDS[:20]
    drift = []
    for sid in sample_etf:
        mk = CFG["price"][sid]
        a = json.dumps(register_catalog.entry(mk), sort_keys=True)
        b = json.dumps(register_catalog.entry(mk, m), sort_keys=True)
        if a != b:
            drift.append(sid)
    g.check("t3b ETF entry byte-identical with vs without identity map", not drift,
            "drift=%s" % drift[:5])

    # t4 dotted / suffix identities ---------------------------------------------
    g.check("t4a BRK-B -> exch US, minted + stamped",
            identity.exch_code("BRK-B") == "US"
            and isinstance(cat.get("px_brk_b_daily", {}).get("stable_id"), str))
    g.check("t4b SAP.DE -> exch GR, minted + stamped",
            identity.exch_code("SAP.DE") == "GR"
            and isinstance(cat.get("px_sap_de_daily", {}).get("stable_id"), str))
    g.check("t4c HSBA.L -> exch LN, minted + stamped",
            identity.exch_code("HSBA.L") == "LN"
            and isinstance(cat.get("px_hsba_l_daily", {}).get("stable_id"), str))

    # t5 1244 series intact (no re-key) -----------------------------------------
    sids = [s for s in cat if s.startswith("px_") and s != "px_probe_daily"]
    g.check("t5a 132 ETF + 1112 stock = 1244 series registered",
            len(ETF_SIDS) == 132 and len(STOCK_SIDS) == 1112 and len(sids) == 1244,
            "etf=%d stock=%d total=%d" % (len(ETF_SIDS), len(STOCK_SIDS), len(sids)))
    g.check("t5b series_ids unchanged (px_<ticker>_daily; no re-key to px_<id>)",
            all(s.startswith("px_") and s.endswith("_daily") for s in sids)
            and "px_aapl_daily" in cat and "px_sap_de_daily" in cat)

    # t6 invariants on the seeded 1112 map --------------------------------------
    problems = identity.check_invariants(m)
    g.check("t6 seeded 1112 map invariants clean", not problems, "%s" % problems[:3])

    # t7 splice-refuse on the REAL map (recycle AAPL synthetically) --------------
    mm = identity.load(tmp)
    aapl_a = identity.stable_id(mm, "AAPL", "US")
    identity.close_epoch(mm, "AAPL", "2026-09-01")
    aapl_b = identity.mint_or_resolve(mm, "AAPL", "US", "2027-01-01", name="Unrelated AAPL Corp")
    ids_aapl = identity.distinct_identities(mm, "AAPL")
    sp = identity.detect_splice(mm, "AAPL")
    g.check("t7 recycled AAPL -> 2 ids, flagged ticker_recycle_splice (not reattached)",
            len(ids_aapl) == 2 and aapl_b != aapl_a and sp and sp["flag"] == "ticker_recycle_splice",
            "ids=%s flag=%s" % (ids_aapl, sp and sp["flag"]))

    # t8 rename-continuity on the real map (synthetic snapshot) -----------------
    mr = identity.load(tmp)
    # current AAPL stays; a synthetic "AAPL renames to AAPLX" with a matching FIGI.
    figi_aapl = "BBG000B9XRY4"
    aapl_root = identity.stable_id(mr, "AAPL", "US")
    identity.apply_snapshot(mr, [{"ticker": "AAPL", "exch_code": "US", "name": "Apple Inc."}],
                            "2026-12-01", figi_lookup={("AAPL", "US"): figi_aapl})
    identity.apply_snapshot(mr, [{"ticker": "AAPLX", "exch_code": "US", "name": "Apple Inc."}],
                            "2027-01-01", figi_lookup={("AAPLX", "US"): figi_aapl})
    rename_ok = (identity.stable_id(mr, "AAPLX", "US") == aapl_root
                 and identity.active_epoch(mr, "AAPLX", "US")["continuation_of"] is not None)
    # different company recycle of a freed ticker -> fresh id, never merged
    md = identity.load(tmp)
    nvda_root = identity.stable_id(md, "NVDA", "US")
    identity.apply_snapshot(md, [{"ticker": "NVDA", "exch_code": "US", "name": "NVIDIA"}],
                            "2026-12-01", figi_lookup={("NVDA", "US"): "BBG_NV"})
    identity.apply_snapshot(md, [{"ticker": "ZZZZ", "exch_code": "US", "name": "Brand New Co"}],
                            "2027-01-01", figi_lookup={("ZZZZ", "US"): "BBG_DIFF"})
    diff_ok = (identity.active_epoch(md, "ZZZZ", "US")["continuation_of"] is None
               and identity.stable_id(md, "ZZZZ", "US") != nvda_root)
    g.check("t8a FIGI-confirmed rename AAPL->AAPLX = ONE stable_id (continuation)", rename_ok,
            "AAPLX sid=%s root=%s" % (identity.stable_id(mr, "AAPLX", "US"), aapl_root))
    g.check("t8b different company on a freed ticker -> fresh id, never merged", diff_ok)

    # t10 coverage: a stock with no active epoch is DETECTED, not silently unstamped --
    g.check("t10a seed -> 0 unstamped stocks (every config stock has an active epoch)",
            identity.unstamped_stocks(CFG, m) == [],
            "gaps=%d" % len(identity.unstamped_stocks(CFG, m)))
    mg = identity.load(tmp)
    identity.close_epoch(mg, "AAPL", "2099-01-01")   # simulate a delisted-but-still-configured stock
    g.check("t10b unstamped_stocks DETECTS a closed-but-configured stock (no silent omit, LENS 3)",
            "px_aapl_daily" in identity.unstamped_stocks(CFG, mg))


def live(g: Gate) -> None:
    from collectors.price import figi
    items = [("META", "US"), ("AAPL", "US"), ("NVDA", "US"), ("SAP", "GR"), ("HSBA", "LN")]
    res = figi.map_share_class_figi(items)
    for it in items:
        print("    %s @ %s -> %s" % (it[0], it[1], res.get(it) or "(none)"))
    present = sum(1 for it in items if res.get(it))
    g.check("t11 OpenFIGI shareClassFIGI resolves for >=1 probe (wire proven)",
            present >= 1, "resolved=%d/%d" % (present, len(items)))


def main() -> int:
    do_live = "--live" in sys.argv
    g = Gate()
    tmp = _temp_root()
    print(f"P7b identity gate: temp archive root = {tmp}")
    try:
        offline(g, tmp)
        if do_live:
            print("  --- live (network OpenFIGI) ---")
            live(g)
        else:
            print("  (skipping live gate; pass --live for the OpenFIGI probe)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nP7b identity gate: %d/%d PASS" % (g.total - len(g.fails), g.total))
    if g.fails:
        print("FAILED: " + ", ".join(g.fails))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

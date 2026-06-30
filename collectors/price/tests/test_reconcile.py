# -*- coding: utf-8 -*-
"""P8c currency-reconcile gate -- fetch-time fast_info.currency vs catalog quote_basis (offline).

NO network: the live-currency lookup (``info_fn``) is INJECTED so a simulated re-denomination is
deterministic. Gates:
  r1 canon       -- GBp/GBX -> GBX; GBP (pounds) stays GBP (DISTINCT, 100x); 'eur' -> 'EUR'; None
  r2 match       -- a GBX series reporting GBp (pence) reconciles clean (no mismatch)
  r3 flip        -- a GBX series reporting GBP (pounds) OR USD is a DEFINITE mismatch (fail-loud)
  r4 unreachable -- info_fn None (metadata throttle) -> unverified (SOFT), never a mismatch
  r5 skip        -- a series with no quote_basis (ETF / SP500 USD) is not reconciled (0 calls)
  r6 neutralize  -- a mismatch is marked ok=False with a loud expected-vs-live reason
  r7 eur         -- a EUR series: EUR clean, USD a mismatch

Run:
  PYTHONPATH=C:\\Projects\\data-core;C:\\Projects\\collectors \\
  python collectors/price/tests/test_reconcile.py
"""
from __future__ import annotations

from pathlib import Path

import yaml

from collectors.price import reconcile, register_catalog

PRICE_DIR = Path(register_catalog.__file__).resolve().parent
CFG = yaml.safe_load((PRICE_DIR / "config.yaml").read_text(encoding="utf-8"))

GBX_SID = "px_hsba_l_daily"     # HSBA.L -- quote_basis GBX (London pence)
EUR_SID = "px_sap_de_daily"     # SAP.DE -- quote_basis EUR
SP_SID = "px_aapl_daily"        # AAPL   -- no quote_basis (SP500, USD)


class Gate:
    def __init__(self):
        self.total = 0
        self.fails: list[str] = []

    def check(self, name, cond, detail=""):
        self.total += 1
        print(("  [PASS] " if cond else "  [FAIL] ") + name + (f" -- {detail}" if detail else ""))
        if not cond:
            self.fails.append(name)


def _fixed(mapping):
    """An info_fn returning a fixed currency per SYMBOL (None if absent -> the unreachable path)."""
    def fn(symbol):
        return mapping.get(symbol)
    return fn


def main() -> int:
    g = Gate()
    sym = lambda sid: CFG["price"][sid]["symbol"]  # noqa: E731
    cc = reconcile.canon_currency

    # r1 canon -----------------------------------------------------------------------
    g.check("r1a GBp -> GBX (London pence)", cc("GBp") == "GBX", cc("GBp"))
    g.check("r1b GBX -> GBX", cc("GBX") == "GBX")
    g.check("r1c GBP (pounds) stays GBP, DISTINCT from GBX (100x)",
            cc("GBP") == "GBP" and cc("GBP") != cc("GBX"))
    g.check("r1d lowercase 'eur' -> EUR", cc("eur") == "EUR")
    g.check("r1e None -> None", cc(None) is None)

    # r2 match: GBX series reporting GBp pence reconciles clean ----------------------
    mism, unv = reconcile.reconcile(CFG, [GBX_SID], info_fn=_fixed({sym(GBX_SID): "GBp"}))
    g.check("r2 GBX series reporting GBp (pence) -> clean (no mismatch, no unverified)",
            mism == {} and unv == [], f"mism={mism} unv={unv}")

    # r3 flip: GBX series reporting GBP (pounds) or USD -> mismatch (fail-loud) ------
    mism2, _ = reconcile.reconcile(CFG, [GBX_SID], info_fn=_fixed({sym(GBX_SID): "GBP"}))
    g.check("r3a GBX series reporting GBP (pounds) is a DEFINITE mismatch (the GBX/GBP flip gate)",
            GBX_SID in mism2, f"mism={mism2}")
    mism3, _ = reconcile.reconcile(CFG, [GBX_SID], info_fn=_fixed({sym(GBX_SID): "USD"}))
    g.check("r3b GBX series reporting USD (sterling->dollar re-denomination) fires",
            GBX_SID in mism3, f"mism={mism3}")

    # r4 unreachable -> SOFT unverified, never a mismatch ----------------------------
    mism4, unv4 = reconcile.reconcile(CFG, [GBX_SID], info_fn=_fixed({}))
    g.check("r4 unreachable fast_info -> unverified (SOFT), not a mismatch",
            mism4 == {} and unv4 == [GBX_SID], f"mism={mism4} unv={unv4}")

    # r5 a no-quote_basis series is skipped (info_fn never called) -------------------
    called: list = []

    def watch_fn(symbol):
        called.append(symbol)
        return "USD"

    mism5, unv5 = reconcile.reconcile(CFG, [SP_SID], info_fn=watch_fn)
    g.check("r5 a no-quote_basis series (SP500/ETF) is skipped -- info_fn never called",
            mism5 == {} and unv5 == [] and called == [], f"called={called}")

    # r6 neutralize marks the mismatched series ok=False loudly ----------------------
    raw = {GBX_SID: {"ok": True, "records": [{"as_of": "2026-06-25", "close": 5000.0}]}}
    reconcile.neutralize(raw, {GBX_SID: ("GBX", "USD")})
    g.check("r6 neutralize marks the mismatched series ok=False with a loud reason",
            raw[GBX_SID]["ok"] is False and "quote_basis" in raw[GBX_SID]["error"],
            f"raw={raw[GBX_SID]}")

    # r7 EUR series: EUR clean, USD a mismatch --------------------------------------
    mism7, _ = reconcile.reconcile(CFG, [EUR_SID], info_fn=_fixed({sym(EUR_SID): "EUR"}))
    mism7b, _ = reconcile.reconcile(CFG, [EUR_SID], info_fn=_fixed({sym(EUR_SID): "USD"}))
    g.check("r7 EUR series: EUR clean, USD a mismatch",
            mism7 == {} and EUR_SID in mism7b, f"clean={mism7} flip={mism7b}")

    print("\nP8c reconcile gate: %d/%d PASS" % (g.total - len(g.fails), g.total))
    if g.fails:
        print("FAILED: " + ", ".join(g.fails))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Declare the px_* daily price series in the price-archive catalog (identity guard).

The P1 archive identity gate refuses any series_id it does not find in the archive's
dedicated ``<DATACORE_ROOT>/catalog/catalog.json``. This registrar reads config.yaml
and writes one catalog entry per configured price series, UPSERTING only the ``px_*``
namespace -- so the P2 probe series (px_probe_daily) and anything else stay untouched
while a re-run always reflects config.yaml.

Target = the PRICE-ARCHIVE checkout (DATACORE_ROOT), NOT the main data-core repo.
The vendored P1 ``assert_safe_root`` refuses to register against the real data-core
base (or with DATACORE_ROOT unset), so a forgotten env can never pollute the
canonical catalog with price identities.

These are IDENTITY-ONLY registrations -- ZERO prices. Real bars arrive in P4. Run:

    DATACORE_ROOT=C:\\Projects\\price-archive \\
    PYTHONPATH=C:\\Projects\\data-core;C:\\Projects\\collectors \\
    python -m collectors.price.register_catalog
"""
from __future__ import annotations
import json
import os
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent

_RECORD_FIELDS = (
    "value = split-adjusted close (headline, == close). Each record also carries "
    "open/high/low/close (split-adjusted OHLC), value_tr (fully-adjusted close), "
    "volume, split_factor (cumulative; as-traded = close * split_factor), dividend "
    "(cash on ex-date else 0.0), recorded_on (bitemporal vintage), provisional (true "
    "only for the not-yet-frozen tip)."
)


def entry(m: dict, identity_map: dict | None = None) -> dict:
    """Catalog entry for one px_*_daily series, routed by ``family`` (P7a).

    P7b (Option A): the STOCK branch stamps an ADDITIVE ``stable_id`` (the internal
    SEC-NNNNNN identity, resolved from ``identity_map`` -> the chain root, so a
    renamed company's tickers share one id) plus the optional ``isin`` /
    ``share_class_figi`` / ``continuation_of`` / ``review_flag`` carried on the
    active epoch. ``identity_map`` is OPTIONAL: when None/empty (a P7a-era caller, or
    a stock with no minted epoch) the stock entry is unchanged, and the ETF branch
    is NEVER touched -> the ETF/P7a catalog stays byte-identical (verify gate 3).

    ETF (default): ``backtest_valid: true`` -- ETFs survive (program Decision c, the
    survivorship-clean track). STOCK: ``backtest_valid: false`` +
    ``survivorship: "current-members-only"`` -- yfinance gives only CURRENT members,
    so a long-horizon backtest on this series is survivorship-biased. The flag is a
    MACHINE-READABLE control (program S3c/R3): the B2/D3 backtest harness HARD-REFUSES
    a flagged series for long-horizon use (an explicit, logged override is required) --
    NOT a passive text caveat. TA on ~6y of current members (50/200-DMA, RSI, MACD) is
    honest now; honest long-horizon stocks wait on P11 (paid point-in-time source)."""
    family = m.get("family", "etf")
    # P8c defense-in-depth (carry-forward #3): a currency-bearing entry MUST be a stock. Without
    # this, an entry carrying currency/quote_basis but MISSING family:stock would fall to the ETF
    # branch below -- which drops currency/quote_basis and stamps backtest_valid:true -- silently
    # misfiling a multi-currency STOXX series as a survivorship-clean ETF (and losing the per-series
    # basis the GBX /100 consumer keys on). 0/609 violate today; this keeps the seam closed.
    if m.get("currency") and family != "stock":
        raise ValueError(
            f"currency {m.get('currency')!r} on {m.get('symbol')!r} requires family=='stock', got "
            f"{family!r} -- a currency-bearing series must not route to the ETF (survivorship-clean) "
            f"branch.")
    # FAIL-CLOSED (adversarial gate MAJOR 1): a missing family defaults to "etf" (the ETF
    # entries legitimately omit it), but an EXPLICIT unknown/miscased value ('Stock', 'stk',
    # '') must NOT silently fall through to the ETF branch -- that is the one direction a stock
    # can lose its survivorship flag (-> backtest_valid:true) and pass every gate. Refuse it.
    if family not in ("etf", "stock"):
        raise ValueError(
            f"unknown family {family!r} for {m.get('symbol')!r} -- expected 'etf' or 'stock'. "
            f"Refusing to fail OPEN to the ETF branch (which would drop the survivorship flag).")
    if family == "stock":
        out = {
            "description": f"{m['name']} ({m['symbol']}) daily price bar -- "
                           f"split-adjusted OHLCV + fully-adjusted close + split/dividend "
                           f"factors. Current index constituent ({m['category']}), "
                           f"CURRENT-MEMBERS-ONLY (survivorship-flagged: backtest_valid=false). "
                           f"Written by collectors/price through datacore.archive "
                           f"(append-only, year-partitioned, bitemporal).",
            "source": "yfinance",
            "basis": "value = split-adjusted close; value_tr = fully-adjusted (Adj Close)",
            "frequency": "daily",
            "window": "open",
            "unit": "price",
            "schema_version": 1,
            "backtest_valid": False,
            "survivorship": "current-members-only",
            "family": "stock",
            "symbol": m["symbol"],
            "category": m["category"],
            "record_fields": _RECORD_FIELDS,
        }
        # Multi-currency families (STOXX600, P7a-2) carry currency + quote_basis per series
        # (decision 4a: store RAW; GBX = London pence -> /100 to GBP is a CONSUMER step, NOT
        # baked into the archive, same spirit as split_factor). currency comes from the index
        # source (iShares), NEVER inferred from the exchange suffix (IHG.L / CPG.L are USD on
        # the LSE). A single-currency family (SP500, USD) omits these -> absence means
        # "native major units"; presence is the machine-readable per-series basis.
        cur = m.get("currency")
        if cur:
            out["currency"] = cur
            out["quote_basis"] = m.get("quote_basis", cur)
            if out["quote_basis"] != cur:
                out["description"] += (f" Quoted in {out['quote_basis']} "
                                       f"(={cur} minor units; /100 -> {cur}).")
        # P7b: additive stable identity (side field; series_id stays px_<ticker>).
        if identity_map:
            from collectors.price import identity as _identity
            ex = _identity.exch_code(m["symbol"])
            sid_val = _identity.stable_id(identity_map, m["symbol"], ex)
            if sid_val:
                out["stable_id"] = sid_val
                ep = _identity.active_epoch(identity_map, m["symbol"], ex)
                if ep:
                    for k in ("isin", "share_class_figi", "continuation_of", "review_flag"):
                        if ep.get(k):
                            out[k] = ep[k]
        return out
    return {
        "description": f"{m['name']} ({m['symbol']}) daily price bar -- "
                       f"split-adjusted OHLCV + fully-adjusted close + split/dividend "
                       f"factors. ETF ({m['category']}), survivorship-clean. Written "
                       f"by collectors/price through datacore.archive (append-only, "
                       f"year-partitioned, bitemporal).",
        "source": "yfinance",
        "basis": "value = split-adjusted close; value_tr = fully-adjusted (Adj Close)",
        "frequency": "daily",
        "window": "open",
        "unit": "price",
        "schema_version": 1,
        "backtest_valid": True,
        "symbol": m["symbol"],
        "category": m["category"],
        "record_fields": _RECORD_FIELDS,
    }


def register(cfg: dict, root: Path, identity_map: dict | None = None) -> tuple[list, list]:
    """Upsert the px_* namespace into <root>/catalog/catalog.json. Returns
    (added, updated) series-id lists. Refuses an unsafe (real data-core) root.

    UPSERT-ONLY (additive): a px_* entry DROPPED from config.yaml (a delisted ETF, or a
    rename's old id) is NOT pruned here -- it lingers as an orphan identity with no
    writer. Pruning is a deliberate manual catalog edit, intentionally out of scope; a
    rename therefore leaves both old and new ids until the old is removed by hand.

    P7b: ``identity_map`` (the stock_identity map) is read from
    <root>/catalog/stock_identity.json when not passed; an absent map -> empty ->
    stock entries gain NO stable_id (backward-compatible with the P7a flow)."""
    # Cardinal-rule guard: refuse the real data-core base / unset env (P1) AND the
    # live price-archive (content-based -- assert_safe_root alone does NOT cover the
    # sibling price-archive). DATACORE_ALLOW_REAL=1 overrides for the P8 real register.
    from collectors.price import identity as _identity
    _identity.assert_temp_archive(root)

    if identity_map is None:
        identity_map = _identity.load(root)   # empty map if absent -> no stamps

    ns = cfg["settings"].get("archive_namespace", "px_")
    path = root / "catalog" / "catalog.json"
    cat = json.loads(path.read_text(encoding="utf-8"))
    series = cat.setdefault("series", {})

    added, updated = [], []
    for sid, m in cfg["price"].items():
        if not sid.startswith(ns):        # px_* namespace only -- never touch others
            continue
        (updated if sid in series else added).append(sid)
        series[sid] = entry(m, identity_map)   # upsert (stock branch stamps stable_id)

    path.write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return added, updated


def main() -> int:
    env = os.environ.get("DATACORE_ROOT")
    if not env:
        raise SystemExit("REFUSED: set DATACORE_ROOT to the price-archive checkout")
    root = Path(env).resolve()
    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))
    added, updated = register(cfg, root)
    print(f"price catalog @ {root}: {len(added)} added, {len(updated)} updated")
    total = len(json.loads((root / 'catalog' / 'catalog.json').read_text(encoding='utf-8'))['series'])
    print(f"catalog now: {total} series")
    for sid in added[:5]:
        print("  +", sid)
    if len(added) > 5:
        print(f"  ... (+{len(added) - 5} more)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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


def entry(m: dict) -> dict:
    """Catalog entry for one px_*_daily series. ``backtest_valid: true`` because
    ETFs survive (program Decision c -- the survivorship-clean track); the stock
    family in P7 will carry ``backtest_valid: false`` instead."""
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


def register(cfg: dict, root: Path) -> tuple[list, list]:
    """Upsert the px_* namespace into <root>/catalog/catalog.json. Returns
    (added, updated) series-id lists. Refuses an unsafe (real data-core) root.

    UPSERT-ONLY (additive): a px_* entry DROPPED from config.yaml (a delisted ETF, or a
    rename's old id) is NOT pruned here -- it lingers as an orphan identity with no
    writer. Pruning is a deliberate manual catalog edit, intentionally out of scope; a
    rename therefore leaves both old and new ids until the old is removed by hand."""
    # Reuse the P1 cardinal-rule guard: refuse the real data-core base / unset env.
    from datacore.archive import assert_safe_root
    assert_safe_root(root)

    ns = cfg["settings"].get("archive_namespace", "px_")
    path = root / "catalog" / "catalog.json"
    cat = json.loads(path.read_text(encoding="utf-8"))
    series = cat.setdefault("series", {})

    added, updated = [], []
    for sid, m in cfg["price"].items():
        if not sid.startswith(ns):        # px_* namespace only -- never touch others
            continue
        (updated if sid in series else added).append(sid)
        series[sid] = entry(m)            # upsert

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

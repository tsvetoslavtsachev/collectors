"""collectors/price/rate_futures.py -- the rate-futures strip (ПЪТ1, 22.11.2026).

Two CME strips from Yahoo, no key, no terminal:

  * ZQ  -- 30-day Fed Funds futures, one contract per calendar month.
           price = 100 - the expected AVERAGE effective fed funds rate of that month.
           Yahoo gives daily history for listed contracts (about two years back).
  * SR3 -- 3-month SOFR futures, quarterly (H M U Z). price = 100 - the expected
           compounded SOFR over the reference quarter. Yahoo gives only the LAST bar
           (no history), so this archive's SR3 history starts on the first run and grows.

The contract list is DERIVED FROM THE DATE (``contracts(on)``), never written by hand:
a new contract appears by itself when Yahoo lists it, an expired one stops being fetched
and stays in the archive with its last bar. Each contract is its own series
``px_<root><code><yy>_<exch>_daily`` (``ZQZ26.CBT`` -> ``px_zqz26_cbt_daily``), written
through the same P1 primitive as every other price (``datacore.archive.append``) into the
price-archive. A contract not yet in the archive catalog is registered here (upsert of
that one entry, ``family: "future"``) -- the ETF/stock ``register_catalog`` never sees it,
because these series are NOT in config.yaml's ``price:`` block (the ETF daily, its
verify_backfill freshness gate and the stock identity gates stay byte-identical).

Usage (DATACORE_ROOT must point at the price-archive, as for ``collectors.price.run``):
    python -m collectors.price.rate_futures --daily            # 1mo window (routine)
    python -m collectors.price.rate_futures --period max       # seed: full Yahoo history
    python -m collectors.price.rate_futures --list [--on DATE] # print the derived strip, no fetch

The numbers are prices of futures, not a forecast and not WIRP.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
MONTH_CODES = "FGHJKMNQUVXZ"
QUARTER_MONTHS = (3, 6, 9, 12)


# --------------------------------------------------------------------------- strip

def add_months(y: int, m: int, k: int) -> tuple[int, int]:
    i = y * 12 + (m - 1) + k
    return i // 12, i % 12 + 1


def third_wednesday(y: int, m: int) -> date:
    first = date(y, m, 1)
    return first + timedelta(days=(2 - first.weekday()) % 7 + 14)


def symbol(root: str, y: int, m: int, exch: str) -> str:
    return f"{root}{MONTH_CODES[m - 1]}{y % 100:02d}.{exch}"


def series_id(sym: str) -> str:
    return "px_" + sym.lower().replace(".", "_") + "_daily"


def parse_symbol(sym: str) -> dict:
    """``ZQZ26.CBT`` -> {root: ZQ, year: 2026, month: 12, exch: CBT}. Fails loud."""
    body, exch = sym.split(".")
    root, code, yy = body[:-3], body[-3], body[-2:]
    if code not in MONTH_CODES or not yy.isdigit():
        raise ValueError(f"not a futures symbol: {sym!r}")
    return {"root": root, "year": 2000 + int(yy), "month": MONTH_CODES.index(code) + 1, "exch": exch}


def contracts(on: date, spec: dict) -> list[dict]:
    """The strip that may be listed on ``on``, derived from the date alone.

    ZQ: the month containing ``on`` (it trades to its last business day) and the next
    ``months_ahead`` months. SR3: from the contract whose reference quarter (3rd Wednesday
    of its month to the 3rd Wednesday three months later) contains ``on``, then
    ``quarters_ahead`` quarterly contracts in all.
    Contracts Yahoo does not list come back as 404 and are reported, not invented."""
    out = []
    for root, s in spec.items():
        exch = s["exchange"]
        if s["cycle"] == "monthly":
            months = [add_months(on.year, on.month, k) for k in range(s["months_ahead"] + 1)]
        elif s["cycle"] == "quarterly":
            # the contract whose reference quarter [3rd Wed M, 3rd Wed M+3) contains `on`
            y, m = on.year, on.month
            while m not in QUARTER_MONTHS:
                y, m = add_months(y, m, -1)
            if on < third_wednesday(y, m):
                y, m = add_months(y, m, -3)
            months = [add_months(y, m, 3 * k) for k in range(s["quarters_ahead"])]
        else:
            raise ValueError(f"unknown cycle {s['cycle']!r} for {root}")
        for y, m in months:
            sym = symbol(root, y, m, exch)
            out.append({"root": root, "symbol": sym, "series_id": series_id(sym),
                        "contract_month": f"{y:04d}-{m:02d}", "name": s["name"],
                        "underlying": s["underlying"]})
    return out


# --------------------------------------------------------------------------- catalog

def catalog_entry(c: dict) -> dict:
    return {
        "description": (f"{c['name']} {c['contract_month']} ({c['symbol']}) daily bar from Yahoo. "
                        f"price = 100 - implied rate ({c['underlying']}). Written by "
                        "collectors/price/rate_futures through datacore.archive (append-only, "
                        "year-partitioned, bitemporal). Futures prices, not a forecast."),
        "source": "yfinance",
        "basis": "value = close (price = 100 - implied rate); no splits, no dividends",
        "frequency": "daily",
        "window": "open",
        "unit": "price",
        "schema_version": 1,
        "backtest_valid": False,
        "history_note": ("Yahoo serves only LISTED contracts: a contract that expired before this "
                         "archive first collected it is absent (ПЪТ1: ZQ before 2026-11, SR3 before "
                         "2026-11-22). SR3 has no Yahoo history: its bars start on the first run."),
        "symbol": c["symbol"],
        "category": "Rate Future",
        "family": "future",
        "contract_root": c["root"],
        "contract_month": c["contract_month"],
        "underlying": c["underlying"],
        "record_fields": ("value = close. open/high/low/close, value_tr (== close), volume, "
                          "split_factor (1.0), dividend (0.0), recorded_on, provisional (true only "
                          "for the not-yet-frozen tip; an expired contract's last bar can stay "
                          "provisional because no later bar arrives)."),
    }


def register(root: Path, todo: list[dict], log=print) -> int:
    """Upsert catalog entries for contracts not yet registered. Returns how many were added.
    Only ever ADDS ``family: "future"`` entries; never rewrites an existing entry."""
    path = Path(root) / "catalog" / "catalog.json"
    cat = json.loads(path.read_text(encoding="utf-8"))
    reg = cat["series"]
    added = 0
    for c in todo:
        if c["series_id"] in reg:
            continue
        reg[c["series_id"]] = catalog_entry(c)
        added += 1
    if added:
        with open(path, "w", encoding="utf-8", newline="\n") as f:   # LF like the committed catalog
            f.write(json.dumps(cat, ensure_ascii=False, indent=2) + "\n")
        log(f"  catalog: +{added} rate-future entries")
    return added


# --------------------------------------------------------------------------- run

def load_spec(cfg: dict | None = None) -> tuple[dict, dict]:
    cfg = cfg or yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))
    return cfg["rate_futures"], cfg["settings"]


TIP_MAX_AGE_DAYS = 7   # a history:false root keeps its newest bar only if it is this fresh


def fetch(strip: list[dict], period: str, round_dp: int, *, on: date, spec: dict,
          fetch_one=None, log=print) -> dict:
    """{series_id: records} for the contracts Yahoo returned; the rest in ``missing``.
    A root with ``period`` in its spec uses it; ``history: false`` keeps the newest bar only."""
    if fetch_one is None:
        from .fetch_prices import fetch_one
    got, missing = {}, []
    for c in strip:
        s = spec[c["root"]]
        try:
            recs = fetch_one(c["symbol"], period=s.get("period", period), round_dp=round_dp)
        except Exception as e:  # noqa: BLE001 -- 404 / not listed / delisted: report, do not invent
            missing.append((c["symbol"], str(e)[:80]))
            continue
        if not s.get("history", True):
            tip = recs[-1]
            if (on - date.fromisoformat(tip["as_of"])).days > TIP_MAX_AGE_DAYS or date.fromisoformat(tip["as_of"]) > on:
                missing.append((c["symbol"], f"newest bar {tip['as_of']} is not within {TIP_MAX_AGE_DAYS} days of {on}"))
                continue
            tip["provisional"] = True
            recs = [tip]
        for r in recs:
            r["split_factor"], r["dividend"] = 1.0, 0.0
        got[c["series_id"]] = recs
    return {"got": got, "missing": missing}


def run(on: date, period: str, root, *, cfg=None, fetch_one=None, log=print) -> dict:
    from datacore import archive

    spec, settings = load_spec(cfg)
    strip = contracts(on, spec)
    res = fetch(strip, period, int(settings.get("round_dp", 6)), on=on, spec=spec,
                fetch_one=fetch_one, log=log)
    by_sid = {c["series_id"]: c for c in strip}
    root = Path(root)
    register(root, [by_sid[s] for s in res["got"]], log=log)
    cat = json.loads((root / "catalog" / "catalog.json").read_text(encoding="utf-8"))
    summary = []
    for sid, recs in res["got"].items():
        s = archive.append(sid, recs, root=str(root), catalog=cat)
        summary.append((sid, s.get("appended", 0), s.get("revised", 0), s.get("restated", 0)))
    listed = {r: [c for c in strip if c["root"] == r and c["series_id"] in res["got"]] for r in spec}
    for r, cs in listed.items():
        span = f"{cs[0]['contract_month']}..{cs[-1]['contract_month']}" if cs else "none"
        log(f"rate futures {r}: {len(cs)} contracts listed ({span})")
    # a hole INSIDE the listed span is suspicious; past the far end is just "not listed yet"
    holes = []
    for r, cs in listed.items():
        if not cs:
            continue
        lo, hi = cs[0]["contract_month"], cs[-1]["contract_month"]
        holes += [c["symbol"] for c in strip
                  if c["root"] == r and lo < c["contract_month"] < hi and c["series_id"] not in res["got"]]
    if holes:
        log(f"  WARN holes inside the listed strip (Yahoo returned nothing): {holes}")
    tot = sum(a for _, a, _, _ in summary)
    log(f"  appended {tot} bars into {len(summary)} series; not listed: {len(res['missing'])}")
    return {"strip": strip, "summary": summary, "missing": res["missing"], "holes": holes}


def _arg(args, flag):
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    on = date.fromisoformat(_arg(args, "--on")) if _arg(args, "--on") else date.today()
    spec, settings = load_spec()
    if "--list" in args:
        for c in contracts(on, spec):
            print(c["symbol"], c["series_id"], c["contract_month"])
        return 0
    period = _arg(args, "--period") or (settings.get("daily_period", "1mo") if "--daily" in args else None)
    if not period:
        print("give --daily or --period <yfinance period>", file=sys.stderr)
        return 2
    root = os.environ.get("DATACORE_ROOT")
    if not root:
        print("DATACORE_ROOT is not set (point it at the price-archive)", file=sys.stderr)
        return 2
    out = run(on, period, root)
    return 0 if out["summary"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

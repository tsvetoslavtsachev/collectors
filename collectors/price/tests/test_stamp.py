# -*- coding: utf-8 -*-
"""P8a verify gate -- record-level stable_id stamping + the archive value-governor
one-liner + the daily FAMILY-SCOPE guard. Offline, against a TEMPORARY archive root.

The P8a gates (mapping to the P8 mandate, section 3 / verify gate-ове):
  p8a1 governor    -- a stable_id that CHANGES for the same (series, as_of) on an
                      unchanged price is a SKIP, never a bitemporal restate and never a
                      same-vintage ArchiveError (datacore.archive._NON_VALUE_KEYS += stable_id).
                      WITHOUT the one-liner this exact case hard-errors -> a discriminating
                      proof, at the archive layer directly.
  p8a2 stock stamp -- to_datacore.push stamps the RESOLVED stable_id (the SEC-NNNNNN chain
                      ROOT from the catalog, == identity.stable_id) onto every stock bar,
                      ON DISK; bar stamp == catalog stamp (single source, no drift).
  p8a3 etf no-stamp -- an ETF bar carries NO stable_id key (byte-shape unchanged); the stamp
                      path is a no-op for the survivorship-clean family.
  p8a4 re-root skip -- push a finalized stock bar, then re-push the SAME price after the
                      catalog's stable_id is re-rooted -> SKIP (not restate, no error); the
                      stored line keeps its original stable_id. Governor + stamp integrated.
  p8a5 family-scope -- run._family_sids partitions etf/stock; `run --daily` selects ONLY the
                      daily_families (ETF) set, `--family stock` selects stocks, a bare full
                      pull is unscoped (only=None), --spot still wins. Закрива Капан 1.
  p8a6 permissive   -- a stock with NO seeded identity map pushes fine WITHOUT a stable_id and
                      WITHOUT refusing (backward-compat: P7a-era flow + d8e stay green; the
                      forward fail-closed on a SHOULD-be-stamped stock is P8b). + register
                      idempotent (a 2nd register adds 0).

Run:
  PYTHONPATH=C:\\Projects\\data-core;C:\\Projects\\collectors \\
  python collectors/price/tests/test_stamp.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

from datacore import archive
from collectors.price import to_datacore, register_catalog, identity, fetch_prices, run

PRICE_DIR = Path(register_catalog.__file__).resolve().parent
CFG = yaml.safe_load((PRICE_DIR / "config.yaml").read_text(encoding="utf-8"))
STOCK_SIDS = [s for s, m in CFG["price"].items() if m.get("family") == "stock"]
ETF_SIDS = [s for s, m in CFG["price"].items() if m.get("family", "etf") == "etf"]

R0, R1 = "2026-06-26", "2026-06-27"
D = "2026-06-25"
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


# --------------------------------------------------------------------------- #
# Helpers (mirror test_daily / test_identity)
# --------------------------------------------------------------------------- #
def _probe_catalog(tmp: Path) -> None:
    (tmp / "catalog").mkdir(parents=True)
    (tmp / "catalog" / "catalog.json").write_text(
        json.dumps({"catalog_schema_version": 1,
                    "series": {"px_probe_daily": {"description": "seed probe",
                                                  "source": "synthetic-probe",
                                                  "schema_version": 1}}},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _seeded_root() -> Path:
    """Temp root with the FULL 1112 identity map seeded then the catalog registered ->
    every stock entry carries its stable_id (the real P8 flow, minus the network)."""
    tmp = Path(tempfile.mkdtemp(prefix="px_p8a_seed_"))
    _probe_catalog(tmp)
    identity.seed(CFG, tmp, SEED_DATE)            # mints SEC-NNNNNN for all 1112, sorted
    register_catalog.register(CFG, tmp)           # auto-loads the map -> stamps stable_id
    return tmp


def _bare_root() -> Path:
    """Temp root registered WITHOUT a seeded map (the P7a-era / d8e flow): stocks are
    registered but carry NO stable_id."""
    tmp = Path(tempfile.mkdtemp(prefix="px_p8a_bare_"))
    _probe_catalog(tmp)
    register_catalog.register(CFG, tmp)
    return tmp


def _bar(as_of, value, *, stable_id=None, provisional=False, source="test"):
    r = {"as_of": as_of, "value": value, "open": value, "high": value, "low": value,
         "close": value, "value_tr": value, "volume": 1000, "split_factor": 1.0,
         "dividend": 0.0, "source": source}
    if stable_id is not None:
        r["stable_id"] = stable_id
    if provisional:
        r["provisional"] = True
    return r


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #
def offline(g: Gate) -> None:
    # p8a1 governor: a stable_id change on an unchanged price is a SKIP, not a restate ----
    tmp = Path(tempfile.mkdtemp(prefix="px_p8a_gov_"))
    SID = "px_test_daily"
    cat1 = {"series": {SID: {}}}
    archive.append(SID, [_bar(D, 100.0, stable_id="SEC-000001")],
                   root=str(tmp), catalog=cat1, recorded_on=R0)        # finalized
    raised = None
    try:
        res = archive.append(SID, [_bar(D, 100.0, stable_id="SEC-000002")],
                             root=str(tmp), catalog=cat1, recorded_on=R0)  # same price, new id, same vintage
    except Exception as e:  # noqa: BLE001
        raised, res = e, {}
    g.check("p8a1a a stable_id change on an unchanged price is a SKIP (not a restate)",
            raised is None and res.get("skipped") == 1 and res.get("restated", 0) == 0,
            f"raised={type(raised).__name__ if raised else None} res={res}")
    lines = [json.loads(ln) for f in (tmp / "archive" / SID).glob("*.jsonl")
             for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    g.check("p8a1b exactly ONE line on disk for the as_of (no restatement line added)",
            sum(1 for x in lines if x["as_of"] == D) == 1, f"lines={len(lines)}")
    g.check("p8a1c the stored line keeps its ORIGINAL stable_id (the skip changed nothing)",
            lines[0].get("stable_id") == "SEC-000001", f"stored={lines[0].get('stable_id')}")
    shutil.rmtree(tmp, ignore_errors=True)

    # p8a2 stock stamp on disk, == the catalog (resolved chain root, single source) -------
    tmp = _seeded_root()
    cat = to_datacore.load_catalog(tmp)
    m = identity.load(tmp)
    cat_sid = cat["series"]["px_nvda_daily"].get("stable_id")
    resolved = identity.stable_id(m, "NVDA", "US")
    to_datacore.push({"px_nvda_daily": {"ok": True, "records": [_bar(D, 100.0)]}},
                     root=str(tmp), catalog=cat, recorded_on=R0)
    disk = archive.read("px_nvda_daily", root=str(tmp))
    g.check("p8a2a the stock bar on disk carries stable_id == the catalog (resolved chain root)",
            len(disk) == 1 and disk[0].get("stable_id") == cat_sid == resolved
            and bool(re.match(r"^SEC-\d{6}$", disk[0].get("stable_id", ""))),
            f"bar={disk[0].get('stable_id') if disk else None} cat={cat_sid} resolved={resolved}")
    g.check("p8a2b the bar was stamped from the catalog, NOT the raw per-epoch internal_id",
            cat_sid == identity.stable_id(m, "NVDA", "US"),  # stable_id = chain ROOT, not internal_id
            f"cat={cat_sid}")

    # p8a3 ETF bar carries NO stable_id (byte-shape unchanged) ----------------------------
    to_datacore.push({"px_spy_daily": {"ok": True, "records": [_bar(D, 400.0)]}},
                     root=str(tmp), catalog=cat, recorded_on=R0)
    etf_disk = archive.read("px_spy_daily", root=str(tmp))
    g.check("p8a3a an ETF bar on disk carries NO stable_id key (stamp is a stock-only no-op)",
            len(etf_disk) == 1 and "stable_id" not in etf_disk[0], f"keys={sorted(etf_disk[0]) if etf_disk else None}")
    g.check("p8a3b NO ETF catalog entry carries stable_id (decision h, no regression)",
            not [s for s in ETF_SIDS if "stable_id" in cat["series"].get(s, {})])

    # p8a4 re-root skip: same price, re-rooted id -> SKIP (governor + stamp integrated) ----
    s1 = disk[0]["stable_id"]
    cat["series"]["px_nvda_daily"]["stable_id"] = "SEC-999999"          # simulate a rename re-root
    res4 = to_datacore.push({"px_nvda_daily": {"ok": True, "records": [_bar(D, 100.0)]}},
                            root=str(tmp), catalog=cat, recorded_on=R0)[0]
    after = archive.read("px_nvda_daily", root=str(tmp))
    g.check("p8a4a re-pushing the same price after a stable_id re-root is a clean SKIP",
            res4.get("ok") and res4.get("skipped", 0) >= 1 and res4.get("restated", 0) == 0,
            f"res={res4}")
    g.check("p8a4b the stored bar keeps its original stable_id (identity is bookkeeping, not price)",
            len(after) == 1 and after[0].get("stable_id") == s1, f"stored={after[0].get('stable_id')}")
    shutil.rmtree(tmp, ignore_errors=True)

    # p8a5 family-scope: partition + run --daily selection (closes Капан 1) ----------------
    etf_only = run._family_sids(CFG, ["etf"])
    stock_only = run._family_sids(CFG, ["stock"])
    g.check("p8a5a _family_sids partitions: %d etf + %d stock, disjoint" % (len(ETF_SIDS), len(STOCK_SIDS)),
            len(etf_only) == len(ETF_SIDS) == 132 and len(stock_only) == len(STOCK_SIDS)
            and set(etf_only).isdisjoint(stock_only),
            f"etf={len(etf_only)} stock={len(stock_only)}")
    g.check("p8a5b every etf-scope sid is family etf; every stock-scope sid is family stock",
            all(CFG["price"][s].get("family", "etf") == "etf" for s in etf_only)
            and all(CFG["price"][s].get("family") == "stock" for s in stock_only))

    # wire-level: run.main with --daily must scope to settings.daily_families, not the full
    # universe. P8b flipped daily_families to ["etf","stock"], so a bare --daily now selects the
    # etf+stock union; the registered-ready filter (which drops unregistered STOXX) is catalog-
    # gated and a no-op in this catalog-less test -> it is proven with a seeded catalog in
    # test_split_heal sh12. Derived from config so the assertion tracks the flip, not a constant.
    captured: dict = {}

    def fake_fetch(cfg, *, period=None, only=None):
        captured["only"] = only
        captured["period"] = period
        return {}

    orig_fetch, orig_push = run.fetch_prices, run.to_datacore.push
    run.fetch_prices = fake_fetch
    run.to_datacore.push = lambda raw, **kw: []
    saved_argv = sys.argv
    try:
        for argv, want, label in (
            (["x", "--daily"], set(run._family_sids(CFG, CFG["settings"]["daily_families"])),
             "p8a5c --daily scopes to settings.daily_families (etf+stock after P8b)"),
            (["x", "--daily", "--family", "stock"], set(stock_only), "p8a5d --family stock overrides to stocks"),
            (["x"], None, "p8a5e a bare full pull is unscoped (only=None)"),
            (["x", "--spot", "SPY"], {"px_spy_daily"}, "p8a5f --spot still wins (explicit symbols)"),
        ):
            captured.clear()
            sys.argv = argv
            run.main()
            got = captured.get("only")
            ok = (got is None) if want is None else (got is not None and set(got) == want)
            g.check(label, ok, f"only={'None' if got is None else len(got)} want={'None' if want is None else len(want)}")
    finally:
        run.fetch_prices, run.to_datacore.push, sys.argv = orig_fetch, orig_push, saved_argv

    # p8a6 permissive (backward-compat) + register idempotent -----------------------------
    bare = _bare_root()
    catb = to_datacore.load_catalog(bare)
    resb = to_datacore.push({"px_nvda_daily": {"ok": True, "records": [_bar(D, 100.0)]}},
                            root=str(bare), catalog=catb, recorded_on=R0)[0]
    bare_disk = archive.read("px_nvda_daily", root=str(bare))
    g.check("p8a6a a stock with NO seeded map pushes fine, WITHOUT a stable_id and WITHOUT refusing",
            resb.get("ok") and len(bare_disk) == 1 and "stable_id" not in bare_disk[0],
            f"res={resb} keys={sorted(bare_disk[0]) if bare_disk else None}")
    # a populated temp catalog trips the content-guard ("looks like the live archive"), so
    # the real P8 re-register opts in with DATACORE_ALLOW_REAL=1 (the gated workflow) --
    # mirror that here to exercise the upsert idempotency (added=0 on the 2nd pass).
    os.environ["DATACORE_ALLOW_REAL"] = "1"
    try:
        added2, _updated2 = register_catalog.register(CFG, bare)
    finally:
        os.environ.pop("DATACORE_ALLOW_REAL", None)
    g.check("p8a6b register is idempotent (a 2nd register adds 0 new, all upserted)",
            added2 == [] and len(_updated2) == 1244, f"added2={len(added2)} updated2={len(_updated2)}")
    shutil.rmtree(bare, ignore_errors=True)


def main() -> int:
    g = Gate()
    print("P8a stamp + governor + family-scope gate (offline, temp root)")
    offline(g)
    print("\nP8a gate: %d/%d PASS" % (g.total - len(g.fails), g.total))
    if g.fails:
        print("FAILED: " + ", ".join(g.fails))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

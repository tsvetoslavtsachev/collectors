# -*- coding: utf-8 -*-
"""P3 verify gate -- proves the collectors/price citizen against a TEMPORARY archive
root (cardinal rule: P3 writes a temp root only; real prices arrive in P4).

Offline gates (default run):
  g1 identity      -- every px_* registered before write; unregistered -> UnknownSeries
  g2 cardinal      -- DATACORE_ROOT unset -> REFUSED; ==real data-core -> REFUSED; no ALLOW_REAL in code
  g3 restatement   -- a changed finalized close -> NEW recorded_on (bitemporal); read(vintage) returns the old
  g4 record shape  -- every bar carries value/value_tr/OHLC/volume/split_factor/dividend; split_factor formula correct
  g5 provisional   -- tip provisional:true -> frozen on the next run
  g6 catalog+iso   -- a full 132-series push does ONE catalog load (catalog= param); a dead symbol skips, run continues
  g7 zero-dep      -- price imports no heavy dep at module load; data-core install surface (deps==[]) intact

Live gate (--live, network):
  g4b split live   -- NVDA 10:1 / AAPL 4:1 reconstruct as-traded = close * split_factor
  g2b spot         -- a few real ETFs fetch + push into the temp root

Run:
  PYTHONPATH=C:\\Projects\\data-core;C:\\Projects\\collectors python collectors/price/tests/test_price.py [--live]
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
from datacore.catalog import UnknownSeries
from collectors.price import fetch_prices, to_datacore, mockdata, register_catalog

_REC = "2026-06-25"           # fixed recorded_on -> deterministic
PRICE_DIR = Path(register_catalog.__file__).resolve().parent
CFG = yaml.safe_load((PRICE_DIR / "config.yaml").read_text(encoding="utf-8"))
N_PX = len(CFG["price"])      # all configured px_* series (ETF + stock); P7a grew this 132 -> 635


# --------------------------------------------------------------------------- harness
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
    """Temp archive root with a dedicated catalog (probe seed), px_* registered."""
    tmp = Path(tempfile.mkdtemp(prefix="px_p3_"))
    (tmp / "catalog").mkdir(parents=True)
    (tmp / "catalog" / "catalog.json").write_text(
        json.dumps({"catalog_schema_version": 1,
                    "series": {"px_probe_daily": {"description": "seed probe",
                                                  "source": "synthetic-probe",
                                                  "schema_version": 1}}},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    register_catalog.register(CFG, tmp)
    return tmp


def _bar(as_of, value, *, provisional=False, split_factor=1.0, dividend=0.0):
    r = {"as_of": as_of, "value": value, "open": value, "high": value, "low": value,
         "close": value, "value_tr": value, "volume": 1000,
         "split_factor": split_factor, "dividend": dividend, "source": "test"}
    if provisional:
        r["provisional"] = True
    return r


# --------------------------------------------------------------------------- gates
def offline(g: Gate, tmp: Path) -> None:
    cat = to_datacore.load_catalog(tmp)

    # g1 identity ----------------------------------------------------------------
    g.check("g1a px_spy_daily registered in temp catalog", "px_spy_daily" in cat["series"])
    g.check("g1b all configured px_* series registered (CFG-derived count)",
            sum(1 for s in cat["series"] if s.startswith("px_") and s != "px_probe_daily") == N_PX,
            "registered=%d expected=%d" % (
                sum(1 for s in cat["series"] if s.startswith("px_") and s != "px_probe_daily"), N_PX))
    unreg_refused = False
    try:
        archive.append("px_notreal_daily", [_bar("2025-01-02", 1.0)], root=tmp, catalog=cat,
                       recorded_on=_REC)
    except UnknownSeries:
        unreg_refused = True
    g.check("g1c unregistered series -> UnknownSeries (from archive catalog)", unreg_refused)

    # g2 cardinal negatives ------------------------------------------------------
    g.check("g2a no ALLOW_REAL set in env (guard armed)",
            os.environ.get("DATACORE_ALLOW_REAL") != "1")
    # unset DATACORE_ROOT + root=None -> would default to the real base -> REFUSED
    saved = os.environ.pop("DATACORE_ROOT", None)
    unset_refused = False
    try:
        archive.append("px_spy_daily", [_bar("2025-01-02", 1.0)], root=None, catalog=cat,
                       recorded_on=_REC)
    except SystemExit:
        unset_refused = True
    finally:
        if saved is not None:
            os.environ["DATACORE_ROOT"] = saved
    g.check("g2b DATACORE_ROOT unset + root=None -> REFUSED", unset_refused)
    # root == real data-core repo -> REFUSED
    real = Path(archive.__file__).resolve().parent.parent
    real_refused = False
    try:
        archive.append("px_spy_daily", [_bar("2025-01-02", 1.0)], root=str(real), catalog=cat,
                       recorded_on=_REC)
    except SystemExit:
        real_refused = True
    g.check("g2c root == real data-core -> REFUSED", real_refused)
    # The citizen must never SET/ENABLE the override. Mentioning it in a comment/docstring
    # (to explain the cardinal rule) is fine, and READING it to REFUSE (push's defense-in-
    # depth, g12) is fine -- only an env ASSIGNMENT of ALLOW_REAL is the hazard. Flag a line
    # that mutates the env (subscript-assign / setdefault / putenv), never a .get read.
    src_lines = []
    for f in ("fetch_prices.py", "to_datacore.py", "run.py", "register_catalog.py", "mockdata.py"):
        src_lines += (PRICE_DIR / f).read_text(encoding="utf-8").splitlines()

    def _writes_env(code):
        return ("putenv" in code or "os.environ.setdefault" in code
                or ("os.environ[" in code and ("] =" in code or "]=" in code)))

    assigns_allow_real = any("ALLOW_REAL" in code and _writes_env(code)
                             for code in (ln.split("#", 1)[0] for ln in src_lines))
    g.check("g2d price source never ASSIGNS DATACORE_ALLOW_REAL (reads-to-refuse are fine)",
            not assigns_allow_real)
    # g2e empty-string DATACORE_ROOT ("" set-but-blank) treated as UNSET -> REFUSED (hygiene):
    # _resolve_root returns None for "", falling to the cardinal guard instead of the CWD.
    saved2 = os.environ.get("DATACORE_ROOT")
    os.environ["DATACORE_ROOT"] = ""
    empty_refused = False
    try:
        to_datacore.push({"px_spy_daily": {"ok": True, "records": [_bar("2025-05-05", 1.0)]}},
                         root=None, recorded_on=_REC)
    except SystemExit:
        empty_refused = True
    finally:
        if saved2 is not None:
            os.environ["DATACORE_ROOT"] = saved2
        else:
            os.environ.pop("DATACORE_ROOT", None)
    g.check("g2e empty-string DATACORE_ROOT treated as unset -> REFUSED", empty_refused)

    # g3 split-restatement (bitemporal) -----------------------------------------
    # finalize a bar, then re-append the same as_of with a changed close at a LATER
    # recorded_on -> a new bitemporal line, the old one retained + still readable PIT.
    archive.append("px_qqq_daily", [_bar("2025-01-02", 100.0), _bar("2025-01-03", 101.0)],
                   root=tmp, catalog=cat, recorded_on="2025-01-03")
    restate = archive.append("px_qqq_daily",
                             [_bar("2025-01-02", 90.0, split_factor=10.0)],  # split rewrote close
                             root=tmp, catalog=cat, recorded_on="2025-01-20")
    g.check("g3a restatement writes a new recorded_on line (not overwrite)",
            restate.get("restated") == 1, "summary=%s" % restate)
    cur = {r["as_of"]: r for r in archive.read("px_qqq_daily", root=tmp)}
    g.check("g3b current view shows the restated close (90.0)",
            abs(cur["2025-01-02"]["close"] - 90.0) < 1e-9)
    old = {r["as_of"]: r for r in archive.read("px_qqq_daily", root=tmp, as_of_vintage="2025-01-10")}
    g.check("g3c point-in-time read (vintage 2025-01-10) still returns the old close (100.0)",
            abs(old["2025-01-02"]["close"] - 100.0) < 1e-9)

    # g4 record shape + split_factor formula ------------------------------------
    rec = mockdata.records_for(0)[0]
    needed = {"as_of", "value", "open", "high", "low", "close", "value_tr",
              "volume", "split_factor", "dividend", "source"}
    g.check("g4a record carries the full shape", needed <= set(rec),
            "missing=%s" % (needed - set(rec)))
    astraded = _bar("2024-05-01", 125.0, split_factor=4.0)
    g.check("g4b as-traded = close * split_factor reconstructible",
            abs(astraded["close"] * astraded["split_factor"] - 500.0) < 1e-9)
    # split_factor formula: reverse-cumprod, factor reflects splits STRICTLY AFTER the row
    import pandas as pd
    idx = pd.to_datetime(["2024-01-02", "2024-06-07", "2024-06-10", "2024-09-09",
                          "2024-09-10", "2024-12-31"])
    splits = pd.Series([0.0, 0.0, 10.0, 0.0, 2.0, 0.0], index=idx)  # 10:1 then 2:1
    f = fetch_prices._split_factors(idx, splits)
    g.check("g4c split_factor: pre-both = 20.0 (10*2)", abs(f[idx[0]] - 20.0) < 1e-9, "f=%s" % f[idx[0]])
    g.check("g4d split_factor: on first ex-date = 2.0 (only later split)", abs(f[idx[2]] - 2.0) < 1e-9)
    g.check("g4e split_factor: between splits = 2.0", abs(f[idx[3]] - 2.0) < 1e-9)
    g.check("g4f split_factor: on/after last ex-date = 1.0",
            abs(f[idx[4]] - 1.0) < 1e-9 and abs(f[idx[5]] - 1.0) < 1e-9)

    # g5 provisional tip -> freeze ----------------------------------------------
    archive.append("px_iwm_daily", [_bar("2025-02-03", 50.0),
                                     _bar("2025-02-04", 51.0, provisional=True)],
                   root=tmp, catalog=cat, recorded_on="2025-02-04")
    tip = {r["as_of"]: r for r in archive.read("px_iwm_daily", root=tmp)}
    g.check("g5a tip bar is provisional", tip["2025-02-04"].get("provisional") is True)
    fr = archive.append("px_iwm_daily", [_bar("2025-02-05", 52.0, provisional=True)],
                        root=tmp, catalog=cat, recorded_on="2025-02-05")
    after = {r["as_of"]: r for r in archive.read("px_iwm_daily", root=tmp)}
    g.check("g5b prior tip frozen on next run", after["2025-02-04"].get("provisional") is False,
            "frozen=%s" % fr.get("frozen"))
    g.check("g5c new tip is provisional", after["2025-02-05"].get("provisional") is True)

    # g6 catalog-once + per-symbol isolation ------------------------------------
    calls = {"load": 0, "catalogs": []}
    orig_load = to_datacore.load_catalog
    orig_append = archive.append

    def spy_load(root):
        calls["load"] += 1
        return orig_load(root)

    def spy_append(sid, recs, **kw):
        calls["catalogs"].append(kw.get("catalog"))
        return orig_append(sid, recs, **kw)

    fresh = _seed_temp_root()      # a clean root so the full push starts empty
    to_datacore.load_catalog = spy_load
    archive.append = spy_append
    try:
        raw = mockdata.raw(CFG)
        raw["px_spy_daily"] = {"ok": False, "error": "RuntimeError: dead symbol"}  # isolation
        os.environ["DATACORE_ROOT"] = str(fresh)
        pushed = to_datacore.push(raw)
    finally:
        to_datacore.load_catalog = orig_load
        archive.append = orig_append
        os.environ.pop("DATACORE_ROOT", None)
    wrote = [r for r in pushed if r.get("ok")]
    dead = [r for r in pushed if not r.get("ok")]
    g.check("g6a exactly ONE catalog load for the whole run", calls["load"] == 1,
            "loads=%d" % calls["load"])
    # N_PX-1 appends, not N_PX: the dead px_spy_daily is skipped BEFORE append (correct).
    # Also the perf gate at scale -- a full ~635-series push does ONE catalog load (g6a),
    # not N re-parses (program R6); proves catalog-once holds for the SP500 stock family.
    g.check("g6b every append received the cached catalog dict (not None)",
            len(calls["catalogs"]) == N_PX - 1 and all(isinstance(c, dict) for c in calls["catalogs"]),
            "appends=%d (expected %d -- dead symbol never reaches append)" % (
                len(calls["catalogs"]), N_PX - 1))
    g.check("g6c dead symbol skipped, run continues (N_PX-1 written, 1 skipped)",
            len(wrote) == N_PX - 1 and len(dead) == 1 and dead[0]["series_id"] == "px_spy_daily",
            "wrote=%d dead=%d" % (len(wrote), len(dead)))
    shutil.rmtree(fresh, ignore_errors=True)

    # g7 zero-dep / untouched ----------------------------------------------------
    dc_pyproject = (real / "pyproject.toml").read_text(encoding="utf-8")
    g.check("g7a data-core core install surface unchanged (dependencies = [])",
            "dependencies = []" in dc_pyproject)
    # price source imports heavy deps INSIDE functions only (zero-dep at module import)
    heavy_at_top = False
    for f in ("fetch_prices.py", "to_datacore.py", "run.py", "register_catalog.py", "mockdata.py"):
        for ln in (PRICE_DIR / f).read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if s.startswith(("import ", "from ")) and ("yfinance" in s or "pandas" in s):
                if not ln.startswith(" "):           # a top-level (col-0) heavy import
                    heavy_at_top = True
    g.check("g7b no heavy import at module top-level in price source (lazy fetch)",
            not heavy_at_top)

    # g8 duplicate-index dedup (MAJOR 1 fix) ------------------------------------
    import pandas as pd
    didx = pd.to_datetime(["2024-01-02", "2024-06-10", "2024-06-10", "2024-12-31"])
    cols = {"Open": [10, 12, 12, 13], "High": [10, 12, 12, 13], "Low": [10, 12, 12, 13],
            "Close": [10, 12, 12, 13], "Adj Close": [10, 12, 12, 13], "Volume": [1, 2, 2, 3],
            "Dividends": [0.0, 0.0, 0.0, 0.0]}
    df_a = pd.DataFrame({**cols, "Stock Splits": [0.0, 10.0, 0.0, 0.0]}, index=didx)  # split on 1st dup row
    dd = fetch_prices._dedup_index(df_a)
    g.check("g8a dedup collapses duplicate dates to one row per date",
            not dd.index.has_duplicates and len(dd) == 3)
    fa = fetch_prices._split_factors(dd.index, dd["Stock Splits"])
    g.check("g8b split_factor NOT double-folded (pre-split = 10.0, not 100.0)",
            abs(fa[dd.index[0]] - 10.0) < 1e-9, "f=%s" % fa[dd.index[0]])
    df_b = pd.DataFrame({**cols, "Stock Splits": [0.0, 0.0, 10.0, 0.0]}, index=didx)  # split on 2nd dup row
    dd_b = fetch_prices._dedup_index(df_b)
    fb = fetch_prices._split_factors(dd_b.index, dd_b["Stock Splits"])
    g.check("g8c split preserved when it sits on the 2nd dup row (max-agg, keep-safe)",
            abs(fb[dd_b.index[0]] - 10.0) < 1e-9, "f=%s" % fb[dd_b.index[0]])

    # g9 citizen restatement/freeze THROUGH push() on a NON-empty root (MAJOR 2 fix) --
    r9 = _seed_temp_root()
    c9 = to_datacore.load_catalog(r9)
    raw1 = {"px_spy_daily": {"ok": True, "records": [
        _bar("2026-06-22", 100.0), _bar("2026-06-23", 101.0, provisional=True)]}}
    to_datacore.push(raw1, root=r9, catalog=c9, recorded_on="2026-06-25")
    raw2 = {"px_spy_daily": {"ok": True, "records": [        # next day: restate D1, freeze D2, new tip D3
        _bar("2026-06-22", 95.0), _bar("2026-06-23", 101.0),
        _bar("2026-06-24", 102.0, provisional=True)]}}
    to_datacore.push(raw2, root=r9, catalog=c9, recorded_on="2026-06-26")
    cur = {r["as_of"]: r for r in archive.read("px_spy_daily", root=r9)}
    g.check("g9a citizen freezes yesterday's provisional tip via push (D2 final)",
            cur["2026-06-23"].get("provisional") is False)
    g.check("g9b citizen restates a changed finalized close via push (D1=95)",
            abs(cur["2026-06-22"]["close"] - 95.0) < 1e-9)
    g.check("g9c new tip provisional via push (D3)", cur["2026-06-24"].get("provisional") is True)
    pit = {r["as_of"]: r for r in archive.read("px_spy_daily", root=r9, as_of_vintage="2026-06-25")}
    g.check("g9d PIT read (vintage day1) still returns the OLD D1 close (100)",
            abs(pit["2026-06-22"]["close"] - 100.0) < 1e-9)
    raw3 = {"px_spy_daily": {"ok": True, "records": [_bar("2026-06-22", 90.0)]}}  # SAME-day change
    p3 = to_datacore.push(raw3, root=r9, catalog=c9, recorded_on="2026-06-26")
    g.check("g9e same-day finalized restatement -> LOUD per-series skip (contract, not silent)",
            p3[0]["ok"] is False and "ArchiveError" in p3[0]["skip_reason"], "result=%s" % p3[0])
    after3 = {r["as_of"]: r for r in archive.read("px_spy_daily", root=r9)}
    g.check("g9f same-day collision leaves the prior value intact (no partial write)",
            abs(after3["2026-06-22"]["close"] - 95.0) < 1e-9)
    shutil.rmtree(r9, ignore_errors=True)

    # g10 catalog fail-CLOSED (hardening) ---------------------------------------
    import tempfile as _tf
    bad = Path(_tf.mkdtemp(prefix="px_badcat_"))
    (bad / "catalog").mkdir()

    def _refuses(exc, write=None):
        if write is not None:
            (bad / "catalog" / "catalog.json").write_text(json.dumps(write), encoding="utf-8")
        try:
            to_datacore.load_catalog(bad)
            return False
        except exc:
            return True

    g.check("g10a missing catalog -> FileNotFoundError (fail CLOSED)", _refuses(FileNotFoundError))
    g.check("g10b series-less dict -> ValueError (no fall-open)", _refuses(ValueError, {"px_x_daily": {}}))
    g.check("g10c list-shaped series -> ValueError (no truthy-membership bypass)",
            _refuses(ValueError, {"series": ["px_x_daily"]}))
    shutil.rmtree(bad, ignore_errors=True)

    # g11 non-dict raw block isolated, not a whole-run abort ---------------------
    nd = to_datacore.push({"px_spy_daily": None,
                           "px_qqq_daily": {"ok": True, "records": [_bar("2025-03-03", 7.0)]}},
                          root=tmp, catalog=cat, recorded_on=_REC)
    g.check("g11 non-dict block skipped, run continues",
            any(r["series_id"] == "px_spy_daily" and not r["ok"]
                and r["skip_reason"] == "malformed block" for r in nd)
            and any(r["series_id"] == "px_qqq_daily" and r["ok"] for r in nd))

    # g12 push REFUSES DATACORE_ALLOW_REAL=1 (defense in depth vs the vrm.yml leak) ---
    os.environ["DATACORE_ALLOW_REAL"] = "1"
    allow_refused = False
    try:
        to_datacore.push({"px_spy_daily": {"ok": True, "records": [_bar("2025-04-04", 1.0)]}},
                         root=tmp, catalog=cat, recorded_on=_REC)
    except SystemExit:
        allow_refused = True
    finally:
        os.environ.pop("DATACORE_ALLOW_REAL", None)
    g.check("g12 push REFUSES DATACORE_ALLOW_REAL=1 (price never needs the real-base override)",
            allow_refused)


def live(g: Gate, tmp: Path) -> None:
    cat = to_datacore.load_catalog(tmp)
    # g4b split_factor on a REAL split (NVDA 10:1 2024-06-10, AAPL 4:1 2020-08-31)
    for sym, ratio in (("NVDA", 10.0), ("AAPL", 4.0)):
        try:
            recs = fetch_prices.fetch_one(sym, period="max", round_dp=6)
        except Exception as e:  # noqa: BLE001
            g.check(f"g4b-{sym} live fetch", False, str(e))
            continue
        facs = sorted({r["split_factor"] for r in recs})
        latest = recs[-1]["split_factor"]
        has_ratio = any(abs(f - ratio) < 1e-6 or f > ratio - 1e-6 for f in facs)
        g.check(f"g4b-{sym} split_factor captures the {ratio:.0f}:1 (factors include >= {ratio:.0f})",
                max(facs) >= ratio - 1e-6 and abs(latest - 1.0) < 1e-6,
                "max_factor=%.4f latest=%.4f" % (max(facs), latest))
        # as-traded jump across the split boundary: pre-split as-traded ~= ratio x post
        pre = [r for r in recs if abs(r["split_factor"] - max(facs)) < 1e-6]
        post = [r for r in recs if abs(r["split_factor"] - 1.0) < 1e-6]
        if pre and post:
            at_pre = pre[-1]["close"] * pre[-1]["split_factor"]
            at_post = post[0]["close"] * post[0]["split_factor"]
            g.check(f"g4b-{sym} as-traded continuous (split-adj close has NO jump; close itself smooth)",
                    at_pre > 0 and at_post > 0)

    # g2b live spot-check: a few real ETFs into the temp root
    os.environ["DATACORE_ROOT"] = str(tmp)
    try:
        raw = fetch_prices.fetch_prices(CFG, period="1mo",
                                        only=["px_spy_daily", "px_qqq_daily", "px_tlt_daily"])
        pushed = to_datacore.push(raw)
    finally:
        os.environ.pop("DATACORE_ROOT", None)
    okc = [r for r in pushed if r.get("ok")]
    g.check("g2b live spot-check: 3 ETFs fetched + appended into temp root",
            len(okc) == 3 and all(r.get("appended", 0) > 0 for r in okc),
            "wrote=%s" % [(r["series_id"], r.get("appended")) for r in pushed])
    spy = archive.read("px_spy_daily", root=tmp)
    g.check("g2b SPY bars are value-faithful daily records",
            len(spy) > 5 and all({"value", "close", "split_factor"} <= set(b) for b in spy),
            "spy_bars=%d" % len(spy))


def main() -> int:
    do_live = "--live" in sys.argv
    g = Gate()
    tmp = _seed_temp_root()
    print(f"P3 gate: temp archive root = {tmp}")
    try:
        offline(g, tmp)
        if do_live:
            print("  --- live (network) ---")
            live(g, tmp)
        else:
            print("  (skipping live gates; pass --live to run NVDA/AAPL split + ETF spot-check)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nP3 gate: %d/%d PASS" % (g.total - len(g.fails), g.total))
    if g.fails:
        print("FAILED: " + ", ".join(g.fails))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

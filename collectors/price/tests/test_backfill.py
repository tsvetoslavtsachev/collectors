# -*- coding: utf-8 -*-
"""P4 backfill gate -- proves the backfill DRIVER + VERIFY against a TEMPORARY archive
root (cardinal rule: the dress rehearsal writes a temp root; the real seed is a replay
of a verified temp, gated by two keys). NO network: a mock fetch drives the same control
flow as the live citizen.

Offline gates (default run):
  b1 two-key      -- guard refuses --real without/with-wrong confirm; passes with BACKFILL-REAL
  b2 batching     -- batched fetch->push seeds the whole universe; one fetch call per batch
  b3 resumable    -- a re-run is an idempotent no-op (appended=0, all skipped)
  b4 isolation    -- a dead symbol is skipped, the rest of the batch still lands
  b5 cardinal     -- backfill aimed at the real data-core base is REFUSED (SystemExit, uncaught)
  b6 promote      -- replay temp->real is byte-faithful + restatement-free (appended>0, restated=0)
  b7 verify-clean -- verify() passes its hard gates on a synthetic 1993-inception archive
  b8 verify-dup   -- verify() HARD-FAILS when a bitemporal duplicate vintage is present
  b9 verify-depth -- verify() HARD-FAILS when SPY does not reach its 1993 inception

Run:
  PYTHONPATH=<data-core>;<collectors> python collectors/price/tests/test_backfill.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

from datacore import archive
from collectors.price import backfill, to_datacore, mockdata, register_catalog, verify_backfill

PRICE_DIR = Path(register_catalog.__file__).resolve().parent
CFG = yaml.safe_load((PRICE_DIR / "config.yaml").read_text(encoding="utf-8"))
_REC = "2026-06-25"


class Gate:
    """Mirror of verify_backfill.Gate: a soft (hard=False) check WARNs, never fails -- so
    a Gate instance handed to verify() records only its hard-gate failures."""
    def __init__(self):
        self.total = 0
        self.fails: list[str] = []
        self.warns: list[str] = []

    def check(self, name, cond, detail="", hard=True):
        self.total += 1
        tag = "[PASS]" if cond else ("[FAIL]" if hard else "[WARN]")
        print(f"  {tag} {name}" + (f" -- {detail}" if detail else ""))
        if not cond and hard:
            self.fails.append(name)
        elif not cond:
            self.warns.append(name)


def _seed_temp_root() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="px_p4_"))
    (tmp / "catalog").mkdir(parents=True)
    (tmp / "catalog" / "catalog.json").write_text(
        json.dumps({"catalog_schema_version": 1,
                    "series": {"px_probe_daily": {"description": "seed probe",
                                                  "source": "synthetic-probe",
                                                  "schema_version": 1}}},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    register_catalog.register(CFG, tmp)
    return tmp


def _mock_fetch(cfg, *, period=None, only=None):
    """Same shape as fetch_prices, no network."""
    return mockdata.raw(cfg, only=only)


def _bar(as_of, value, **kw):
    r = {"as_of": as_of, "value": value, "open": value, "high": value, "low": value,
         "close": value, "value_tr": value, "volume": 1000,
         "split_factor": kw.get("split_factor", 1.0), "dividend": 0.0, "source": "test"}
    if kw.get("provisional"):
        r["provisional"] = True
    return r


def offline(g: Gate) -> None:
    # b1 two-key guard ----------------------------------------------------------
    def _refused(real, confirm):
        try:
            backfill.guard_real_write(real, confirm)
            return False
        except SystemExit:
            return True

    g.check("b1a --real with empty confirm -> REFUSED", _refused(True, ""))
    g.check("b1b --real with wrong confirm -> REFUSED", _refused(True, "nope"))
    g.check("b1c --real with BACKFILL-REAL -> allowed", not _refused(True, "BACKFILL-REAL"))
    g.check("b1d confirm WITHOUT --real -> REFUSED (foot-slip guard)",
            _refused(False, "BACKFILL-REAL"))
    g.check("b1e plain rehearsal (no real, no confirm) -> allowed", not _refused(False, ""))

    # b2 batching: whole universe seeded, one fetch per batch --------------------
    tmp = _seed_temp_root()
    calls = {"n": 0, "batch_sizes": []}

    def counting_fetch(cfg, *, period=None, only=None):
        calls["n"] += 1
        calls["batch_sizes"].append(len(only) if only else 0)
        return mockdata.raw(cfg, only=only)

    res = backfill.backfill(CFG, tmp, batch_size=25, batch_sleep=0, recorded_on=_REC,
                            fetch=counting_fetch, log=lambda *a: None)
    n_series = len(CFG["price"])
    expected_batches = (n_series + 24) // 25
    g.check("b2a one fetch call per batch (132 series / 25)",
            calls["n"] == expected_batches, "calls=%d expected=%d" % (calls["n"], expected_batches))
    g.check("b2b every configured series written",
            sum(1 for r in res.values() if r.get("ok")) == n_series,
            "ok=%d/%d" % (sum(1 for r in res.values() if r.get('ok')), n_series))
    g.check("b2c each batch <= batch_size", max(calls["batch_sizes"]) <= 25,
            "max=%d" % max(calls["batch_sizes"]))
    spy = archive.read("px_spy_daily", root=str(tmp))
    g.check("b2d a seeded series is readable with bars", len(spy) == 3)

    # b3 resumability: a re-run is an idempotent no-op --------------------------
    res2 = backfill.backfill(CFG, tmp, batch_size=25, batch_sleep=0, recorded_on=_REC,
                             fetch=_mock_fetch, log=lambda *a: None)
    appended2 = sum(r.get("appended", 0) for r in res2.values())
    skipped2 = sum(r.get("skipped", 0) for r in res2.values())
    g.check("b3a re-run appends nothing (idempotent resume)", appended2 == 0,
            "appended=%d" % appended2)
    g.check("b3b re-run skips every prior bar", skipped2 > 0, "skipped=%d" % skipped2)
    shutil.rmtree(tmp, ignore_errors=True)

    # b4 per-symbol isolation ---------------------------------------------------
    tmp = _seed_temp_root()

    def dead_fetch(cfg, *, period=None, only=None):
        raw = mockdata.raw(cfg, only=only)
        if "px_spy_daily" in raw:
            raw["px_spy_daily"] = {"ok": False, "error": "RuntimeError: dead symbol"}
        return raw

    res = backfill.backfill(CFG, tmp, batch_size=25, batch_sleep=0, recorded_on=_REC,
                            fetch=dead_fetch, log=lambda *a: None)
    g.check("b4a dead symbol marked not-ok, run continues",
            res["px_spy_daily"]["ok"] is False
            and sum(1 for r in res.values() if r.get("ok")) == len(CFG["price"]) - 1)
    g.check("b4b dead symbol never written to the archive",
            archive.read("px_spy_daily", root=str(tmp)) == [])
    shutil.rmtree(tmp, ignore_errors=True)

    # b5 cardinal: backfill at the real data-core base is REFUSED ----------------
    real = Path(archive.__file__).resolve().parent.parent   # the real data-core repo
    refused = False
    try:
        # mock fetch (no network) -> first push -> archive.append -> assert_safe_root REFUSES
        backfill.backfill(CFG, real, batch_size=25, batch_sleep=0, recorded_on=_REC,
                          only=["px_spy_daily"], fetch=_mock_fetch,
                          catalog={"series": {"px_spy_daily": {}}}, log=lambda *a: None)
    except SystemExit:
        refused = True
    g.check("b5 backfill aimed at the real data-core base -> REFUSED (uncaught SystemExit)",
            refused)

    # b6 promote (replay) is byte-faithful + restatement-free -------------------
    src = _seed_temp_root()
    backfill.backfill(CFG, src, batch_size=50, batch_sleep=0, recorded_on=_REC,
                      fetch=_mock_fetch, log=lambda *a: None)
    dst = _seed_temp_root()
    pres = backfill.promote(src, dst, recorded_on=_REC, log=lambda *a: None)
    appended = sum(r.get("appended", 0) for r in pres.values())
    restated = sum(r.get("restated", 0) for r in pres.values())
    g.check("b6a promote appends every bar into the real root", appended > 0, "appended=%d" % appended)
    g.check("b6b promote is restatement-free (single vintage seed)", restated == 0,
            "restated=%d" % restated)
    faithful = all(archive.read(sid, root=str(src)) == archive.read(sid, root=str(dst))
                   for sid in CFG["price"])
    g.check("b6c promoted archive is byte-faithful to the verified temp (read-equal)", faithful)
    shutil.rmtree(src, ignore_errors=True)
    shutil.rmtree(dst, ignore_errors=True)

    # b7/b8/b9 verify() gate logic on synthetic archives ------------------------
    # Build a clean 1993-inception archive: SPY back to 1993 + a split series, full shape.
    clean = _seed_temp_root()
    cat = to_datacore.load_catalog(clean)
    archive.append("px_spy_daily",
                   [_bar("1993-01-29", 43.0), _bar("2024-06-10", 540.0),
                    _bar("2024-06-11", 541.0, provisional=True)],
                   root=str(clean), catalog=cat, recorded_on=_REC)
    # a FORWARD-split series: factor 10 pre-split, 1.0 after (anchors at 1.0)
    archive.append("px_qqq_daily",
                   [_bar("2024-06-06", 12.0, split_factor=10.0), _bar("2024-06-07", 1.25),
                    _bar("2024-06-10", 1.26, provisional=True)],
                   root=str(clean), catalog=cat, recorded_on=_REC)
    # a REVERSE-split series (USO 1:8 -> 0.125 pre-split, 1.0 after): factor RISES over
    # time. This is the regression guard for the false "monotone non-increasing" v3 check.
    archive.append("px_uso_daily",
                   [_bar("2020-04-27", 2.5, split_factor=0.125), _bar("2020-04-29", 20.5),
                    _bar("2020-04-30", 21.0, provisional=True)],
                   root=str(clean), catalog=cat, recorded_on=_REC)
    gv = Gate()
    verify_backfill.verify(clean, CFG, gv)
    # This partial synthetic exercises the split/conflict/shape gates (v3/v4/v6). It does
    # NOT cover the full 132-series universe, so v1b (baseline) + v2a (coverage) legitimately
    # fail here -- those are proven to PASS by the live verify on the real complete temp seed.
    relevant = [f for f in gv.fails if f[:2] in ("v3", "v4", "v6")]
    g.check("b7a clean archive passes the split/conflict/shape gates (v3/v4/v6)",
            relevant == [], "unexpected=%s" % relevant)
    g.check("b7b verify() does NOT warn on v3 for a REVERSE-split series (reverse splits are valid)",
            not any(w.startswith("v3") for w in gv.warns), "v3_warns=%s" %
            [w for w in gv.warns if w.startswith("v3")])

    # b8 inject a bitemporal duplicate vintage -> v4 must HARD-FAIL
    archive.append("px_qqq_daily", [_bar("2024-06-07", 99.0)],   # restate finalized bar
                   root=str(clean), catalog=cat, recorded_on="2026-06-30")
    gv2 = Gate()
    verify_backfill.verify(clean, CFG, gv2)
    g.check("b8 verify() HARD-FAILS on a duplicate vintage (restatement in a seed)",
            any(f.startswith("v4") for f in gv2.fails), "fails=%s" % gv2.fails)
    shutil.rmtree(clean, ignore_errors=True)

    # b9 SPY without 1993 inception -> v1 must HARD-FAIL
    shallow = _seed_temp_root()
    cat2 = to_datacore.load_catalog(shallow)
    archive.append("px_spy_daily", [_bar("2020-01-02", 320.0),
                                     _bar("2020-01-03", 321.0, provisional=True)],
                   root=str(shallow), catalog=cat2, recorded_on=_REC)
    gv3 = Gate()
    verify_backfill.verify(shallow, CFG, gv3)
    g.check("b9 verify() HARD-FAILS when SPY misses its 1993 inception",
            any(f.startswith("v1") for f in gv3.fails), "fails=%s" % gv3.fails)
    shutil.rmtree(shallow, ignore_errors=True)

    # b10/b11 -- the ultracode-reproduced blockers: a TRUNCATED baselined ETF and an
    # INCOMPLETE seed must HARD-FAIL (the old gate passed both green). SPY reaches 1993
    # (v1a ok) but XLF is truncated to 2024 (26y lost) and 130/132 series are missing.
    trunc = _seed_temp_root()
    catt = to_datacore.load_catalog(trunc)
    archive.append("px_spy_daily", [_bar("1993-01-29", 43.0),
                                     _bar("2026-06-25", 600.0, provisional=True)],
                   root=str(trunc), catalog=catt, recorded_on=_REC)
    archive.append("px_xlf_daily", [_bar("2024-01-02", 38.0),     # truncated, true inception 1998
                                     _bar("2026-06-25", 52.0, provisional=True)],
                   root=str(trunc), catalog=catt, recorded_on=_REC)
    gv4 = Gate()
    verify_backfill.verify(trunc, CFG, gv4)
    g.check("b10 verify() HARD-FAILS on a truncated baselined ETF (v1b silent-truncation catch)",
            any(f.startswith("v1b") for f in gv4.fails), "fails=%s" % gv4.fails)
    g.check("b11 verify() HARD-FAILS on an incomplete seed (v2a coverage, dead series present)",
            any(f.startswith("v2a") for f in gv4.fails), "fails=%s" % gv4.fails)
    shutil.rmtree(trunc, ignore_errors=True)


def main() -> int:
    g = Gate()
    print("P4 backfill gate (offline, temp root)")
    offline(g)
    print("\nP4 backfill gate: %d/%d PASS" % (g.total - len(g.fails), g.total))
    if g.fails:
        print("FAILED: " + ", ".join(g.fails))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

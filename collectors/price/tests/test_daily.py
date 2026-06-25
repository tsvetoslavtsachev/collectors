# -*- coding: utf-8 -*-
"""P5 daily gate -- proves the ROUTINE daily increment (run --daily window + the daily
verify mode) against a TEMPORARY archive root. NO network: crafted daily-window fetch
blocks drive the SAME push->archive.append path the live citizen uses.

The 9 verify gates (mapping to the P5 mandate):
  d1 one-new-row   -- two consecutive daily runs append EXACTLY one new row/symbol; the
                      prior window bars are idempotent (a re-run appends nothing)
  d2 freeze        -- the prior provisional tip FINALIZES on the next run (revise-in-window
                      AND freeze-when-window-moved-past); exactly one provisional, at the tip
  d3 no-silent     -- a finalized bar NEVER silently overwrites: a value change is a bitemporal
                      restate (new recorded_on, prior line retained); a same-vintage change is
                      REFUSED (the stale value is dropped, the stored line untouched)
  d4 cascade-bound -- a value_tr (dividend) shift restates ONLY the in-window bars; history
                      OUTSIDE the window keeps its value_tr (the short window bounds the cascade)
  d5 year-blob     -- a current-year append re-writes ONLY the current-year file; prior-year
                      files stay byte-identical (the git year-partition invariant, file-level)
  d6 cardinal      -- real data-core root REFUSED; DATACORE_ROOT unset REFUSED; ALLOW_REAL=1
                      REFUSED; the daily workflow never SETS ALLOW_REAL (vrm.yml step not copied)
  d7 workflow      -- price-daily.yml is workflow_dispatch + scheduled, has NO two-key confirm
                      (routine, not the backfill), runs `run --daily` + `verify --daily`, pushes
                      origin + bus-factor backup
  d8 daily-verify  -- verify(--daily) PASSES a legitimate bitemporal restatement + frozen tip,
                      and HARD-FAILS a duplicate vintage / a mid-history provisional bar

(d9 -- "P1 archive.py byte-intact + citizen touched only per the agreed (a) extension" -- is a
git-level check done at build end, not a unit test.)

Run:
  PYTHONPATH=<data-core>;<collectors> python collectors/price/tests/test_daily.py
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

import yaml

from datacore import archive
from collectors.price import to_datacore, register_catalog, verify_backfill

PRICE_DIR = Path(register_catalog.__file__).resolve().parent
CFG = yaml.safe_load((PRICE_DIR / "config.yaml").read_text(encoding="utf-8"))

R0, R1, R2, R3 = "2026-06-25", "2026-06-26", "2026-06-27", "2026-06-28"


class Gate:
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


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _seed_temp_root() -> Path:
    """A temp archive root with the px_* universe registered (mirrors test_backfill)."""
    tmp = Path(tempfile.mkdtemp(prefix="px_p5_"))
    (tmp / "catalog").mkdir(parents=True)
    (tmp / "catalog" / "catalog.json").write_text(
        json.dumps({"catalog_schema_version": 1,
                    "series": {"px_probe_daily": {"description": "seed probe",
                                                  "source": "synthetic-probe",
                                                  "schema_version": 1}}},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    register_catalog.register(CFG, tmp)
    return tmp


def _bar(as_of, value, *, value_tr=None, split_factor=1.0, provisional=False, source="test"):
    r = {"as_of": as_of, "value": value, "open": value, "high": value, "low": value,
         "close": value, "value_tr": value if value_tr is None else value_tr,
         "volume": 1000, "split_factor": split_factor, "dividend": 0.0, "source": source}
    if provisional:
        r["provisional"] = True
    return r


def _run(tmp, cat, blocks, rec):
    """One daily run: push {sid: [bars]} through the citizen path; -> {sid: result_dict}."""
    raw = {sid: {"ok": True, "records": bars} for sid, bars in blocks.items()}
    res = to_datacore.push(raw, root=str(tmp), catalog=cat, recorded_on=rec)
    return {r["series_id"]: r for r in res}


def _seq(start_iso, n):
    d0 = date.fromisoformat(start_iso)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def _write_raw(tmp, sid, recs):
    """Write jsonl lines DIRECTLY (bypassing append) to stage an edge/corrupt archive for d8."""
    d = tmp / "archive" / sid
    d.mkdir(parents=True, exist_ok=True)
    byyear: dict[str, list] = {}
    for r in recs:
        byyear.setdefault(r["as_of"][:4], []).append(r)
    for yr, rs in byyear.items():
        (d / f"{yr}.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rs), encoding="utf-8")


def _find_daily_workflow():
    """price-daily.yml lives in the SEPARATE price-archive repo. Best-effort locate it for a
    local build (hard lint); absent in the collectors-only CI checkout (soft skip)."""
    for c in (PRICE_DIR.parents[3] / "price-archive" / ".github" / "workflows" / "price-daily.yml",
              Path("C:/Projects/price-archive/.github/workflows/price-daily.yml")):
        if c.exists():
            return c
    return None


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #
def offline(g: Gate) -> None:
    SID = "px_spy_daily"

    # d1 + d2: consecutive daily runs, one new row each, prior tip finalizes -------------
    tmp = _seed_temp_root()
    cat = to_datacore.load_catalog(tmp)
    # window0 establishes [D20 final, D22 final, D23 provisional-tip]
    _run(tmp, cat, {SID: [_bar("2026-06-20", 100), _bar("2026-06-22", 101),
                          _bar("2026-06-23", 102, provisional=True)]}, R0)
    # Run A: window slides one day -> D24 new tip; D23 finalizes
    a = _run(tmp, cat, {SID: [_bar("2026-06-22", 101), _bar("2026-06-23", 102),
                              _bar("2026-06-24", 103, provisional=True)]}, R1)[SID]
    # Run B: window slides again -> D25 new tip; D24 finalizes
    b = _run(tmp, cat, {SID: [_bar("2026-06-23", 102), _bar("2026-06-24", 103),
                              _bar("2026-06-25", 104, provisional=True)]}, R2)[SID]
    g.check("d1a Run A appends EXACTLY one new bar", a.get("appended") == 1,
            f"appended={a.get('appended')} revised={a.get('revised')} skipped={a.get('skipped')}")
    g.check("d1b Run B appends EXACTLY one new bar", b.get("appended") == 1,
            f"appended={b.get('appended')}")
    # idempotent re-run of Run B's window -> appends nothing, all skipped
    b2 = _run(tmp, cat, {SID: [_bar("2026-06-23", 102), _bar("2026-06-24", 103),
                               _bar("2026-06-25", 104, provisional=True)]}, R2)[SID]
    g.check("d1c re-running the same window is idempotent (appended=0, all skipped)",
            b2.get("appended") == 0 and b2.get("skipped") == 3,
            f"appended={b2.get('appended')} skipped={b2.get('skipped')}")

    view = archive.read(SID, root=str(tmp))
    provs = [r["as_of"] for r in view if r.get("provisional")]
    g.check("d2a exactly one provisional bar, and it is the tip",
            provs == ["2026-06-25"], f"provs={provs} tip={view[-1]['as_of']}")
    d24 = next(r for r in view if r["as_of"] == "2026-06-24")
    g.check("d2b the prior provisional tip finalized (revise path, window re-included it)",
            d24.get("provisional") is False, f"D24.provisional={d24.get('provisional')}")
    shutil.rmtree(tmp, ignore_errors=True)

    # d2c: the FREEZE path -- a window that JUMPED PAST the prior tip (a missed run) freezes it
    tmp = _seed_temp_root()
    cat = to_datacore.load_catalog(tmp)
    _run(tmp, cat, {SID: [_bar("2026-06-10", 90), _bar("2026-06-11", 91, provisional=True)]}, R0)
    f = _run(tmp, cat, {SID: [_bar("2026-06-15", 95), _bar("2026-06-16", 96, provisional=True)]}, R1)[SID]
    g.check("d2c freeze path: window past the prior tip finalizes it (frozen>=1)",
            f.get("frozen") == 1, f"frozen={f.get('frozen')} appended={f.get('appended')}")
    v = archive.read(SID, root=str(tmp))
    d11 = next(r for r in v if r["as_of"] == "2026-06-11")
    g.check("d2d frozen bar is now finalized, exactly one provisional (the new tip)",
            d11.get("provisional") is False
            and [r["as_of"] for r in v if r.get("provisional")] == ["2026-06-16"])
    shutil.rmtree(tmp, ignore_errors=True)

    # d3: no silent overwrite -- value change = bitemporal restate; same-vintage = refused ----
    tmp = _seed_temp_root()
    cat = to_datacore.load_catalog(tmp)
    _run(tmp, cat, {SID: [_bar("2026-06-22", 100), _bar("2026-06-23", 101, provisional=True)]}, R0)
    _run(tmp, cat, {SID: [_bar("2026-06-23", 101), _bar("2026-06-24", 102, provisional=True)]}, R1)
    # restate a FINALIZED bar (a split / vendor correction) with an ADVANCING vintage
    r = _run(tmp, cat, {SID: [_bar("2026-06-22", 999)]}, R2)[SID]
    raw_22 = [ln for ln in (tmp / "archive" / SID).glob("*.jsonl")
              for ln in ln.read_text(encoding="utf-8").splitlines()]
    n_22 = sum(1 for ln in raw_22 if json.loads(ln)["as_of"] == "2026-06-22")
    cur = next(x for x in archive.read(SID, root=str(tmp)) if x["as_of"] == "2026-06-22")
    old = next(x for x in archive.read(SID, root=str(tmp), as_of_vintage=R0)
               if x["as_of"] == "2026-06-22")
    g.check("d3a a changed finalized value is a bitemporal restate (restated=1)",
            r.get("restated") == 1, f"restated={r.get('restated')}")
    g.check("d3b both vintages retained on disk (2 lines for the as_of)", n_22 == 2, f"lines={n_22}")
    g.check("d3c current view shows the new value; point-in-time shows the old (retained)",
            cur["value"] == 999 and old["value"] == 100, f"cur={cur['value']} old={old['value']}")
    # a SAME-vintage change must be REFUSED (no silent overwrite of the latest line)
    ref = _run(tmp, cat, {SID: [_bar("2026-06-22", 555)]}, R2)[SID]
    cur2 = next(x for x in archive.read(SID, root=str(tmp)) if x["as_of"] == "2026-06-22")
    g.check("d3d a same-vintage change is REFUSED (per-series skip, not a silent overwrite)",
            ref.get("ok") is False and "ArchiveError" in str(ref.get("skip_reason")),
            f"reason={ref.get('skip_reason')}")
    g.check("d3e the stored value is unchanged after the refused same-vintage write",
            cur2["value"] == 999, f"value={cur2['value']}")
    shutil.rmtree(tmp, ignore_errors=True)

    # d4: value_tr (dividend) cascade is BOUNDED to the window ---------------------------
    tmp = _seed_temp_root()
    cat = to_datacore.load_catalog(tmp)
    days = _seq("2025-03-03", 30)                       # B0..B29 (B29 provisional tip)
    seed = [_bar(d, 100.0 + i, value_tr=100.0 + i,
                 provisional=(i == 29)) for i, d in enumerate(days)]
    _run(tmp, cat, {SID: seed}, R0)
    # an ex-dividend re-pull: ONLY the last 4 days re-fetched, their value_tr shifted ~0.1%;
    # value/close UNCHANGED so the restatement is driven purely by value_tr (the dividend axis).
    win = days[26:30] + ["2025-04-02"]                  # B26..B29 + a new bar B30
    block = []
    for j, d in enumerate(win):
        if d == "2025-04-02":
            block.append(_bar(d, 130.0, value_tr=130.0, provisional=True))
        else:
            i = 26 + j
            block.append(_bar(d, 100.0 + i, value_tr=round((100.0 + i) * 0.999, 6)))
    rr = _run(tmp, cat, {SID: block}, R1)[SID]
    g.check("d4a value_tr shift restates ONLY the in-window finalized bars (restated=3, not 29)",
            rr.get("restated") == 3, f"restated={rr.get('restated')} (history=29)")
    view = archive.read(SID, root=str(tmp))
    untouched_ok = all(
        next(x for x in view if x["as_of"] == days[i])["value_tr"] == 100.0 + i
        for i in range(26))                              # B0..B25 outside the window
    g.check("d4b history OUTSIDE the window keeps its original value_tr (cascade bounded)",
            untouched_ok)
    shutil.rmtree(tmp, ignore_errors=True)

    # d5: a current-year append re-writes ONLY the current-year file ---------------------
    tmp = _seed_temp_root()
    cat = to_datacore.load_catalog(tmp)
    _run(tmp, cat, {SID: [_bar("2024-12-30", 100), _bar("2024-12-31", 101),
                          _bar("2025-01-02", 102, provisional=True)]}, R0)
    y2024 = tmp / "archive" / SID / "2024.jsonl"
    before = y2024.read_bytes()
    res5 = _run(tmp, cat, {SID: [_bar("2025-01-02", 102),
                                 _bar("2025-01-03", 103, provisional=True)]}, R1)[SID]
    touched = res5.get("files_touched") or []
    g.check("d5a a 2025 append touches ONLY 2025.jsonl (not the prior-year file)",
            "2025.jsonl" in touched and "2024.jsonl" not in touched, f"touched={touched}")
    g.check("d5b the prior-year file is byte-identical after the current-year append",
            y2024.read_bytes() == before)
    shutil.rmtree(tmp, ignore_errors=True)

    # d6: cardinal-rule refusals --------------------------------------------------------
    tmp = _seed_temp_root()
    cat = to_datacore.load_catalog(tmp)
    real = Path(archive.__file__).resolve().parent.parent      # the real data-core repo
    refused_real = False
    try:
        to_datacore.push({SID: {"ok": True, "records": [_bar("2026-06-25", 1.0)]}},
                         root=str(real), catalog={"series": {SID: {}}}, recorded_on=R0)
    except SystemExit:
        refused_real = True
    g.check("d6a a daily write aimed at the real data-core base -> REFUSED (uncaught SystemExit)",
            refused_real)

    saved = os.environ.pop("DATACORE_ROOT", None)
    refused_unset = False
    try:
        to_datacore.push({SID: {"ok": True, "records": [_bar("2026-06-25", 1.0)]}},
                         catalog={"series": {SID: {}}}, recorded_on=R0)  # root=None, env unset
    except SystemExit:
        refused_unset = True
    finally:
        if saved is not None:
            os.environ["DATACORE_ROOT"] = saved
    g.check("d6b DATACORE_ROOT unset -> REFUSED (would default to the real base)", refused_unset)

    os.environ["DATACORE_ALLOW_REAL"] = "1"
    refused_allow = False
    try:
        to_datacore.push({SID: {"ok": True, "records": [_bar("2026-06-25", 1.0)]}},
                         root=str(tmp), catalog=cat, recorded_on=R0)
    except SystemExit:
        refused_allow = True
    finally:
        os.environ.pop("DATACORE_ALLOW_REAL", None)
    g.check("d6c push REFUSES DATACORE_ALLOW_REAL=1 (price never needs the real-base override)",
            refused_allow)
    shutil.rmtree(tmp, ignore_errors=True)

    # d6d/d7: the daily workflow lint (hard if co-located, soft skip in collectors-only CI) --
    wf = _find_daily_workflow()
    if wf is None:
        g.check("d7 price-daily.yml lint (price-archive not co-located -> skipped here)",
                True, "run from a full local checkout to lint the workflow", hard=False)
    else:
        text = wf.read_text(encoding="utf-8")
        def _sets_allow_real(t):
            # an ASSIGNMENT of ALLOW_REAL (env: KEY: 1 or KEY=1); a "do NOT set" comment is fine
            import re
            return bool(re.search(r"DATACORE_ALLOW_REAL\s*[:=]\s*[\"']?1", t))
        allow_ok = not _sets_allow_real(text)
        g.check("d6d the daily workflow never SETS DATACORE_ALLOW_REAL=1 (vrm.yml step not copied)",
                allow_ok, "" if allow_ok else "found a DATACORE_ALLOW_REAL=1 assignment in the workflow")
        g.check("d7a workflow is workflow_dispatch + scheduled (routine daily cadence)",
                "workflow_dispatch" in text and "schedule" in text and "cron" in text)
        g.check("d7b NO two-key confirm input (routine daily, not the one-time backfill)",
                "BACKFILL-REAL" not in text and "confirm:" not in text)
        verify_line_daily = any("verify_backfill" in ln and "--daily" in ln
                                for ln in text.splitlines())
        g.check("d7c runs the SHORT-window citizen (run --daily) AND the daily verify gate (verify --daily)",
                "run --daily" in text and verify_line_daily,
                "" if ("run --daily" in text and verify_line_daily)
                else "run --daily present=%s; verify line carries --daily=%s"
                     % ("run --daily" in text, verify_line_daily))
        g.check("d7d commits the current-year blob and mirrors to the bus-factor backup",
                "git add archive/" in text and "backup" in text)

    # d8: the daily verify mode -- passes a legit restatement, fails corruption -----------
    # legit: a finalized history + one bitemporal restatement (advancing vintage) + frozen tip.
    good = _seed_temp_root()
    catg = to_datacore.load_catalog(good)
    _run(good, catg, {SID: [_bar("1993-01-29", 43.0), _bar("2024-06-10", 540.0),
                            _bar("2024-06-11", 541.0, provisional=True)]}, R0)
    _run(good, catg, {SID: [_bar("2024-06-11", 541.0),
                            _bar("2024-06-12", 542.0, provisional=True)]}, R1)   # freeze 06-11
    _run(good, catg, {SID: [_bar("2024-06-10", 545.0)]}, R2)                     # restate 06-10
    gg = Gate()
    verify_backfill.verify(good, CFG, gg, daily=True)
    daily_fails = [f for f in gg.fails if f.startswith("v4")]
    g.check("d8a verify(--daily) PASSES a legitimate bitemporal restatement + frozen tip",
            daily_fails == [], f"unexpected v4 fails={daily_fails}")
    # the SAME archive under the SEED gate (no --daily) MUST fail v4 (proves the modes differ)
    gs = Gate()
    verify_backfill.verify(good, CFG, gs, daily=False)
    g.check("d8b the seed gate (no --daily) HARD-FAILS the same restated archive (modes differ)",
            any(f.startswith("v4") for f in gs.fails), f"seed fails={[f for f in gs.fails if f[:2]=='v4']}")
    shutil.rmtree(good, ignore_errors=True)

    # corruption 1: a DUPLICATE vintage (two lines, same recorded_on) -> v4a' HARD-FAIL
    bad = _seed_temp_root()
    _write_raw(bad, SID, [
        {**_bar("2024-06-10", 540.0), "series_id": SID, "schema_version": 1, "recorded_on": R0},
        {**_bar("2024-06-10", 545.0), "series_id": SID, "schema_version": 1, "recorded_on": R0},
        {**_bar("2024-06-11", 541.0, provisional=True), "series_id": SID,
         "schema_version": 1, "recorded_on": R0},
    ])
    gb = Gate()
    verify_backfill.verify(bad, CFG, gb, daily=True)
    g.check("d8c verify(--daily) HARD-FAILS a duplicate vintage (unreachable prior = silent overwrite)",
            any(f.startswith("v4a'") for f in gb.fails), f"fails={[f for f in gb.fails if f[:2]=='v4']}")
    shutil.rmtree(bad, ignore_errors=True)

    # corruption 2: a MID-HISTORY provisional bar (the tip never froze) -> v4c' HARD-FAIL
    bad2 = _seed_temp_root()
    _write_raw(bad2, SID, [
        {**_bar("2024-06-10", 540.0, provisional=True), "series_id": SID,
         "schema_version": 1, "recorded_on": R0},
        {**_bar("2024-06-11", 541.0, provisional=True), "series_id": SID,
         "schema_version": 1, "recorded_on": R0},
    ])
    gb2 = Gate()
    verify_backfill.verify(bad2, CFG, gb2, daily=True)
    g.check("d8d verify(--daily) HARD-FAILS a mid-history provisional bar (finalization broke)",
            any(f.startswith("v4c'") for f in gb2.fails), f"fails={[f for f in gb2.fails if f[:2]=='v4']}")
    shutil.rmtree(bad2, ignore_errors=True)

    # d8e: a COMPLETE (all-132) archive in daily mode -- the coverage + recency + daily-restatement
    # gates are GREEN (the "green direction" at the unit layer). The depth gates (v1b/v2b) legitimately
    # fail on a SHALLOW synthetic; full-depth green is proven LIVE by the real-archive --daily run.
    allsids = list(CFG["price"])
    comp = _seed_temp_root()
    catc = to_datacore.load_catalog(comp)
    _run(comp, catc, {sid: [_bar("2025-01-08", 10.0), _bar("2025-01-09", 10.1),
                            _bar("2025-01-10", 10.2, provisional=True)] for sid in allsids}, R0)
    gc = Gate()
    verify_backfill.verify(comp, CFG, gc, daily=True)
    green = [f for f in gc.fails if f[:3] in ("v2a", "v2c") or f.startswith("v4")]
    g.check("d8e complete archive: coverage (v2a) + recency (v2c) + daily restatement (v4') gates GREEN",
            green == [], f"unexpected fails={green}")
    shutil.rmtree(comp, ignore_errors=True)

    # d8f: one symbol stuck >4 sessions behind the universe -> v2c SURFACES it (soft WARN), so a
    # PARTIAL Yahoo throttle is no longer a SILENT green (and it does not block the other commits).
    comp2 = _seed_temp_root()
    catc2 = to_datacore.load_catalog(comp2)
    lagged = allsids[1]
    _run(comp2, catc2,
         {sid: ([_bar("2024-12-31", 9.0), _bar("2025-01-02", 9.1, provisional=True)] if sid == lagged
                else [_bar("2025-01-09", 10.0), _bar("2025-01-10", 10.1, provisional=True)])
          for sid in allsids}, R0)
    gd = Gate()
    verify_backfill.verify(comp2, CFG, gd, daily=True)
    g.check("d8f a symbol lagging the universe by >4d is surfaced by v2c (partial-throttle de-silenced)",
            any(w.startswith("v2c") for w in gd.warns), f"v2c_warns={[w for w in gd.warns if w[:3]=='v2c']}")
    shutil.rmtree(comp2, ignore_errors=True)


def main() -> int:
    g = Gate()
    print("P5 daily gate (offline, temp root)")
    offline(g)
    print("\nP5 daily gate: %d/%d PASS" % (g.total - len(g.fails), g.total))
    if g.fails:
        print("FAILED: " + ", ".join(g.fails))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

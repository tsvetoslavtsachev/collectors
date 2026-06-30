# -*- coding: utf-8 -*-
"""P8b split-heal gate -- the BLOCKER, proven offline against a TEMPORARY archive root.

The synthetic NVDA 10:1 replay is DISCRIMINATING: it first proves the hazard is REAL (a
short daily window strands pre-window bars at the old scale -> a 10x cliff in close/value),
then proves the heal CLOSES it (a full-depth re-pull restates the whole stored range -> zero
cliff), with as-traded immutability and uniform depth preserved. The heal runs at the SAME
recorded_on as the daily push (the production same-day scenario) -> no vintage collision.

Gates:
  sh1 hazard-real   -- a short daily window over a 10:1 split leaves pre-window bars at the
                       OLD scale: a 10x discontinuity in close (the series TA reads)
  sh2 detect        -- detect_split_symbols flags the split stock (window split_factor != 1.0)
  sh3 zero-cliff    -- after heal EVERY stored bar is on the new scale (no 10x jump anywhere)
  sh4 immutable     -- as-traded = close*split_factor still reconstructs the pre-split $1000
  sh5 depth-uniform -- heal trims to stored earliest: appends nothing older, depth unchanged
  sh6 same-day-safe -- heal at the daily's recorded_on restates pre-window bars with NO
                       ArchiveError (in-window bars are byte-identical -> skipped)
  sh7 no-op         -- a split-free window -> detection empty (heal would be a pure no-op)
  sh8 stock-scoped  -- an ETF is NEVER selected for heal (live ETF daily byte-identical)
  sh9 empty/missing -- heal([]) is a no-op; a heal of a series with no stored history is safe
  sh15 EU split     -- P8c: a STOXX (.DE) split is detected + healed zero-cliff exactly like a US
                       stock (the mechanism keys on split_factor, not on a US listing)

Run:
  PYTHONPATH=C:\\Projects\\data-core;C:\\Projects\\collectors \\
  python collectors/price/tests/test_split_heal.py
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
from collectors.price import to_datacore, register_catalog, identity, split_heal, run

PRICE_DIR = Path(register_catalog.__file__).resolve().parent
CFG = yaml.safe_load((PRICE_DIR / "config.yaml").read_text(encoding="utf-8"))

R0, R1, R2 = "2026-06-26", "2026-06-27", "2026-06-29"   # backfill; daily+heal (same day R1); a later re-pull
SEED_DATE = "2026-06-28"
SID = "px_nvda_daily"                       # a stock with a 10:1 split canary in config.yaml


class Gate:
    def __init__(self):
        self.total = 0
        self.fails: list[str] = []

    def check(self, name, cond, detail=""):
        self.total += 1
        print(("  [PASS] " if cond else "  [FAIL] ") + name + (f" -- {detail}" if detail else ""))
        if not cond:
            self.fails.append(name)


def _probe_catalog(tmp: Path) -> None:
    (tmp / "catalog").mkdir(parents=True)
    (tmp / "catalog" / "catalog.json").write_text(
        json.dumps({"catalog_schema_version": 1,
                    "series": {"px_probe_daily": {"description": "seed probe",
                                                  "source": "synthetic-probe",
                                                  "schema_version": 1}}},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _seeded_root() -> Path:
    """Temp root with the FULL identity map seeded + catalog registered -> px_nvda_daily
    carries its stable_id (the real P8 flow, minus the network)."""
    tmp = Path(tempfile.mkdtemp(prefix="px_p8b_heal_"))
    _probe_catalog(tmp)
    identity.seed(CFG, tmp, SEED_DATE)
    register_catalog.register(CFG, tmp)
    return tmp


def _bare_root() -> Path:
    """Temp root registered WITHOUT a seeded identity map -> stocks are registered but carry
    NO stable_id (the P7a-era / F5-target state)."""
    tmp = Path(tempfile.mkdtemp(prefix="px_p8b_bare_"))
    _probe_catalog(tmp)
    register_catalog.register(CFG, tmp)
    return tmp


def _seq(start_iso, n):
    d0 = date.fromisoformat(start_iso)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def _bar(as_of, value, *, split_factor=1.0, provisional=False, source="test"):
    r = {"as_of": as_of, "value": value, "open": value, "high": value, "low": value,
         "close": value, "value_tr": value, "volume": 1000,
         "split_factor": split_factor, "dividend": 0.0, "source": source}
    if provisional:
        r["provisional"] = True
    return r


def offline(g: Gate) -> None:
    tmp = _seeded_root()
    cat = to_datacore.load_catalog(tmp)

    all_days = _seq("2026-01-01", 61)          # day1..day61
    hist_days = all_days[:60]                   # day1..day60 (the backfilled history)
    win_days = all_days[54:61]                  # day55..day61 (the short daily window)
    EX = all_days[56]                           # day57 = the 10:1 ex-date
    OLD, NEW, RATIO = 1000.0, 100.0, 10.0

    # 1) BACKFILL the full history at the OLD (pre-split) scale, vintage R0.
    hist = [_bar(d, OLD, split_factor=1.0, provisional=(i == 59))
            for i, d in enumerate(hist_days)]
    to_datacore.push({SID: {"ok": True, "records": hist}}, root=str(tmp), catalog=cat, recorded_on=R0)

    # 2) the 10:1 split: a SHORT daily window re-pulled at the NEW scale (close 100;
    #    split_factor 10 before the ex-date, 1.0 on/after), vintage R1.
    window = [_bar(d, NEW, split_factor=(RATIO if d < EX else 1.0), provisional=(d == win_days[-1]))
              for d in win_days]
    daily_raw = {SID: {"ok": True, "records": window}}
    to_datacore.push(daily_raw, root=str(tmp), catalog=cat, recorded_on=R1)

    # sh1: WITHOUT heal the pre-WINDOW bars strand at the old scale -> a 10x cliff.
    view = {r["as_of"]: r for r in archive.read(SID, root=str(tmp))}
    cliff = view[hist_days[53]]["close"] / view[hist_days[54]]["close"]   # day54 / day55
    g.check("sh1 hazard is REAL: a short window strands pre-window bars (10x cliff in close)",
            abs(cliff - RATIO) < 1e-6, f"day54/day55 close ratio = {cliff}")

    # sh2: detection flags the split stock from the daily window.
    detected = split_heal.detect_split_symbols(CFG, daily_raw)
    g.check("sh2 detection flags the split symbol (window split_factor != 1.0)",
            detected == [SID], f"detected={detected}")

    # 3) HEAL at the SAME vintage as the daily push (R1 -- the production same-day case).
    #    Injected fetch: full inception at the NEW scale + 5 bars OLDER than the seed
    #    (to exercise the trim-to-stored-earliest).
    older = _seq("2025-12-25", 5)               # all < day1 -> must be trimmed away
    full_days = older + all_days

    def fake_fetch(cfg, *, period=None, only=None):
        recs = [_bar(d, NEW, split_factor=(RATIO if d < EX else 1.0),
                     provisional=(d == full_days[-1])) for d in full_days]
        return {SID: {"ok": True, "records": recs}}

    healed = split_heal.heal(CFG, detected, root=str(tmp), catalog=cat,
                             recorded_on=R1, fetch=fake_fetch)
    hres = healed.get(SID, {})

    hview = archive.read(SID, root=str(tmp))
    closes = [r["close"] for r in hview]
    g.check("sh3 after heal EVERY bar is on the new scale -> zero cliff in close/value",
            all(abs(c - NEW) < 1e-6 for c in closes),
            f"distinct closes = {sorted(set(closes))[:5]}")

    astraded = {r["as_of"]: r["close"] * r.get("split_factor", 1.0) for r in hview}
    g.check("sh4 as-traded = close*split_factor still reconstructs the immutable pre-split $1000",
            abs(astraded[hist_days[0]] - OLD) < 1e-6 and abs(astraded[all_days[-1]] - NEW) < 1e-6,
            f"day1 as-traded={astraded[hist_days[0]]}, tip={astraded[all_days[-1]]}")

    g.check("sh5 heal trims to stored earliest -> depth unchanged (nothing older appended)",
            hview[0]["as_of"] == hist_days[0] and len(hview) == 61,
            f"earliest={hview[0]['as_of']} n={len(hview)}")

    g.check("sh6 same-day heal restates pre-window bars with NO ArchiveError (in-window skipped)",
            hres.get("ok") and hres.get("restated", 0) >= 54 and hres.get("appended", 0) == 0,
            f"res={ {k: hres.get(k) for k in ('ok','restated','appended','skipped')} }")
    shutil.rmtree(tmp, ignore_errors=True)

    # sh7: a split-free window -> detection empty (heal a pure no-op).
    nosplit = {SID: {"ok": True, "records": [_bar(d, NEW) for d in win_days]}}
    g.check("sh7 no split in window -> detection empty (heal is a no-op)",
            split_heal.detect_split_symbols(CFG, nosplit) == [],
            f"detected={split_heal.detect_split_symbols(CFG, nosplit)}")

    # sh8: detection is STOCK-scoped -- an ETF with a (hypothetical) in-window factor is
    # never selected, so the live ETF daily stays byte-identical.
    etf_raw = {"px_spy_daily": {"ok": True, "records": [_bar("2026-06-25", 400.0, split_factor=2.0)]}}
    g.check("sh8 detection is stock-scoped (an ETF is never selected for heal)",
            split_heal.detect_split_symbols(CFG, etf_raw) == [],
            f"detected={split_heal.detect_split_symbols(CFG, etf_raw)}")

    # sh9: heal([]) is a no-op; a heal of a series with no stored history is safe (no crash).
    g.check("sh9a heal of an empty symbol list is a no-op", split_heal.heal(CFG, []) == {})
    tmp2 = _seeded_root()
    cat2 = to_datacore.load_catalog(tmp2)

    def fetch_fresh(cfg, *, period=None, only=None):
        return {SID: {"ok": True, "records": [_bar(d, NEW) for d in all_days[:10]]}}

    r9 = split_heal.heal(CFG, [SID], root=str(tmp2), catalog=cat2, recorded_on=R1, fetch=fetch_fresh)
    g.check("sh9b heal of a series with no stored history seeds it (no crash, all appended)",
            r9.get(SID, {}).get("ok") and r9[SID].get("appended", 0) == 10,
            f"res={ {k: r9.get(SID, {}).get(k) for k in ('ok','appended')} }")
    shutil.rmtree(tmp2, ignore_errors=True)

    # sh10 (C2 fix): the re-pull OMITS an interior stored date -> coverage-complete heal
    # rescales the missing bar onto the new scale via immutability (no residual cliff).
    tmp = _seeded_root()
    cat = to_datacore.load_catalog(tmp)
    hist = [_bar(d, OLD, split_factor=1.0, provisional=(i == 59)) for i, d in enumerate(hist_days)]
    to_datacore.push({SID: {"ok": True, "records": hist}}, root=str(tmp), catalog=cat, recorded_on=R0)
    to_datacore.push(daily_raw, root=str(tmp), catalog=cat, recorded_on=R1)   # the split window
    OMIT = hist_days[30]                                                       # an interior date the vendor drops

    def fetch_omit(cfg, *, period=None, only=None):
        recs = [_bar(d, NEW, split_factor=(RATIO if d < EX else 1.0), provisional=(d == all_days[-1]))
                for d in all_days if d != OMIT]
        return {SID: {"ok": True, "records": recs}}

    split_heal.heal(CFG, [SID], root=str(tmp), catalog=cat, recorded_on=R1, fetch=fetch_omit)
    hv = {r["as_of"]: r for r in archive.read(SID, root=str(tmp))}
    g.check("sh10a a re-pull that OMITS an interior date still heals it (rescaled, no cliff)",
            abs(hv[OMIT]["close"] - NEW) < 1e-6, f"omitted {OMIT}: close={hv[OMIT]['close']}")
    g.check("sh10b the rescaled bar preserves immutable as-traded (close*split_factor = $1000)",
            abs(hv[OMIT]["close"] * hv[OMIT].get("split_factor", 1.0) - OLD) < 1e-6,
            f"as-traded={hv[OMIT]['close'] * hv[OMIT].get('split_factor', 1.0)}")
    g.check("sh10c zero old-scale survivors across the whole stored range after a gappy heal",
            all(abs(hv[d]["close"] - NEW) < 1e-6 for d in hist_days),
            f"distinct closes = {sorted({hv[d]['close'] for d in hist_days})[:4]}")
    shutil.rmtree(tmp, ignore_errors=True)

    # sh13 (C2-round2 fix): the re-pull omits the bar ABUTTING the ex-date. Forward lookup
    # would grab the ex-date's POST-split factor (no rescale -> cliff); backward lookup grabs
    # the pre-split neighbor's factor and heals it. Fresh old-scale backfill, no daily push.
    tmp = _seeded_root()
    cat = to_datacore.load_catalog(tmp)
    hist = [_bar(d, OLD, split_factor=1.0, provisional=(i == 59)) for i, d in enumerate(hist_days)]
    to_datacore.push({SID: {"ok": True, "records": hist}}, root=str(tmp), catalog=cat, recorded_on=R0)
    ABUT = all_days[55]                  # day56 -- the last pre-split bar (ex-date = day57 = all_days[56])

    def fetch_abut(cfg, *, period=None, only=None):
        recs = [_bar(d, NEW, split_factor=(RATIO if d < EX else 1.0), provisional=(d == all_days[-1]))
                for d in all_days if d != ABUT]
        return {SID: {"ok": True, "records": recs}}

    split_heal.heal(CFG, [SID], root=str(tmp), catalog=cat, recorded_on=R1, fetch=fetch_abut)
    hv = {r["as_of"]: r for r in archive.read(SID, root=str(tmp))}
    g.check("sh13a omitted bar ABUTTING the ex-date is healed via backward lookup (no cliff)",
            abs(hv[ABUT]["close"] - NEW) < 1e-6
            and abs(hv[ABUT]["close"] * hv[ABUT].get("split_factor", 1.0) - OLD) < 1e-6,
            f"abut {ABUT}: close={hv[ABUT]['close']} factor={hv[ABUT].get('split_factor')}")
    g.check("sh13b zero old-scale survivors across the whole stored range",
            all(abs(hv[d]["close"] - NEW) < 1e-6 for d in hist_days),
            f"distinct closes = {sorted({hv[d]['close'] for d in hist_days})[:4]}")
    shutil.rmtree(tmp, ignore_errors=True)

    # sh14 (volume-raw fix): a rescaled-missing bar keeps RAW volume (not split-scaled), so it
    # stays consistent with vendor-raw siblings AND does not trigger a PHANTOM restatement when
    # the dropped date is later re-included at its raw volume.
    tmp = _seeded_root()
    cat = to_datacore.load_catalog(tmp)
    hist = [_bar(d, OLD, split_factor=1.0, provisional=(i == 59)) for i, d in enumerate(hist_days)]
    to_datacore.push({SID: {"ok": True, "records": hist}}, root=str(tmp), catalog=cat, recorded_on=R0)
    GAP = hist_days[29]                                                 # an interior pre-split date dropped

    def fetch_gap(cfg, *, period=None, only=None):
        recs = [_bar(d, NEW, split_factor=(RATIO if d < EX else 1.0), provisional=(d == all_days[-1]))
                for d in all_days if d != GAP]
        return {SID: {"ok": True, "records": recs}}

    split_heal.heal(CFG, [SID], root=str(tmp), catalog=cat, recorded_on=R1, fetch=fetch_gap)
    hg = {r["as_of"]: r for r in archive.read(SID, root=str(tmp))}
    g.check("sh14a a rescaled-missing bar keeps RAW volume (== vendor raw, not split-scaled)",
            hg[GAP]["volume"] == 1000 and abs(hg[GAP]["close"] - NEW) < 1e-6,
            f"volume={hg[GAP]['volume']} close={hg[GAP]['close']}")
    # the vendor re-includes the dropped date next pull at its RAW volume -> identical -> SKIP,
    # never a phantom restatement (the volume-scaling bug would have made it restated=1).
    reinc = to_datacore.push(
        {SID: {"ok": True, "records": [_bar(GAP, NEW, split_factor=RATIO)]}},
        root=str(tmp), catalog=cat, recorded_on=R2)[0]
    g.check("sh14b re-including the dropped date at raw volume is a SKIP (no phantom restatement)",
            reinc.get("restated", 0) == 0 and reinc.get("skipped", 0) == 1,
            f"restated={reinc.get('restated')} skipped={reinc.get('skipped')}")
    shutil.rmtree(tmp, ignore_errors=True)

    # sh15 (P8c EU split-heal): the heal mechanism is EXCHANGE-AGNOSTIC -- it keys on split_factor
    # != 1.0, never on a US listing. A STOXX (.DE, EUR, foreign suffix) stock with an in-window
    # split is detected + healed to zero-cliff exactly like NVDA, confirming EU corporate actions
    # heal through the inherited P8b mechanism (mandate P8c step 4). require_stamp=True also proves
    # a stamped STOXX series flows the F5 forward path.
    EU_SID, EU_RATIO = "px_sap_de_daily", 4.0     # SAP.DE -- a STOXX (EUR) stock, foreign exchange
    tmp = _seeded_root()
    cat = to_datacore.load_catalog(tmp)
    eu_hist = [_bar(d, OLD, split_factor=1.0, provisional=(i == 59)) for i, d in enumerate(hist_days)]
    to_datacore.push({EU_SID: {"ok": True, "records": eu_hist}}, root=str(tmp), catalog=cat, recorded_on=R0)
    eu_window = [_bar(d, OLD / EU_RATIO, split_factor=(EU_RATIO if d < EX else 1.0),
                      provisional=(d == win_days[-1])) for d in win_days]
    eu_daily = {EU_SID: {"ok": True, "records": eu_window}}
    detected_eu = split_heal.detect_split_symbols(CFG, eu_daily)
    g.check("sh15a EU (.DE STOXX) split detected -- detection is exchange-agnostic, not US-only",
            detected_eu == [EU_SID], f"detected={detected_eu}")
    to_datacore.push(eu_daily, root=str(tmp), catalog=cat, recorded_on=R1)

    def fetch_eu(cfg, *, period=None, only=None):
        recs = [_bar(d, OLD / EU_RATIO, split_factor=(EU_RATIO if d < EX else 1.0),
                     provisional=(d == all_days[-1])) for d in all_days]
        return {EU_SID: {"ok": True, "records": recs}}

    split_heal.heal(CFG, detected_eu, root=str(tmp), catalog=cat, recorded_on=R1,
                    fetch=fetch_eu, require_stamp=True)
    euv = archive.read(EU_SID, root=str(tmp))
    eu_closes = [r["close"] for r in euv]
    g.check("sh15b EU split heals to zero-cliff (every stored bar on the new scale)",
            all(abs(c - OLD / EU_RATIO) < 1e-6 for c in eu_closes),
            f"distinct closes = {sorted(set(eu_closes))[:4]}")
    eu_astraded = {r["as_of"]: r["close"] * r.get("split_factor", 1.0) for r in euv}
    g.check("sh15c EU as-traded immutability preserved (close*split_factor = pre-split 1000)",
            abs(eu_astraded[hist_days[0]] - OLD) < 1e-6 and len(euv) == 61,
            f"day1 as-traded={eu_astraded[hist_days[0]]} n={len(euv)}")
    shutil.rmtree(tmp, ignore_errors=True)

    # sh11 (C5 backstop): push require_stamp F5 -- a REGISTERED unstamped stock is refused on
    # the forward path; the permissive default still writes it; an ETF is never affected.
    bare = _bare_root()
    catb = to_datacore.load_catalog(bare)
    perm = to_datacore.push({SID: {"ok": True, "records": [_bar("2026-06-25", 100.0)]}},
                            root=str(bare), catalog=catb, recorded_on=R0)[0]
    g.check("sh11a permissive push (default) still writes a registered unstamped stock (backward-compat)",
            perm.get("ok") and perm.get("appended", 0) == 1, f"res={perm}")
    ref = to_datacore.push({"px_aapl_daily": {"ok": True, "records": [_bar("2026-06-25", 100.0)]}},
                           root=str(bare), catalog=catb, recorded_on=R0, require_stamp=True)[0]
    g.check("sh11b require_stamp REFUSES a registered-but-unstamped stock (F5 fail-closed, loud)",
            ref.get("ok") is False and "F5" in str(ref.get("skip_reason")), f"res={ref}")
    etf = to_datacore.push({"px_spy_daily": {"ok": True, "records": [_bar("2026-06-25", 400.0)]}},
                           root=str(bare), catalog=catb, recorded_on=R0, require_stamp=True)[0]
    g.check("sh11c require_stamp never affects an ETF (no family key -> writes normally)",
            etf.get("ok") and etf.get("appended", 0) == 1, f"res={etf}")
    shutil.rmtree(bare, ignore_errors=True)

    # sh12 (C5 scope): _daily_ready_scope keeps ready stamped stock + ETF, drops unstamped +
    # unregistered (the early/loud half of F5, now covering --family stock --daily too).
    seeded = _seeded_root()
    cat_s = to_datacore.load_catalog(seeded)
    cat_s["series"]["px_aapl_daily"].pop("stable_id", None)            # make AAPL unstamped on disk
    (seeded / "catalog" / "catalog.json").write_text(
        json.dumps(cat_s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.environ["DATACORE_ROOT"] = str(seeded)
    try:
        ready = run._daily_ready_scope(
            CFG, ["px_nvda_daily", "px_spy_daily", "px_aapl_daily", "px_fake_daily"])
    finally:
        os.environ.pop("DATACORE_ROOT", None)
    g.check("sh12 _daily_ready_scope keeps ready stamped stock + ETF, drops unstamped + unregistered",
            set(ready) == {"px_nvda_daily", "px_spy_daily"}, f"ready={ready}")
    shutil.rmtree(seeded, ignore_errors=True)


def main() -> int:
    g = Gate()
    print("P8b split-heal gate (offline, temp root) -- synthetic NVDA 10:1 replay")
    offline(g)
    print("\nP8b split-heal gate: %d/%d PASS" % (g.total - len(g.fails), g.total))
    if g.fails:
        print("FAILED: " + ", ".join(g.fails))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""Offline verify for the P7b stable-identity / splice / rename core
(no network, no data-core, no archive) -- the COT verify_splice.py analog.

Proves the program R4 gap is closed with a SYNTHETIC replay (a stock fetch-by-ticker
returns the CURRENT company, so a splice never happens in one pull -- exactly why
COT's own verify_splice.py is synthetic too; flagged honestly in the mandate):

  I0 exch_code map        -- US (BRK-B) / GR (SAP.DE) / LN (HSBA.L) suffixes resolve.
  I1 mint determinism      -- sorted seed -> SEC-000001..; a re-seed mints 0, never
                              renumbers (append-only).
  I2 lifecycle             -- seen -> effective_to NULL; disappear -> effective_to set;
                              re-seen -> a NEW id (mint-new-on-reappear).
  I3 splice-refuse (CORE)  -- a ticker seen->retire->re-seen (DIFFERENT company) carries
                              TWO distinct internal_ids; detect_splice flags
                              ticker_recycle_splice; the recycled ticker never reattaches
                              the retired identity (program R4 verify gate).
  I4 invariants            -- unique ids, every current ticker exactly one active epoch,
                              dense 1..N, no dangling continuation_of.
  I5 rename-continuity      -- a FIGI-confirmed same-company ticker change (FB->META)
                              gives ONE stable_id (continuation_of link); a reader
                              resolves identity by stable_id, NOT by ticker.
  I6 reuse still refuses    -- a DIFFERENT company on a freed ticker / a FIGI conflict
                              mints a fresh id + flags; it is NEVER merged.
  I7 FIGI-offline fallback  -- OpenFIGI unreachable + only a fuzzy name hit ->
                              review_flag, NO merge, NO splice, NO crash.

Run: python -m collectors.price.verify_identity   (PYTHONPATH = collectors repo)
     python -m collectors.price.verify_identity --live   (+ a real OpenFIGI probe)
"""
from __future__ import annotations

import sys

from . import identity

D0, D1, D2 = "2022-01-01", "2023-01-01", "2024-01-01"


def _seed(symbols, date=D0):
    """Mint epochs in sorted order (mirrors identity.seed without a filesystem)."""
    m = identity.empty_map()
    for sym in sorted(symbols):
        identity.mint_or_resolve(m, sym, identity.exch_code(sym), date, name=sym)
    return m


def main() -> int:
    fails = []

    # I0 -- exch_code suffix map -------------------------------------------------
    cases = {"BRK-B": "US", "AAPL": "US", "SAP.DE": "GR", "HSBA.L": "LN",
             "A2A.MI": "IM", "AAK.ST": "SS", "AIR.PA": "FP"}
    bad = {s: identity.exch_code(s) for s, want in cases.items() if identity.exch_code(s) != want}
    if bad:
        fails.append(f"I0: exch_code wrong: {bad}")
    else:
        print(f"  I0 PASS exch_code map ({len(cases)} suffixes: US/GR/LN/IM/SS/FP)")

    # I1 -- mint determinism + append-only no-renumber ---------------------------
    syms = ["MSFT", "AAPL", "NVDA", "BRK-B", "SAP.DE"]
    m1 = _seed(syms)
    m2 = _seed(syms)
    ids1 = [e["internal_id"] for e in m1["epochs"]]
    ids2 = [e["internal_id"] for e in m2["epochs"]]
    first = identity.active_epoch(m1, "AAPL")           # AAPL sorts first -> SEC-000001
    n_before = len(m1["epochs"])
    for sym in syms:                                    # re-seed: must mint 0 / no renumber
        identity.mint_or_resolve(m1, sym, identity.exch_code(sym), D0, name=sym)
    if ids1 != ids2:
        fails.append(f"I1: non-deterministic ids {ids1} != {ids2}")
    elif first is None or first["internal_id"] != "SEC-000001":
        fails.append(f"I1: sorted seed wrong (AAPL != SEC-000001: {first})")
    elif len(m1["epochs"]) != n_before:
        fails.append(f"I1: re-seed renumbered/grew ({len(m1['epochs'])} != {n_before})")
    else:
        print(f"  I1 PASS deterministic sorted mint (AAPL=SEC-000001), re-seed mints 0")

    # I2 -- lifecycle: seen -> retire -> re-seen ---------------------------------
    m = _seed(["T", "KEEP"])
    e0 = identity.active_epoch(m, "T")
    open_ok = e0 is not None and e0["effective_to"] is None
    id_a = identity.close_epoch(m, "T", D1)             # disappears -> close
    closed_ok = identity.active_epoch(m, "T") is None and e0["effective_to"] == D1
    id_b = identity.mint_or_resolve(m, "T", "US", D2, name="T")  # re-seen -> NEW id
    reseen_ok = id_b != id_a and identity.active_epoch(m, "T")["internal_id"] == id_b
    if not (open_ok and closed_ok and reseen_ok):
        fails.append(f"I2: lifecycle wrong (open={open_ok} close={closed_ok} reseen={reseen_ok})")
    else:
        print(f"  I2 PASS lifecycle: open->close({id_a})->mint-new({id_b})")

    # I3 -- splice-refuse CORE ---------------------------------------------------
    ids = identity.distinct_identities(m, "T")
    splice = identity.detect_splice(m, "T")
    if len(ids) != 2:
        fails.append(f"I3: recycled ticker T has {len(ids)} ids, expected 2 ({ids})")
    elif not splice or splice["flag"] != "ticker_recycle_splice":
        fails.append(f"I3: recycle not flagged ticker_recycle_splice ({splice})")
    elif id_a not in ids or id_b not in ids:
        fails.append(f"I3: distinct ids do not include both epochs ({ids})")
    else:
        print(f"  I3 PASS recycle T -> 2 ids {ids}, flag={splice['flag']} (not reattached)")

    # I4 -- invariants -----------------------------------------------------------
    problems = identity.check_invariants(m)
    if problems:
        fails.append(f"I4: invariant violations on a healthy map: {problems}")
    else:
        # a deliberately broken map MUST be caught (the guard has teeth)
        broken = identity.empty_map()
        broken["epochs"] = [
            identity._new_epoch("SEC-000001", "X", "US", D0, "X", ""),
            identity._new_epoch("SEC-000001", "Y", "US", D0, "Y", ""),  # dup id
        ]
        if not identity.check_invariants(broken):
            fails.append("I4: guard missed a duplicate internal_id")
        else:
            print("  I4 PASS invariants hold on healthy map; dup-id is caught")

    # I5 -- rename-continuity (FIGI-confirmed) -----------------------------------
    # snapshot N: FB present (figi F_META). snapshot N+1: FB gone, META present (same FIGI).
    F_META = "BBG000MM2P62"
    mr = _seed(["FB", "KEEP"])
    fb_id = identity.active_epoch(mr, "FB")["internal_id"]
    identity.apply_snapshot(mr, [{"ticker": "FB", "exch_code": "US", "name": "Meta Platforms"},
                                 {"ticker": "KEEP", "exch_code": "US", "name": "Keep"}],
                            D1, figi_lookup={("FB", "US"): F_META, ("KEEP", "US"): "BBG_K"})
    rep = identity.apply_snapshot(
        mr,
        [{"ticker": "META", "exch_code": "US", "name": "Meta Platforms"},
         {"ticker": "KEEP", "exch_code": "US", "name": "Keep"}],
        D2, figi_lookup={("META", "US"): F_META, ("KEEP", "US"): "BBG_K"})
    meta_ep = identity.active_epoch(mr, "META")
    sid_meta = identity.stable_id(mr, "META")
    sid_keep = identity.stable_id(mr, "KEEP")
    one_id = sid_meta == fb_id and meta_ep["continuation_of"] == fb_id
    # reader resolves identity by stable_id, NOT ticker: FB(history) and META share it
    resolves_by_id = sid_meta != sid_keep and identity.detect_splice(mr, "META") is None
    if not one_id:
        fails.append(f"I5: rename did not continue one stable_id (sid_meta={sid_meta}, "
                     f"fb_id={fb_id}, continuation_of={meta_ep['continuation_of']})")
    elif not resolves_by_id:
        fails.append(f"I5: identity not resolvable by stable_id ({sid_meta} vs {sid_keep})")
    elif ("FB", "META") not in rep["continued"]:
        fails.append(f"I5: continuation not reported ({rep['continued']})")
    else:
        print(f"  I5 PASS rename FB->META: one stable_id {sid_meta} (continuation_of), "
              f"resolved by id not ticker")

    # I6 -- reuse still refuses (different company / FIGI conflict -> no merge) ---
    # (a) different company: X (figi F1) disappears, Y (figi F2) appears -> fresh, no link.
    md = _seed(["X"])
    x_id = identity.active_epoch(md, "X")["internal_id"]
    identity.apply_snapshot(md, [{"ticker": "X", "exch_code": "US", "name": "Old Co"}],
                            D1, figi_lookup={("X", "US"): "BBG_F1"})
    identity.apply_snapshot(md, [{"ticker": "Y", "exch_code": "US", "name": "New Unrelated Co"}],
                            D2, figi_lookup={("Y", "US"): "BBG_F2"})
    y_ep = identity.active_epoch(md, "Y")
    diff_ok = y_ep["continuation_of"] is None and y_ep["internal_id"] != x_id
    # (b) ambiguous FIGI: two disappeared share Y's FIGI -> flag, never merge.
    ma = _seed(["P", "Q"])
    identity.apply_snapshot(ma, [{"ticker": "P", "exch_code": "US", "name": "P"},
                                 {"ticker": "Q", "exch_code": "US", "name": "Q"}],
                            D1, figi_lookup={("P", "US"): "BBG_SAME", ("Q", "US"): "BBG_SAME"})
    identity.apply_snapshot(ma, [{"ticker": "R", "exch_code": "US", "name": "R"}],
                            D2, figi_lookup={("R", "US"): "BBG_SAME"})
    r_ep = identity.active_epoch(ma, "R")
    ambig_ok = r_ep["continuation_of"] is None and r_ep["review_flag"] == "rename_ambiguous_figi"
    if not diff_ok:
        fails.append(f"I6a: different company merged ({y_ep})")
    elif not ambig_ok:
        fails.append(f"I6b: ambiguous FIGI merged instead of flagged ({r_ep})")
    else:
        print(f"  I6 PASS reuse refused: diff-company fresh id; ambiguous FIGI flagged "
              f"({r_ep['review_flag']}), not merged")

    # I7 -- FIGI-offline -> fallback + flag, no crash ----------------------------
    mo = _seed(["OLD"])
    try:
        identity.apply_snapshot(mo, [{"ticker": "OLD", "exch_code": "US", "name": "Acme Group"}],
                                D1, figi_lookup={("OLD", "US"): "BBG_O"})
        # OpenFIGI unreachable (figi_lookup=None) on the rename snapshot; only a name hit.
        identity.apply_snapshot(mo, [{"ticker": "NEW", "exch_code": "US", "name": "Acme Group"}],
                                D2, figi_lookup=None)
        new_ep = identity.active_epoch(mo, "NEW")
        offline_ok = (new_ep["continuation_of"] is None
                      and new_ep["review_flag"] == "rename_figi_offline")
        crashed = False
    except Exception as exc:           # noqa: BLE001 -- the gate is "never crash"
        offline_ok, crashed = False, True
        fails.append(f"I7: apply_snapshot crashed offline: {exc!r}")
    if not crashed and not offline_ok:
        fails.append(f"I7: FIGI-offline not flagged/separated ({new_ep})")
    elif not crashed:
        print(f"  I7 PASS FIGI-offline -> flag={new_ep['review_flag']}, not merged, no crash")

    # I8 -- continuous-handover recycle: a DIFFERENT company on a continuously-present
    # ticker (conflicting FIGI, NO intervening absent snapshot) -> close+remint+flag,
    # NOT a silent splice onto the retired id (the V = Vivendi->Visa hazard).
    mc = _seed(["V", "KEEP2"])
    v_old = identity.active_epoch(mc, "V")["internal_id"]
    identity.apply_snapshot(mc, [{"ticker": "V", "exch_code": "US", "name": "Vivendi"},
                                 {"ticker": "KEEP2", "exch_code": "US", "name": "Keep2"}],
                            D1, figi_lookup={("V", "US"): "BBG_VIVENDI", ("KEEP2", "US"): "BBG_K2"})
    identity.apply_snapshot(mc, [{"ticker": "V", "exch_code": "US", "name": "Visa Inc.",
                                  "share_class_figi": "BBG_VISA", "isin": "US92826C8394"},
                                 {"ticker": "KEEP2", "exch_code": "US", "name": "Keep2"}],
                            D2, figi_lookup={("V", "US"): "BBG_VISA", ("KEEP2", "US"): "BBG_K2"})
    v_ep = identity.active_epoch(mc, "V")
    ids_v = identity.distinct_identities(mc, "V")
    sp_v = identity.detect_splice(mc, "V")
    contra_ok = (v_ep["internal_id"] != v_old and len(ids_v) == 2
                 and sp_v and sp_v["flag"] == "ticker_recycle_splice"
                 and v_ep["review_flag"] == "ticker_recycle_contradiction"
                 and v_ep["share_class_figi"] == "BBG_VISA")
    if not contra_ok:
        fails.append(f"I8: continuous-handover recycle not refused "
                     f"(v_old={v_old}, ids={ids_v}, splice={sp_v}, ep={v_ep})")
    else:
        print(f"  I8 PASS continuous recycle V -> 2 ids {ids_v}, "
              f"flag=ticker_recycle_contradiction (Visa NOT spliced onto Vivendi)")

    # I9 -- 1:N dual share class: one disappeared X, two appeared Y share its FIGI
    # (GOOG/GOOGL). One continues; the OTHER is FLAGGED, never a silent fragment.
    md2 = _seed(["OLDC"])
    identity.apply_snapshot(md2, [{"ticker": "OLDC", "exch_code": "US", "name": "Alphabet"}],
                            D1, figi_lookup={("OLDC", "US"): "BBG_ABC"})
    rep9 = identity.apply_snapshot(
        md2,
        [{"ticker": "GOOG", "exch_code": "US", "name": "Alphabet C"},
         {"ticker": "GOOGL", "exch_code": "US", "name": "Alphabet A"}],
        D2, figi_lookup={("GOOG", "US"): "BBG_ABC", ("GOOGL", "US"): "BBG_ABC"})
    linked9 = [b for (_a, b) in rep9["continued"]]
    losers = [t for t in ("GOOG", "GOOGL") if t not in linked9]
    one_links = len(linked9) == 1
    loser_flagged = bool(losers) and all(
        identity.active_epoch(md2, t)["review_flag"] == "rename_multi_class_figi" for t in losers)
    # symmetry: the LINKED winner is also flagged (it is an order-dependent guess)
    winner_flagged = one_links and (
        identity.active_epoch(md2, linked9[0])["review_flag"] == "rename_multi_class_winner")
    if not (one_links and loser_flagged and winner_flagged):
        fails.append(f"I9: 1:N dual-class not symmetric (continued={rep9['continued']}, "
                     f"flagged={rep9['flagged']}, winner_flagged={winner_flagged})")
    else:
        print(f"  I9 PASS 1:N dual-class: winner flagged rename_multi_class_winner, "
              f"loser(s) {losers} flagged (no silent fragment, no silent guess)")

    # I10 -- continuous handover with NO prior FIGI (cached None): the new identity is
    # grafted (no contradiction is computable) but FLAGGED stayed_identity_review, not
    # silent (the LENS 1 graft residual closed).
    mh = _seed(["XX"])
    identity.apply_snapshot(mh, [{"ticker": "XX", "exch_code": "US", "name": "Visa Inc.",
                                  "share_class_figi": "BBG_VISA"}],
                            D1, figi_lookup={("XX", "US"): "BBG_VISA"})
    xx_ep = identity.active_epoch(mh, "XX")
    if not (xx_ep["review_flag"] == "stayed_identity_review"
            and xx_ep["share_class_figi"] == "BBG_VISA"):
        fails.append(f"I10: continuous cached-None handover not flagged ({xx_ep})")
    else:
        print(f"  I10 PASS continuous cached-None handover -> flagged stayed_identity_review "
              f"(graft surfaced, not silent)")

    print()
    if fails:
        for f in fails:
            print("  FAIL:", f)
        return 1
    print("  ALL STABLE-IDENTITY GATES PASS")

    if "--live" in sys.argv:
        print("\n  --- live OpenFIGI probe (shareClassFIGI present?) ---")
        from . import figi
        items = [("META", "US"), ("AAPL", "US"), ("NVDA", "US"),
                 ("SAP", "GR"), ("HSBA", "LN")]
        res = figi.probe(items)
        present = sum(1 for it in items if res.get(it))
        print(f"  live probe: {present}/{len(items)} shareClassFIGI resolved "
              f"(0 is acceptable -> graceful fallback path; non-zero proves the wire)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

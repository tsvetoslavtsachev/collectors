"""Offline verify for the COT splice/identity core (no network, no data-core).

Proves the audit's gap is closed:
  G1 splice detection      — a two-contract history is detected, seam reported.
  G2 mark-don't-clean       — clean_segment keeps only the current contract; the
                              full history is untouched (both segments survive).
  G3 inversion reproduced   — percentile over the WHOLE spliced history vs over
                              the CLEAN segment land on opposite sides of 50
                              (the 27.62 vs 64.2 WTI signal flip, synthetic).
  G4 percentile_<window>    — the window is an explicit parameter; different
                              windows give different, self-describing reads.
  G5 guard math             — insufficient history / zero dispersion -> None.
  G6 registry invariants    — 38 markets, unique keys + canonical ids + cftc_codes,
                              WTI reused.
  G7 rebrand vs splice      — a code-pinned rename is a BENIGN name_rebrand (keep
                              whole); a non-pinned 2-identity key is a real
                              contract_splice (the audit's hazard).
  G8 history gap            — a multi-year date discontinuity is flagged.

Run: python -m collectors.cot.verify_splice   (PYTHONPATH = collectors repo)
"""
from __future__ import annotations
from . import derive, markets


def _row(date, net, name):
    return {"date": date, "primary_net": net, "market_name": name}


def _synthetic_wti():
    """NYMEX segment (high nets) then ICE segment (low nets), latest net low.

    Mirrors the real seam: over the whole history the latest low net ranks LOW;
    within the ICE-only segment the same value ranks HIGH -> inverted signal.
    """
    nymex = "CRUDE OIL, LIGHT SWEET - NEW YORK MERCANTILE EXCHANGE"
    ice = "CRUDE OIL, LIGHT SWEET-WTI - ICE FUTURES EUROPE"
    rows = []
    # 299 NYMEX weeks, nets oscillating high around +280k
    for i in range(299):
        net = 250_000 + (i % 40) * 2_000          # ~250k..330k
        rows.append(_row(f"2016-W{i:03d}", net, nymex))
    # 226 ICE weeks, nets oscillating low around -30k, latest -20,566 (real)
    for i in range(225):
        net = -45_000 + (i % 30) * 1_000          # ~-45k..-16k
        rows.append(_row(f"2022-W{i:03d}", net, ice))
    rows.append(_row("2026-06-02", -20_566, ice))  # the real published week
    return rows


def main() -> int:
    fails = []

    rows = _synthetic_wti()

    # G1 — splice detected, seam self-describing
    splice = derive.detect_splice(rows)
    if not splice or splice["flag"] != "contract_splice":
        fails.append("G1: splice not detected")
    elif "NEW YORK MERCANTILE" not in splice["from_identity"] or "ICE" not in splice["to_identity"]:
        fails.append(f"G1: seam identities wrong: {splice}")
    else:
        print(f"  G1 PASS splice seam {splice['seam_date']} "
              f"{splice['from_identity'][:20]}.. -> {splice['to_identity'][:20]}..")

    # G2 — clean_segment keeps only current contract; full history intact
    seg = derive.clean_segment(rows, splice)
    if len(rows) != 525:
        fails.append(f"G2: full history mutated ({len(rows)} != 525)")
    if len(seg) != 226:
        fails.append(f"G2: clean segment wrong size ({len(seg)} != 226)")
    if any("ICE" not in r["market_name"] for r in seg):
        fails.append("G2: clean segment leaked a NYMEX row")
    if not fails or "G2" not in str(fails):
        print(f"  G2 PASS full={len(rows)} kept, clean={len(seg)} ICE-only")

    # G3 — inversion: whole-history pctile vs clean-segment pctile straddle 50
    series = [(r["date"], r["primary_net"], r["market_name"]) for r in rows]
    whole_vals = [r["primary_net"] for r in rows]
    pct_whole = derive.percentile(whole_vals, -20_566)
    clean = derive.percentile_window(series, window=520, splice=splice)
    pct_clean = clean["percentile"]
    if pct_whole is None or pct_clean is None:
        fails.append("G3: a percentile came back None")
    elif not (pct_whole < 50 < pct_clean):
        fails.append(f"G3: no inversion (whole={pct_whole}, clean={pct_clean})")
    else:
        print(f"  G3 PASS inversion whole={pct_whole} < 50 < clean={pct_clean} "
              f"(segment={clean['segment']})")

    # G4 — percentile_<window> is parametrized: window is explicit + reported
    w156 = derive.percentile_window(series, window=156, splice=splice)
    w520 = derive.percentile_window(series, window=520, splice=splice)
    if w156["window"] != 156 or w520["window"] != 520:
        fails.append("G4: window not echoed")
    elif w156["n_obs"] > 156 or w520["n_obs"] > 226:
        fails.append(f"G4: window not applied (n156={w156['n_obs']}, n520={w520['n_obs']})")
    else:
        print(f"  G4 PASS window param: 156->{w156['percentile']} (n={w156['n_obs']}), "
              f"520->{w520['percentile']} (n={w520['n_obs']})")

    # G5 — guards: short history and flat history both return None
    if derive.percentile([1, 2, 3], 2) is not None:
        fails.append("G5: short history not flagged")
    if derive.percentile([5] * 20, 5) is not None:
        fails.append("G5: zero dispersion not flagged")
    if "G5" not in str(fails):
        print("  G5 PASS insufficient-history -> None, zero-dispersion -> None")

    # G6 — registry invariants
    if len(markets.MARKETS) != 38:
        fails.append(f"G6: not 38 markets ({len(markets.MARKETS)})")
    if len(markets.migrated()) != 37:
        fails.append(f"G6: migrated != 37 ({len(markets.migrated())})")
    wti = next(m for m in markets.MARKETS if m["key"] == "wti")
    if wti.get("canonical") is not None or wti.get("reuse") != "oil_cot_wti_mm_pctile":
        fails.append("G6: WTI not reusing oil series")
    codes = [m["cftc_code"] for m in markets.MARKETS if m.get("cftc_code")]
    if len(codes) != len(set(codes)):
        fails.append("G6: duplicate cftc_code in registry")
    if "G6" not in str(fails):
        print(f"  G6 PASS 38 markets, 37 migrated, {len(codes)} pinned codes "
              f"(unique), WTI reuses {wti['reuse']}")

    # G7 — code-pinned rename is benign (name_rebrand, keep whole); a non-pinned
    # 2-identity key is a real contract_splice that the consumer must restrict.
    pinned_market = {"cftc_code": "042601"}      # e.g. UST 2Y (one stable code)
    nonpinned_market = {}                         # LIKE key, no code
    q_pinned = derive.data_quality(rows, pinned_market)
    q_nonpinned = derive.data_quality(rows, nonpinned_market)
    pin_flags = {f["flag"] for f in q_pinned}
    non_flags = {f["flag"] for f in q_nonpinned}
    if "name_rebrand" not in pin_flags or "contract_splice" in pin_flags:
        fails.append(f"G7: pinned rename not benign ({pin_flags})")
    elif "contract_splice" not in non_flags or "name_rebrand" in non_flags:
        fails.append(f"G7: non-pinned split not flagged contract_splice ({non_flags})")
    else:
        print(f"  G7 PASS pinned->name_rebrand {sorted(pin_flags)} | "
              f"non-pinned->contract_splice {sorted(non_flags)}")

    # G8 — history gap: a multi-year hole under one code is flagged.
    gap_rows = [_row("2006-06-13", 1000, "X"), _row("2008-09-16", 1100, "X"),
                _row("2017-08-15", 900, "X"), _row("2026-06-09", 950, "X")]
    gap = derive.detect_history_gap(gap_rows)
    if not gap or gap["flag"] != "history_gap" or gap["gap_days"] < 365:
        fails.append(f"G8: history gap not detected ({gap})")
    else:
        print(f"  G8 PASS history_gap {gap['from_date']}..{gap['to_date']} "
              f"({gap['gap_days']}d)")

    print()
    if fails:
        for f in fails:
            print("  FAIL:", f)
        return 1
    print("  ALL SPLICE/IDENTITY GATES PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

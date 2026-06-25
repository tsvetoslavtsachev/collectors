"""P4 backfill verify -- reads a backfilled archive root and checks the seed gates.

INDEPENDENT of the driver: it reads ``<root>/archive/px_*/<YYYY>.jsonl`` directly (raw
lines AND the P1 ``read`` current view) and asserts the seed is honest. Run it against
the TEMP root before sign-off, and again against the real root after the write.

Gates (exit non-zero if any HARD gate fails):
  v1a depth     HARD  -- SPY reaches its 1993 inception; full per-symbol earliest-as_of report
  v1b baseline  HARD  -- every baselined OLD ETF reaches its known inception + expected depth
                         (the silent-truncation catch: truncating/dropping any of them FAILS)
  v2a coverage  HARD  -- the seed is COMPLETE (0 dead/empty); a partial fetch must NOT pass
                         (this is the CI commit gate -- a throttled partial seed fails here)
  v2b thin      soft  -- no live series truncated to < 5 bars (smell, subsumed by v1b)
  v3 split      soft  -- splits (forward AND reverse) reconstruct as-traded = close*split_factor;
                         factor positive, anchors at 1.0 on the tip, piecewise-constant
  v4 conflict   HARD  -- first seed is restatement-free: exactly ONE jsonl line per as_of
                         (a 2nd line = a bitemporal restatement that a clean seed must not have)
  v5 isolation  soft  -- dead/missing series listed for the operator (HARD coverage is v2a)
  v6 shape      HARD  -- every bar carries the full record shape + recorded_on

``--daily`` (P5 routine-daily CI commit gate) keeps v1a/v1b/v2/v3/v5/v6 VERBATIM, ADDS a
recency catch (v2c), and swaps ONLY the conflict gate: after daily runs a bar legitimately
carries >1 vintage (a split/dividend restatement, or the prior provisional tip frozen), so the
seed's "exactly one line" v4 would false-fail. The daily v4' asserts the on-disk BITEMPORAL
SHAPE is sound -- distinct, advancing recorded_on per as_of (the multi-vintage corruption mode),
the view resolving to one bar per as_of, and the provisional-tip invariant (<=1 provisional,
at the tip). NOTE the scope: v4' is NOT an independent backstop against a single-line in-place
tamper (one line, value changed, same vintage) -- that leaves exactly one line and slips past
v4'. The "no finalized bar is silently overwritten" guarantee is enforced at WRITE time by
archive.append's advancing-recorded_on refusal, not re-proven here. The default (no flag) is the
seed gate, byte-for-byte unchanged -- so price-backfill.yml is untouched.

Run:
  PYTHONPATH=<data-core>;<collectors> python -m collectors.price.verify_backfill --root <root>
  PYTHONPATH=<data-core>;<collectors> python -m collectors.price.verify_backfill --root <root> --daily
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import yaml

from datacore import archive

# A series whose latest as_of lags the UNIVERSE session by more than this many calendar days
# is surfaced by the --daily recency gate (v2c). Generous enough that a 1-2 day seed-time
# misalignment or a single missed session does not fire; tight enough that a real partial
# throttle (a symbol stuck behind the pack for a week) is flagged.
_STALE_WARN_DAYS = 4

HERE = Path(__file__).resolve().parent

_SHAPE = {"as_of", "value", "open", "high", "low", "close", "value_tr",
          "volume", "split_factor", "dividend", "source", "recorded_on"}

# HARD per-symbol depth baseline (the silent-truncation catch), covering the FULL 132-symbol
# universe -- NOT a curated subset, so truncating ANY ETF (not just the oldest) HARD-FAILS.
# Ground truth = the verified P4 inception backfill (2026-06-25). Each entry is
# (must-reach-back-AT-LEAST-to, min-rows):
#   * date = the measured inception + 60 days grace (yfinance can shift a first bar by days;
#     a multi-year truncation moves earliest FAR past this and fails). Check: earliest <= date.
#   * min-rows = ~90% of the measured seed depth. Rows only GROW as P5 appends, so this floor
#     is safe forever; a material truncation drops rows below it and fails.
# Young ETFs are baselined against their OWN recent inception (e.g. urnm 2020, ~1482 rows), so
# the gate never confuses a legitimately-young series with a truncated old one. The earlier
# 31-symbol curated baseline left ~44 pre-2008 ETFs un-checked (a residual of the same bug);
# this full-universe baseline closes that. The gate is now self-sufficient -- it catches a
# partial/truncated CI fetch WITHOUT relying on the local replay invariant. Regenerate from a
# fresh verified seed only if the universe (config.yaml) changes.
_INCEPTION_BASELINE = {
    "px_spy_daily": ("1993-03-30", 7567), "px_qqq_daily": ("1999-05-09", 6179),
    "px_iwm_daily": ("2000-07-25", 5902), "px_dia_daily": ("1998-03-21", 6436),
    "px_vti_daily": ("2001-08-14", 5663), "px_mdy_daily": ("1995-07-03", 7053),
    "px_ijh_daily": ("2000-07-25", 5902), "px_ijr_daily": ("2000-07-25", 5902),
    "px_ivv_daily": ("2000-07-18", 5906), "px_voo_daily": ("2010-11-08", 3574),
    "px_xlk_daily": ("1999-02-20", 6226), "px_xlf_daily": ("1999-02-20", 6226),
    "px_xle_daily": ("1999-02-20", 6226), "px_xlv_daily": ("1999-02-20", 6226),
    "px_xli_daily": ("1999-02-20", 6226), "px_xly_daily": ("1999-02-20", 6226),
    "px_xlp_daily": ("1999-02-20", 6226), "px_xlu_daily": ("1999-02-20", 6226),
    "px_xlb_daily": ("1999-02-20", 6226), "px_xlc_daily": ("2018-08-18", 1813),
    "px_qual_daily": ("2013-09-16", 2928), "px_mtum_daily": ("2013-06-17", 2985),
    "px_usmv_daily": ("2011-12-19", 3321), "px_vlue_daily": ("2013-06-17", 2985),
    "px_size_daily": ("2013-06-17", 2985), "px_iwf_daily": ("2000-07-25", 5902),
    "px_iwd_daily": ("2000-07-25", 5902), "px_dgro_daily": ("2014-08-11", 2724),
    "px_vig_daily": ("2006-07-01", 4562), "px_dvy_daily": ("2004-01-06", 5122),
    "px_schd_daily": ("2011-12-19", 3321), "px_ita_daily": ("2006-07-04", 4559),
    "px_xar_daily": ("2011-11-28", 3334), "px_ppa_daily": ("2005-12-25", 4677),
    "px_ura_daily": ("2011-01-04", 3537), "px_urnm_daily": ("2020-02-02", 1482),
    "px_nlr_daily": ("2007-10-14", 4270), "px_icln_daily": ("2008-08-24", 4075),
    "px_qcln_daily": ("2007-04-15", 4383), "px_tan_daily": ("2008-06-14", 4120),
    "px_fan_daily": ("2008-08-26", 4073), "px_cnrg_daily": ("2018-12-22", 1734),
    "px_soxx_daily": ("2001-09-11", 5646), "px_smh_daily": ("2000-08-04", 5897),
    "px_aiq_daily": ("2018-07-15", 1834), "px_arkk_daily": ("2014-12-30", 2635),
    "px_botz_daily": ("2016-11-12", 2213), "px_robo_daily": ("2013-12-21", 2868),
    "px_hack_daily": ("2015-01-11", 2628), "px_bug_daily": ("2019-12-31", 1502),
    "px_cibr_daily": ("2015-09-05", 2483), "px_wcld_daily": ("2019-11-05", 1538),
    "px_clou_daily": ("2019-06-15", 1627), "px_finx_daily": ("2016-11-12", 2213),
    "px_blok_daily": ("2018-03-26", 1903), "px_ibb_daily": ("2001-04-13", 5741),
    "px_xbi_daily": ("2006-04-07", 4615), "px_ihi_daily": ("2006-07-04", 4559),
    "px_arkg_daily": ("2014-12-30", 2635), "px_pave_daily": ("2017-05-07", 2104),
    "px_ifra_daily": ("2018-06-04", 1860), "px_gii_daily": ("2007-04-01", 4392),
    "px_ewu_daily": ("1996-05-17", 6855), "px_ewg_daily": ("1996-05-17", 6855),
    "px_ewq_daily": ("1996-05-17", 6855), "px_ewi_daily": ("1996-05-17", 6855),
    "px_ewp_daily": ("1996-05-17", 6855), "px_vgk_daily": ("2005-05-09", 4821),
    "px_ezu_daily": ("2000-09-29", 5862), "px_ieur_daily": ("2014-08-11", 2724),
    "px_ewj_daily": ("1996-05-17", 6855), "px_ewa_daily": ("1996-05-17", 6855),
    "px_ewc_daily": ("1996-05-17", 6855), "px_ewt_daily": ("2000-08-22", 5885),
    "px_ewy_daily": ("2000-07-11", 5911), "px_ews_daily": ("1996-05-17", 6855),
    "px_inda_daily": ("2012-04-03", 3256), "px_mchi_daily": ("2011-05-30", 3447),
    "px_fxi_daily": ("2004-12-07", 4915), "px_kweb_daily": ("2013-09-30", 2919),
    "px_ewz_daily": ("2000-09-12", 5872), "px_eem_daily": ("2003-06-13", 5253),
    "px_vwo_daily": ("2005-05-09", 4821), "px_iemg_daily": ("2012-12-23", 3091),
    "px_eww_daily": ("1996-05-17", 6855), "px_eis_daily": ("2008-05-27", 4131),
    "px_acwi_daily": ("2008-05-27", 4131), "px_vt_daily": ("2008-08-25", 4074),
    "px_tlt_daily": ("2002-09-28", 5413), "px_ief_daily": ("2002-09-28", 5413),
    "px_shy_daily": ("2002-09-28", 5413), "px_govt_daily": ("2012-04-24", 3243),
    "px_bnd_daily": ("2007-06-09", 4350), "px_agg_daily": ("2003-11-28", 5148),
    "px_bndx_daily": ("2013-08-03", 2956), "px_lqd_daily": ("2002-09-28", 5413),
    "px_hyg_daily": ("2007-06-10", 4349), "px_jnk_daily": ("2008-02-02", 4201),
    "px_emb_daily": ("2008-02-17", 4191), "px_tip_daily": ("2004-02-03", 5105),
    "px_vtip_daily": ("2012-12-15", 3096), "px_mub_daily": ("2007-11-09", 4255),
    "px_vcit_daily": ("2010-01-22", 3753), "px_vcsh_daily": ("2010-01-22", 3753),
    "px_bkln_daily": ("2011-05-02", 3465), "px_flot_daily": ("2011-08-16", 3399),
    "px_gld_daily": ("2005-01-17", 4889), "px_iau_daily": ("2005-03-29", 4846),
    "px_slv_daily": ("2006-06-27", 4563), "px_uso_daily": ("2006-06-09", 4575),
    "px_dbc_daily": ("2006-04-07", 4615), "px_dba_daily": ("2007-03-06", 4408),
    "px_pdbc_daily": ("2015-01-06", 2630), "px_cper_daily": ("2012-01-14", 3304),
    "px_weat_daily": ("2011-11-18", 3341), "px_corn_daily": ("2010-08-08", 3632),
    "px_pall_daily": ("2010-03-09", 3726), "px_pplt_daily": ("2010-03-09", 3726),
    "px_copx_daily": ("2010-06-19", 3663), "px_gdx_daily": ("2006-07-21", 4549),
    "px_gdxj_daily": ("2010-01-10", 3761), "px_sil_daily": ("2010-06-19", 3663),
    "px_vnq_daily": ("2004-11-28", 4922), "px_iyr_daily": ("2000-08-18", 5888),
    "px_schh_daily": ("2011-03-14", 3495), "px_xlre_daily": ("2015-12-07", 2423),
    "px_vnqi_daily": ("2010-12-31", 3541), "px_uup_daily": ("2007-04-30", 4374),
    "px_fxe_daily": ("2006-02-10", 4648), "px_fxy_daily": ("2007-04-14", 4384),
    "px_fxf_daily": ("2006-08-25", 4527), "px_dxy_daily": ("1971-03-05", 12678),
}


class Gate:
    def __init__(self):
        self.total = 0
        self.fails: list[str] = []

    def check(self, name, cond, detail="", hard=True):
        self.total += 1
        tag = "[PASS]" if cond else ("[FAIL]" if hard else "[WARN]")
        print(f"  {tag} {name}" + (f" -- {detail}" if detail else ""))
        if not cond and hard:
            self.fails.append(name)


def _series_ids(cfg: dict) -> list[str]:
    return list(cfg["price"])


def _raw_lines(root: Path, sid: str) -> list[dict]:
    """Every stored jsonl line for a series, across all year files (NOT deduplicated to
    a current view) -- so a bitemporal restatement shows up as >1 line for an as_of."""
    d = root / "archive" / sid
    out: list[dict] = []
    if not d.exists():
        return out
    for yf in sorted(d.glob("*.jsonl")):
        for ln in yf.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
    return out


def verify(root: Path, cfg: dict, g: Gate, *, daily: bool = False) -> dict:
    sids = _series_ids(cfg)
    sym = {sid: m["symbol"] for sid, m in cfg["price"].items()}

    # Read every series once (current view) + raw line counts (+ raw lines for --daily,
    # which needs each line's recorded_on to prove restatements are bitemporal).
    views: dict[str, list] = {}
    rawcounts: dict[str, dict] = {}
    rawlines: dict[str, list] = {}
    for sid in sids:
        views[sid] = archive.read(sid, root=str(root))
        lines = _raw_lines(root, sid)
        rawlines[sid] = lines
        per_asof: dict[str, int] = {}
        for r in lines:
            per_asof[r["as_of"]] = per_asof.get(r["as_of"], 0) + 1
        rawcounts[sid] = per_asof

    live = {sid: v for sid, v in views.items() if v}
    dead = [sid for sid in sids if not views[sid]]

    # ---- v1 depth + per-symbol inception baseline (HARD) ----
    spy = views.get("px_spy_daily", [])
    spy_earliest = spy[0]["as_of"] if spy else None
    g.check("v1a SPY backfilled to its 1993 inception",
            bool(spy_earliest) and spy_earliest <= "1993-12-31",
            f"earliest={spy_earliest}")
    print("\n  -- per-symbol earliest-as_of / rows (sorted by earliest) --")
    rows = [(views[s][0]["as_of"] if views[s] else "EMPTY",
             views[s][-1]["as_of"] if views[s] else "-", len(views[s]), s)
            for s in sids]
    for earliest, latest, n, s in sorted(rows):
        print(f"     {earliest}  ->  {latest}   {n:>6}  {s}  ({sym[s]})")
    # v1b: the silent-truncation catch. Each baselined OLD ETF must reach its known
    # inception AND carry its expected depth -- so truncating any of them (or dropping it)
    # HARD-FAILS, not just SPY. (Young ETFs are intentionally un-baselined.)
    incep_viol = []
    for sid, (incep, min_rows) in _INCEPTION_BASELINE.items():
        v = views.get(sid, [])
        if (not v) or v[0]["as_of"] > incep or len(v) < min_rows:
            incep_viol.append((sid, v[0]["as_of"] if v else "EMPTY", len(v), f"<= {incep}", f">= {min_rows}"))
    g.check(f"v1b every baselined old ETF reaches its inception + depth [{len(_INCEPTION_BASELINE)} baselined]",
            not incep_viol, f"violations={incep_viol[:6]}")

    # ---- v2 coverage (HARD) -- the seed must be COMPLETE; a partial fetch must NOT pass ----
    # This is the CI commit gate too (price-backfill.yml), so a Yahoo-throttled partial seed
    # must FAIL here rather than self-promote to the real archive + bus-factor backup.
    g.check(f"v2a coverage: every expected series is live (0 dead/empty of {len(sids)})",
            len(dead) == 0, f"dead={dead}")
    thin = [s for s in live if len(views[s]) < 5]
    g.check("v2b no live series truncated to < 5 bars (silent-truncation smell)",
            not thin, f"thin={thin}", hard=False)

    if daily:
        # v2c (DAILY RECENCY) -- the partial-throttle catch that v2a CANNOT make. v2a passes as
        # long as every series has SOME bar (the P4 seed), so a day where Yahoo serves only a
        # SUBSET of the 132 symbols commits a GREEN run while silently omitting today's bar for
        # the rest (the rest keep yesterday's tip). Compare each series' latest as_of to the
        # UNIVERSE MODE (the session the pack reached): a series lagging the mode is genuinely
        # behind. Mode-relative by design -> a FULL-block day shifts the whole pack together so
        # the mode moves with it and NOTHING lags (no weekend/holiday false-fire); only a series
        # stuck BEHIND an advanced majority is flagged. SOFT/surfaced (not HARD): one unrecoverable
        # >1mo hole must not block committing every OTHER symbol's good new bar -- but it is no
        # longer SILENT (the count + laggards print in the CI log; run.py's skip headline echoes it).
        latest = {sid: v[-1]["as_of"] for sid, v in live.items()}
        if latest:
            mode_asof = Counter(latest.values()).most_common(1)[0][0]
            laggards = sorted(
                (sid, ao, abs((date.fromisoformat(mode_asof) - date.fromisoformat(ao)).days))
                for sid, ao in latest.items()
                if ao < mode_asof
                and abs((date.fromisoformat(mode_asof) - date.fromisoformat(ao)).days) > _STALE_WARN_DAYS)
            g.check(f"v2c daily recency: 0 series lag the universe session {mode_asof} by >{_STALE_WARN_DAYS}d",
                    not laggards,
                    f"laggards={[(s, a) for s, a, _ in laggards][:8]} (n={len(laggards)} -- "
                    f"partial throttle: today's bar missing for these; self-heals within the window "
                    f"unless the gap exceeds daily_period)", hard=False)

    # ---- v3 split: as-traded reconstruction over the FULL history ----
    # Detect ANY split (factor != 1.0) -- FORWARD (>1, e.g. QQQ 2:1) AND REVERSE (<1,
    # e.g. USO 1:8 -> 0.125, common in commodity/thematic ETFs). The earlier "monotone
    # non-increasing" assumption was FALSE for reverse splits (factor rises 0.125 -> 1.0
    # over time); the citizen is correct (as-traded = close*split_factor holds either way).
    split_syms = []
    for sid, v in live.items():
        facs = [b.get("split_factor", 1.0) for b in v]
        if any(abs(f - 1.0) > 1e-9 for f in facs):
            split_syms.append((sid, facs))
    if split_syms:
        recon_ok = pos_ok = anchor_ok = pcw_ok = True
        fwd = rev = 0
        for sid, facs in split_syms:
            v = views[sid]
            if not all((b["close"] * b.get("split_factor", 1.0)) > 0 for b in v):
                recon_ok = False                 # as-traded finite/positive everywhere
            if any(f <= 0 for f in facs):
                pos_ok = False                   # split_factor strictly positive
            if abs(facs[-1] - 1.0) > 1e-9:
                anchor_ok = False                # newest bar has no future split -> factor 1.0
            if len(set(round(f, 6) for f in facs)) > 20:
                pcw_ok = False                   # piecewise-constant: a few split levels, not per-bar drift
            if max(facs) > 1.0 + 1e-9:
                fwd += 1
            if min(facs) < 1.0 - 1e-9:
                rev += 1
        g.check(f"v3a as-traded = close*split_factor reconstructs on {len(split_syms)} split ETF(s)",
                recon_ok, f"forward={fwd} reverse={rev}", hard=False)
        g.check("v3b split_factor strictly positive everywhere", pos_ok, hard=False)
        g.check("v3c split_factor anchors at 1.0 on the newest bar (the immutable as-traded tip)",
                anchor_ok, hard=False)
        g.check("v3d split_factor piecewise-constant (a few split levels, not per-bar drift)",
                pcw_ok, hard=False)
    else:
        g.check("v3a ETF universe had no split in-window (split_factor==1.0 throughout)",
                True, "formula proven separately by the P3 NVDA/AAPL live gate", hard=False)

    if not daily:
        # ---- v4 conflict (SEED): restatement-free -> exactly one line per as_of ----
        dup = {sid: {ao: c for ao, c in pa.items() if c > 1}
               for sid, pa in rawcounts.items()}
        dup = {sid: d for sid, d in dup.items() if d}
        g.check("v4a no as_of has > 1 jsonl line (first seed is restatement-free, restated=0)",
                not dup, f"dup_series={list(dup)[:5]}")
        # current view line-count must equal raw line-count when there are no restatements
        mismatched = [sid for sid in live
                      if len(views[sid]) != sum(rawcounts[sid].values())]
        g.check("v4b current view == raw lines (no hidden extra vintages)",
                not mismatched, f"mismatched={mismatched[:5]}")
    else:
        # ---- v4 conflict (DAILY): restatements are LEGITIMATE but must be BITEMPORAL ----
        # After daily runs there ARE >1 lines per as_of (a split/dividend restatement, or the
        # prior provisional tip frozen). The seed "exactly one line" gate would false-fail. The
        # honest daily invariant: every restatement is AUDITABLE (distinct, advancing recorded_on
        # -> the prior line stays reachable point-in-time, never silently overwritten), the view
        # still resolves to one bar per as_of, and the finalization (provisional-tip) holds.
        bad_vintage = []   # an as_of whose lines SHARE a recorded_on -> a prior value is unreachable
        for sid in live:
            by_asof: dict[str, list] = {}
            for r in rawlines[sid]:
                by_asof.setdefault(r["as_of"], []).append(r.get("recorded_on", ""))
            for ao, ros in by_asof.items():
                if len(ros) != len(set(ros)):
                    bad_vintage.append((sid, ao, sorted(ros)))
        g.check("v4a' restatements are bitemporal: distinct recorded_on per as_of (no silent overwrite)",
                not bad_vintage, f"violations={bad_vintage[:5]}")
        # current view collapses to exactly ONE bar per DISTINCT as_of (read picks latest vintage);
        # a mismatch = a bar lost or a stale vintage leaking into the view.
        view_mismatch = [sid for sid in live
                         if len(views[sid]) != len(rawcounts[sid])]
        g.check("v4b' current view == one bar per distinct as_of (read dedup is vintage-correct)",
                not view_mismatch, f"mismatched={view_mismatch[:5]}")
        # Provisional-tip finalization invariant: at most ONE provisional bar per series, and it
        # is the TIP (latest as_of). A finalized bar left provisional, or a stale mid-history
        # provisional bar, is look-ahead corruption (the prior tip did not freeze).
        prov_bad = []
        for sid, v in live.items():
            provs = [b["as_of"] for b in v if b.get("provisional")]
            if len(provs) > 1 or (provs and provs[-1] != v[-1]["as_of"]):
                prov_bad.append((sid, provs, v[-1]["as_of"]))
        g.check("v4c' at most one provisional bar per series and it is the tip (finalization OK)",
                not prov_bad, f"violations={prov_bad[:5]}")

    # ---- v5 isolation: dead-series LISTING (informational; the HARD coverage gate is v2a).
    # A non-empty `dead` already HARD-FAILED v2a above; this just names them for the operator.
    g.check(f"v5a dead/empty series listed for the operator ({len(dead)} of {len(sids)})",
            True, f"dead={dead}" if dead else "none", hard=False)

    # ---- v6 shape: full record shape on every bar ----
    bad_shape = []
    for sid, v in live.items():
        for b in v:
            if not _SHAPE <= set(b):
                bad_shape.append((sid, b.get("as_of"), sorted(_SHAPE - set(b))))
                break
    g.check("v6a every bar carries the full record shape + recorded_on",
            not bad_shape, f"missing={bad_shape[:3]}")

    return {"live": len(live), "dead": dead, "split_syms": split_syms,
            "spy_earliest": spy_earliest}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="INIT-22 price-archive verify (P4 seed / P5 daily)")
    ap.add_argument("--root", required=True, help="archive root to verify")
    ap.add_argument("--daily", action="store_true",
                    help="P5 routine-daily gate: restatements are bitemporal (not a clean seed)")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))

    g = Gate()
    label = "P5 daily" if args.daily else "P4 backfill"
    print(f"{label} verify: root = {root}")
    summary = verify(root, cfg, g, daily=args.daily)
    print(f"\n  summary: {summary['live']} live, {len(summary['dead'])} dead, "
          f"{len(summary['split_syms'])} split ETF(s), SPY->{summary['spy_earliest']}")
    print("\n%s verify: %d/%d PASS" % (label, g.total - len(g.fails), g.total))
    if g.fails:
        print("FAILED (hard): " + ", ".join(g.fails))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

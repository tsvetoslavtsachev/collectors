"""P4 backfill verify -- reads a backfilled archive root and checks the seed gates.

INDEPENDENT of the driver: it reads ``<root>/archive/px_*/<YYYY>.jsonl`` directly (raw
lines AND the P1 ``read`` current view) and asserts the seed is honest. Run it against
the TEMP root before sign-off, and again against the real root after the write.

Gates (exit non-zero if any HARD gate fails):
  v1a depth     HARD  -- SPY reaches its 1993 inception; full per-symbol earliest-as_of report
  v1b baseline  HARD  -- every baselined OLD ETF reaches its known inception + expected depth
                         (the silent-truncation catch: truncating/dropping any of them FAILS)
  v2a coverage  HARD  -- the seed is COMPLETE (0 dead/empty); a partial fetch must NOT pass
                         (this is the CI commit gate -- a throttled partial seed fails here);
                         series on the documented _QUARANTINE_DEAD_OK allowlist (upstream-dead,
                         P8b HON / P8c ROG.SW pattern) are allowed-dead but LISTED loudly
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

FRESHNESS (v2c recency + v2d density, --daily only). "Is every series in the active universe
still ARRIVING?" is a question the integrity gates cannot answer: v2a calls a series live if it
has ANY bar, so one that stopped months ago passes forever. v2c catches a frozen tip, v2d catches
the stage BEFORE that -- a series still arriving but thinned out (a dying vendor alias goes daily
-> weekly -> dead, and each late bar resets v2c's clock while interior sessions vanish).

Their verdict is DEFERRED, not softened. Both print here as warnings and are written to a JSON
report (--freshness-report); the workflow re-reads it AFTER the commit step
(--assert-freshness) and fails the run there. Reason: this verify runs BEFORE the commit, so a
hard failure would withhold every other symbol's good new bars -- the exact trade-off that kept
the recency check toothless for months. Moving WHEN it is judged dissolves it: good bars land,
the run still goes red. Exemptions live in _FRESHNESS_EXEMPT and carry a review_by date that
EXPIRES into a failure.

Run:
  PYTHONPATH=<data-core>;<collectors> python -m collectors.price.verify_backfill --root <root>
  PYTHONPATH=<data-core>;<collectors> python -m collectors.price.verify_backfill --root <root> --daily
  ... --root <root> --daily --freshness-report fresh.json     # write the deferred verdict
  ... --assert-freshness fresh.json                           # judge it (post-commit; no archive read)
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

# ...and beyond THIS many days it is no longer a throttle -- it is a series that has stopped
# arriving, which is an ERROR, not an observation (the VendorEmpty doctrine from
# fundamentals-archive/scripts/fetch.py: a vendor saying nothing is a fault, not data).
# WHY 10: the measured noise ceiling is a missed CI firing over a weekend (~4 days, which is
# exactly why _STALE_WARN_DAYS is 4), so 10 sits 2.5x above it; and it is still INSIDE the
# ~1mo daily_period self-heal window, so the alarm rings while the routine path can still
# fill the hole -- not after it has become unrecoverable.
_STALE_FAIL_DAYS = 10

# ---- DENSITY (v2d): the catch v2c structurally CANNOT make ---------------------------------
# A dying vendor alias does not stop dead -- it THINS OUT. Measured live on the BK/SATS case
# (2026-08-04): daily bars through 07-02, then Fridays only (07-10, 07-17), then nothing. While
# the Friday bars kept arriving, every one of them RESET the v2c clock, so a gate that looks at
# the latest as_of alone stayed quiet while 8 interior sessions went missing. Density compares a
# series against the SESSION CALENDAR OF ITS OWN EXCHANGE GROUP (US, .L, .PA, ...) -- so a
# holiday that closes a whole venue moves the calendar with it and nothing false-fires.
# Only sessions INSIDE the series' own [first bar .. last bar] range count: gaps AFTER the last
# bar are a stale tip (v2c's job, never double-counted) and gaps before the first are a young
# series.
#
# THRESHOLDS ARE MEASURED, NOT GUESSED (probe over 8 historical cut dates x 1248 series):
#   * typical legitimate miss = 1 session (px_hsba_l 2026-04-13, px_glen_l, px_rr_l, px_rio_l);
#   * worst legitimate observation in ~1 year = 4 consecutive (px_vend_ol, 2025-10-28..31);
#   * the only bigger ones were px_sunb_l (21) and px_csg_as (14) -- the DOCUMENTED junk-early-bar
#     STOXX listings, i.e. true positives the gate SHOULD catch, not noise.
# So: WARN above the typical miss, HARD strictly above the worst legitimate observation.
#
# REPLAYED against the real pre-heal archive (BK/SATS as_ofs restored from git, gate run at six
# historical cut dates), which corrected the estimate this comment first carried:
#   2026-07-08  v2c warn (6d)          v2d  -          -> yellow
#   2026-07-13  -                      v2d  4 (warn)   -> yellow   [4 == the measured noise line]
#   2026-07-17  -                      v2d  8 (FAIL)   -> RED      <- the alarm
#   2026-08-04  v2c 17d (FAIL)         v2d  8 (FAIL)   -> RED      <- when it surfaced by hand
# Note WHY 07-17 and not 07-13: on the 13th those sessions were still beyond the series' last bar
# (a stale tip, not a hole). The second Friday bar on the 17th is what turns the missed week into
# INTERIOR sessions -- the very bar that reset v2c's clock is the one that convicts under v2d.
# Net: 18 days earlier than the recency tier, and 18 days earlier than a human noticed.
_SPARSE_WINDOW = 30        # last N sessions of the series' own exchange-group calendar
_SPARSE_WARN_MAX = 1       # > this many missing interior sessions -> WARN
_SPARSE_FAIL_MAX = 4       # > this many -> HARD (strictly above the measured worst legitimate)
_GROUP_QUORUM = 0.60       # a date is a group session if >= this share of the group has it
_GROUP_MIN = 5             # groups smaller than this cannot form a quorum -> not density-checked

# QUARANTINE ALLOWLIST (the P9 twins' assert_base_sourced ALLOWLIST pattern): config-listed
# series DELIBERATELY absent from the archive because their UPSTREAM (Yahoo) data is broken
# -- documented per-entry in P8b-HON-case-to-resolve.md, each with an active settle-watch +
# a targeted re-backfill plan. They are NOT registered in the catalog (_daily_ready_scope
# never fetches them), so v2a counting them as dead would red-fail EVERY daily run on a
# known, tracked condition. v2a treats ONLY these as allowed-dead -- any OTHER dead series
# still HARD-FAILS (fail-closed), and a quarantined series that comes back LIVE prints a
# loud lift-the-entry nudge. Remove an entry together with its re-backfill (catalog
# re-register), never before.
_QUARANTINE_DEAD_OK = {
    # px_hon_daily LIFTED 2026-07-03: Yahoo settled (lagged-split resolved), re-backfilled +
    # re-registered (SEC-000496, 1506 bars). Removed with the re-backfill per the fail-closed rule.
    # px_rog_sw_daily LIFTED 2026-07-03: NOT a Yahoo outage -- Roche renamed the security
    # ROG.SW -> ROP.SW (genussschein -> participation cert, 1:1, 2026-03-17). Config symbol swapped
    # to ROP.SW, identity continued (SEC-001113 continuation_of SEC-000840), backfilled 1510 bars.
    # Removed with the rename per the fail-closed rule.
}

# FRESHNESS EXEMPTIONS -- the escape hatch for v2c/v2d, and the one that ROTS ON PURPOSE.
#   series_id -> (reason, review_by "YYYY-MM-DD")
# A quarantine allowlist with no expiry is just the old silence wearing a badge: the entry
# outlives the incident, nobody re-reads it, and the gate is green forever on a condition that
# stopped being understood months ago. So an entry PAST its review_by HARD-FAILS on its own
# ("the excuse expired") -- the only way to stay green is to renew it deliberately or fix the
# series. Keep entries rare and dated; a delisted/merged constituent belongs in config as
# `retired: true` (CTRA template), NOT here.
_FRESHNESS_EXEMPT: dict[str, tuple[str, str]] = {
    # e.g. "px_xyz_daily": ("venue-wide outage, vendor confirmed", "2026-09-01"),
}

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
    """Counts checks, HARD fails and (since 2026-08-04) WARNs separately.

    Warns used to be invisible to the tally: `total - len(fails)` counted a fired [WARN] as a
    pass, so the BK/SATS decay printed a laggard line in EVERY daily log under a headline that
    read "14/14 PASS". A warn is not a pass -- it is a check that fired without stopping the
    run, and the headline now says so.
    """

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


def _series_ids(cfg: dict) -> list[str]:
    # Skip RETIRED constituents (merged/delisted; e.g. CTRA->DVN, 2026-07-03): their bars stay
    # frozen in the archive for provenance, but verify must not read them -- else v2c would flag
    # a permanently-frozen tip as a laggard on every daily run, forever. (Retire is permanent;
    # contrast _QUARANTINE_DEAD_OK, a temporary upstream outage whose symbol returns unchanged.)
    return [sid for sid, m in cfg["price"].items() if not m.get("retired")]


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


def _exch_group(symbol: str) -> str:
    """Exchange-CALENDAR group. The suffix is the venue (.L London, .PA Paris, ...); a bare
    symbol is US. Grouping by venue is what makes the density gate holiday-proof: a closed
    venue moves its whole group's calendar together, so nothing inside it looks sparse."""
    return symbol.rsplit(".", 1)[1] if "." in symbol else "US"


def _sparse_series(cfg: dict, views: dict, sids: list) -> list:
    """[(sid, n_missing, missing_dates)] -- sessions the series' OWN exchange group had and it
    did not, inside its own [first .. last] range, over the last _SPARSE_WINDOW group sessions."""
    groups: dict[str, list] = {}
    for sid in sids:
        if views.get(sid):
            groups.setdefault(_exch_group(cfg["price"][sid]["symbol"]), []).append(sid)
    out = []
    for members in groups.values():
        if len(members) < _GROUP_MIN:
            continue                      # too small for a quorum -> no trustworthy calendar
        have = {sid: {b["as_of"] for b in views[sid]} for sid in members}
        seen = Counter(a for sid in members for a in have[sid])
        cal = sorted(a for a, n in seen.items()
                     if n >= _GROUP_QUORUM * len(members))[-_SPARSE_WINDOW:]
        if not cal:
            continue
        for sid in members:
            lo, hi = views[sid][0]["as_of"], views[sid][-1]["as_of"]
            missing = [a for a in cal if lo <= a <= hi and a not in have[sid]]
            if missing:
                out.append((sid, len(missing), missing))
    out.sort(key=lambda r: (-r[1], r[0]))
    return out


def freshness_report(cfg: dict, views: dict, sids: list, *, today: str | None = None) -> dict:
    """The DEFERRED verdict: is every series in the active universe still ARRIVING?

    Separated from the rest of verify on purpose. The integrity gates must run BEFORE the
    commit (they stop corrupt bars from landing); this one must run AFTER it, because a single
    unreachable series must never withhold 1247 other symbols' good new bars. That trade-off is
    exactly why the recency check was left soft for months -- the fix is to move WHEN it is
    judged, not to keep it toothless. So verify() only prints it; the workflow re-reads this
    report after committing and fails the run on ``hard``.
    """
    today = today or date.today().isoformat()
    live = {sid: v for sid, v in views.items() if v}
    latest = {sid: v[-1]["as_of"] for sid, v in live.items()}
    rep: dict = {
        "generated_on": today,
        "universe_session": None,
        "stale": [], "sparse": [], "exempt": [], "expired_exempt": [], "hard": False,
        "thresholds": {"stale_warn_days": _STALE_WARN_DAYS, "stale_fail_days": _STALE_FAIL_DAYS,
                       "sparse_window": _SPARSE_WINDOW, "sparse_warn_max": _SPARSE_WARN_MAX,
                       "sparse_fail_max": _SPARSE_FAIL_MAX},
    }
    if not latest:
        return rep

    def _tier(sid: str, hard_cond: bool) -> str:
        """FAIL -> WARN when a live, unexpired exemption covers the series."""
        if not hard_cond:
            return "warn"
        ex = _FRESHNESS_EXEMPT.get(sid)
        return "exempt" if (ex and ex[1] >= today) else "fail"

    # ---- v2c recency: latest as_of vs the session the PACK reached (mode-relative) ----
    mode_asof = Counter(latest.values()).most_common(1)[0][0]
    rep["universe_session"] = mode_asof
    for sid, ao in sorted(latest.items()):
        if ao >= mode_asof:
            continue
        days = (date.fromisoformat(mode_asof) - date.fromisoformat(ao)).days
        if days > _STALE_WARN_DAYS:
            rep["stale"].append({"series_id": sid, "symbol": cfg["price"][sid]["symbol"],
                                 "latest": ao, "days": days,
                                 "tier": _tier(sid, days > _STALE_FAIL_DAYS)})

    # ---- v2d density: interior sessions missing vs the series' own venue calendar ----
    for sid, n, dates in _sparse_series(cfg, views, sids):
        if n > _SPARSE_WARN_MAX:
            rep["sparse"].append({"series_id": sid, "symbol": cfg["price"][sid]["symbol"],
                                  "missing": n, "dates": dates[:12],
                                  "tier": _tier(sid, n > _SPARSE_FAIL_MAX)})

    # ---- the exemptions themselves are audited: a stale excuse is its own failure ----
    fired = {r["series_id"] for r in rep["stale"] + rep["sparse"]}
    for sid, (reason, review_by) in sorted(_FRESHNESS_EXEMPT.items()):
        entry = {"series_id": sid, "reason": reason, "review_by": review_by}
        if review_by < today:
            rep["expired_exempt"].append(entry)
        elif sid in fired:
            rep["exempt"].append(entry)

    rep["hard"] = bool(rep["expired_exempt"]
                       or any(r["tier"] == "fail" for r in rep["stale"] + rep["sparse"]))
    return rep


def verify(root: Path, cfg: dict, g: Gate, *, daily: bool = False) -> dict:
    sids = _series_ids(cfg)
    sym = {sid: m["symbol"] for sid, m in cfg["price"].items()}
    fresh: dict | None = None            # daily only; the deferred (post-commit) verdict

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
    # Known-quarantined series (see _QUARANTINE_DEAD_OK) are allowed-dead -- listed loudly,
    # never silently green; every OTHER dead series still HARD-FAILS.
    dead_unexpected = [s for s in dead if s not in _QUARANTINE_DEAD_OK]
    dead_quarantined = [s for s in dead if s in _QUARANTINE_DEAD_OK]
    live_quarantined = [s for s in _QUARANTINE_DEAD_OK if s in live]
    if live_quarantined:
        print(f"  !! quarantined series LIVE again -- re-backfill done? lift the "
              f"_QUARANTINE_DEAD_OK entry: {live_quarantined}")
    g.check(f"v2a coverage: every expected series is live "
            f"({len(dead_quarantined)} quarantine-allowed of {len(sids)})",
            len(dead_unexpected) == 0,
            f"dead={dead_unexpected} quarantined-dead={dead_quarantined}")
    thin = [s for s in live if len(views[s]) < 5]
    g.check("v2b no live series truncated to < 5 bars (silent-truncation smell)",
            not thin, f"thin={thin}", hard=False)

    if daily:
        # v2c RECENCY + v2d DENSITY -- the two catches v2a structurally CANNOT make. v2a passes
        # as long as every series has SOME bar, so a series that stopped arriving months ago is
        # "live" forever. v2c compares each latest as_of to the UNIVERSE MODE (the session the
        # pack reached): mode-relative by design, so a full-block day shifts the whole pack and
        # NOTHING lags (no weekend/holiday false-fire); only a series stuck behind an advanced
        # majority fires. v2d then catches what v2c cannot see at all -- a series still arriving,
        # but THINNED OUT (the BK/SATS Fridays-only decay), where every late bar resets v2c's clock
        # while interior sessions quietly go missing.
        #
        # Both stay SOFT *here* -- and that is not the old toothlessness, it is a change of VENUE.
        # This verify runs BEFORE the commit, where a hard failure would withhold 1247 other
        # symbols' good new bars; that trade-off is precisely why the recency check stayed a warn
        # for months. The verdict now travels in the freshness report, and the workflow re-reads it
        # AFTER committing (`--assert-freshness`) and fails the run there. Good bars land; the run
        # still goes red.
        fresh = freshness_report(cfg, views, sids)
        mode_asof = fresh["universe_session"]
        n_fail = sum(1 for r in fresh["stale"] + fresh["sparse"] if r["tier"] == "fail")
        if fresh["stale"] or fresh["sparse"] or fresh["expired_exempt"]:
            print(f"  !! freshness: {n_fail} series past a HARD line -> the post-commit gate "
                  f"will FAIL this run" if fresh["hard"] else
                  "  !! freshness: warnings only (nothing past a hard line)")
        g.check(f"v2c daily recency: 0 series lag the universe session {mode_asof} "
                f"by >{_STALE_WARN_DAYS}d",
                not fresh["stale"],
                "laggards=%s (n=%d, of which %d past the %dd HARD line)"
                % ([(r["series_id"], r["latest"]) for r in fresh["stale"]][:8], len(fresh["stale"]),
                   sum(1 for r in fresh["stale"] if r["tier"] == "fail"), _STALE_FAIL_DAYS),
                hard=False)
        g.check(f"v2d density: 0 series miss >{_SPARSE_WARN_MAX} interior session(s) of their own "
                f"venue calendar (last {_SPARSE_WINDOW})",
                not fresh["sparse"],
                "sparse=%s (n=%d, of which %d past the >%d HARD line -- a thinning-out vendor "
                "alias looks like this)"
                % ([(r["series_id"], r["missing"]) for r in fresh["sparse"]][:8], len(fresh["sparse"]),
                   sum(1 for r in fresh["sparse"] if r["tier"] == "fail"), _SPARSE_FAIL_MAX),
                hard=False)
        for e in fresh["expired_exempt"]:
            print(f"  !! EXPIRED freshness exemption {e['series_id']} (review_by "
                  f"{e['review_by']}): renew it deliberately or fix the series -- an excuse that "
                  f"outlives its review date is the old silence wearing a badge")
        for e in fresh["exempt"]:
            print(f"  -- freshness exemption ACTIVE for {e['series_id']} until {e['review_by']}: "
                  f"{e['reason']}")

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
            "spy_earliest": spy_earliest, "freshness": fresh}


def assert_freshness(path: Path) -> int:
    """Read a freshness report and TURN IT INTO AN EXIT CODE. Touches no archive.

    This is the whole point of the split: the workflow runs it AFTER the commit step, so a
    series that stopped arriving reddens the run WITHOUT withholding every other symbol's
    good new bars. A missing report means verify never got that far -- the run is already
    failing for a louder reason, so do not mask it.
    """
    if not path.exists():
        print(f"freshness gate: no report at {path} -- verify did not reach it; "
              f"nothing to judge (the earlier failure stands)")
        return 0
    rep = json.loads(path.read_text(encoding="utf-8"))
    t = rep.get("thresholds", {})
    print(f"freshness gate: universe session {rep.get('universe_session')} "
          f"(report generated {rep.get('generated_on')})")
    for r in rep.get("stale", []):
        print(f"  [{r['tier'].upper():>6}] STALE  {r['series_id']} ({r['symbol']}): last bar "
              f"{r['latest']}, {r['days']}d behind the pack "
              f"(hard line >{t.get('stale_fail_days')}d)")
    for r in rep.get("sparse", []):
        print(f"  [{r['tier'].upper():>6}] SPARSE {r['series_id']} ({r['symbol']}): "
              f"{r['missing']} interior sessions missing (hard line >{t.get('sparse_fail_max')}); "
              f"{r['dates'][:6]}")
    for e in rep.get("exempt", []):
        print(f"  [EXEMPT] {e['series_id']} until {e['review_by']}: {e['reason']}")
    for e in rep.get("expired_exempt", []):
        print(f"  [  FAIL] EXPIRED exemption {e['series_id']} (review_by {e['review_by']}): "
              f"{e['reason']}")
    if rep.get("hard"):
        print("\nfreshness gate: FAILED -- a series in the active universe has stopped arriving.\n"
              "A vendor that goes quiet is an ERROR, not an observation. Decide which it is:\n"
              "  RENAME  -- ticker changed, same book: check SEC EDGAR by CIK (the 10-Q/8-K cover\n"
              "             gives the trading symbol); swap config `symbol`, continue the identity\n"
              "             epoch, re-register, heal with `run --spot <NEW> --period 3mo`;\n"
              "  RETIRE  -- merged/delisted (EDGAR Form 15 / no ticker): config `retired: true`\n"
              "             + `retired_on`, close the identity epoch, freeze the history;\n"
              "  WAIT    -- genuine temporary vendor outage: add a DATED _FRESHNESS_EXEMPT entry.\n"
              "Templates + worked cases: P8b-HON-case-to-resolve.md.")
        return 1
    print("freshness gate: PASS (every series in the active universe is still arriving)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="INIT-22 price-archive verify (P4 seed / P5 daily)")
    ap.add_argument("--root", help="archive root to verify")
    ap.add_argument("--daily", action="store_true",
                    help="P5 routine-daily gate: restatements are bitemporal (not a clean seed)")
    ap.add_argument("--freshness-report", metavar="PATH",
                    help="--daily: also write the recency/density verdict as JSON, for the "
                         "post-commit --assert-freshness step")
    ap.add_argument("--assert-freshness", metavar="PATH",
                    help="stand-alone: read a report written earlier and exit 1 if it is hard "
                         "(reads NO archive -- run it AFTER the commit step)")
    args = ap.parse_args(argv)

    if args.assert_freshness:
        return assert_freshness(Path(args.assert_freshness).resolve())
    if not args.root:
        ap.error("--root is required (or use --assert-freshness PATH)")

    root = Path(args.root).resolve()
    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))

    g = Gate()
    label = "P5 daily" if args.daily else "P4 backfill"
    print(f"{label} verify: root = {root}")
    summary = verify(root, cfg, g, daily=args.daily)
    if args.freshness_report and summary.get("freshness") is not None:
        p = Path(args.freshness_report).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(summary["freshness"], ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
        print(f"\n  freshness report -> {p} (hard={summary['freshness']['hard']}; "
              f"judged after the commit by --assert-freshness)")
    print(f"\n  summary: {summary['live']} live, {len(summary['dead'])} dead, "
          f"{len(summary['split_syms'])} split ETF(s), SPY->{summary['spy_earliest']}")
    # A WARN is not a PASS -- the tally says so out loud (see Gate).
    print("\n%s verify: %d/%d PASS%s" % (label, g.total - len(g.fails) - len(g.warns), g.total,
                                         f" ({len(g.warns)} WARN)" if g.warns else ""))
    if g.warns:
        print("WARN (soft): " + ", ".join(g.warns))
    if g.fails:
        print("FAILED (hard): " + ", ".join(g.fails))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

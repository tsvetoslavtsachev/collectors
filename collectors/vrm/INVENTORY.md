# VRM weekly collector — input inventory (Gate 1)

INIT-22 E4/E5. Authoritative series contract, verified from
`data-core/catalog/catalog.json` + the two engines, 2026-06-22. The collector
un-freezes the 51 VRM canonical series (frozen at S6 migration: prices 2026-06-12,
macro 2026-05-31) so the engines compute on fresh food instead of stale.

## 51 VRM series (the fetch set)

Count by feed (verified against catalog `vrm_role` membership):

| Feed | Count | How fetched | Write basis |
|---|---|---|---|
| yfinance | 34 | `yfinance` — 32 ETF/idx + `^VIX`/`^MOVE` | dual (value+value_tr) for 32; value-only for idx/vix/move |
| FRED level | 13 | `api.stlouisfed.org` + `FRED_API_KEY` | value = raw level (engine transforms itself) |
| computed | 2 | derived from a fetched FRED level | value = transformed |
| manual ISM | 2 | hand-entered slot file (licensed) | value, provisional |
| **total** | **51** | | |

> Correction (anti-illusion): the build mandate's table said "yfinance 35". The
> catalog truth is **32 `source=yfinance`** ETF/idx + **2** market-context symbols
> (`^VIX`/`^MOVE`, catalog source `etf-rr-barometer`) fetched the same way = **34
> yfinance-fetched symbols**, not 35. Counts here are re-derived from catalog.

### Feed 1 — yfinance (34)

32 ETF/idx, `series_id etf_XXX -> XXX`, dual basis (`value`=Close, `value_tr`=Adj
Close), W-FRI weekly. Exceptions: `idx_dxy -> DX-Y.NYB` (value-only, value_tr
mirrors value for shape parity). Forward-only (not in MID, `bloomberg_era=false`
always): `etf_uso, etf_xlk, etf_uup, etf_soxx, etf_ewy`.

`etf_spy qqq iwm xle xlf xlv xlp xlu xly xli xlb tlt ief tip lqd hyg gld slv dbc
vnq efa eem fxi emb vug vtv uso xlk uup soxx ewy` + `idx_dxy` (32)
\+ market-context `mkt_vix -> ^VIX`, `mkt_move -> ^MOVE` (value-only, **resolution
daily** — NOT W-FRI resampled, matching catalog frequency + the sibling FRED
mkt_* dailies). (34)

> `idx_dxy` is `dual_basis:false` (value-only) but **overlay GMS reads `value_tr`
> for dxy** — so the fetcher mirrors `value_tr=value` (DXY has no distributions;
> the frozen canonical already carries `value_tr==value`). This mirror is
> load-bearing — keep it if value-only handling is ever refactored.

### Feed 2 — FRED (13 level + 1 computed = 14 catalog entries)

`series_id` → `ticker` (transform / model freq / source freq):
- `macro_awh_total_private` AWHAE (level / monthly)
- `macro_u6` U6RATE (level / monthly)
- `macro_continued_claims` CCSA (level / **monthly ← weekly mean_of_month** —
  *assumed*; the S6b "verified" set covers only TGA/ANFCI, so confirm CCSA's VRM2
  cadence vs the frozen monthly value at Gate 4)
- `macro_retail_sales` RSXFS (level / monthly)
- `macro_core_pce` PCEPILFE (level / monthly)
- `macro_ppi_commodity` PPIACO (level / monthly)
- `macro_shelter_cpi` CUSR0000SAH1 (level / monthly)
- `macro_core_cpi` CPILFESL (level / monthly) — also feeds pce_nowcast
- `liq_tga_level` WTREGEN (level / **monthly ← daily mean_of_month**)
- `liq_anfci` ANFCI (level / **monthly ← weekly mean_of_month**)
- `mkt_hy_oas` BAMLH0A0HYM2 (level / daily)
- `mkt_breakeven_10y` T10YIE (level / daily)
- `mkt_curve_10y2y` T10Y2Y (level / daily)
- `macro_ahe_yoy` CES0500000003 (**computed: 12m YoY %** / monthly) → compute.py

`mean_of_month` is the VERIFIED downsample for **TGA/ANFCI** (S6b threshold_baseline:
VRM2 uses mean-of-month; eom would mis-flag). For **CCSA** it is *assumed* and not
yet reconciled against the frozen monthly value — Gate-4 check.

### Feed 3 — computed (2)

- `macro_ahe_yoy` = 12m YoY % of CES0500000003 (the S9 salary watcher input).
- `macro_pce_nowcast` = `0.1252 + 0.5632 * CPI_MoM(core CPI)` (frozen 60m OLS
  snapshot, catalog basis), provisional. Coefficients pinned as constants in
  compute.py — config formula is documentation only (no eval).

### Feed 4 — manual ISM (2)

`macro_ism_mfg`, `macro_ism_services` — licensed, no free FRED (THE gap, S3).
Hand-entered into `ism_manual.json` (gitignored); collector reads, never fetches.
provisional=true (D8 contract). Missing slot → skipped (red in Health, not a fake).

## Where the two engines read (the consumption contract)

Verified by reading both engines this session.

**regime_engine.py (S6c)** reads **9 monthly macro** series, field `value` (raw
level), then applies its OWN transforms (YoY pct_change(12) / diff(12)):
`macro_ism_mfg, macro_ism_services, macro_awh_total_private, macro_u6,
macro_continued_claims, macro_retail_sales, macro_core_pce, macro_ppi_commodity,
macro_shelter_cpi`. → emits state `vrm_regime` (NOT canonical).

**overlay_engine.py (S6d)** reads **weekly prices**: `value_tr` (Adj Close) for
momentum/GMS, `value` (Close) for KS. SPY dates = the master Friday grid. Universe:
baskets `spy qqq iwm xlf xly xli xlv xlp xlu xle xlb gld slv dbc tip lqd hyg vnq`
(18) + GMS inputs `eem efa tlt idx_dxy` (spy/gld shared) = 22 distinct. Requires
`vrm_regime` (S6c output) present in `data/state/` first.

The remaining canonical series the collector also refreshes feed OTHER consumers
(S10 matrix needs `etf_xlk`; B5 needs `idx_dxy`+`etf_tlt`; watchers need
`macro_ahe_yoy`; liq/mkt-context feed barometer/risk) — so the collector refreshes
the WHOLE 51, not just the 9+22 the two core engines read.

## Write path (verified)

`datacore.write(series_id, records, schema_version=SCHEMA_VERSION)` — the single
guarded path (schema + identity + shape gates, then health stamp). Required record
fields: `as_of, value, source` (+ stamped `series_id, schema_version`). Extra
fields pass through: `value_tr, resolution, bloomberg_era, provisional`.

`storage.write_canonical` **overwrites the whole series file** (`json.dump`, mode
"w") — so each series' records arrive in ONE write call (mirrors the S6 importer).

VRM series are ALREADY declared in the catalog (identity guard passes) — unlike
cot/S13, NO `register_catalog` step is needed.

**Two structural safety guards (added in the Gate-1 ultracode pass — the cardinal
rule must not rest on operator memory):**
- **Root guard** (`run.py assert_safe_root`): `DATACORE_ROOT` defaults to the REAL
  data-core repo when unset, so a forgotten env var would let even `--mock`
  overwrite the frozen canonical. The collector now REFUSES to run unless a TEMP
  root is set, or `DATACORE_ALLOW_REAL=1` is explicit (Gate 5).
- **Truncation floor** (`to_datacore MIN_RETAIN_RATIO=0.5`): full-replace overwrites
  the whole file, so a short live pull would silently truncate 19y of history.
  A write that would drop a series below half its existing rows is REFUSED.

> Faithfulness note: FRED/computed records carry `resolution` but deliberately
> **not** `bloomberg_era`. With `full_replace` decided, a live FRED re-pull is
> FRED-sourced, NOT the VRM2 Bloomberg paste — so stamping `bloomberg_era=true`
> (as the frozen S6b macro did) would mislabel provenance. Omitting it is the
> honest call; the `source` field ("FRED <ticker>") already records provenance.
> (Price series keep `bloomberg_era` = as_of ≤ CUT — that boundary still means
> "reconciled against the MID/Bloomberg history" for the 32 ETF/idx.)
> ISM is the EXCEPTION in the macro feed: it is STILL the Bloomberg paste
> (`manual_bloomberg`, hand-entered), NOT a FRED re-pull — so `manual_ism` DOES stamp
> `bloomberg_era` = as_of ≤ CUT, keeping ISM byte-faithful to the frozen S6b canonical.
> The FRED honest-omission above does not apply to ISM (Gate-3 ultracode finding).

## Decisions (Цветослав, 2026-06-22) — both resolved

1. **Write mode = `full_replace`** (re-pull full history each run, one write/series,
   mirrors the S6 importer). Catches FRED revisions. **Safe for macro** because S6b
   already verified FRED ≈ the Bloomberg paste (9 flips, all value-aligned), so
   replacing Bloomberg-paste history with FRED should reproduce the regime; price
   `value_tr` micro-drift was already accepted in S6e. ⚠️ **Gate-4 must confirm**
   the regime/overlay still reproduce after the live FRED data replaces the frozen
   Bloomberg-paste macro. The truncation floor guards a short pull.
2. **VIX/MOVE = bridge from the ETF-rr barometer** (D2 intent). Marked `bridge:true`
   in config; `fetch_prices` skips them; `fetch_bridge.py` reads the barometer feed
   (one source shared with the barometer). Feed URL/shape wired at Gate 2 (stub
   returns pending until then). Not on the regime/overlay critical path.

## Gate status

- **Gate 1 (this) ✅** — inventory + skeleton, mock smoke into TEMP root: 51/51
  written, 0 skipped, wiring check OK; real base git-clean + untouched (90 files,
  newest 2026-06-18); py_compile of live fetchers = 0. **Ultracode adversarial
  verify (4 lenses): 1 blocker + 2 major + 3 minor found & FIXED** — root guard,
  truncation floor, daily VIX/MOVE, resolution fields, honesty corrections; guards
  re-verified (refuse real base; refuse truncation 6→1). Lenses 1-2 (inventory,
  engine contract) clean on first pass.
- **Gate 2 ✅ (2026-06-23)** — live fetchers pull fresh into TEMP root: 49 written,
  2 skipped (ISM = Gate 3). Prices to 2026-06-19 (fresh week; Close overlap-diff vs
  frozen = 0.000000 for all 32; value_tr drift = accepted yfinance re-adjustment).
  Macro to May/April. **Spot-check 5/5 INDEPENDENT** (yf.Ticker.history + raw FRED API,
  a different code path): SPY/GLD 06-19, T10YIE 06-22, core_cpi May, CCSA May mean.
  Bridge live (mkt_vix 17.28 / mkt_move 70.01 from the ETF-rr barometer published feed,
  forward-accumulate). **Cardinal rule PROVEN: 90 real canonical byte-identical (SHA),
  real git clean** — verified after every code change.
  **4 live-edge fixes the frozen importer never needed (all faithful to the frozen
  contract):** (1) `fetch_prices._weekly` SESSION-aware completed-week guard (label <
  today; drops partial intraday / mid-week / holiday-Friday bars that mislabel an
  intraweek close as the Friday — the overlay's KS/momentum read it; 5 unit tests in
  `tests/test_weekly.py`); (2) `fetch_fred` month-END labels + current-incomplete-month
  drop (the 9 regime macro MUST share one timestamp/month for `regime_engine`'s
  `pd.DataFrame` join — else a NaN explosion; `macro_ahe_yoy` keeps FRED month-start per
  the frozen S9b convention); (3) `to_datacore` window-preservation (forward-only; "max"
  reached SPY 1993 / PPI 1913 → false `bloomberg_era` + a different expanding-Z window
  that flips regime labels; overlay is window-inert [bounded lookbacks], regime is not);
  (4) `to_datacore` edge/gap detection (head erosion / tail regression / interior gaps
  surfaced as loud per-series WARNINGS, never silent). **CCSA `mean_of_month` fidelity
  CONFIRMED** (the INVENTORY "assumed" flag resolves: median month diff 0.7%; only
  2020-COVID months large = live FRED more correct than the frozen Bloomberg paste).
  **⚠ THE substantive Gate-2 finding — a live FRED SOURCE GAP, not a collector bug:**
  `U6RATE` + `CUSR0000SAH1` + `CPILFESL` carry `"."` for **2025-10** (the BLS Oct-2025
  release delay/shutdown); `PCEPILFE` May not yet released (today 06-23). The frozen
  Bloomberg paste FILLED these cells; honest FRED leaves the holes → one interior NaN at
  2025-10 nulls ~6 regime months through `classify()`'s `rolling(3).diff(3)` velocity,
  the missing PCE tip kills the 7th → without a fill the TEMP regime stops at 2026-04.
  **FILL POLICY DECIDED (Цветослав, 2026-06-23): CARRY-FORWARD.** New `carry_forward.py`
  fills interior gaps + tail-aligns the month-end macro cohort by carrying the last known
  value forward into each missing month up to the cohort frontier, flagged
  `filled=carry_forward` + `provisional=True` (deterministic rule → cardinal-rule clean;
  SELF-HEALING — `full_replace` overwrites the fill when FRED publishes the real value).
  **Reproduction (`regime_engine` on TEMP base): 222/228 = 97.4% over the frozen SET**,
  and the regime now reaches the **current month 2026-05**. 3 flips on the intersection
  (2007-12 awh-head + warmup; 2013-01 FRED revision; 2020-07 COVID `continued_claims`
  correction — live more correct). Only **3 unproduced** = the awh head (2007-06..08;
  FRED `AWHAE` starts 2006-03, no prior to carry back — and the live AWH is the more
  honest version: the 3 frozen head rows are flat 98.0 with no FRED backing).
  **Ultracode adversarial verify (5 lenses → synthesis): fix-required, 0 BLOCKERS; 3
  majors + minors found & FIXED.** Majors: look-ahead in `_weekly`; recent-regime
  completeness regression (above); one-directional window/floor. Minors fixed: bridge
  per-indicator abort + silent-zero (VIX/MOVE must be > 0); `manual_ism` row guard.
  Cardinal rule confirmed INTACT throughout. **Deferred to Gate 5:** move the cardinal
  `assert_safe_root` into the write path (currently entrypoint-scoped — a latent
  defense-in-depth gap; real base provably untouched today); FRED passthrough
  one-obs-per-month assert (inert for current tickers).
- **Gate 3 ✅ (2026-06-23)** — ISM manual slot + temp append; bystander SHA. New
  `seed_ism_slot.py` bootstrapped `ism_manual.json` (gitignored, licensed) from the
  frozen canonical ISM = Цветослав's own verified Bloomberg prints (246 mo each,
  2005-12..2026-05, month-end) — not fabrication, his data re-asserted through the
  manual path. **Live run -> TEMP: 51 written, 0 skipped** (Gate 2 was 49/2); both ISM
  provisional + month-end. **Regime reaches 2026-05-31 (REFLATION), 225 rows** (the
  3-shorter head vs frozen 228 = the awh head, a Gate-4 item). **Bystander: real
  canonical 90 files BYTE-IDENTICAL + full data-core git tree clean** (canonical +
  health + state + catalog; HEAD `9f08b0e` unchanged) — cardinal rule held.
  **ISM-lag question RESOLVED + the premise corrected:** `carry_forward` did NOT cover
  ISM, and ISM is an EARLY release (normally LEADS the FRED cohort; only LAGS when a
  print is not yet hand-entered). So a naive "add ISM to the cohort" would let ISM
  PUSH the frontier and over-extend FRED onto carried-forward stale data. Fix:
  decoupled the FRONTIER (FRED/computed anchor cohort, `_anchor_ids`) from the FILL set
  (`_fill_ids` = anchor + ISM) — ISM is carried forward when it lags but never moves
  the frontier; real ISM months beyond the frontier are kept. Proven: unit asserts
  (lags->carried+flagged, leads->preserved) + on the real engine (drop ISM May ->
  regime falls to April; carry Apr->May -> back to May). For today's data ISM is at
  the frontier, so zero fill fires and the FRED-cohort output is unchanged.
  **+1 faithfulness fix (ultracode):** `manual_ism` now stamps `bloomberg_era` (ISM is
  still Bloomberg; see Faithfulness note). **Ultracode adversarial verify (4 lenses +
  synthesis, sonnet): ship-with-fixes, 0 BLOCKERS;** the bloomberg_era fix applied,
  the rest flagged to Gate 4/5 (below). Collector still UNCOMMITTED (Gate 5 = first commit).
- **Gate 4 ✅ ACCEPT-with-notes (2026-06-23)** — temp (fresh collector data) regime+overlay
  vs Excel for the same week (`VRM_WEEK.md`, 2026-06-15..19, approved 06-21). **The pipeline
  reproduces Excel:** current regime **REFLATION = REFLATION**; index/velocity within tol
  (G 52.26 vs 52.16 · I 58.99 vs 59.86 · G_Vel 1.92 vs 1.90 · I_Vel 4.07 vs 4.40); **overlay
  Alignment 6/8 = 6.0/8 · GMS 4/8 MEDIUM = 4/8 MEDIUM (exact)**. Excel = MASTER_MODEL look-ahead
  -> compared to temp LEGACY (full Z); honest = product track. Historical: temp honest 225 vs
  frozen 228, **222/225 = 98.7%**, the **3 flips all the documented explicable ones** (2007-12
  warmup · 2013-01 FRED/ISM-vintage · 2020-07 COVID claims). Cardinal rule: real base 90
  byte-identical + git tree clean. Driver: `C:\Projects\_vrm_gate4.py` (compute via engines, NOT
  the hardcoded-to-real-base `import_founding_state`). **Ultracode (sonnet/high, 3 lenses +
  synth): accept-with-notes, 0 correctness blockers** (the 1 "blocker" = a `cmp()` str(6)!=str(6.0)
  print bug in the driver, FIXED + re-run -> alignment MATCH). **Carry into Gate 5 (decisions +
  monitors):**
  - **DECISION awh head -> REMOVE / accept honest FRED start (Цветослав 23.06).** Verified the 3
    head AWH values (2005-12/2006-01/2006-02) are a FLAT 98.0 placeholder (all equal; FRED AWHAE
    starts 2006-03, also 98.0) -> not real data -> "confuses" -> remove. NO code: `full_replace`
    already drops them (expected head-erosion WARN on macro_awh_total_private); real `vrm_regime`
    225 not 228 at Gate 5 (the 3 dropped labels 2007-06/07/08 are provisional, never product-facing).
  - **DECISION 2020-07 flip -> INVESTIGATED + RESOLVED (Цветослав 23.06).** Of the 9 inputs, ONLY
    `continued_claims` (CCSA) differs frozen-vs-fresh (other 8 byte-identical). COVID-spike months
    (esp. 2020-03: GROWTH 37.98 fresh vs 40.71 frozen) shift the 3M velocity baseline -> g_vel[07]
    -0.24->+0.70 -> DEFLATION->GROWTH. NOT a regression: live FRED CCSA is the more-correct vintage
    (same as the Gate-2 CCSA finding). Benign, COVID-zone, all-provisional.
  - **MONITOR I-side low bias** — temp inflation is directionally below Excel (I −0.87, I_Vel −0.33,
    ~PCE carry-forward); within tol but ~55% of the velocity budget. Watch in the CI parallel.
  - **NOTE** bloomberg_era=False on the latest week (2026-06-19) -> value_tr==value (yfinance
    retroactive dividend adj); score-safe this week, self-corrects on next pull.
- **Gate 5** — sign-off → real base write + CI autorun (Session 2). **Carry into Gate 5
  (ultracode flags):** (b) **liq_* frontier creep** — `liq_tga_level`/`liq_anfci` are
  FRED-monthly so they sit in `_anchor_ids`; they complete a month earlier than the
  regime macro, so in an early-month run they could push the frontier and phantom-extend
  the regime by a carried month. Recommend `frontier_anchor: false` for liq + core_cpi +
  pce_nowcast (bound the frontier to the 7 FRED regime inputs). (c) **MIN_RETAIN_RATIO
  0.5** too loose for a fixed-length series — a cleared/short slot at 49.6% passes;
  tighten or assert the known length. (d) move `assert_safe_root` into the write path
  (already a Gate-2 deferral). (e) **verify_s6b is migration-scoped** — it asserts
  `bloomberg_era` on the FRED macro too, which `full_replace` intentionally drops; the
  recurring collector verify is regime-reproduction + bystander + git-clean, NOT
  verify_s6b. (f) adopt `git status --porcelain` clean as the canonical bystander; git-track
  the SHA baseline manifest.

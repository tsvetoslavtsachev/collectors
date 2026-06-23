# collectors/vrm — VRM weekly collector

Third citizen of data-core (INIT-22 E4/E5). Un-freezes the 51 VRM canonical series
the regime/overlay engines read. See `INVENTORY.md` for the full series contract.

## Modules

| File | Role |
|---|---|
| `config.yaml` | series map = machine-readable inventory (symbols, tickers, transforms) |
| `fetch_prices.py` | FEED 1 — yfinance dual-basis W-FRI (live port of S6 importer) |
| `fetch_fred.py` | FEED 2 — FRED official API + key (live port of S9b) + downsample |
| `compute.py` | FEED 3 — ahe_yoy (12m YoY) + pce_nowcast (pinned OLS) |
| `manual_ism.py` | FEED 4 — ISM manual slot reader (licensed, hand-entered) |
| `to_datacore.py` | citizen step — one `datacore.write` per series |
| `run.py` | orchestration + `--mock` + completeness report |
| `mockdata.py` | offline synthetic raw (all 51 series) for `--mock` smoke |

## Run

```powershell
# Cardinal rule: Gate 1-4 write to a TEMP base; the real canonical is never
# touched until acceptance + Цветослав sign-off (Gate 5).
$env:DATACORE_ROOT = "C:\Projects\_vrm_tmp_datacore"      # TEMP, not C:\Projects\data-core
$env:PYTHONPATH    = "C:\Projects\data-core;C:\Projects\collectors"
# temp base needs the catalog (identity guard): copy catalog\ into the temp root once.

python -m collectors.vrm.run --mock      # Gate 1: offline wiring (no network)
$env:FRED_API_KEY = "<key>"              # Gate 2+: live FRED (key from env only, never logged)
python -m collectors.vrm.run             # Gate 2: live yfinance + FRED into TEMP
```

`--mock` exercises the full identity-guard + write path offline (no yfinance, no
FRED). Live run needs `yfinance`, `pandas`, and `FRED_API_KEY` in env.

## ISM manual slot

`fetch_fred`/`fetch_prices` are automatic; ISM is the only hand-fed input (licensed,
no free FRED). Maintain `ism_manual.json` (gitignored — not redistributed):

```json
{
  "macro_ism_mfg":      [{"as_of": "2026-05-31", "value": 48.5}],
  "macro_ism_services": [{"as_of": "2026-05-31", "value": 51.2}]
}
```

A missing/empty slot → those two series are skipped (red in Health), never a fake
number. provisional=true is stamped on every ISM record (D8 contract).

## Cardinal rule

A model never writes numbers here — only this deterministic path does. Gate 1-4
route `DATACORE_ROOT` to a temp base; the real canonical (90 files, frozen at the
S6 migration date) is untouched until Gate 5 sign-off.

## Deploy state — Gate 5 (2026-06-23): FOOD-ONLY, state engines FROZEN

Gate 5 shipped the collector against the **real** base (51 canonical series refreshed;
the `vrm.yml` CI does this weekly). This is a **canonical (food) refresh ONLY** — it does
**NOT** recompute the VRM **state engines**: `vrm_regime`, `vrm_overlay`, `vrm_ks_velocity`,
`vrm_b5_correlation`, `vrm_watch_state`, `vrm_confirmation_matrix`, `vrm_b6_divergence`,
`vrm_b7_scorecard` stay **frozen** in `data/state/`.

This is the strangler discipline: Excel remains the source of truth until the weekly
parallel run agrees with it over several consecutive weeks. The **brain-switch** — re-running
`regime_engine` / `overlay_engine` / the S7–S12 consumers on the fresh canonical and writing
the fresh state — is the deferred next step, NOT part of this CI.

**Do not** wire a state recompute into `vrm.yml` before that switch. Notes:
- `regime_engine` (S6c `import_founding_state.py`) HARDCODES the real base + is migration-scoped;
  de-hardcode it before any recurring use.
- The recurring collector verify is **regime-reproduction (engines vs Excel) + bystander SHA
  (non-VRM canonical byte-identical) + git-clean** — NOT `verify_s6b` (migration-scoped; it
  asserts `bloomberg_era`, which `full_replace` intentionally drops).
- ISM stays manual (licensed, gitignored) → CI refreshes the 49 free-source series; the 2 ISM
  canonical are preserved (skipped, never zeroed) until a local monthly Bloomberg-print update.
- `macro_ahe_yoy` (the salary watcher input) IS refreshed each run — the Watch checker reads it
  from fresh canonical, which is correct for the food-only scope.

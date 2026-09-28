# collectors/gold — gold spot + fund tonnage collector

Fourth citizen of data-core (ЗЛТ2, 28.09.2026). Closes `gaps_map.csv` ред 36 ("на
златото ... липсва изцяло"): before this the observatory held only the GLD fund
*price* (`etf_gld`, yfinance) -- no independent spot price, no currency other than
USD, and no way to tell "rates push gold" from "ETF holders are leaving" (no
tonnage). Depends on ЗЛТ1 (both write `catalog.json`; ЗЛТ1 must be on origin first).

## Series

| series_id | source | unit | history |
|---|---|---|---|
| `mkt_gold_usd` | LBMA Gold Price PM fix | USD/oz | 1968-04-01 |
| `mkt_gold_gbp` | LBMA Gold Price PM fix | GBP/oz | 1968-04-01 (11 closure-day gaps) |
| `mkt_gold_eur` | LBMA Gold Price PM fix | EUR/oz | 1999-01-04 (pre-euro) |
| `etf_gld_tonnes` | SPDR GLD historical archive | tonnes | 2004-11-18 |
| `etf_gld_oz` | SPDR GLD historical archive | troy oz | 2004-11-18 |

## Modules

| File | Role |
|---|---|
| `config.yaml` | series map: LBMA currency index, SPDR archive columns |
| `fetch_lbma.py` | LBMA `gold_pm.json` -> per-currency records |
| `fetch_gld.py` | SPDR `.xlsx` archive -> tonnage/ounces records (+ NAV/close kept for Г3) |
| `to_datacore.py` | citizen step + write-time guard (forward-only, anti-truncation floor, edge warnings — "upsert, не презапис", mirrors `collectors.vrm.to_datacore`) |
| `register_catalog.py` | declares the 5 series in `catalog.json` (upsert, own keys only) |
| `run.py` | orchestration + `--mock` + freshness check |
| `mockdata.py` | offline synthetic raw (all 5 series) for `--mock` smoke |
| `verify.py` | live Г1-Г4 gate runner (network + a written canonical + price-archive) |
| `tests/` | offline unit tests (parsers, write-guard + mutation, verify-gate math) |

Run: `python -m collectors.gold.run [--mock]`

## Scope decisions (not gaps)

- **IAU (iShares) is not collected.** iShares serves no machine-readable row
  without a browser (checked 28.09.2026, no login attempted). If iShares ever
  ships a public JSON/CSV feed, add it as a second `etf_iau_*` pair alongside
  `etf_gld_*` — same to_datacore guard, no schema change needed.
- **LBMA AM fix (`gold_am.json`) is not collected.** PM is LBMA's own headline
  fix; one canonical spot series per currency is enough for the observatory's
  present consumer (ЗЛТ4). AM is available from 1968-01-02 if a future session
  wants a same-source independent cross-check.
- **The SPDR archive's own NAV and Closing Price columns are read but never
  written to canonical** (`fetch_gld.NAV_COLUMN`, `CLOSE_COLUMN`) — they exist
  only to cross-check `etf_gld_tonnes`/`etf_gld_oz` against the trust's own
  reported value (Г3). Principle 4 of the plan ("без серия без потребител"):
  neither has a declared consumer.

## Гейтове (виж verify.py за живите числа)

- **Г1** наличност: първите/последните дати по-горе; последната ≥ последната в
  източника в деня на пуска.
- **Г2** еврото: LBMA EUR срещу (LBMA USD / EURUSD от price-archive), от 2003.
- **Г3** тоновете: Total Ounces (SPDR) x LBMA USD срещу Total NAV (SPDR) — само за
  сверката, не пише в канона.
- **Г4** спот срещу фонда: корелация на седмичните % промени на LBMA USD срещу
  `etf_gld.json`.

Всеки гейт е мутационно тестван (разменени валути за Г2, грешна валута за Г3,
разбъркан ред за Г4) — виж `verify.py` docstring и доклада на сесията за числата.

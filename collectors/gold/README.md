# collectors/gold — gold/silver spot + fund tonnage collector

Fourth citizen of data-core (ЗЛТ2, 28.09.2026). Closes `gaps_map.csv` ред 36 ("на
златото ... липсва изцяло"): before this the observatory held only the GLD fund
*price* (`etf_gld`, yfinance) -- no independent spot price, no currency other than
USD, and no way to tell "rates push gold" from "ETF holders are leaving" (no
tonnage). Depends on ЗЛТ1 (both write `catalog.json`; ЗЛТ1 must be on origin first).

Extended by ЗЛТ5а Част 2 (28.09.2026, "контролата"): GLDM tonnage (a control
series for `etf_gld_tonnes` — same trust family, lower fee, catches a
fund-switch masquerading as a gold exit) and LBMA spot silver (USD/GBP/EUR).
Both feed the same `run.py`/`gold.yml` — no new collector, per the brief.
The central-bank gold reserves half of ЗЛТ5а lives in its own package,
`collectors/cbgold/` — see its README for why.

## Series

| series_id | source | unit | history |
|---|---|---|---|
| `mkt_gold_usd` | LBMA Gold Price PM fix | USD/oz | 1968-04-01 |
| `mkt_gold_gbp` | LBMA Gold Price PM fix | GBP/oz | 1968-04-01 (11 closure-day gaps) |
| `mkt_gold_eur` | LBMA Gold Price PM fix | EUR/oz | 1999-01-04 (pre-euro) |
| `etf_gld_tonnes` | SPDR GLD historical archive | tonnes | 2004-11-18 |
| `etf_gld_oz` | SPDR GLD historical archive | troy oz | 2004-11-18 |
| `etf_gldm_tonnes` | SPDR GLDM historical archive | tonnes | 2018-06-26 -- **control of `etf_gld_tonnes`** (ЗЛТ5а) |
| `etf_gldm_oz` | SPDR GLDM historical archive | troy oz | 2018-06-26 -- control |
| `mkt_silver_usd` | LBMA Silver Price | USD/oz | 1968-01-02 (ЗЛТ5а) |
| `mkt_silver_gbp` | LBMA Silver Price | GBP/oz | 1968-01-02, no gaps (ЗЛТ5а) |
| `mkt_silver_eur` | LBMA Silver Price | EUR/oz | 1999-01-04 (pre-euro, ЗЛТ5а) |

## Modules

| File | Role |
|---|---|
| `config.yaml` | series map: LBMA gold/silver currency index, SPDR GLD/GLDM archive columns |
| `fetch_lbma.py` | LBMA `gold_pm.json` -> per-currency gold records |
| `fetch_gld.py` | SPDR GLD `.xlsx` archive -> tonnage/ounces records (+ NAV/close kept for Г3) |
| `fetch_gldm.py` | SPDR GLDM `.xlsx` archive -> tonnage/ounces records (ЗЛТ5а, reuses `fetch_gld.parse_archive`/`fetch_bytes`, own `to_records` for the source label + series_id's) |
| `fetch_silver.py` | LBMA `silver.json` -> per-currency silver records (ЗЛТ5а, same shape as `fetch_lbma.py`) |
| `to_datacore.py` | citizen step + write-time guard (forward-only, anti-truncation floor, edge warnings — "upsert, не презапис", mirrors `collectors.vrm.to_datacore`) |
| `register_catalog.py` | declares the 10 series in `catalog.json` (upsert, own keys only) |
| `run.py` | orchestration + `--mock` + freshness check |
| `mockdata.py` | offline synthetic raw (all 10 series) for `--mock` smoke |
| `verify.py` | live gate runner: ЗЛТ2's Г1-Г4 (LBMA gold/GLD) + ЗЛТ5а's Г4/Г5/Г6 (GLDM/silver/correlation) -- separate numbering namespaces, see the module docstring |
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
  neither has a declared consumer. Same decision for `fetch_gldm`'s copies.
- **PSLV/PHYS (Sprott), SIVR/SGOL (abrdn) are NOT collected (ЗЛТ5а, 28.09.2026).**
  Разузнаване, само четене (виж доклада на сесията за пълната таблица):
  - **PSLV/PHYS**: Sprott publishes a keyless JSON endpoint,
    `https://sprott.com/api/FinancialData/v1/BullionCalculatorData` (no login,
    HTTP 200) -- but tested live 28.09.2026, it is a **snapshot only** (today's
    holdings for all Sprott bullion trusts in one fixed-order array, no ticker
    label in the payload) with **no historical range**: a `?days=30` query
    param is silently ignored, response is byte-identical to the bare call.
    Does not satisfy "същия шаблон" (GLD/GLDM/LBMA's full-history backfill) --
    a forward-only accrual series is a materially different write pattern
    (no `Г1`-style history, index-to-ticker mapping unconfirmed by the issuer)
    that this session is not building without Ц.'s sign-off.
  - **SIVR/SGOL**: abrdn's daily-returns CSV
    (`https://www.aberdeeninvestments.com/api/funds/etfReturns?shareClassId=...
    &frequency=daily`, no login) has NO ounces/tonnes field at all --
    `Date,Price,NAV,Shares Outstanding,Fund Assets,Benchmark Price` only. The
    real holdings document (ICBC bar list, serial numbers + weights) is
    PDF-only. Same category as the already-excluded SLV/IAU (iShares): no
    machine-readable ounces feed.

## Гейтове (виж verify.py за живите числа)

ЗЛТ2 (собствена номерация):
- **Г1** наличност: първите/последните дати по-горе; последната ≥ последната в
  източника в деня на пуска.
- **Г2** еврото: LBMA EUR срещу (LBMA USD / EURUSD от price-archive), от 2003.
- **Г3** тоновете: Total Ounces (SPDR) x LBMA USD срещу Total NAV (SPDR) — само за
  сверката, не пише в канона.
- **Г4** спот срещу фонда: корелация на седмичните % промени на LBMA USD срещу
  `etf_gld.json`.

ЗЛТ5а (отделна номерация, брифът на сесията; печатат се след ЗЛТ2's Г1-Г4 в
`verify.py`'s изход):
- **Г4** GLDM тоновете: Total Ounces (GLDM) x LBMA USD срещу Total NAV (GLDM) —
  същата форма като ЗЛТ2's Г3, GLDM вместо GLD.
- **Г5** среброто еврото: LBMA silver EUR срещу (LBMA silver USD / EURUSD) —
  същата форма като ЗЛТ2's Г2, сребро вместо злато.
- **Г6** контролата (само измерване, праг няма): корелация на седмичните %
  промени в тоновете GLD и GLDM — информация за ЗЛТ4, не гейт с праг. Измерена
  28.09.2026 върху цялата обща история (2018-06-26 → 2026-09-25, 2074 общи дни,
  415 седмични проби): 0.0155 — забележимо ниска, доминирана от GLDM's огромни
  ранни % промени (фондът тръгва от 0.62 тона; всяко малко изменение в първите
  месеци е стотици % промяна). Дневна (не седмична) сверка дава подобен ред
  (0.0051). Четенето защо е работа на ЗЛТ4.

Всеки гейт е мутационно тестван (разменени валути за Г2/Г5, грешна валута/цена
за Г3/ЗЛТ5а-Г4, разбъркан ред за Г4, умножение по 10^6 в cbgold's Г2) — виж
`verify.py` docstring, `tests/` и доклада на сесията за числата.

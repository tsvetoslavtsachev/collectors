# collectors/cbgold — central-bank gold reserves (IMF IRFCL)

Fifth citizen of data-core (ЗЛТ5а Част 1, 28.09.2026). Closes `gaps_map.csv`
ред 36's "покупки на централните банки" gap (plan.md §1 row г): before this
the observatory had no series for official-sector gold reserves at all.
Depends on nothing (own dataflow, own package -- decided in Фаза А, see below).

## Series

88 series, one per country/aggregate the `IMF.STA,IRFCL` dataflow serves for
indicator `IRFCLDT1_IRFCL56V_FTO` / sector `S1XS1311` (monetary gold, official
reserves) as of 28.09.2026: 86 ISO3 countries + 2 aggregates (`EZB` = European
Central Bank, `G163` = Euro Area). Naming: `cb_gold_<code, lowercased>_tonnes`.
Unit is tonnes for every series -- raw troy ounces are never written to
canonical (kept only in-memory for the Г2 Treasury cross-check, USA), same
scope decision as `collectors.gold`'s SPDR NAV/Close columns.

The five the ЗЛТ3/ЗЛТ5а brief gated on explicitly:

| series_id | country | first period | ~last period (28.09.2026) |
|---|---|---|---|
| `cb_gold_usa_tonnes` | United States | 2000-10-31 | 2026-07-31 |
| `cb_gold_chn_tonnes` | China | 2015-06-30 | 2026-07-31 |
| `cb_gold_pol_tonnes` | Poland | 2000-04-30 | 2026-08-31 |
| `cb_gold_tur_tonnes` | Türkiye | 2000-08-31 | 2026-08-31 |
| `cb_gold_ind_tonnes` | India | 2007-10-31 | 2026-07-31 |

The other 83 series each have their own first/last dates (some much shorter
histories, some with longer reporting gaps) -- see `verify.py`'s Г1 for the
five gated countries; the full catalog carries every country's own dates.

## Фаза А decision: own package, not `collectors/gold/`

МВФ данните живеят в собствена папка, не в `collectors/gold/`, защото:
- **Различен източник и формат**: IMF SDMX-JSON, не LBMA/SPDR XLSX/JSON.
- **Различна честота**: месечна с 2-3 месеца лаг и ревизии, не дневна.
- **Различен мащаб**: 88 серии (една на страна/агрегат), не шепа фиксирани.
- **Различен CI ритъм**: собствен `cbgold.yml`, все пак съботен, но не
  зависи от нищо в `gold.yml` и не го дели.

`collectors/gold/` остава домът на LBMA/SPDR семейството (спот + фондове);
`collectors/cbgold/` е домът на официалните резерви.

## Обхватът: всички 88, не само петте

Брифът поиска "минимум петте", не таван от пет. Работещата заявка с празен
COUNTRY сегмент (wildcard) дава всички 88 страни/агрегата, които dataflow-ът
пази за този индикатор+сектор+честота -- взети целите, не само подмножеството
от ЗЛТ3's proof-of-concept заявка. Прецедент за широчина от 1:1 инструмент:
`collectors/cot` вече регистрира 30+ пазарни серии от една и съща поредица.

## Modules

| File | Role |
|---|---|
| `countries.py` | COUNTRY dimension snapshot (id -> display name), 88 entries, generated from the live IMF codelist 28.09.2026 -- used only for catalog descriptions |
| `config.yaml` | dataflow URL + naming template |
| `fetch_imf.py` | SDMX-JSON -> {country: [(as_of, oz)]}, ordered by mapped TIME_PERIOD (not by dict/index order -- see its docstring for the trap) |
| `to_datacore.py` | citizen step + write-time guard (forward-only, anti-truncation floor), mirrors `collectors.gold.to_datacore` |
| `register_catalog.py` | declares the 88 series in `catalog.json` (upsert, own keys only) |
| `run.py` | orchestration + `--mock` + freshness check (120d threshold -- monthly data with normal 2-3 month lag, not gold's daily cadence) |
| `mockdata.py` | offline synthetic raw (24 months x 88 series) for `--mock` smoke |
| `verify.py` | live Г1 (five-country availability) + Г2 (USA vs Treasury) gate runner |
| `tests/` | offline unit tests (SDMX ordering mutation, write-guard mutation, wiring, Г2 math mutation) |

Run: `python -m collectors.cbgold.run [--mock]`

## Гейтове (виж verify.py и tests/ за живите числа)

- **Г1** наличност: петте страни от брифа, последен период <= ~100 дни стар,
  първа дата <= ЗЛТ3 baseline.
- **Г2** САЩ срещу Treasury: `fine_troy_ounce_qty` сума
  (`api.fiscaldata.treasury.gov` **v2** -- не v1, виж бележката по-долу --
  `/accounting/od/gold_reserve`, всички facility/location редове) за
  последния общ месец срещу суровите унции на МВФ, разлика <= 0.01%.
  Мутация (умножи по 10^6, капанът със SCALE) -- офлайн, `tests/test_verify_gates.py`.
- **Г3** SDMX подредбата: mock отговор с разбъркан индекс -> индекс, който
  НЕ отговаря на хронологичния ред (реалният отговор е такъв, проверено
  28.09.2026) -- последният период се хваща по датата. Офлайн,
  `tests/test_fetch_imf.py`. Мутация: избор по индекс вместо по дата --
  документирана изрично в `test_mutation_pick_by_index_would_get_the_wrong_answer`.

## Капани (ЗЛТ3 → тук)

- Хостът е `api.imf.org`, не `data.imf.org` (403 през Akamai).
- Dataflow id-то е `IMF.STA,IRFCL` -- запетая, не двоеточие (404 с двоеточие).
- Ключът има точно 4 измерения: COUNTRY.INDICATOR.SECTOR.FREQUENCY.
- Секторът е `S1XS1311` (не `_Z` -- `_Z` дава 200 с нула серии, не грешка).
- `OBS_VALUE` вече е в чисти унции; `SCALE="6"` в отговора е за показване,
  никога множител.
- **Treasury endpoint-ът е v2, не v1** (намерено 28.09.2026 при строежа на
  Г2: `v1/accounting/od/gold_reserve` дава 404; истинският endpoint е
  `v2/accounting/od/gold_reserve`, открит през страницата на dataset-а,
  `page-data` JSON-а на Gatsby build-а). Полето е `fine_troy_ounce_qty`, по
  ред за facility/form/location -- трябва СУМА по `record_date`, не един ред
  (8 реда на дата: Denver/Fort Knox/West Point deep storage + working stock
  coins + Fed NY vault bullion/coins + Fed display bullion/coins).
- Наблюденията в SDMX-JSON НЕ са подредени по време, дори самият
  `TIME_PERIOD` списък в `structure.dimensions.observation` не е хронологичен
  (проверено 28.09.2026: индекси 0-4 бяха 2020-M10..2021-M02, опашката беше
  2000-M07..2000-M02) -- виж `fetch_imf.py` docstring.

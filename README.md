# collectors (public) · INIT-22

Сетивата на организма. Всеки колектор дърпа данни от свободен първоизточник и ги
**пише в `data-core` през writer lib-а** (identity guard + schema_version + health).
Числа никога не се пишат на ръка — само оттук (cardinal rule).

Публичен compute (GitHub Actions, без таван) · частна истина (data-core) · 0 лв/мес.

## Колектори

| Колектор | Серии в data-core | Източници |
|---|---|---|
| **oil** (Два часовника) | oil_brent_m1_m2 · oil_brent_wti_spread · oil_wti_close · oil_hormuz_transit_pct · oil_eia_crude_deviation · oil_cot_wti_mm_pctile | yfinance · IMF PortWatch · EIA · CFTC |

oil е **първият гражданин** на новата архитектура (INIT-22 E2): роден през принципите от ден 1.

## Локален run

```powershell
$env:DATACORE_ROOT = "C:\Projects\data-core"      # къде живее истината
$env:PYTHONPATH    = "C:\Projects\data-core;C:\Projects\collectors"
python -m collectors.oil.run --mock                # mock = без мрежа/ключове
```
Резултат: 6-те серии кацат в `data-core/data/canonical/` + health ред; светофарът → `collectors/docs/index.html`.

Реален run (без `--mock`) иска `EIA_API_KEY` за серия С3 (eia.gov/opendata).

## CI (cross-repo)

`.github/workflows/oil.yml` чеква collectors + data-core (private), пуска oil, commit-ва каноничните данни обратно в data-core и светофара тук. Нужни secrets:
- `DATACORE_PAT` — PAT (repo scope) за push в private data-core
- `EIA_API_KEY` — за серия С3

Pages: Settings → Pages → main /docs (светофарът).

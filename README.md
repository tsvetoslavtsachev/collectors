# collectors (public) · INIT-22

Сетивата на организма. Всеки колектор дърпа данни от свободен първоизточник и ги
**пише в `data-core` през writer lib-а** (identity guard + schema_version + health).
Числа никога не се пишат на ръка — само оттук (cardinal rule).

Публичен compute (GitHub Actions, без таван) · частна истина (data-core) · 0 лв/мес.

## Колектори

Репото има няколко граждани. Всеки пише през gate-а (identity + schema + health);
числата живеят в data-core (или в price-archive за цените), тук е само fetch/compute
логиката.

| Колектор | Пише в | Какво | Източници |
|---|---|---|---|
| **oil** | data-core | oil_* серии (spread, WTI close, Hormuz transit, EIA deviation, COT pctile) | yfinance · IMF PortWatch · EIA · CFTC |
| **cot** | data-core | cot_<key>_net позиционни серии + персентили (S13) | CFTC |
| **vrm** | data-core | 51 VRM серии (ETF/idx dual-basis, FRED levels, computed, manual ISM) + brain M-модулите | yfinance · FRED · manual ISM |
| **price** | price-archive | каноничен дневен ETF/stock архив (append-only, year-partitioned, bitemporal) | yfinance |
| **vrm/alfred** | data-core (vintage/) | ALFRED PIT vintage история на 7-те FRED режимни серии (M1) | FRED ALFRED |

oil е **първият гражданин** на новата архитектура (INIT-22 E2): роден през принципите от ден 1.
cot, vrm, price и alfred следват същия шаблон (base-first, cardinal rule, health-per-write).

## Локален run

```powershell
$env:DATACORE_ROOT = "C:\Projects\data-core"      # къде живее истината
$env:PYTHONPATH    = "C:\Projects\data-core;C:\Projects\collectors"
python -m collectors.oil.run --mock                # mock = без мрежа/ключове
```
Резултат: 6-те серии кацат в `data-core/data/canonical/` + health ред; светофарът → `collectors/docs/index.html`.

Реален run (без `--mock`) иска `EIA_API_KEY` за серия С3 (eia.gov/opendata).

## CI (cross-repo)

Всеки workflow чеква collectors + private data-core, пуска колектора и commit-ва
каноничните данни обратно (cross-repo push):
- `oil.yml` — oil серии (Ср + Пт)
- `cot.yml` — COT серии (съб, staggered след vrm)
- `vrm.yml` — VRM серии + brain M-модулите (съб)
- `alfred-vintage.yml` — ALFRED PIT vintage (месечно, 5-о число)

Нужни secrets (по workflow): `DATACORE_PAT` (push в private data-core) ·
`EIA_API_KEY` (oil серия С3) · `FRED_API_KEY` (vrm/alfred) · `PRICE_ARCHIVE_RO_PAT`
(brain M-модули четат price-archive) · `OPS_PAT` (oil health digest, guarded).

Pages: Settings → Pages → main /docs (светофарът).

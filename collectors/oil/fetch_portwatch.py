"""Серия 2: транзити през Ормузкия пролив — IMF PortWatch (ArcGIS REST).

PortWatch публикува дневни данни за chokepoints на сателитна AIS база.
Имената на полетата могат да се променят — затова кандидатите са в config.yaml.
Ако услугата отговори с друга схема, скриптът пише наличните полета в грешката,
за да се коригира конфигът без четене на код.

Семантика (диагноза 28.07.2026): серията мери AIS-ВИДИМИ транзити. При
затъмнени транспондери (война, dark fleet) тя подценява физическия поток —
долна граница, не физически петролен поток. Сривът от март 2026 е Ормуз-
специфичен в източника (останалите 27 chokepoints стабилни, нос Добра Надежда
се покачва = пренасочване), т.е. реални данни, не счупена схема.

Гаранции срещу тихо изкривяване:
- сървърът реже отговора на maxRecordCount (1000) независимо от заявеното —
  пагинираме до изчерпване, иначе прозорецът се плъзга и изяжда историята
  (и в крайна сметка предвоенната база);
- предвоенната база се приема само ако прозорецът ѝ е ≥90% пълен с дни;
- незавършената опашна W-FRI седмица не се публикува (2-дневна "седмица"
  после ревизира замразения ред).
"""
from __future__ import annotations
import requests
import pandas as pd

PAGE_SIZE = 1000          # колкото е сървърният maxRecordCount; пагинацията носи останалото
BASELINE_MIN_COVERAGE = 0.9


def _resolve(fields: list[str], candidates: list[str]) -> str | None:
    low = {f.lower(): f for f in fields}
    for c in candidates:
        if c.lower() in low:
            return low[c.lower()]
    return None


def _query_all(url: str, where: str) -> list[dict]:
    """Пагиниран pull: ArcGIS връща най-много maxRecordCount реда на заявка
    и вдига exceededTransferLimit — въртим resultOffset до изчерпване."""
    feats: list[dict] = []
    offset = 0
    while True:
        params = {
            "where": where,
            "outFields": "*",
            "orderByFields": "date ASC",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
            "f": "json",
        }
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        js = r.json()
        if "error" in js:
            raise RuntimeError(f"PortWatch: ArcGIS грешка: {js['error']}")
        batch = js.get("features", [])
        feats.extend(batch)
        if not batch or not js.get("exceededTransferLimit"):
            return feats
        offset += len(batch)


def fetch_hormuz(cfg: dict) -> dict:
    s2 = cfg["series2_hormuz"]
    where = f"{s2['port_filter_field']} LIKE '%{s2['port_filter_value']}%'"
    if s2.get("history_start"):
        where += f" AND date >= TIMESTAMP '{s2['history_start']} 00:00:00'"
    feats = _query_all(s2["arcgis_url"], where)
    if not feats:
        raise RuntimeError("PortWatch: празен отговор за WHERE: " + where)

    rows = [f.get("attributes", {}) for f in feats]
    fields = list(rows[0].keys())
    date_f = _resolve(fields, s2["date_field_candidates"])
    tank_f = _resolve(fields, s2["tanker_field_candidates"])
    if not date_f or not tank_f:
        raise RuntimeError(f"PortWatch: непозната схема. Налични полета: {fields}")

    df = pd.DataFrame(rows)[[date_f, tank_f]].dropna()
    # ArcGIS датите често са epoch ms
    if pd.api.types.is_numeric_dtype(df[date_f]) and df[date_f].max() > 10**11:
        df[date_f] = pd.to_datetime(df[date_f], unit="ms")
    else:
        df[date_f] = pd.to_datetime(df[date_f])
    df = df.sort_values(date_f).rename(columns={date_f: "date", tank_f: "tankers"})
    df["tankers"] = pd.to_numeric(df["tankers"], errors="coerce")
    df = df.dropna().set_index("date")

    base_win = df.loc[s2["baseline_start"]:s2["baseline_end"], "tankers"]
    expected_days = (pd.Timestamp(s2["baseline_end"]) - pd.Timestamp(s2["baseline_start"])).days + 1
    if len(base_win) < BASELINE_MIN_COVERAGE * expected_days:
        raise RuntimeError(
            f"PortWatch: непълна предвоенна база — {len(base_win)}/{expected_days} дни "
            f"в {s2['baseline_start']}..{s2['baseline_end']}; отказвам тиха дефектна база"
        )
    base = base_win.mean()
    if not base or pd.isna(base):
        raise RuntimeError("PortWatch: не мога да изчисля предвоенна база")

    daily_pct = (df["tankers"] / base * 100).round(1)
    weekly_pct = daily_pct.resample("W-FRI").mean().dropna().round(1)
    # опашната W-FRI кофа е завършена само ако данните стигат до нейния петък
    weekly_pct = weekly_pct[weekly_pct.index <= df.index.max()]

    return {
        "ok": True,
        "baseline_tankers_per_day": round(float(base), 1),
        "last_7d_pct": round(float(daily_pct.tail(7).mean()), 1),
        "weekly_pct": [(d.strftime("%Y-%m-%d"), float(v)) for d, v in weekly_pct.items()],
        "daily_tail": [(d.strftime("%Y-%m-%d"), float(v)) for d, v in daily_pct.tail(60).items()],
    }

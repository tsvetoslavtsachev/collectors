"""Серия 2: транзити през Ормузкия пролив — IMF PortWatch (ArcGIS REST).

PortWatch публикува дневни данни за chokepoints на сателитна AIS база.
Имената на полетата могат да се променят — затова кандидатите са в config.yaml.
Ако услугата отговори с друга схема, скриптът пише наличните полета в грешката,
за да се коригира конфигът без четене на код.
"""
from __future__ import annotations
import requests
import pandas as pd


def _resolve(fields: list[str], candidates: list[str]) -> str | None:
    low = {f.lower(): f for f in fields}
    for c in candidates:
        if c.lower() in low:
            return low[c.lower()]
    return None


def fetch_hormuz(cfg: dict) -> dict:
    s2 = cfg["series2_hormuz"]
    params = {
        "where": f"{s2['port_filter_field']} LIKE '%{s2['port_filter_value']}%'",
        "outFields": "*",
        "orderByFields": "date DESC" if "date" in str(s2["date_field_candidates"]) else "",
        "resultRecordCount": 4000,
        "f": "json",
    }
    r = requests.get(s2["arcgis_url"], params=params, timeout=60)
    r.raise_for_status()
    js = r.json()
    feats = js.get("features", [])
    if not feats:
        raise RuntimeError(f"PortWatch: празен отговор. Сурово: {str(js)[:300]}")

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

    base = df.loc[s2["baseline_start"]:s2["baseline_end"], "tankers"].mean()
    if not base or pd.isna(base):
        raise RuntimeError("PortWatch: не мога да изчисля предвоенна база")

    daily_pct = (df["tankers"] / base * 100).round(1)
    weekly_pct = daily_pct.resample("W-FRI").mean().dropna().round(1)

    return {
        "ok": True,
        "baseline_tankers_per_day": round(float(base), 1),
        "last_7d_pct": round(float(daily_pct.tail(7).mean()), 1),
        "weekly_pct": [(d.strftime("%Y-%m-%d"), float(v)) for d, v in weekly_pct.items()],
        "daily_tail": [(d.strftime("%Y-%m-%d"), float(v)) for d, v in daily_pct.tail(60).items()],
    }

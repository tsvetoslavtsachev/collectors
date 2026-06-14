"""Серия 3: EIA седмични запаси (US crude + Cushing) срещу 5-годишна сезонна норма."""
from __future__ import annotations
import os
import requests
import pandas as pd

API = "https://api.eia.gov/v2/seriesid/{sid}"


def _series(sid: str, key: str) -> pd.Series:
    r = requests.get(API.format(sid=sid),
                     params={"api_key": key, "out": "json"}, timeout=60)
    r.raise_for_status()
    data = r.json()["response"]["data"]
    df = pd.DataFrame(data)[["period", "value"]]
    df["period"] = pd.to_datetime(df["period"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna().set_index("period")["value"].sort_index()


def fetch_eia(cfg: dict) -> dict:
    key = os.environ.get("EIA_API_KEY", "")
    if not key:
        raise RuntimeError("Липсва EIA_API_KEY (регистрация: eia.gov/opendata)")
    s3 = cfg["series3_eia"]

    crude = _series(s3["series_crude"], key)
    cushing = _series(s3["series_cushing"], key)

    chg = crude.diff().dropna()  # хил. барела седмично
    wk = chg.index.isocalendar().week
    norm_src = chg[chg.index.year >= chg.index.year.max() - s3["seasonal_years"]]
    norm_by_week = norm_src.groupby(norm_src.index.isocalendar().week).mean()
    deviation = (chg - wk.map(norm_by_week).values) / 1000.0  # млн. барела

    return {
        "ok": True,
        "crude_last_mbbl": round(float(crude.iloc[-1]) / 1000, 1),
        "cushing_last_mbbl": round(float(cushing.iloc[-1]) / 1000, 1),
        "last_change_mbbl": round(float(chg.iloc[-1]) / 1000, 2),
        "deviations_mbbl": [(d.strftime("%Y-%m-%d"), round(float(v), 2))
                            for d, v in deviation.tail(26).items()],
        "consecutive_draws": int(_consecutive_negative(chg)),
    }


def _consecutive_negative(chg: pd.Series) -> int:
    n = 0
    for v in reversed(chg.values):
        if v < 0:
            n += 1
        else:
            break
    return n

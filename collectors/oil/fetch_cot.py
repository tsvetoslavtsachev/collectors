"""Серия 5: CFTC Disaggregated COT — managed money net в WTI, персентили."""
from __future__ import annotations
import requests
import pandas as pd


def fetch_cot(cfg: dict) -> dict:
    s5 = cfg["series5_cot"]
    params = {
        "cftc_contract_market_code": s5["contract_code"],
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": s5["history_rows"],
        "$select": ("report_date_as_yyyy_mm_dd,"
                    "m_money_positions_long_all,m_money_positions_short_all"),
    }
    r = requests.get(s5["socrata_url"], params=params, timeout=60)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    if df.empty:
        raise RuntimeError("CFTC: празен отговор")

    df["date"] = pd.to_datetime(df["report_date_as_yyyy_mm_dd"])
    for c in ("m_money_positions_long_all", "m_money_positions_short_all"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna().sort_values("date").set_index("date")
    net = df["m_money_positions_long_all"] - df["m_money_positions_short_all"]

    pctile = net.rank(pct=True) * 100  # персентил спрямо цялата изтеглена история
    return {
        "ok": True,
        "net_last": int(net.iloc[-1]),
        "pctile_last": round(float(pctile.iloc[-1]), 1),
        "pctile_2w_ago": round(float(pctile.iloc[-3]), 1) if len(pctile) > 2 else None,
        "pctile_4w_ago": round(float(pctile.iloc[-5]), 1) if len(pctile) > 4 else None,
        "pctile_series": [(d.strftime("%Y-%m-%d"), round(float(v), 1))
                          for d, v in pctile.tail(52).items()],
        "report_date": net.index[-1].strftime("%Y-%m-%d"),
    }

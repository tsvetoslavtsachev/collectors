"""Серия 1 + фалсификатор: Brent M1-M2 спред и WTI флат цена (yfinance)."""
from __future__ import annotations
import datetime as dt
import pandas as pd

MONTH_CODES = "FGHJKMNQUVXZ"  # ян..дек


def _candidate_contracts(prefix: str, suffix: str, n: int = 6) -> list[str]:
    """Следващите n месечни контракта, започвайки от следващия месец."""
    today = dt.date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        m += 1
        if m > 12:
            m, y = 1, y + 1
        out.append(f"{prefix}{MONTH_CODES[m - 1]}{str(y)[-2:]}{suffix}")
    return out


def fetch_prices(cfg: dict) -> dict:
    import yfinance as yf

    p = cfg["prices"]
    out: dict = {"ok": False}

    # WTI флат — за фалсификатора
    wti = yf.download(p["wti_flat_ticker"], period="1y", interval="1d",
                      progress=False, auto_adjust=False)
    if wti is None or wti.empty:
        raise RuntimeError("Няма данни за WTI флат")
    closes = wti["Close"]
    if isinstance(closes, pd.DataFrame):
        closes = closes.iloc[:, 0]
    closes = closes.dropna()
    out["wti_closes"] = [(d.strftime("%Y-%m-%d"), round(float(v), 2))
                         for d, v in closes.items()]
    out["wti_last"] = round(float(closes.iloc[-1]), 2)

    # Brent флат + Канарчето (Brent−WTI)
    brent = yf.download(p["brent_flat_ticker"], period="1y", interval="1d",
                        progress=False, auto_adjust=False)
    if brent is not None and not brent.empty:
        bc = brent["Close"]
        if isinstance(bc, pd.DataFrame):
            bc = bc.iloc[:, 0]
        bw = pd.concat([bc.rename("b"), closes.rename("w")], axis=1).dropna()
        bw["spr"] = bw["b"] - bw["w"]
        out["bw_spread_series"] = [(d.strftime("%Y-%m-%d"), round(float(v), 2))
                                   for d, v in bw["spr"].items()]
        out["bw_last"] = round(float(bw["spr"].iloc[-1]), 2)

    # Brent M1 и M2 — първите два валидни месечни контракта
    legs = []
    for tkr in _candidate_contracts(p["brent_contract_prefix"], p["contract_suffix"]):
        try:
            h = yf.download(tkr, period="3mo", interval="1d",
                            progress=False, auto_adjust=False)
            if h is not None and not h.empty:
                c = h["Close"]
                if isinstance(c, pd.DataFrame):
                    c = c.iloc[:, 0]
                c = c.dropna()
                if len(c) > 0:
                    legs.append((tkr, c))
            if len(legs) == 2:
                break
        except Exception:
            continue
    if len(legs) < 2:
        raise RuntimeError("Не намерих два валидни Brent контракта за M1-M2")

    (t1, c1), (t2, c2) = legs
    joined = pd.concat([c1.rename("m1"), c2.rename("m2")], axis=1).dropna()
    joined["spread"] = joined["m1"] - joined["m2"]
    out["m1_ticker"], out["m2_ticker"] = t1, t2
    out["spread_series"] = [(d.strftime("%Y-%m-%d"), round(float(v), 3))
                            for d, v in joined["spread"].items()]
    out["spread_last"] = round(float(joined["spread"].iloc[-1]), 3)
    out["brent_m1_last"] = round(float(joined["m1"].iloc[-1]), 2)
    out["ok"] = True
    return out

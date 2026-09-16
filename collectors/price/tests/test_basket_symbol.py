# -*- coding: utf-8 -*-
"""ЦАБ1 G1 -- картата гол тикер -> Yahoo символ е ПРАВИЛО по борса, не таблица.

Offline гейтове (без мрежа):
  b1 по един символ на борса   -- HKEX (ляво допълване), B3 (ON/PN отделни), NYSE/NASDAQ
                                  (без суфикс), TSE, KRX KOSPI/KOSDAQ, TWSE/TPEx.
  b2 мутации (честен отказ)     -- непозната борса -> UnmappableHolding С ИМЕТО на борсата,
                                  не суфикс по подразбиране; редът `-` в KRX -> отказ;
                                  хонконгски код с буква -> отказ.
  b3 series_id                  -- 0700.HK -> px_0700_hk_daily (конвенцията на каталога).
  b4 живите кошници             -- всеки Equity ред на петте *_us се картира ИЛИ отказва
                                  с име; пилотът fxi_us + ewz_us: нула откази, 96 символа,
                                  нула сблъсъка (PETR3 и PETR4 са две серии).
  b5 config == правилото        -- всеки член на fxi_us и ewz_us е ред в config.yaml, 1:1
                                  с изхода на функцията (нула ръчни редове).

b4/b5 четат data-core/migrations/m_conc/holdings; без него са SKIP (не PASS).

Run:
  PYTHONPATH=C:\\Projects\\data-core;C:\\Projects\\collectors \\
  python collectors/price/tests/test_basket_symbol.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

from collectors.price import basket_symbol as B

sys.stdout.reconfigure(encoding="utf-8")
FAILS = []


def check(name, ok, detail=""):
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else f"  -> {detail!r}"))
    if not ok:
        FAILS.append(name)


def refuses(ticker, exchange, must_contain):
    try:
        got = B.yahoo_symbol(ticker, exchange)
    except B.UnmappableHolding as e:
        return must_contain in str(e), str(e)
    return False, got


def holdings_root():
    for p in (os.environ.get("M_CONC_HOLDINGS"),
              *[str(Path(x) / "migrations" / "m_conc" / "holdings")
                for x in os.environ.get("PYTHONPATH", "").split(os.pathsep) if x]):
        if p and Path(p).is_dir():
            return Path(p)
    return None


print("b1 по един символ на борса")
for tk, ex, want in [
    ("939", "Hong Kong Exchanges And Clearing Ltd", "0939.HK"),
    ("700", "Hong Kong Exchanges And Clearing Ltd", "0700.HK"),
    ("9988", "Hong Kong Exchanges And Clearing Ltd", "9988.HK"),
    ("PETR3", "XBSP", "PETR3.SA"),
    ("PETR4", "XBSP", "PETR4.SA"),
    ("BPAC11", "XBSP", "BPAC11.SA"),
    ("NU", "NYSE", "NU"),
    ("XP", "NASDAQ", "XP"),
    ("7203", "Tokyo Stock Exchange", "7203.T"),
    ("005930", "Korea Exchange (Stock Market)", "005930.KS"),
    ("247540", "Korea Exchange (Kosdaq)", "247540.KQ"),
    ("2330", "Taiwan Stock Exchange", "2330.TW"),
    ("6488", "Gretai Securities Market", "6488.TWO"),
]:
    got = B.yahoo_symbol(tk, ex)
    check(f"{tk} @ {ex} -> {want}", got == want, got)

print("b2 мутации: честен отказ, не суфикс по подразбиране")
ok, d = refuses("700", "Shanghai Stock Exchange", "Shanghai Stock Exchange")
check("непозната борса -> отказ С ИМЕТО на борсата", ok, d)
ok, d = refuses("-", "Korea Exchange (Stock Market)", "'-'")
check("редът `-` в ewy_us -> отказ с тикера", ok, d)
ok, d = refuses("70A", "Hong Kong Exchanges And Clearing Ltd", "HKEX")
check("хонконгски код с буква -> отказ", ok, d)
ok, d = refuses("", "XBSP", "XBSP")
check("празен тикер -> отказ", ok, d)

print("b3 series_id")
check("0700.HK -> px_0700_hk_daily", B.series_id("0700.HK") == "px_0700_hk_daily", B.series_id("0700.HK"))
check("BT-A.L -> px_bt_a_l_daily (старата конвенция)", B.series_id("BT-A.L") == "px_bt_a_l_daily")

root = holdings_root()
if root is None:
    print("  SKIP b4/b5: няма m_conc/holdings (PYTHONPATH без data-core)")
else:
    print(f"b4 живите кошници ({root})")
    pilot = []
    for etf in ("fxi_us", "ewz_us", "ewj_us", "ewy_us", "ewt_us"):
        snap = B.last_snapshot(root, etf)
        eq = [h for h in snap["holdings"] if h.get("asset_class") == "Equity"]
        mapped, refused = [], []
        for h in eq:
            try:
                mapped.append(B.yahoo_symbol(h["ticker"], h["exchange"]))
            except B.UnmappableHolding as e:
                refused.append(str(e))
        print(f"    {etf}: {len(eq)} Equity, {len(mapped)} символа, {len(refused)} отказа {refused[:3]}")
        check(f"{etf}: всеки ред е символ ИЛИ назован отказ", len(mapped) + len(refused) == len(eq))
        check(f"{etf}: нула сблъсъка на символ", len(set(mapped)) == len(mapped))
        if etf in ("fxi_us", "ewz_us"):
            check(f"{etf} (пилот): нула откази", not refused, refused)
            pilot += mapped
    check("пилотът е 96 символа (50 FXI + 46 EWZ)", len(set(pilot)) == 96, len(set(pilot)))

    print("b5 config.yaml == правилото (нула ръчни редове)")
    cfg = yaml.safe_load((Path(B.__file__).resolve().parent / "config.yaml")
                         .read_text(encoding="utf-8"))["price"]
    for etf in ("fxi_us", "ewz_us"):
        rows = B.rows_for(root, etf)
        diff = [sid for sid, m in rows if cfg.get(sid) != m]
        check(f"{etf}: {len(rows)} реда в config, 1:1 с функцията", not diff, diff[:5])
    extra = sorted(sid for sid, m in cfg.items()
                   if m.get("origin") == "ishares-basket"
                   and sid not in {s for e in ("fxi_us", "ewz_us") for s, _ in B.rows_for(root, e)})
    check("нито един ishares-basket ред извън правилото", not extra, extra[:5])

print("GREEN" if not FAILS else f"RED ({len(FAILS)})")
sys.exit(1 if FAILS else 0)

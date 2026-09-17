# -*- coding: utf-8 -*-
"""ЦАБ1: голият тикер на кошница `*_us` -> Yahoo символ. ПРАВИЛО по борса, не таблица.

Holdings файловете на М-КОНЦ (data-core/migrations/m_conc/holdings/<etf>_us/2026.jsonl)
носят само местния тикер (`700`, `PETR3`, `7203`) плюс полето `exchange`, както го пише
iShares. Yahoo символ няма никъде (MANDATE-asia-brazil-prices §1), затова символът се
ИЗВЕЖДА от борсата: една функция на борса, тест по един символ на борса.

Честният отказ (G1): непозната борса или тикер, който не отговаря на формата на своята
борса (например редът `-` в ewy_us), вдига `UnmappableHolding` с името, никога суфикс
по подразбиране. Гаденият символ би дал серия на ГРЕШНА компания (урокът за голия тикер:
DTE, BA, TSCO са различни компании в два екрана).

Огледалото в четеца: data-core/migrations/drill/s8_fund.py `basket_ticker` носи СЪЩОТО
хонконгско допълване; гейтът там сверява, че всеки член на кошница със суфикс има серия
в каталога, тоест разминаване между двете места пада като липсваща серия, не мълчи.

CLI (генератор и проверка на редовете в config.yaml, G1 „нула ръчни редове"):
    python -m collectors.price.basket_symbol --holdings <m_conc/holdings> ewj_us ewy_us ewt_us
    python -m collectors.price.basket_symbol --holdings <...> --check fxi_us ewz_us ewj_us ewy_us ewt_us
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


class UnmappableHolding(ValueError):
    """Редът от кошницата няма Yahoo символ по правило (борса или форма на тикера)."""


def _hk(t: str) -> str:
    # HKEX: цифров код; Yahoo го пише с поне 4 цифри (700 -> 0700.HK, 9988 -> 9988.HK).
    if not re.fullmatch(r"\d{1,5}", t):
        raise UnmappableHolding(f"HKEX тикер {t!r} не е цифров код от 1-5 цифри")
    return t.zfill(4) + ".HK"


def _b3(t: str) -> str:
    # B3: 4 букви/цифри + клас (3 ON, 4-8 PN, 11 unit). Обикновените и преференциалните
    # са ОТДЕЛНИ серии: PETR3.SA и PETR4.SA не се сливат.
    if not re.fullmatch(r"[A-Z0-9]{4}\d{1,2}", t):
        raise UnmappableHolding(f"B3 тикер {t!r} не е <4 знака><клас>")
    return t + ".SA"


def _us(t: str) -> str:
    # US листинг: без суфикс. Клас с точка (BRK.B) става тире (BRK-B), както в каталога.
    if not re.fullmatch(r"[A-Z]{1,5}([.\-][A-Z])?", t):
        raise UnmappableHolding(f"US тикер {t!r} не е 1-5 букви (+ клас)")
    return t.replace(".", "-")


def _tse(t: str) -> str:
    # TSE: 4-знаков код (цифри; новите кодове имат буква, 285A).
    if not re.fullmatch(r"\d{3}[0-9A-Z]", t):
        raise UnmappableHolding(f"TSE тикер {t!r} не е 4-знаков код")
    return t + ".T"


def _krx(suffix: str):
    def rule(t: str) -> str:
        # KRX: 6-знаков код с цифров корен; holdings пише 005930, Koyfin A005930.
        # Новите KRX кодове имат буква (0126Z0): Yahoo `0126Z0.KS` има серия и затварянето
        # на 11.09.2026 (340 000) == Last Price на Koyfin (ЦАБ2, 17.09.2026).
        if not re.fullmatch(r"\d{4}[0-9A-Z]{2}", t):
            raise UnmappableHolding(f"KRX тикер {t!r} не е 6-знаков код с цифров корен")
        return t + suffix
    return rule


def _tw(suffix: str):
    def rule(t: str) -> str:
        # TWSE / TPEx: 4-6 знака, цифров корен.
        if not re.fullmatch(r"\d{4}[0-9A-Z]{0,2}", t):
            raise UnmappableHolding(f"тайвански тикер {t!r} не е 4-6-знаков код")
        return t + suffix
    return rule


#: Борсата, както я пише iShares в holdings -> правилото. Ред тук е РЕШЕНИЕ за борса;
#: тикер никога не се вписва на ръка.
EXCHANGE_RULE = {
    "Hong Kong Exchanges And Clearing Ltd": _hk,
    "XBSP": _b3,
    "NYSE": _us,
    "NASDAQ": _us,
    "Tokyo Stock Exchange": _tse,
    "Korea Exchange (Stock Market)": _krx(".KS"),
    "Korea Exchange (Kosdaq)": _krx(".KQ"),
    "Taiwan Stock Exchange": _tw(".TW"),
    "Gretai Securities Market": _tw(".TWO"),
}


def yahoo_symbol(ticker, exchange) -> str:
    """(голи тикер, борса от holdings) -> Yahoo символ, или `UnmappableHolding` с името."""
    rule = EXCHANGE_RULE.get(exchange)
    if rule is None:
        raise UnmappableHolding(f"непозната борса {exchange!r} (тикер {ticker!r}): "
                                f"няма правило, суфикс не се гади")
    t = str(ticker or "").strip().upper()
    if not t:
        raise UnmappableHolding(f"празен тикер на борса {exchange!r}")
    return rule(t)


def series_id(symbol: str) -> str:
    """ERA.PA -> px_era_pa_daily; 0700.HK -> px_0700_hk_daily (конвенцията на каталога)."""
    return "px_" + re.sub(r"[.\-]", "_", symbol.lower()) + "_daily"


def last_snapshot(holdings_root, etf: str) -> dict:
    d = Path(holdings_root) / etf
    snaps = sorted(d.glob("*.jsonl"))
    if not snaps:
        raise SystemExit(f"няма снимка за {etf} в {d}")
    lines = [ln for ln in snaps[-1].read_text(encoding="utf-8").splitlines() if ln.strip()]
    return json.loads(lines[-1])


#: Назованите откази: (кошница, тикер, име) -> причина. Само точно този ред се прескача;
#: друг отказ (или същият тикер с друго име) спира генератора. Решение, не таблица на символи.
KNOWN_REFUSALS = {
    ("ewy_us", "-", "ECOPRO BM CO LTD"):
        "ред без тикер (0,01%); същата компания е 247540 ECOPRO BM LTD (0,37%), която има "
        "серия 247540.KQ (ЦАБ2, 17.09.2026)",
}


def rows_for(holdings_root, etf: str) -> list[tuple[str, dict]]:
    """[(series_id, config ред)] за Equity редовете на последната снимка. Отказ = стоп,
    освен точно назованите в KNOWN_REFUSALS."""
    snap = last_snapshot(holdings_root, etf)
    out = []
    for h in snap.get("holdings") or []:
        if h.get("asset_class") != "Equity":
            continue
        if (etf, h.get("ticker"), h.get("name")) in KNOWN_REFUSALS:
            continue
        sym = yahoo_symbol(h.get("ticker"), h.get("exchange"))
        cur = h.get("currency")
        out.append((series_id(sym), {
            "symbol": sym, "name": h.get("name"), "category": h.get("sector"),
            "currency": cur, "quote_basis": cur, "family": "stock",
            "origin": "ishares-basket", "basket": etf,
        }))
    return out


def _yaml_row(sid: str, m: dict) -> str:
    keys = ("symbol", "name", "category", "currency", "quote_basis", "family", "origin", "basket")
    body = ", ".join(f"{k}: {json.dumps(m[k], ensure_ascii=False)}" for k in keys)
    return f"  {sid}: {{{body}}}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ЦАБ1: редове в config.yaml от кошници *_us")
    ap.add_argument("--holdings", required=True, help="data-core/migrations/m_conc/holdings")
    ap.add_argument("--check", action="store_true",
                    help="сверка: всеки член на кошницата е ред в config.yaml, 1:1")
    ap.add_argument("etfs", nargs="+")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    if args.check:
        import yaml
        cfg = yaml.safe_load((Path(__file__).resolve().parent / "config.yaml")
                             .read_text(encoding="utf-8"))["price"]
        bad = 0
        for etf in args.etfs:
            rows = rows_for(args.holdings, etf)
            for sid, m in rows:
                have = cfg.get(sid)
                if have is None or any(have.get(k) != v for k, v in m.items()):
                    bad += 1
                    print(f"  FAIL {etf} {sid}: config {have!r} != правило {m!r}")
            print(f"{etf}: {len(rows)} члена, {len(rows) - sum(1 for s, _ in rows if s not in cfg)} в config")
        print("G1 GREEN" if bad == 0 else f"G1 RED ({bad})")
        return 0 if bad == 0 else 1

    seen = set()
    for etf in args.etfs:
        for sid, m in rows_for(args.holdings, etf):
            if sid in seen:
                continue
            seen.add(sid)
            print(_yaml_row(sid, m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

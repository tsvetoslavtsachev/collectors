"""ЗЛТ5а гейтове Г1-Г2 -- живи сверки (мрежа + вече написан временен канон).
Г3 (SDMX подредбата, разбъркан ред) е офлайн pytest мутация -- виж
tests/test_fetch_imf.py, не тук (mock отговор, без мрежа, без data-core).

Run (сверява срещу вече написан временен канон от collectors.cbgold.run):
    DATACORE_ROOT=<temp root> \\
    PYTHONPATH=C:\\Projects\\collectors python -m collectors.cbgold.verify

Г1 наличност: петте страни от брифа (USA/CHN/POL/TUR/IND) -- последният
период не по-стар от ~100 дни (3 месеца + запас за лага), първата дата
<= ЗЛТ3 baseline (Полша 2000-04, Турция 2000-08, САЩ 2000-10, Индия 2007-10,
Китай 2015-06).
Г2 САЩ срещу Treasury: сума на fine_troy_ounce_qty (fiscaldata.treasury.gov
v2/accounting/od/gold_reserve, всички facility/location редове) за последния
ОБЩ месец срещу суровите унции на МВФ за САЩ същия месец, разлика <= 0.01%.
Мутацията (умножи по 10^6 -- капанът със SCALE) е офлайн, в
tests/test_verify_gates.py, не тук.
Г4 Бразилия срещу BCB (29.09.2026, след x1000 от МВФ за 2026-03..08): SGS
серия 3553 "Reservas internacionais - Ouro (volume)", хиляди тройунции,
api.bcb.gov.br без ключ -- източникът на самия репортер, не копие от МВФ.
Сверява ПОПРАВЕНИТЕ от plausibility.repair() унции на МВФ за всеки общ
месец от последните 12, разлика <= 0.1% (BCB закръгля до 1000 oz = ~0.02%
при ~5.5 млн oz). Офлайн мутацията (x1000 не минава) -- tests/test_verify_gates.py.
"""
from __future__ import annotations
import datetime as dt
import json
import os
from pathlib import Path

import requests

from . import fetch_imf

TREASURY_URL = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
                "v2/accounting/od/gold_reserve")
BCB_GOLD_OZ_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.3553/dados"
BCB_TOL = 0.001

# ЗЛТ3 baseline first-period dates (month-end convention)
REQUIRED = {
    "USA": "2000-10-31",
    "CHN": "2015-06-30",
    "POL": "2000-04-30",
    "TUR": "2000-08-31",
    "IND": "2007-10-31",
}


def _load_canonical(canon_dir: Path, series_id: str) -> dict:
    p = canon_dir / f"{series_id}.json"
    return {r["as_of"]: r["value"] for r in json.loads(p.read_text(encoding="utf-8"))}


def gate1_availability(canon_dir: Path, today: dt.date | None = None) -> dict:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    out = {}
    for code, baseline_first in REQUIRED.items():
        sid = f"cb_gold_{code.lower()}_tonnes"
        d = _load_canonical(canon_dir, sid)
        dates = sorted(d)
        last = dt.date.fromisoformat(dates[-1])
        age_days = (today - last).days
        out[code] = {"rows": len(dates), "first": dates[0], "last": dates[-1],
                     "age_days": age_days,
                     "first_ok": dates[0] <= baseline_first,
                     "fresh_ok": age_days <= 100}
    return out


def _treasury_latest_date(url: str = TREASURY_URL) -> str:
    r = requests.get(url, timeout=60, headers={"Accept": "application/json"},
                     params={"sort": "-record_date", "page[size]": 1,
                            "fields": "record_date"})
    r.raise_for_status()
    return r.json()["data"][0]["record_date"]


def _treasury_ounces_for_date(record_date: str, url: str = TREASURY_URL) -> float:
    r = requests.get(url, timeout=60, headers={"Accept": "application/json"},
                     params={"filter": f"record_date:eq:{record_date}",
                            "page[size]": 20})
    r.raise_for_status()
    rows = r.json()["data"]
    return sum(float(row["fine_troy_ounce_qty"]) for row in rows)


def gate2_us_vs_treasury(usa_pairs: dict) -> dict:
    """usa_pairs: {as_of: oz} for USA, from fetch_imf.parse(...)["USA"]."""
    imf_latest = max(usa_pairs)
    treas_latest = _treasury_latest_date()
    common = min(imf_latest, treas_latest)
    if common not in usa_pairs:
        common = max(d for d in usa_pairs if d <= common)
    treas_oz = _treasury_ounces_for_date(common)
    imf_oz = usa_pairs[common]
    rel = abs(imf_oz - treas_oz) / treas_oz
    return {"common_month": common, "imf_oz": imf_oz, "treasury_oz": treas_oz,
           "rel_diff": rel}


def _bcb_gold_oz(url: str = BCB_GOLD_OZ_URL, months: int = 12) -> dict:
    """{YYYY-MM: troy oz} from BCB SGS 3553 (thousand oz), last `months` rows."""
    r = requests.get(f"{url}/ultimos/{months}", timeout=60,
                     params={"formato": "json"})
    r.raise_for_status()
    out = {}
    for row in r.json():
        _, mm, yyyy = row["data"].split("/")          # "01/08/2026"
        out[f"{yyyy}-{mm}"] = float(row["valor"]) * 1000
    return out


def gate4_compare(imf_oz: dict, bcb_oz: dict, tol: float = BCB_TOL) -> dict:
    """imf_oz: {as_of: oz} (repaired), bcb_oz: {YYYY-MM: oz} -> per-month
    rel diffs over the common months + the verdict."""
    imf_m = {d[:7]: v for d, v in imf_oz.items()}
    common = sorted(set(imf_m) & set(bcb_oz))
    rel = {m: abs(imf_m[m] - bcb_oz[m]) / bcb_oz[m] for m in common}
    return {"months": common, "max_rel_diff": max(rel.values(), default=None),
            "ok": bool(rel) and max(rel.values()) <= tol}


def main() -> int:
    canon_dir = Path(os.environ["DATACORE_ROOT"]) / "data" / "canonical"

    print("== Г1 наличност (петте страни) ==")
    g1 = gate1_availability(canon_dir)
    for code, info in g1.items():
        ok = "OK" if info["first_ok"] and info["fresh_ok"] else "FAIL"
        print(f"  {code}: {info['rows']} rows, {info['first']} -> {info['last']} "
             f"(age {info['age_days']}d) [{ok}]")

    print("== Г2 САЩ срещу Treasury ==")
    payload = fetch_imf.fetch_json()
    usa_pairs = dict(fetch_imf.parse(payload)["USA"])
    g2 = gate2_us_vs_treasury(usa_pairs)
    print(f"  common_month={g2['common_month']} IMF={g2['imf_oz']:,.0f} oz "
         f"Treasury={g2['treasury_oz']:,.0f} oz rel_diff={g2['rel_diff']:.6%}")

    print("== Г4 Бразилия срещу BCB SGS 3553 (поправени унции) ==")
    from . import plausibility
    usd = fetch_imf.parse(fetch_imf.fetch_json(fetch_imf.URL_USD))
    fixed, _ = plausibility.repair(fetch_imf.parse(payload), usd)
    g4 = gate4_compare(dict(fixed["BRA"]), _bcb_gold_oz())
    print(f"  {len(g4['months'])} common months {g4['months'][0]}..{g4['months'][-1]} "
          f"max_rel_diff={g4['max_rel_diff']:.4%} [{'OK' if g4['ok'] else 'FAIL'}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

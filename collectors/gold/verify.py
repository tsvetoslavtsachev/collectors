"""ЗЛТ2 гейтове Г1-Г4 -- живи сверки (мрежа + реален/временен data-core), не CI unit
тестове (за тези виж tests/test_verify_gates.py -- офлайн, синтетични, само математиката).

Run (сверява срещу вече написан временен канон + реалния price-archive):
    DATACORE_ROOT=<temp root written by collectors.gold.run> \\
    PYTHONPATH=C:\\Projects\\collectors python -m collectors.gold.verify

Г1 наличност: първата/последната дата на петте серии.
Г2 еврото:    LBMA EUR срещу (LBMA USD / EURUSD) от price-archive, от 2003.
Г3 тоновете:  Total Ounces (SPDR XLSX) x LBMA USD срещу Total NAV (SPDR XLSX, само
             за сверката -- никога не влиза в канона, няма деклариран консуматор).
Г4 фондът:    корелация на седмичните % промени на LBMA USD срещу etf_gld.json.

Прагът за Г2-Г4 се мери и печата на ПЪРВИЯ пуск, преди каквато и да е поправка
(медиана + 99-и персентил на относителната разлика; корелация за Г4) -- виж брифа.
"""
from __future__ import annotations
import bisect
import glob
import json
import os
from pathlib import Path

from . import fetch_gld, fetch_gldm


def _load_canonical(canon_dir: Path, series_id: str) -> dict:
    p = canon_dir / f"{series_id}.json"
    return {r["as_of"]: r["value"] for r in json.loads(p.read_text(encoding="utf-8"))}


def gate1_availability(canon_dir: Path) -> dict:
    out = {}
    for sid in ("mkt_gold_usd", "mkt_gold_gbp", "mkt_gold_eur",
               "etf_gld_tonnes", "etf_gld_oz"):
        d = _load_canonical(canon_dir, sid)
        dates = sorted(d)
        out[sid] = {"rows": len(dates), "first": dates[0], "last": dates[-1]}
    return out


def _relative_error_stats(pairs: list[tuple]) -> dict:
    """pairs: [(rel, ...)], already computed. Returns median/p99/max -- median-first
    so the sort used for percentiles never corrupts the (rel, label) correspondence."""
    ordered = sorted(pairs, key=lambda x: x[0])
    n = len(ordered)
    return {
        "n": n,
        "median": ordered[n // 2][0],
        "p99": ordered[int(0.99 * (n - 1))][0],
        "max": ordered[-1],   # full tuple, so the caller can name the worst date
    }


def gate2_eur_cross(canon_dir: Path, eurusd_glob: str,
                    eur_sid: str = "mkt_gold_eur") -> dict:
    """LBMA EUR vs (LBMA USD / EURUSD), from 2003 (EURUSD price-archive coverage)."""
    usd = _load_canonical(canon_dir, "mkt_gold_usd")
    eur = _load_canonical(canon_dir, eur_sid)
    eurusd = {}
    for fp in glob.glob(eurusd_glob):
        for line in Path(fp).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            eurusd[row["as_of"]] = row["value"]
    dates = sorted(d for d in eur if d >= "2003-01-01" and d in usd and d in eurusd)
    pairs = []
    for d in dates:
        implied = usd[d] / eurusd[d]
        rel = abs(implied - eur[d]) / eur[d]
        pairs.append((rel, d, implied, eur[d]))
    return _relative_error_stats(pairs)


def gate3_tonnage_cross(canon_dir: Path, xlsx_bytes: bytes) -> dict:
    """Total Ounces (SPDR) x LBMA USD vs the trust's own Total NAV (SPDR)."""
    usd = _load_canonical(canon_dir, "mkt_gold_usd")
    df = fetch_gld.parse_archive(xlsx_bytes)
    pairs = []
    for _, row in df.iterrows():
        d = row["as_of"]
        if d not in usd:
            continue
        oz, nav = row["Total Ounces of Gold in the Trust"], row[fetch_gld.NAV_COLUMN]
        if not oz or not nav:
            continue
        implied_nav = oz * usd[d]
        rel = abs(implied_nav - nav) / nav
        pairs.append((rel, d, implied_nav, nav))
    return _relative_error_stats(pairs)


def gate_gldm_tonnage_cross(canon_dir: Path, xlsx_bytes: bytes) -> dict:
    """ЗЛТ5а Г4: Total Ounces (GLDM) x LBMA USD vs the trust's own Total NAV
    (GLDM) -- same shape as gate3_tonnage_cross, GLDM instead of GLD."""
    usd = _load_canonical(canon_dir, "mkt_gold_usd")
    df = fetch_gldm.parse_archive(xlsx_bytes)
    pairs = []
    for _, row in df.iterrows():
        d = row["as_of"]
        if d not in usd:
            continue
        oz, nav = row["Total Ounces of Gold in the Trust"], row[fetch_gldm.NAV_COLUMN]
        if not oz or not nav:
            continue
        implied_nav = oz * usd[d]
        rel = abs(implied_nav - nav) / nav
        pairs.append((rel, d, implied_nav, nav))
    return _relative_error_stats(pairs)


def gate5_silver_eur_cross(canon_dir: Path, eurusd_glob: str) -> dict:
    """ЗЛТ5а Г5: LBMA silver EUR vs (LBMA silver USD / EURUSD), from 2003
    (EURUSD price-archive coverage) -- same shape as gate2_eur_cross, silver
    instead of gold."""
    usd = _load_canonical(canon_dir, "mkt_silver_usd")
    eur = _load_canonical(canon_dir, "mkt_silver_eur")
    eurusd = {}
    for fp in glob.glob(eurusd_glob):
        for line in Path(fp).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            eurusd[row["as_of"]] = row["value"]
    dates = sorted(d for d in eur if d >= "2003-01-01" and d in usd and d in eurusd)
    pairs = []
    for d in dates:
        implied = usd[d] / eurusd[d]
        rel = abs(implied - eur[d]) / eur[d]
        pairs.append((rel, d, implied, eur[d]))
    return _relative_error_stats(pairs)


def gate6_gld_gldm_correlation(canon_dir: Path) -> dict:
    """ЗЛТ5а Г6 (само измерване, праг няма): корелация на седмичните %
    промени в тоновете GLD и GLDM за общия период -- информация за ЗЛТ4."""
    gld = _load_canonical(canon_dir, "etf_gld_tonnes")
    gldm = _load_canonical(canon_dir, "etf_gldm_tonnes")
    common = sorted(set(gld) & set(gldm))
    if len(common) < 10:
        return {"n_weeks": 0, "correlation": None}
    # sample weekly: every 5th trading day, like gate4's GLD-cadence sampling
    weekly = common[::5]
    gld_chg, gldm_chg = [], []
    for i in range(1, len(weekly)):
        d, d0 = weekly[i], weekly[i - 1]
        gld_chg.append((gld[d] - gld[d0]) / gld[d0])
        gldm_chg.append((gldm[d] - gldm[d0]) / gldm[d0])
    return {"n_weeks": len(gld_chg), "correlation": correlation(gld_chg, gldm_chg)}


def gate4_spot_vs_fund(canon_dir: Path, etf_gld_path: str) -> dict:
    """Correlation of weekly %-changes: LBMA USD (daily, sampled on-or-before each
    GLD date) vs the GLD fund price (etf_gld.json, weekly)."""
    usd = _load_canonical(canon_dir, "mkt_gold_usd")
    gld = json.loads(Path(etf_gld_path).read_text(encoding="utf-8"))
    gld_dates = [r["as_of"] for r in gld]
    gld_val = {r["as_of"]: r["value"] for r in gld}
    lbma_dates = sorted(usd)

    def on_or_before(d):
        i = bisect.bisect_right(lbma_dates, d) - 1
        return lbma_dates[i] if i >= 0 else None

    pairs = [(d, usd[ld]) for d in gld_dates if (ld := on_or_before(d))]
    gld_chg, lbma_chg = [], []
    for i in range(1, len(pairs)):
        d, lb = pairs[i]
        d0, lb0 = pairs[i - 1]
        gld_chg.append((gld_val[d] - gld_val[d0]) / gld_val[d0])
        lbma_chg.append((lb - lb0) / lb0)
    return {"n_weeks": len(gld_chg), "correlation": correlation(gld_chg, lbma_chg),
           "abs_diff": _relative_error_stats(
               [(abs(g - l), None) for g, l in zip(gld_chg, lbma_chg)])}


def correlation(a: list[float], b: list[float]) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / n
    sa = (sum((x - ma) ** 2 for x in a) / n) ** 0.5
    sb = (sum((y - mb) ** 2 for y in b) / n) ** 0.5
    return cov / (sa * sb)


def main() -> int:
    canon_dir = Path(os.environ["DATACORE_ROOT"]) / "data" / "canonical"
    eurusd_glob = str(Path(r"C:\Projects\price-archive\archive\px_eurusd_daily") / "*.jsonl")
    etf_gld_path = r"C:\Projects\data-core\data\canonical\etf_gld.json"
    xlsx_path = os.environ.get("GLD_XLSX_PATH")

    print("== Г1 наличност ==")
    for sid, info in gate1_availability(canon_dir).items():
        print(f"  {sid}: {info['rows']} rows, {info['first']} -> {info['last']}")

    print("== Г2 еврото ==")
    g2 = gate2_eur_cross(canon_dir, eurusd_glob)
    print(f"  n={g2['n']} median_rel={g2['median']:.5f} p99_rel={g2['p99']:.5f} "
         f"max_rel={g2['max'][0]:.5f} ({g2['max'][1]})")

    if xlsx_path:
        print("== Г3 тоновете ==")
        g3 = gate3_tonnage_cross(canon_dir, Path(xlsx_path).read_bytes())
        print(f"  n={g3['n']} median_rel={g3['median']:.5f} p99_rel={g3['p99']:.5f} "
             f"max_rel={g3['max'][0]:.5f} ({g3['max'][1]})")

    print("== Г4 спот срещу фонда ==")
    g4 = gate4_spot_vs_fund(canon_dir, etf_gld_path)
    print(f"  n_weeks={g4['n_weeks']} correlation={g4['correlation']:.4f} "
         f"median_abs_diff={g4['abs_diff']['median']:.5f} "
         f"p99_abs_diff={g4['abs_diff']['p99']:.5f}")

    # ЗЛТ5а (28.09.2026) -- gate numbering below is the SESSION brief's (Г4/Г5/Г6),
    # a separate namespace from ЗЛТ2's own Г1-Г4 above.
    gldm_xlsx_path = os.environ.get("GLDM_XLSX_PATH")
    if gldm_xlsx_path:
        print("== ЗЛТ5а Г4: GLDM тоновете срещу NAV ==")
        g4b = gate_gldm_tonnage_cross(canon_dir, Path(gldm_xlsx_path).read_bytes())
        print(f"  n={g4b['n']} median_rel={g4b['median']:.5f} p99_rel={g4b['p99']:.5f} "
             f"max_rel={g4b['max'][0]:.5f} ({g4b['max'][1]})")

    print("== ЗЛТ5а Г5: среброто еврото ==")
    g5 = gate5_silver_eur_cross(canon_dir, eurusd_glob)
    print(f"  n={g5['n']} median_rel={g5['median']:.5f} p99_rel={g5['p99']:.5f} "
         f"max_rel={g5['max'][0]:.5f} ({g5['max'][1]})")

    print("== ЗЛТ5а Г6: GLD-GLDM корелация (само измерване) ==")
    g6 = gate6_gld_gldm_correlation(canon_dir)
    print(f"  n_weeks={g6['n_weeks']} correlation={g6['correlation']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

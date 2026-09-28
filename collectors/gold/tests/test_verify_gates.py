"""Офлайн тестове за математиката на verify.py (корелация, персентили) -- синтетични
данни, без мрежа, без data-core. Живите Г1-Г4 числа (реални LBMA/SPDR/price-archive)
се пускат ръчно с collectors.gold.verify (виж неговия docstring) и влизат в доклада.

Run: python -m pytest collectors/gold/tests/test_verify_gates.py
"""
from collectors.gold import verify as v


def test_relative_error_stats_percentiles():
    # 100 стойности 0.01, плюс една отскочила 0.50 -- p99 трябва да я хване, median не
    pairs = [(0.01, f"d{i}") for i in range(99)] + [(0.50, "outlier")]
    stats = v._relative_error_stats(pairs)
    assert stats["n"] == 100
    assert stats["median"] == 0.01
    assert stats["max"][0] == 0.50
    assert stats["max"][1] == "outlier"


def test_relative_error_stats_does_not_corrupt_correspondence():
    # регресия за бъга от разузнаването: sort-ване на списъка НА МЯСТО преди zip
    # разбърква съответствието стойност<->дата; тук данните НЕ са предварително
    # сортирани, за да го хване
    pairs = [(0.30, "worst"), (0.01, "a"), (0.02, "b"), (0.90, "very_worst"), (0.015, "c")]
    stats = v._relative_error_stats(pairs)
    assert stats["max"] == (0.90, "very_worst")


def test_correlation_perfect_positive():
    a = [0.01, -0.02, 0.03, 0.04, -0.01]
    assert v.correlation(a, a) == 1.0


def test_correlation_perfect_negative():
    a = [0.01, -0.02, 0.03, 0.04, -0.01]
    b = [-x for x in a]
    assert v.correlation(a, b) == -1.0


def test_gate5_silver_eur_cross_matches_and_mutation_swap_breaks_it(tmp_path):
    import json
    canon_dir = tmp_path / "canon"
    canon_dir.mkdir()
    archive_dir = tmp_path / "eurusd"
    archive_dir.mkdir()

    dates = [f"2020-01-0{i}" for i in range(2, 6)]
    usd_vals = {d: 18.0 + i for i, d in enumerate(dates)}
    eurusd_vals = {d: 1.10 for d in dates}
    eur_vals = {d: usd_vals[d] / eurusd_vals[d] for d in dates}   # exact identity
    gbp_vals = {d: usd_vals[d] * 0.79 for d in dates}             # decoy, wrong ccy

    (canon_dir / "mkt_silver_usd.json").write_text(json.dumps(
        [{"as_of": d, "value": v} for d, v in usd_vals.items()]), encoding="utf-8")
    (canon_dir / "mkt_silver_eur.json").write_text(json.dumps(
        [{"as_of": d, "value": v} for d, v in eur_vals.items()]), encoding="utf-8")
    (archive_dir / "2020.jsonl").write_text("\n".join(
        json.dumps({"as_of": d, "value": v}) for d, v in eurusd_vals.items()),
        encoding="utf-8")

    g5 = v.gate5_silver_eur_cross(canon_dir, str(archive_dir / "*.jsonl"))
    assert g5["median"] < 1e-9   # exact identity -> ~0

    # ФАЛШИФИКАТОР: swap EUR canon for the GBP decoy (wrong currency) -> gate breaks
    (canon_dir / "mkt_silver_eur.json").write_text(json.dumps(
        [{"as_of": d, "value": v} for d, v in gbp_vals.items()]), encoding="utf-8")
    g5_mutated = v.gate5_silver_eur_cross(canon_dir, str(archive_dir / "*.jsonl"))
    assert g5_mutated["median"] > 0.1   # gate fails hard


def test_gate_gldm_tonnage_cross_matches_and_mutation_wrong_price_breaks_it(tmp_path):
    import io
    import json
    import openpyxl
    from collectors.gold import fetch_gldm as fgm

    canon_dir = tmp_path / "canon"
    canon_dir.mkdir()

    usd_price = {"2018-06-26": 1250.0, "2018-06-27": 1255.0}
    (canon_dir / "mkt_gold_usd.json").write_text(json.dumps(
        [{"as_of": d, "value": p} for d, p in usd_price.items()]), encoding="utf-8")

    header = ["Date", "Closing Price", "Ounces of Gold per Share",
             "NAV/Share at 10:30am NYT", "Indicative Price per Share at 4:15pm NYT",
             "Mid point of bid/ask spread at 4:15pm NYT",
             "Premium/Discount of GLDM Mid Point vs Indicative Value of GLDM at 4:15pm NYT",
             "Daily Share Volume", "Total Ounces of Gold in the Trust",
             "Tonnes of Gold", "Total Net Asset Value in the Trust"]
    oz = {"26-Jun-2018": 20000.0, "27-Jun-2018": 20000.0}
    # NAV built EXACTLY as oz * usd_price -> gate should read ~0 median
    rows = [[d, 1.0, 0.01, 1.0, 1.0, 1.0, 0.0, 1000, oz[d],
            oz[d] / 32150.7465, oz[d] * usd_price[d.replace("26-Jun-2018", "2018-06-26")
                                                  .replace("27-Jun-2018", "2018-06-27")]]
            for d in oz]

    def _xlsx():
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        ws = wb.create_sheet(fgm.SHEET)
        ws.append(header)
        for r in rows:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    g4 = v.gate_gldm_tonnage_cross(canon_dir, _xlsx())
    assert g4["median"] < 1e-9

    # ФАЛШИФИКАТОР: NAV built from a wrong (halved) price -> gate breaks
    rows_mutated = [[d, 1.0, 0.01, 1.0, 1.0, 1.0, 0.0, 1000, oz[d],
                     oz[d] / 32150.7465, oz[d] * 0.5]
                    for d in oz]

    def _xlsx_mutated():
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        ws = wb.create_sheet(fgm.SHEET)
        ws.append(header)
        for r in rows_mutated:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    g4_mutated = v.gate_gldm_tonnage_cross(canon_dir, _xlsx_mutated())
    assert g4_mutated["median"] > 0.1


def test_mutation_shuffled_series_collapses_correlation():
    # ФАЛШИФИКАТОР на Г4: същите числа, разбъркан ред (все едно датите не съвпадат)
    # -> корелацията пада драстично спрямо подредения ред
    import random
    a = [((-1) ** i) * (i % 7 + 1) * 0.001 for i in range(200)]
    b = list(a)
    aligned = v.correlation(a, b)
    random.seed(7)
    shuffled = list(b)
    random.shuffle(shuffled)
    misaligned = v.correlation(a, shuffled)
    assert abs(aligned - 1.0) < 1e-9
    assert abs(misaligned) < 0.3

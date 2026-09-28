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

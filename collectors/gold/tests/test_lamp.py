"""ЗЛТ8 червената лампа: every active gold series aged against its own limit."""
import datetime as dt

from collectors.gold import run as gold_run
from collectors.gold.run import GROUPS, LAMP_DAYS, lamp

TODAY = dt.date(2026, 11, 23)                     # a Monday


def _store(ages: dict, default: int = 0):
    """series_id -> age in days (None = series missing)."""
    def read(sid):
        age = ages.get(sid, default)
        if age is None:
            return []
        return [{"as_of": "2010-01-04", "value": 1.0},
                {"as_of": (TODAY - dt.timedelta(days=age)).isoformat(), "value": 1.0}]
    return read


def _stale(lines):
    return [ln.split()[1].rstrip(":") for ln in lines if ln.startswith("STALE")]


def test_every_series_is_checked():
    _, lines = lamp(TODAY, _store({}))
    assert len(lines) == sum(len(v) for v in GROUPS.values()) == 8


def test_all_fresh_is_green():
    code, lines = lamp(TODAY, _store({}))
    assert code == 0 and not _stale(lines)


def test_limit_is_inclusive_one_day_past_is_red():
    # mutation proof: the same series flips exactly at limit + 1, for every group
    for name, sids in GROUPS.items():
        sid, lim = sids[0], LAMP_DAYS[name]
        assert lamp(TODAY, _store({sid: lim}))[0] == 0, name
        code, lines = lamp(TODAY, _store({sid: lim + 1}))
        assert code == 1 and _stale(lines) == [sid], name


def test_dead_spot_turns_red_after_two_missed_nights_only():
    assert lamp(TODAY, _store({"mkt_gold_spot_usd": 3}))[0] == 0
    code, lines = lamp(TODAY, _store({"mkt_gold_spot_usd": 4}))
    assert code == 1 and _stale(lines) == ["mkt_gold_spot_usd"]


def test_one_dead_spdr_column_is_caught_even_if_the_rest_move():
    code, lines = lamp(TODAY, _store({"etf_gldm_oz": 9}))
    assert code == 1 and _stale(lines) == ["etf_gldm_oz"]


def test_missing_series_is_red():
    code, lines = lamp(TODAY, _store({"mkt_term_premium_acm_10y": None}))
    assert code == 1 and _stale(lines) == ["mkt_term_premium_acm_10y"]


def test_sunday_spot_stamped_monday_is_fine():
    assert lamp(TODAY, _store({"mkt_gold_spot_usd": -1}))[0] == 0


def test_christmas_cluster_stays_green():
    xmas = dt.date(2026, 12, 29)                  # Tue; GLD/fix newest Thu 24.12
    def read(sid):
        d = "2026-12-24" if sid in GROUPS["SPDR"] else "2026-12-28"
        return [{"as_of": d, "value": 1.0}]
    assert lamp(xmas, read)[0] == 0


def test_main_lamp_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(gold_run.sys, "argv", ["run", "--lamp"])
    monkeypatch.setattr(gold_run.storage, "read_canonical",
                        _store({"mkt_gold_fut_usd": 30}))
    assert gold_run.main() == 1
    out = capsys.readouterr().out
    assert "STALE mkt_gold_fut_usd" in out and out.rstrip().endswith(
        "FAIL: a gold series is past its limit")

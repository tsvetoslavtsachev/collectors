"""ЗЛТ8 — дневният пазач: cot и gold пишат вечер само ако източникът е напред.

Изолация: хранилището и мрежата са подменени, реалният data-core не се докосва.
Run: python -m pytest collectors/gold/tests/test_fresh_guard.py
"""
import pytest

from collectors import fresh_guard as fg
from collectors.cot import run as cot_run
from collectors.gold import run as gold_run


def _store(last_by_sid):
    return lambda sid: [{"as_of": "2020-01-01"}, {"as_of": last_by_sid.get(sid, "2020-01-01")}]


# --- общото решение ---------------------------------------------------------------

def test_decide_writes_only_when_source_is_ahead():
    assert fg.decide("2026-11-20", "2026-11-19", "x")[0] is True
    assert fg.decide("2026-11-19", "2026-11-19", "x")[0] is False
    assert fg.decide("2026-11-18", "2026-11-19", "x")[0] is False   # регрес не пише
    assert fg.decide(None, "2026-11-19", "x")[0] is False           # източникът мълчи
    assert fg.decide("2026-11-19", None, "x")[0] is True            # празен канон


def test_canonical_last_is_the_newest_across_series_and_survives_missing():
    def read(sid):
        if sid == "gone":
            raise FileNotFoundError(sid)
        return {"a": [{"as_of": "2026-11-12"}], "b": [{"as_of": "2026-11-19T00:00"}]}[sid]
    assert fg.canonical_last(["a", "b", "gone"], read) == "2026-11-19"


# --- gold -------------------------------------------------------------------------

def _raw(last, ok=True):
    return {sid: {"ok": ok, "records": [{"as_of": "2026-11-11"}, {"as_of": last}]}
            for sid in gold_run.ACTIVE}


def test_gold_skips_when_spdr_has_nothing_newer():
    go, msg = gold_run.if_newer(_raw("2026-11-19"), read=_store(
        {sid: "2026-11-19" for sid in gold_run.ACTIVE}))
    assert go is False and "nothing newer" in msg


def test_gold_writes_when_spdr_added_friday():
    go, _ = gold_run.if_newer(_raw("2026-11-20"), read=_store(
        {sid: "2026-11-19" for sid in gold_run.ACTIVE}))
    assert go is True


def test_gold_source_down_is_no_write_not_a_crash():
    go, msg = gold_run.if_newer(_raw("2026-11-20", ok=False), read=_store({}))
    assert go is False and "unknown" in msg


def test_gold_main_daily_path_never_pushes_on_a_quiet_day(monkeypatch):
    monkeypatch.setattr(gold_run, "assemble", lambda: _raw("2026-11-19"))
    monkeypatch.setattr(gold_run.storage, "read_canonical",
                        _store({sid: "2026-11-19" for sid in gold_run.ACTIVE}))
    monkeypatch.setattr(gold_run.to_datacore, "push",
                        lambda raw: pytest.fail("pushed on a quiet day"))
    monkeypatch.setattr("sys.argv", ["run", "--if-newer"])
    assert gold_run.main() == 0


# --- cot --------------------------------------------------------------------------

class _Resp:
    def __init__(self, d, fail=False):
        self.d, self.fail = d, fail

    def raise_for_status(self):
        if self.fail:
            raise OSError("503")

    def json(self):
        return [{"d": self.d}]


CFG = {"endpoints": {"tff": "https://t", "disaggregated": "https://d"},
       "fetch": {"timeout": 5, "user_agent": "t"}}


def test_cot_source_last_takes_the_newest_dataset_and_tolerates_one_down():
    dates = {"https://t": _Resp("2026-11-17T00:00:00.000"), "https://d": _Resp("", fail=True)}
    assert cot_run.source_last(CFG, get=lambda u, **k: dates[u]) == "2026-11-17"


def test_cot_delayed_report_is_picked_up_monday_evening():
    # 10.11 качен в пн 16.11 20:30 UTC: каноничният е 03.11 -> пише
    cot_sids = [m["canonical"] for m in cot_run.markets.migrated()]
    go, _ = cot_run.if_newer(CFG, get=lambda u, **k: _Resp("2026-11-10T00:00:00.000"),
                             read=_store({s: "2026-11-03" for s in cot_sids}))
    assert go is True


def test_cot_quiet_evening_does_not_fetch():
    cot_sids = [m["canonical"] for m in cot_run.markets.migrated()]
    go, msg = cot_run.if_newer(CFG, get=lambda u, **k: _Resp("2026-11-17T00:00:00.000"),
                               read=_store({s: "2026-11-17" for s in cot_sids}))
    assert go is False and "nothing newer" in msg


def test_mutation_guard_off_would_push_every_night(monkeypatch):
    # ФАЛШИФИКАТОР: ако decide винаги казва „пиши“, тихата вечер стига до push.
    # Ако това някога не стигне до push, тестът за тихия ден горе не различава пазача.
    monkeypatch.setattr(fg, "decide", lambda s, c, n: (True, "forced"))
    hits = []
    monkeypatch.setattr(gold_run, "assemble", lambda: _raw("2026-11-19"))
    monkeypatch.setattr(gold_run.storage, "read_canonical", _store({}))
    monkeypatch.setattr(gold_run.to_datacore, "push", lambda raw: hits.append(1) or [])
    monkeypatch.setattr("sys.argv", ["run", "--if-newer"])
    gold_run.main()
    assert hits == [1]

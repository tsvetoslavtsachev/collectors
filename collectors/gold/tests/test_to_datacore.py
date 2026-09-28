"""Гейт: пазачът на записа в collectors.gold.to_datacore -- всеки гейт е full-history
(няма rolling_window тук), тоест подът се мери БЕЗУСЛОВНО върху цялата съществуваща
серия при всеки пуск (за разлика от vrm, където само обявените серии биха минали
без праг). Изолация: push() върху памет (storage.read_canonical/datacore.write
подменени) -- реалният data-core не се докосва.

Run: python -m pytest collectors/gold/tests/test_to_datacore.py
"""
import pytest

from collectors.gold import to_datacore as td

SID = "mkt_gold_usd"


def _rec(as_of, value):
    return {"as_of": as_of, "value": value, "source": "LBMA PM"}


def _rows(dates, base=2000.0):
    return [_rec(d, round(base + i, 2)) for i, d in enumerate(dates)]


def _dates(n, start="2020-01-01"):
    import datetime as dt
    d0 = dt.date.fromisoformat(start)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range(n)]


class Store:
    def __init__(self, records, sid=SID):
        self.records = list(records)
        self.sid = sid
        self.writes = []

    def read(self, series_id):
        return list(self.records) if series_id == self.sid else []

    def write(self, series_id, records, schema_version):
        stamped = [{**r, "series_id": series_id, "schema_version": schema_version}
                  for r in records]
        self.records = stamped
        res = {"series_id": series_id, "rows": len(stamped),
              "as_of": max(r["as_of"] for r in stamped), "source": "LBMA"}
        self.writes.append(res)
        return res


@pytest.fixture
def store(monkeypatch):
    def install(records, sid=SID):
        s = Store(records, sid)
        monkeypatch.setattr(td.storage, "read_canonical", s.read)
        monkeypatch.setattr(td.datacore, "write", s.write)
        return s
    return install


def _run(records, sid=SID):
    return td.push({sid: {"ok": True, "records": records}})


def test_new_series_is_written_whole(store):
    canon = store([])
    dates = _dates(200)
    res = _run(_rows(dates))[0]
    assert res["rows"] == 200
    assert canon.records[0]["as_of"] == dates[0]


def test_healthy_refresh_extends_forward(store):
    dates = _dates(500)
    canon = store(_rows(dates[:-1]))          # canon missing the last day
    res = _run(_rows(dates))[0]                # source now has all 500
    assert res["rows"] == 500
    assert canon.records[-1]["as_of"] == dates[-1]


def test_g0_short_answer_is_refused_not_absorbed(store):
    """Г0: LBMA/SPDR отговарят с последния месец (~21 реда) вместо цялата история
    (~500 реда). Подът тук е БЕЗУСЛОВЕН (няма rolling_window изключение за злато) --
    отказ, каноничният файл непроменен."""
    dates = _dates(500)
    canon = store(_rows(dates))
    before = list(canon.records)
    res = _run(_rows(dates[-21:]))[0]
    assert "refused" in res["skipped"]
    assert res["skipped"] == f"refused: would truncate {len(dates)}->21 rows"
    assert canon.records == before
    assert canon.writes == []


def test_answer_older_than_the_canon_is_not_extended_backward(store):
    dates = _dates(300)
    canon = store(_rows(dates[100:]))          # canon starts at dates[100]
    _run(_rows(dates))                          # source offers the whole thing
    assert canon.records[0]["as_of"] == dates[100]


def test_tail_regression_and_gap_are_warned_not_refused(store):
    dates = _dates(300)
    canon = store(_rows(dates))
    kept = _rows(dates)[:-5]                    # 5 fewer than existing tail -- above floor
    del kept[100]                               # one interior hole
    res = _run(kept)[0]
    assert "skipped" not in res
    assert any(w.startswith("tail regressed") for w in res["warnings"])
    assert any("interior gap" in w for w in res["warnings"])


def test_mutation_without_the_floor_the_short_answer_lands(store, monkeypatch):
    # ФАЛШИФИКАТОР на Г0: подът е свален (списан 0 -> винаги минава) -> ако този
    # тест мине без реалния тест по-горе да пада заедно с него, гейтът не различава.
    monkeypatch.setattr(td, "MIN_RETAIN_RATIO", 0.0)
    dates = _dates(500)
    canon = store(_rows(dates))
    res = _run(_rows(dates[-21:]))[0]
    assert "skipped" not in res
    assert res["rows"] == 21
    assert canon.records[0]["as_of"] == dates[-21]

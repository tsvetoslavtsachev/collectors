"""Гейт: пазачът на записа в collectors.cbgold.to_datacore -- пълен-история
под, безусловен, идентичен по форма на collectors.gold.to_datacore (виж
неговия test_to_datacore.py за пълния разказ). Изолация: push() върху памет.

Run: python -m pytest collectors/cbgold/tests/test_to_datacore.py
"""
import pytest

from collectors.cbgold import to_datacore as td

SID = "cb_gold_usa_tonnes"


def _rec(as_of, value):
    return {"as_of": as_of, "value": value, "source": "IMF IRFCL"}


def _rows(dates, base=8000.0):
    return [_rec(d, round(base + i, 3)) for i, d in enumerate(dates)]


def _dates(n, start="2000-10-31"):
    # monthly month-end dates, approximated with 30-day steps (fine for a guard test)
    import datetime as dt
    d0 = dt.date.fromisoformat(start)
    return [(d0 + dt.timedelta(days=30 * i)).isoformat() for i in range(n)]


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
              "as_of": max(r["as_of"] for r in stamped), "source": "IMF"}
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
    dates = _dates(100)
    res = _run(_rows(dates))[0]
    assert res["rows"] == 100
    assert canon.records[0]["as_of"] == dates[0]


def test_healthy_refresh_extends_forward(store):
    dates = _dates(200)
    canon = store(_rows(dates[:-1]))
    res = _run(_rows(dates))[0]
    assert res["rows"] == 200
    assert canon.records[-1]["as_of"] == dates[-1]


def test_short_answer_is_refused_not_absorbed(store):
    dates = _dates(200)
    canon = store(_rows(dates))
    before = list(canon.records)
    res = _run(_rows(dates[-5:]))[0]
    assert "refused" in res["skipped"]
    assert canon.records == before
    assert canon.writes == []


def test_mutation_without_the_floor_the_short_answer_lands(store, monkeypatch):
    monkeypatch.setattr(td, "MIN_RETAIN_RATIO", 0.0)
    dates = _dates(200)
    canon = store(_rows(dates))
    res = _run(_rows(dates[-5:]))[0]
    assert "skipped" not in res
    assert res["rows"] == 5

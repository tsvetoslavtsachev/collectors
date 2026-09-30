"""ЗЛТ1 Г4 — пазачът на натрупването в to_datacore.push (head retention).

Болестта: ICE сериите във FRED (BAMLH0A0HYM2) се дават само за последните 3 години и
прозорецът се плъзга с ден за ден. push() презаписва целия файл, тоест канонът (който
пази повече от източника: 2023-09-26 срещу 2023-09-29) би губил главата си при всеки
пуск, а долният праг (0.9 от съществуващите редове) би спрял записа съвсем след около
година. Гейтът: първата дата на серия никога не се мести напред заради по-къс отговор.

Изолация: push() се тества върху паметно хранилище (storage.read_canonical, datacore.write
и ledger-ът са подменени) — реалният data-core не се докосва.

Run: python -m pytest collectors/vrm/tests/test_head_guard.py
"""
import datetime as dt

import pytest

from collectors.vrm import to_datacore as td

SID = "mkt_hy_oas"
SRC = "FRED BAMLH0A0HYM2"


def _bdays(first, last):
    d = dt.date.fromisoformat(first)
    end = dt.date.fromisoformat(last)
    out = []
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _rec(as_of, value):
    return {"as_of": as_of, "value": value, "source": SRC, "resolution": "daily"}


def _val(as_of):
    """Deterministic value per date, so 'the same row' is checkable byte for byte."""
    return round(3.0 + (int(as_of.replace("-", "")) % 97) / 100.0, 2)


def _rows(first, last, valfn=_val):
    return [_rec(d, valfn(d)) for d in _bdays(first, last)]


class Store:
    """In-memory canon: push() reads it and writes back the FULL file (overwrite)."""

    def __init__(self, records, sid=SID):
        self.records = list(records)
        self.writes = []
        self.sid = sid

    def read(self, series_id):
        return list(self.records) if series_id == self.sid else []

    def write(self, series_id, records, schema_version):
        stamped = [{**r, "series_id": series_id, "schema_version": schema_version}
                   for r in records]
        self.records = stamped
        res = {"series_id": series_id, "rows": len(stamped),
               "as_of": max(r["as_of"] for r in stamped), "source": "FRED"}
        self.writes.append(res)
        return res


@pytest.fixture
def store(monkeypatch):
    def install(records, sid=SID):
        s = Store(records, sid)
        monkeypatch.setattr(td, "assert_safe_root", lambda: None)
        monkeypatch.setattr(td.storage, "read_canonical", s.read)
        monkeypatch.setattr(td.datacore, "write", s.write)
        monkeypatch.setattr(td.price_guard, "write_ledger", lambda *a, **k: "ledger")
        return s
    return install


def _run(source_records):
    return td.push({SID: {"ok": True, "records": source_records}})


def test_three_year_answer_keeps_the_first_date(store):
    # каноничният ред: 2023-09-26 .. 2026-09-24; FRED днес: последните 3 години,
    # от 2023-09-29 до 2026-09-25
    canon = store(_rows("2023-09-26", "2026-09-24"))
    n_before = len(canon.records)
    res = _run(_rows("2023-09-29", "2026-09-25"))[0]
    assert canon.records[0]["as_of"] == "2023-09-26"          # Г4
    assert canon.records[-1]["as_of"] == "2026-09-25"         # напред пак се разширява
    assert res["retained_head"] == 3                          # 26, 27, 28 септември
    assert len(canon.records) == n_before + 1
    assert "warnings" not in res                              # главата не е „по-къса"


def test_carried_head_is_byte_identical(store):
    canon = store(_rows("2023-09-26", "2026-09-24"))
    head = [dict(r) for r in canon.records if r["as_of"] < "2023-09-29"]
    _run(_rows("2023-09-29", "2026-09-25"))
    kept = [r for r in canon.records if r["as_of"] < "2023-09-29"]
    assert [(r["as_of"], r["value"], r["source"]) for r in kept] == \
           [(r["as_of"], r["value"], r["source"]) for r in head]


def test_source_stays_the_truth_inside_its_window(store):
    canon = store(_rows("2023-09-26", "2026-09-24"))
    _run(_rows("2023-09-29", "2026-09-25", valfn=lambda d: 9.99))
    inside = [r for r in canon.records if r["as_of"] >= "2023-09-29"]
    assert inside and all(r["value"] == 9.99 for r in inside)
    outside = [r for r in canon.records if r["as_of"] < "2023-09-29"]
    assert all(r["value"] != 9.99 for r in outside)


def test_sliding_window_for_a_year_and_a_half_never_moves_the_head(store):
    # плъзгащият се прозорец: всяка събота източникът започва 7 дни по-късно. След
    # ~1 г. каноничните редове са с ~35% повече от отговора: старият под 0.9 отказва
    # записа (серията замръзва), а без пазача главата ерозира всяка седмица.
    canon = store(_rows("2023-09-26", "2026-09-24"))
    today = dt.date(2026, 9, 26)
    for _ in range(78):
        today += dt.timedelta(days=7)
        win_start = (today.replace(year=today.year - 3)).isoformat()
        last = (today - dt.timedelta(days=1)).isoformat()
        res = _run(_rows(win_start, last))[0]
        assert res.get("rows") is not None, res              # никога „refused"
        assert canon.records[0]["as_of"] == "2023-09-26"
    assert canon.records[-1]["as_of"] > "2028-01-01"
    # никакви дупки, никакви дубликати
    dates = [r["as_of"] for r in canon.records]
    assert dates == sorted(set(dates))


def test_full_history_source_carries_nothing(store):
    # източник, който дава цялата история (T10YIE от 2003), не дърпа нищо назад
    canon = store(_rows("2003-01-02", "2026-09-18"))
    res = _run(_rows("2003-01-02", "2026-09-25"))[0]
    assert "retained_head" not in res
    assert canon.records[0]["as_of"] == "2003-01-02"
    assert canon.records[-1]["as_of"] == "2026-09-25"


def test_new_series_is_written_whole(store):
    canon = store([])
    res = _run(_rows("2003-01-02", "2026-09-25"))[0]
    assert "retained_head" not in res
    assert canon.records[0]["as_of"] == "2003-01-02"
    assert res["rows"] == len(_bdays("2003-01-02", "2026-09-25"))


def test_broken_short_pull_is_still_refused(store):
    # 5 реда насред историята (като мок пуск върху реален канон): ръкоят на пазача
    # не бива да превръща катастрофалния къс отговор в тих запис
    canon = store(_rows("2023-09-26", "2026-09-24"))
    before = list(canon.records)
    res = _run(_rows("2026-06-08", "2026-06-12"))[0]
    assert "refused" in res["skipped"]
    assert canon.records == before


def test_half_of_the_overlap_is_still_refused(store):
    # отговор, който покрива своя период наполовина (дупки): това е повредата, която
    # праговете 0.9 хващат, и тя остава хваната и с пазача
    canon = store(_rows("2023-09-26", "2026-09-24"))
    before = list(canon.records)
    half = _rows("2023-09-29", "2026-09-25")[::2]
    res = _run(half)[0]
    assert "refused" in res["skipped"]
    assert canon.records == before


def test_answer_older_than_the_canon_is_not_extended_backward(store):
    # старото правило „само напред" остава: отговор с по-ранна дата не мести началото
    canon = store(_rows("2023-09-26", "2026-09-24"))
    _run(_rows("2020-01-02", "2026-09-25"))
    assert canon.records[0]["as_of"] == "2023-09-26"


def test_mutation_without_the_guard_the_first_date_moves(store, monkeypatch):
    # ФАЛШИФИКАТОР: махнат пазач -> същият сценарий мести първата дата напред.
    # Ако този тест някога мине, значи основният сценарий вече не различава пазача.
    monkeypatch.setattr(td, "_retain_head", lambda existing, records: (records, 0))
    canon = store(_rows("2023-09-26", "2026-09-24"))
    _run(_rows("2023-09-29", "2026-09-25"))
    assert canon.records[0]["as_of"] == "2023-09-29"


def test_lihvenite_serii_are_wired_in_config():
    import yaml
    from pathlib import Path
    from collectors.vrm import run as vrm_run
    cfg = yaml.safe_load((Path(vrm_run.__file__).parent / "config.yaml")
                         .read_text(encoding="utf-8"))
    want = {"mkt_ust_10y": "DGS10", "mkt_real_10y": "DFII10",
            "mkt_term_premium_10y": "THREEFYTP10"}
    for sid, tkr in want.items():
        m = cfg["fred"][sid]
        assert m["ticker"] == tkr
        assert m["transform"] == "level" and m["model_freq"] == "daily"
        assert not m.get("computed")
        assert sid in vrm_run.expected_series(cfg)
    assert len(vrm_run.expected_series(cfg)) == 61   # +4 УБР2, +1 УБР5б


# ── ЗЛТ2 Фаза 0б: пренасянето на главата е само за обявените подвижни прозорци ──────
FULL = "mkt_ust_10y"        # DGS10: източник с ПЪЛНА история от 1962, не е подвижен прозорец


def _run_full(source_records):
    return td.push({FULL: {"ok": True, "records": source_records}})


def test_rolling_window_is_declared_only_for_hy():
    # ключът е в config.yaml; днес само ICE серията в FRED е подвижен прозорец
    assert td.rolling_window_series() == {SID}
    assert FULL not in td.rolling_window_series()


def test_g0_full_history_series_answering_with_last_month_is_refused(store):
    # Г0: източник с пълна история внезапно връща само последния месец (23 реда).
    # Преди Фаза 0б подът се мереше върху припокриването (23), отказ нямаше и се
    # пренасяха ~6 000 реда тихо; сега подът е върху всички съществуващи редове.
    canon = store(_rows("2003-01-02", "2026-09-24"), sid=FULL)
    before = list(canon.records)
    month = _rows("2026-08-25", "2026-09-24")
    assert len(month) == 23
    res = _run_full(month)[0]
    assert "refused" in res["skipped"]
    assert res["skipped"].startswith(f"refused: would truncate {len(before)}->")
    assert canon.records == before              # каноничният файл е непроменен
    assert canon.writes == []                   # и не е имало опит за запис


def test_undeclared_series_never_carries_a_head(store):
    # малко по-къс отговор, който минава прага (губи ~40 от ~6 000 реда): главата НЕ
    # се пренася (не е обявен подвижен прозорец) -> старото поведение: пише се
    # отговорът и се вдига предупреждение „head shorter", видимо, не тихо
    canon = store(_rows("2003-01-02", "2026-09-24"), sid=FULL)
    res = _run_full(_rows("2003-03-03", "2026-09-25"))[0]
    assert "retained_head" not in res
    assert canon.records[0]["as_of"] == "2003-03-03"
    assert any(w.startswith("head shorter") for w in res["warnings"])


def test_declared_hy_still_carries_its_head(store):
    # пазачът на ЗЛТ1 за обявения прозорец остава същият (главата се пренася)
    canon = store(_rows("2023-09-26", "2026-09-24"))
    res = _run(_rows("2023-09-29", "2026-09-25"))[0]
    assert res["retained_head"] == 3
    assert canon.records[0]["as_of"] == "2023-09-26"


def test_mutation_without_the_key_check_the_short_answer_lands(store, monkeypatch):
    # ФАЛШИФИКАТОР на Г0: махната проверка на ключа (всяка серия минава като подвижен
    # прозорец) -> същият къс отговор се приема и се пренасят ~6 000 реда. Ако този
    # тест някога мине без Г0 да е паднал в същата мутация, тестът Г0 не различава пазача.
    monkeypatch.setattr(td, "rolling_window_series", lambda cfg=None: {FULL})
    canon = store(_rows("2003-01-02", "2026-09-24"), sid=FULL)
    res = _run_full(_rows("2026-08-25", "2026-09-24"))[0]
    assert "skipped" not in res and res["retained_head"] > 5000


"""ЗЛТ8 — спотът от Swissquote, фючърсът GC=F и срочната премия ACM.

Гейтовете от поръчката: пробен пуск пише нов ред; тих пуск не пише; тест с мутация
гърми, ако JSON-ът на Swissquote, JSON-ът на Yahoo или колоната ACMTP10 (листът
„ACM Daily“) сменят формата си. Мрежата е подменена; реалният data-core не се докосва.

Run:  python -m pytest collectors/gold/tests/test_zlt8_sources.py
Live: ZLT8_LIVE=1 python -m pytest collectors/gold/tests/test_zlt8_sources.py
"""
import copy
import datetime as dt
import os

import pandas as pd
import pytest

from collectors.gold import fetch_acm as fa, fetch_fut as ff, fetch_spot as fs
from collectors.gold import run as gold_run

UTC = dt.timezone.utc

# --- Swissquote ----------------------------------------------------------------------

SQ = [
    {"topo": {"platform": "AT", "server": "AT"},
     "spreadProfilePrices": [{"spreadProfile": "premium", "bid": 3533.0, "ask": 3535.0}],
     "ts": 1795212000070},
    {"topo": {"platform": "SwissquoteCapitalMarkets", "server": "Live7"},
     "spreadProfilePrices": [
         {"spreadProfile": "prime", "bid": 3533.956, "ask": 3534.874},
         {"spreadProfile": "premium", "bid": 3533.946, "ask": 3534.884}],
     "ts": 1795212000070},          # 2026-11-20 22:00:00 UTC = 17:00 ET, Friday close
]


def test_spot_parse_takes_scm_premium_mid():
    q = fs.parse(SQ)
    assert q == {"bid": 3533.946, "ask": 3534.884, "mid": 3534.415,
                 "quote_ts": "2026-11-20T22:00:00Z"}


@pytest.mark.parametrize("mutate", [
    lambda p: p.clear(),                                                  # empty list
    lambda p: [b.pop("spreadProfilePrices") for b in p],                  # key renamed
    lambda p: [x.update(spreadProfile="std") for b in p                   # profile gone
               for x in b["spreadProfilePrices"]],
    lambda p: [x.update(bid="n/a") for b in p for x in b["spreadProfilePrices"]],
    lambda p: [b.pop("ts") for b in p],
    lambda p: [x.update(bid=9000.0) for b in p for x in b["spreadProfilePrices"]],  # bid>ask
])
def test_mutation_swissquote_shape_change_raises(mutate):
    # ФАЛШИФИКАТОР: смени формата на JSON-а -> четецът гърми, не пише тихо грешно число
    p = copy.deepcopy(SQ)
    mutate(p)
    with pytest.raises(fs.FormatError):
        fs.parse(p)


def test_spot_parse_rejects_non_list():
    with pytest.raises(fs.FormatError):
        fs.parse({"error": "rate limited"})


@pytest.mark.parametrize("quote_ts,want", [
    ("2026-11-20T22:00:00Z", "2026-11-20"),   # Fri 17:00 EST close -> Friday
    ("2026-11-21T03:00:00Z", "2026-11-23"),   # Fri 22:00 EST (after close) -> Monday
    ("2026-11-23T02:00:00Z", "2026-11-23"),   # Sun 21:00 EST -> Monday session
    ("2026-11-24T02:30:00Z", "2026-11-24"),   # Mon 21:30 EST -> Tuesday session
    ("2026-11-24T15:00:00Z", "2026-11-24"),   # Tue 10:00 EST -> Tuesday
    ("2026-07-10T21:00:00Z", "2026-07-10"),   # summer: Fri 17:00 EDT -> Friday
])
def test_spot_trading_date(quote_ts, want):
    assert fs.trading_date(quote_ts) == want


def _row(as_of, quote_ts, value=3500.0):
    return {"as_of": as_of, "value": value, "source": fs.SOURCE, "bid": value - 0.5,
            "ask": value + 0.5, "quote_ts": quote_ts, "series_id": fs.SERIES,
            "schema_version": 1}


def test_spot_first_run_writes_one_row():
    out = fs.extend([], fs.parse(SQ))
    assert out["ok"] and [r["as_of"] for r in out["records"]] == ["2026-11-20"]
    assert out["records"][0]["value"] == 3534.415


def test_spot_friday_close_replaces_friday_early_quote_and_keeps_history():
    canon = [_row("2026-11-19", "2026-11-19T03:00:00Z"), _row("2026-11-20", "2026-11-20T03:00:00Z")]
    out = fs.extend(canon, fs.parse(SQ))["records"]
    assert [r["as_of"] for r in out] == ["2026-11-19", "2026-11-20"]
    assert out[-1]["quote_ts"] == "2026-11-20T22:00:00Z" and out[-1]["value"] == 3534.415
    assert out[0] == {k: v for k, v in canon[0].items()
                      if k not in ("series_id", "schema_version")}


def test_spot_same_quote_keeps_stored_row():
    canon = [_row("2026-11-20", "2026-11-20T22:00:00Z", 3534.415)]
    out = fs.extend(canon, fs.parse(SQ))
    assert out["records"][0]["bid"] == 3534.415 - 0.5 and "not newer" in out["note"]


# --- Yahoo GC=F ----------------------------------------------------------------------

def _ny_midnight(day):
    return int(dt.datetime.combine(dt.date.fromisoformat(day), dt.time(0), ff.NY).timestamp())


def _yahoo(days=("2026-11-18", "2026-11-19", "2026-11-20"), closes=(3600.1, None, 3515.4)):
    return {"chart": {"result": [{
        "meta": {"instrumentType": "FUTURE", "exchangeName": "CMX", "currency": "USD"},
        "timestamp": [_ny_midnight(d) for d in days],
        "indicators": {"quote": [{"close": list(closes)}]}}], "error": None}}


def test_fut_parse_completed_sessions_only():
    after_close = dt.datetime(2026, 11, 20, 23, 0, tzinfo=UTC)      # Fri 18:00 ET
    during = dt.datetime(2026, 11, 20, 15, 0, tzinfo=UTC)           # Fri 10:00 ET
    assert [(r["as_of"], r["value"]) for r in ff.parse(_yahoo(), after_close)] == [
        ("2026-11-18", 3600.1), ("2026-11-20", 3515.4)]           # None skipped
    assert [r["as_of"] for r in ff.parse(_yahoo(), during)] == ["2026-11-18"]


@pytest.mark.parametrize("mutate", [
    lambda p: p["chart"].update(result=[]),
    lambda p: p["chart"]["result"][0].pop("indicators"),
    lambda p: p["chart"]["result"][0]["meta"].update(instrumentType="EQUITY"),
    lambda p: p["chart"]["result"][0]["meta"].update(exchangeName="NYQ"),
    lambda p: p["chart"]["result"][0]["timestamp"].pop(),
    lambda p: p["chart"]["result"][0]["indicators"]["quote"][0].update(close=[1.0, 1.0, 1.0]),
])
def test_mutation_yahoo_shape_change_raises(mutate):
    p = _yahoo()
    mutate(p)
    with pytest.raises(ff.FormatError):
        ff.parse(p, dt.datetime(2026, 11, 21, tzinfo=UTC))


def test_fut_upsert_keeps_rows_older_than_the_window():
    canon = [{"as_of": "2016-11-18", "value": 1200.0, "source": ff.SOURCE,
              "series_id": ff.SERIES, "schema_version": 1},
             {"as_of": "2026-11-18", "value": 3599.0, "source": ff.SOURCE}]
    fresh = ff.parse(_yahoo(), dt.datetime(2026, 11, 21, tzinfo=UTC))
    out = ff.upsert(canon, fresh)
    assert [r["as_of"] for r in out["records"]] == ["2016-11-18", "2026-11-18", "2026-11-20"]
    assert out["records"][1]["value"] == 3600.1 and "+1 session(s)" in out["note"]
    assert "series_id" not in out["records"][0]


# --- NY Fed ACM ----------------------------------------------------------------------

def _acm_frame(dates=("16-Nov-2026", "17-Nov-2026", "18-Nov-2026", "19-Nov-2026")):
    return pd.DataFrame({"DATE": list(dates), "ACMY01": 1.0,
                         "ACMTP10": [0.68, 0.69, 0.6919023545, 0.6957351138]})


def test_acm_parse_frame(monkeypatch):
    monkeypatch.setattr(fa, "MIN_ROWS", 3)
    out = fa.parse_frame(_acm_frame())
    assert out[-1] == {"as_of": "2026-11-19", "value": 0.695735, "source": fa.SOURCE}
    assert [r["as_of"] for r in out] == sorted(r["as_of"] for r in out)


@pytest.mark.parametrize("mutate", [
    lambda df: df.rename(columns={"ACMTP10": "ACM_TP10"}),           # column renamed
    lambda df: df.rename(columns={"DATE": "Date"}),
    lambda df: df.assign(DATE=["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"]),
    lambda df: df.assign(ACMTP10=["0.68", "0.69", "n/a", "0.70"]),   # not numeric
    lambda df: df.assign(ACMTP10=[68.0, 69.0, 69.2, 69.6]),          # basis points now
    lambda df: df.iloc[:2],                                         # truncated file
])
def test_mutation_acm_format_change_raises(monkeypatch, mutate):
    monkeypatch.setattr(fa, "MIN_ROWS", 3)
    with pytest.raises(fa.FormatError):
        fa.parse_frame(mutate(_acm_frame()))


def test_acm_missing_sheet_raises(monkeypatch):
    def no_sheet(*a, **k):
        raise ValueError("Worksheet named 'ACM Daily' not found")
    monkeypatch.setattr(pd, "read_excel", no_sheet)
    with pytest.raises(fa.FormatError):
        fa.parse(b"xls")


# --- the daily guard, per source ----------------------------------------------------

def _store(rows_by_sid):
    return lambda sid: rows_by_sid.get(sid, [])


def test_daily_run_writes_only_the_source_that_moved(monkeypatch):
    spdr = {sid: {"ok": True, "records": [{"as_of": "2026-11-19"}]}
            for sid in gold_run.GROUPS["SPDR"]}
    raw = {**spdr,
           fs.SERIES: fs.extend([_row("2026-11-20", "2026-11-20T03:00:00Z")], fs.parse(SQ)),
           ff.SERIES: {"ok": True, "records": [{"as_of": "2026-11-20"}]},
           fa.SERIES: {"ok": True, "records": [{"as_of": "2026-11-19"}]}}
    canon = {**{sid: [{"as_of": "2026-11-19"}] for sid in gold_run.GROUPS["SPDR"]},
             fs.SERIES: [_row("2026-11-20", "2026-11-20T03:00:00Z")],
             ff.SERIES: [{"as_of": "2026-11-20"}],
             fa.SERIES: [{"as_of": "2026-11-19"}]}
    pushed = []
    monkeypatch.setattr(gold_run, "assemble", lambda: raw)
    monkeypatch.setattr(gold_run.storage, "read_canonical", _store(canon))
    monkeypatch.setattr(gold_run.to_datacore, "push",
                        lambda r: pushed.append(sorted(r)) or [])
    monkeypatch.setattr("sys.argv", ["run", "--if-newer"])
    gold_run.main()
    assert pushed == [[fs.SERIES]]          # Friday's close lands; nothing else rewritten


def test_quiet_weekend_run_writes_nothing(monkeypatch):
    close = _row("2026-11-20", "2026-11-20T22:00:00Z", 3534.415)
    raw = {fs.SERIES: fs.extend([close], fs.parse(SQ)),
           ff.SERIES: {"ok": True, "records": [{"as_of": "2026-11-20"}]},
           fa.SERIES: {"ok": True, "records": [{"as_of": "2026-11-20"}]}}
    canon = {fs.SERIES: [close], ff.SERIES: [{"as_of": "2026-11-20"}],
             fa.SERIES: [{"as_of": "2026-11-20"}]}
    keep, msgs = gold_run.newer_subset(raw, read=_store(canon))
    assert keep == {} and all("nothing newer" in m for m in msgs)


def test_source_down_is_no_write_and_does_not_block_the_others():
    raw = {fs.SERIES: {"ok": False, "error": "Swissquote: FormatError"},
           fa.SERIES: {"ok": True, "records": [{"as_of": "2026-11-20"}]}}
    keep, msgs = gold_run.newer_subset(raw, read=_store({fa.SERIES: [{"as_of": "2026-11-19"}]}))
    assert list(keep) == [fa.SERIES] and any("unknown" in m for m in msgs)


# --- live (opt-in) -------------------------------------------------------------------

@pytest.mark.skipif(not os.environ.get("ZLT8_LIVE"), reason="live: set ZLT8_LIVE=1")
def test_live_three_sources_parse():
    q = fs.parse(fs.fetch())
    assert fs.SANE[0] < q["mid"] < fs.SANE[1]
    fut = ff.parse(ff.fetch())
    assert len(fut) > 2000 and fut[-1]["as_of"] >= (dt.date.today() - dt.timedelta(days=6)).isoformat()
    acm = fa.parse(fa.fetch())
    assert acm[0]["as_of"] == "1961-06-14" and len(acm) > 16000

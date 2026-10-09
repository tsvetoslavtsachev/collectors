"""ЗЛТ6 -- mkt_gold_usd read out of the SPDR GLD archive (NAV/Share / oz/Share = the
LBMA Gold Price PM), and LBMA itself never called again. Offline: synthetic GLD
.xlsx, stubbed canonical history and UK holiday calendar, and any request to the
LBMA host fails the test.

Run: python -m pytest collectors/gold/tests/test_gld_fix.py
"""
import datetime as dt
import io

import openpyxl
import pytest
import requests
from datacore import storage

from collectors.gold import fetch_gld, fetch_gldm, gld_fix, run
from .test_fetch_gld import HEADER

UK_HOLIDAYS = {"2026-08-31", "2026-12-25", "2026-12-28", "2027-01-01"}
EXISTING = [
    {"as_of": "2026-09-24", "value": 4240.10, "source": "LBMA PM",
     "series_id": "mkt_gold_usd", "schema_version": 1},
    {"as_of": "2026-09-25", "value": 4261.05, "source": "LBMA PM",
     "series_id": "mkt_gold_usd", "schema_version": 1},
]
# date, NAV/Share, oz/Share -- implied = NAV / oz
GLD_ROWS = [
    ("25-Sep-2026", 390.6182, 0.091672),     # == last canonical day: not re-added
    ("28-Sep-2026", 389.8800, 0.091671),     # -> 4253.04
    ("24-Dec-2026", 400.0000, 0.091600),     # half day (AM only): dropped
    ("28-Dec-2026", 401.0000, 0.091600),     # UK bank holiday: dropped
    ("29-Dec-2026", 402.0000, 0.091600),     # -> 4388.65
]


def _gld_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = fetch_gld.SHEET
    ws.append(HEADER)
    for d, nav, oz in GLD_ROWS:
        ws.append([d, 1.0, oz, nav, 1.0, 1.0, 0.0, 1000, 3.4e7, 1060.0, 1.4e11])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _no_lbma(url, *args, **kwargs):
    if "lbma.org.uk" in url:
        raise AssertionError(f"LBMA is closed (ЗЛТ6) but was called: {url}")
    raise requests.ConnectionError(f"offline test, unexpected request: {url}")


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(requests, "get", _no_lbma)
    monkeypatch.setattr(fetch_gld, "fetch_bytes", lambda *a, **k: _gld_xlsx())
    monkeypatch.setattr(fetch_gldm, "fetch", lambda *a, **k: {
        sid: {"ok": True, "records": [{"as_of": "2026-09-28", "value": 1.0,
                                       "source": fetch_gldm.SOURCE}]}
        for sid in fetch_gldm.COLUMNS})
    monkeypatch.setattr(storage, "read_canonical", lambda sid: list(EXISTING))
    monkeypatch.setattr(gld_fix, "fetch_uk_holidays", lambda *a, **k: set(UK_HOLIDAYS))


def test_assemble_returns_exactly_the_active_series(offline):
    raw = run.assemble()                        # any LBMA call raises here
    assert set(raw) == set(run.ACTIVE)
    assert all(raw[sid]["ok"] and raw[sid]["records"] for sid in raw)


def test_usd_continues_from_gld_implied_fix(offline):
    usd = run.assemble()["mkt_gold_usd"]
    recs = usd["records"]
    assert [r["as_of"] for r in recs] == ["2026-09-24", "2026-09-25",
                                          "2026-09-28", "2026-12-29"]
    assert recs[1] == {"as_of": "2026-09-25", "value": 4261.05, "source": "LBMA PM"}
    assert recs[2] == {"as_of": "2026-09-28", "value": 4253.04, "source": gld_fix.SOURCE}
    assert recs[3]["value"] == round(402.0 / 0.0916, 2)
    assert "+2 row(s) after 2026-09-25" in usd["note"]


def test_holiday_calendar_down_skips_usd_but_keeps_tonnage(offline, monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("gov.uk down")
    monkeypatch.setattr(gld_fix, "fetch_uk_holidays", boom)
    raw = run.assemble()
    assert raw["mkt_gold_usd"]["ok"] is False
    assert "gov.uk down" in raw["mkt_gold_usd"]["error"]
    for sid in list(fetch_gld.COLUMNS) + list(fetch_gldm.COLUMNS):
        assert raw[sid]["ok"]


def test_gld_down_skips_its_series_but_keeps_gldm(offline, monkeypatch):
    def boom(*a, **k):
        raise requests.HTTPError("403 Client Error: Forbidden")
    monkeypatch.setattr(fetch_gld, "fetch_bytes", boom)
    raw = run.assemble()
    for sid in list(fetch_gld.COLUMNS) + [gld_fix.SERIES]:
        assert raw[sid]["ok"] is False and "SPDR GLD" in raw[sid]["error"]
    for sid in fetch_gldm.COLUMNS:
        assert raw[sid]["ok"]


def test_extend_without_history_refuses():
    assert gld_fix.extend([], None, set())["ok"] is False


@pytest.mark.parametrize("day,expected", [
    ("2026-12-24", True),     # Thu before Christmas
    ("2026-12-31", True),     # Thu before New Year
    ("2016-12-23", True),     # 24 Dec was a Saturday
    ("2026-12-23", False),
    ("2026-12-29", False),
])
def test_half_day(day, expected):
    assert gld_fix.is_half_day(dt.date.fromisoformat(day), UK_HOLIDAYS) is expected

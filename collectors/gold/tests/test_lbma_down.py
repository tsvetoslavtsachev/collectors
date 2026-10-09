"""ЗЛТ6 -- LBMA answers 403 (Cloudflare block, recorded 10.10.2026) and the run must
not die with it: GLD/GLDM still write, the LBMA series SKIP loudly, and
mkt_gold_usd continues from the GLD-implied PM fix. Offline: the 403 is the
recorded response body replayed through a real requests.Response.

Mutation (Г3): against the pre-ЗЛТ6 assemble() the first test raises HTTPError.

Run: python -m pytest collectors/gold/tests/test_lbma_down.py
"""
import datetime as dt
import io
from pathlib import Path

import openpyxl
import pytest
import requests
from datacore import storage

from collectors.gold import fallback_usd, fetch_gld, fetch_gldm, run
from .test_fetch_gld import HEADER

FIXTURE = Path(__file__).parent / "fixtures" / "lbma_403_cloudflare.html"
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


def _cloudflare_403(url, *args, **kwargs):
    r = requests.models.Response()
    r.status_code, r.reason, r.url = 403, "Forbidden", url
    r.headers["Content-Type"] = "text/html; charset=UTF-8"
    r._content = FIXTURE.read_bytes()
    return r


@pytest.fixture
def lbma_down(monkeypatch):
    monkeypatch.setattr(requests, "get", _cloudflare_403)      # every LBMA call
    monkeypatch.setattr(fetch_gld, "fetch_bytes", lambda *a, **k: _gld_xlsx())
    monkeypatch.setattr(fetch_gldm, "fetch", lambda *a, **k: {
        sid: {"ok": True, "records": [{"as_of": "2026-09-28", "value": 1.0,
                                       "source": fetch_gldm.SOURCE}]}
        for sid in fetch_gldm.COLUMNS})
    monkeypatch.setattr(storage, "read_canonical", lambda sid: list(EXISTING))
    monkeypatch.setattr(fallback_usd, "fetch_uk_holidays", lambda *a, **k: set(UK_HOLIDAYS))


def test_fixture_is_the_recorded_cloudflare_block():
    body = FIXTURE.read_text(encoding="utf-8")
    assert "Sorry, you have been blocked" in body
    assert "2001:db8::1" in body                # the caller's IP is redacted


def test_lbma_403_does_not_take_gld_down(lbma_down):
    raw = run.assemble()                        # pre-ЗЛТ6: raises HTTPError here
    for sid in list(fetch_gld.COLUMNS) + list(fetch_gldm.COLUMNS):
        assert raw[sid]["ok"] and raw[sid]["records"]
    for sid in ("mkt_gold_gbp", "mkt_gold_eur",
                "mkt_silver_usd", "mkt_silver_gbp", "mkt_silver_eur"):
        assert raw[sid]["ok"] is False
        assert "403" in raw[sid]["error"]


def test_usd_continues_from_gld_implied_fix(lbma_down):
    usd = run.assemble()["mkt_gold_usd"]
    assert usd["ok"]
    recs = usd["records"]
    assert [r["as_of"] for r in recs] == ["2026-09-24", "2026-09-25",
                                          "2026-09-28", "2026-12-29"]
    assert recs[1] == {"as_of": "2026-09-25", "value": 4261.05, "source": "LBMA PM"}
    assert recs[2] == {"as_of": "2026-09-28", "value": 4253.04,
                       "source": fallback_usd.SOURCE}
    assert recs[3]["value"] == round(402.0 / 0.0916, 2)
    assert "403" in usd["note"] and "+2 row(s) after 2026-09-25" in usd["note"]


def test_fallback_failure_is_reported_not_raised(lbma_down, monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("gov.uk down")
    monkeypatch.setattr(fallback_usd, "fetch_uk_holidays", boom)
    usd = run.assemble()["mkt_gold_usd"]
    assert usd["ok"] is False
    assert "gov.uk down" in usd["error"] and "403" in usd["error"]


def test_lbma_up_never_touches_the_fallback(monkeypatch):
    monkeypatch.setattr(run.fetch_lbma, "fetch", lambda: {
        sid: {"ok": True, "records": [{"as_of": "2026-10-09", "value": 1.0,
                                       "source": "LBMA PM"}]}
        for sid in run.fetch_lbma.CCY_INDEX})
    monkeypatch.setattr(fetch_gld, "fetch_bytes", lambda *a, **k: _gld_xlsx())
    monkeypatch.setattr(fetch_gldm, "fetch", lambda *a, **k: {})
    monkeypatch.setattr(run.fetch_silver, "fetch", lambda *a, **k: {})

    def unexpected(*a, **k):
        raise AssertionError("fallback called while LBMA is up")
    monkeypatch.setattr(fallback_usd, "extend", unexpected)
    assert run.assemble()["mkt_gold_usd"]["records"][0]["source"] == "LBMA PM"


def test_extend_without_history_refuses():
    assert fallback_usd.extend([], None, set())["ok"] is False


@pytest.mark.parametrize("day,expected", [
    ("2026-12-24", True),     # Thu before Christmas
    ("2026-12-31", True),     # Thu before New Year
    ("2016-12-23", True),     # 24 Dec was a Saturday
    ("2026-12-23", False),
    ("2026-12-29", False),
])
def test_half_day(day, expected):
    assert fallback_usd.is_half_day(dt.date.fromisoformat(day), UK_HOLIDAYS) is expected

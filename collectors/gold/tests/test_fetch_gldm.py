"""Offline test for the SPDR GLDM archive parser -- no network, mirrors
test_fetch_gld.py (fetch_gldm.py reuses fetch_gld.parse_archive/fetch_bytes;
only to_records is local, so this test focuses on that + the holiday-drop
reuse actually working through the thin wrapper).

Run: python -m pytest collectors/gold/tests/test_fetch_gldm.py
"""
import io
import openpyxl

from collectors.gold import fetch_gldm as fgm

HEADER = ["Date", "Closing Price", "Ounces of Gold per Share",
         "NAV/Share at 10:30am NYT", "Indicative Price per Share at 4:15pm NYT",
         "Mid point of bid/ask spread at 4:15pm NYT",
         "Premium/Discount of GLDM Mid Point vs Indicative Value of GLDM at 4:15pm NYT",
         "Daily Share Volume", "Total Ounces of Gold in the Trust",
         "Tonnes of Gold", "Total Net Asset Value in the Trust"]

ROWS = [
    ["26-Jun-2018", 12.58, 0.00999995, 12.602938, 12.589, 12.59, 0.0045,
     1519548, 20000.0, 0.62, 25205875.7],
    ["27-Jun-2018", 12.52, 0.0099999, 12.545876, 12.519, 12.52, 0.005,
     165608, 20000.0, 0.62, 25091751.96],
    ["04-Jul-2018", "US Holiday", "US Holiday", "US Holiday", "US Holiday",
     "US Holiday", "US Holiday", "US Holiday", "US Holiday", "US Holiday", "US Holiday"],
    ["05-Jul-2018", 12.61, 0.00999985, 12.632847, 12.611, 12.61, 0.006,
     213000, 225000.0, 7.0, 283925000.0],
]


def _xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    wb.create_sheet("Disclaimer")
    ws = wb.create_sheet(fgm.SHEET)
    ws.append(HEADER)
    for row in ROWS:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_holiday_row_is_dropped_via_shared_parser():
    df = fgm.parse_archive(_xlsx_bytes())
    assert len(df) == 3
    assert "2018-07-04" not in set(df["as_of"])


def test_to_records_shape_and_source():
    df = fgm.parse_archive(_xlsx_bytes())
    out = fgm.to_records(df)
    assert set(out) == {"etf_gldm_tonnes", "etf_gldm_oz"}
    assert out["etf_gldm_tonnes"]["records"][0] == {
        "as_of": "2018-06-26", "value": 0.62, "source": "SPDR GLDM archive"}
    assert out["etf_gldm_oz"]["records"][-1]["value"] == 225000.0


def test_nav_column_reused_from_fetch_gld_not_redefined():
    from collectors.gold import fetch_gld
    assert fgm.NAV_COLUMN == fetch_gld.NAV_COLUMN
    df = fgm.parse_archive(_xlsx_bytes())
    assert fgm.NAV_COLUMN in df.columns

"""Fiscal citizen (ПРОЛЕТБ2): readers, the "null" trap, the auction table guard, the lamp.
No network."""
import datetime as dt

import pytest

from collectors.fiscal import auctions, run, sources


def _td(day="20261125", btc="2.43", pd="3748000000", di="14445058100", ind="25325938800", **kw):
    x = {"securityType": "Note", "inflationProtected": "No", "floatingRate": "No",
         "treasuryAuctionResultsXmlFileName": f"R_{day}_3.xml" if day else "",
         "bidToCoverRatio": btc, "primaryDealersCompetitiveParAwarded": pd,
         "directBiddersCompetitiveParAwarded": di, "indirectBiddersCompetitiveParAwarded": ind,
         "cusip": "91282CSA0", "securityTerm": "7-Year", "originalSecurityTerm": "7-Year",
         "reopening": "No", "offeringAmount": "44000000000", "highYield": "4.708"}
    x.update(kw)
    return x


def _many(rows, n=1000):
    return rows + [_td(day=f"2010{(i % 12) + 1:02d}{(i % 28) + 1:02d}", cusip=f"X{i}") for i in range(n)]


def test_num_reads_null_text_and_blank_as_none():
    assert sources.num("null") is None
    assert sources.num("NULL") is None
    assert sources.num("") is None
    assert sources.num(" ") is None
    assert sources.num(None) is None
    assert sources.num("2.260000") == 2.26
    with pytest.raises(sources.FormatError):
        sources.num("n/a")


def test_auction_with_null_numbers_is_not_read_as_a_number():
    """Mutation: Fiscal Data writes the TEXT "null" for an unpublished result."""
    rows = sources.parse_auctions(_many([_td(day="20261122", btc="null", pd="null", cusip="NULL1")]))
    assert all(r["cusip"] != "NULL1" for r in rows)
    assert all(isinstance(r["bid_to_cover"], float) for r in rows)


def test_announced_auction_without_result_is_dropped():
    rows = sources.parse_auctions(_many([_td(day="", cusip="FUTURE")]))
    assert all(r["cusip"] != "FUTURE" for r in rows)


def test_tips_and_frn_are_not_coupon_auctions_here():
    rows = sources.parse_auctions(_many([_td(cusip="TIPS1", inflationProtected="Yes"),
                                         _td(cusip="FRN1", floatingRate="Yes")]))
    assert {"TIPS1", "FRN1"}.isdisjoint(r["cusip"] for r in rows)


def test_dealer_share_is_of_competitive_awarded():
    r = [x for x in sources.parse_auctions(_many([_td()])) if x["cusip"] == "91282CSA0"][0]
    assert r["auction_date"] == "2026-11-25"
    assert r["dealers_pct"] == pytest.approx(100 * 3748000000 / (3748000000 + 14445058100 + 25325938800), abs=0.01)
    assert r["bid_to_cover"] == 2.43


def test_avg_rate_keeps_only_total_marketable():
    rows = [{"record_date": f"20{y:02d}-01-31", "security_desc": "Total Marketable",
             "avg_interest_rate_amt": "3.5"} for y in range(0, 26)] * 9
    rows.append({"record_date": "2026-10-31", "security_desc": "Treasury Bills", "avg_interest_rate_amt": "4.0"})
    rows.append({"record_date": "2026-10-31", "security_desc": "Total Marketable", "avg_interest_rate_amt": "null"})
    out = sources.parse_avg_rate(rows)
    assert all(r["value"] == 3.5 for r in out)


def test_soma_sums_the_treasury_parts_in_bn():
    doc = {"soma": {"summary": [{"asOfDate": "2026-11-18", "bills": "580659926700",
                                 "notesbonds": "3597864734500", "tips": "277655585900", "frn": "22873047100",
                                 "tipsInflationCompensation": "108078446229.29", "mbs": "1876153373779.30"}] * 1000}}
    out = sources.parse_soma(doc)
    assert out[-1]["value"] == pytest.approx(4587.132, abs=0.001)


def test_auction_table_refuses_truncation(tmp_path):
    rows = [{"auction_date": f"2020-01-{i % 28 + 1:02d}", "source": "x"} for i in range(100)]
    assert auctions.write(rows, root=tmp_path)["rows"] == 100
    res = auctions.write(rows[:50], root=tmp_path)
    assert "refused" in res["skipped"]
    assert len(auctions.read(tmp_path)) == 100


def test_lamp_reds_a_stale_series():
    today = dt.date(2026, 11, 28)
    fresh = {sources.AVG_RATE: "2026-10-31", sources.SOMA_UST: "2026-11-25", sources.DEBT_PUBLIC: "2026-11-25"}
    read = lambda sid: [{"as_of": fresh[sid], "value": 1.0}]
    code, _ = run.lamp(today, read, lambda: [{"auction_date": "2026-11-25"}])
    assert code == 0
    code, lines = run.lamp(today, read, lambda: [{"auction_date": "2026-10-15"}])
    assert code == 1 and any(l.startswith("STALE ust_coupon_auctions") for l in lines)

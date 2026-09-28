"""Offline tests for the IMF SDMX-JSON parser -- no network. Builds a synthetic
payload in the same shape as the real IMF.STA,IRFCL response (verified
28.09.2026): a shared, DELIBERATELY NON-CHRONOLOGICAL TIME_PERIOD list (the
real one is scrambled too -- see fetch_imf module docstring), obs dicts keyed
by index into that list.

Run: python -m pytest collectors/cbgold/tests/test_fetch_imf.py
"""
from collectors.cbgold import fetch_imf as fi

# Real-shaped scramble: index order != chronological order (mirrors the
# actual 28.09.2026 response where index 0..4 were 2020-M10..2021-M02 but
# the series' own latest values sat at low indices like '65'..'69').
TIME_PERIODS = ["2021-M02", "2000-M01", "2026-M07", "2000-M02", "2026-M06", "2005-M03"]


def _payload(country_values, series_map):
    """series_map: {series_key: {obs_idx_str: [value, ...]}}"""
    return {
        "dataSets": [{"series": series_map}],
        "structure": {
            "dimensions": {
                "series": [
                    {"id": "COUNTRY", "values": country_values},
                    {"id": "INDICATOR", "values": [{"id": "IRFCLDT1_IRFCL56V_FTO"}]},
                    {"id": "SECTOR", "values": [{"id": "S1XS1311"}]},
                    {"id": "FREQUENCY", "values": [{"id": "M"}]},
                ],
                "observation": [
                    {"id": "TIME_PERIOD",
                    "values": [{"id": p} for p in TIME_PERIODS]},
                ],
            }
        },
    }


def test_picks_last_period_by_date_not_by_index():
    # index 2 -> "2026-M07" (the real latest), index 4 -> "2026-M06" (one
    # month earlier but a LOWER index-as-string than "2" would suggest if
    # someone sorted keys as strings or picked max(int(key))).
    countries = [{"id": "USA"}]
    series = {"0:0:0:0": {"observations": {
        "2": ["261499000", None, 0, None, None],   # 2026-M07 (latest)
        "4": ["261498000", None, 0, None, None],   # 2026-M06
        "1": ["8000000", None, 0, None, None],     # 2000-M01
    }}}
    out = fi.parse(_payload(countries, series))
    assert out["USA"][-1] == ("2026-07-31", 261499000.0)


def test_mutation_pick_by_index_would_get_the_wrong_answer():
    # ФАЛШИФИКАТОР: this documents that max(obs_idx) is NOT the same answer
    # as sorting by mapped date -- the real regression this gate catches.
    countries = [{"id": "USA"}]
    series = {"0:0:0:0": {"observations": {
        "2": ["261499000", None, 0, None, None],   # 2026-M07, real latest
        "4": ["261498000", None, 0, None, None],   # 2026-M06
    }}}
    out = fi.parse(_payload(countries, series))
    by_date = out["USA"][-1]
    by_max_index_key = max(("2", "4"), key=int)     # the wrong shortcut
    assert by_date == ("2026-07-31", 261499000.0)
    assert by_max_index_key == "4"                  # would have picked 2026-M06


def test_null_observation_is_skipped():
    countries = [{"id": "POL"}]
    series = {"0:0:0:0": {"observations": {
        "1": [None, None, 0, None, None],
        "2": ["100000", None, 0, None, None],
    }}}
    out = fi.parse(_payload(countries, series))
    assert out["POL"] == [("2026-07-31", 100000.0)]


def test_country_index_maps_through_the_series_key():
    countries = [{"id": "USA"}, {"id": "CHN"}]
    series = {
        "0:0:0:0": {"observations": {"2": ["1000", None, 0, None, None]}},
        "1:0:0:0": {"observations": {"2": ["2000", None, 0, None, None]}},
    }
    out = fi.parse(_payload(countries, series))
    assert out["USA"] == [("2026-07-31", 1000.0)]
    assert out["CHN"] == [("2026-07-31", 2000.0)]


def test_month_end_conversion():
    assert fi._month_end("2026-M07") == "2026-07-31"
    assert fi._month_end("2026-M02") == "2026-02-28"   # non-leap
    assert fi._month_end("2024-M02") == "2024-02-29"   # leap year


def test_to_records_converts_ounces_to_tonnes():
    # 261,499,000 oz -> tonnes = oz * 31.1034768 / 1e6
    parsed = {"USA": [("2026-07-31", 261_499_000.0)]}
    out = fi.to_records(parsed)
    assert set(out) == {"cb_gold_usa_tonnes"}
    rec = out["cb_gold_usa_tonnes"]["records"][0]
    assert rec["as_of"] == "2026-07-31"
    assert abs(rec["value"] - 8_133.46) < 0.5   # ~8,133.5 t per ЗЛТ3
    assert rec["source"] == "IMF IRFCL"


def test_aggregate_country_code_lowercased_in_series_id():
    parsed = {"EZB": [("2026-07-31", 1000.0)], "G163": [("2026-07-31", 2000.0)]}
    out = fi.to_records(parsed)
    assert set(out) == {"cb_gold_ezb_tonnes", "cb_gold_g163_tonnes"}

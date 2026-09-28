"""Fetch IMF IRFCL central-bank gold reserves (SDMX-JSON) -> per-country/
aggregate tonnage records.

Source (ЗЛТ3 Р1, verified 28.09.2026, no key): host MUST be `api.imf.org`
(`data.imf.org` 403s via Akamai). Dataflow id is `IMF.STA,IRFCL` -- a comma,
not a colon (colon 404s). The key is COUNTRY.INDICATOR.SECTOR.FREQUENCY,
exactly 4 dimensions (a 5th or 3rd 400s). Sector is `S1XS1311` (monetary
gold, official reserves) -- `_Z` returns 200 with zero series, not an error.

    https://api.imf.org/external/sdmx/2.1/data/IMF.STA,IRFCL/
    .IRFCLDT1_IRFCL56V_FTO.S1XS1311.M

(empty COUNTRY segment = every country/aggregate the dataflow has for this
indicator -- 88 as of 28.09.2026, not just a hand-picked subset.)

Trap carried forward from ЗЛТ3: in SDMX-JSON the per-series `observations`
dict is keyed by an INDEX into `structure.dimensions.observation[0].values`
(the shared TIME_PERIOD list) -- and that list is itself NOT chronological
(verified 28.09.2026: index 0..4 of a real response were 2020-M10..2021-M02,
the tail was 2000-M07..2000-M02). The only safe path is: map every obs key
through the list to get its actual `TIME_PERIOD`, then sort the resulting
(date, value) pairs by date. Never treat "last key in the dict" or "highest
index" as "most recent" -- see test_fetch_imf.py for the mutation that
catches a regression to either shortcut.

OBS_VALUE is already fine troy ounces (the dataset's SCALE="6" attribute is
a display hint only, not a multiplier -- ЗЛТ3). Tonnes = oz * 31.1034768 /
1_000_000. Raw ounces are exposed via `parse()` for the Г2 Treasury
cross-check (USA only) but `to_records()`/`fetch()` -- what actually reaches
the canonical store -- write tonnes only, mirroring the gold citizen's
NAV/Close precedent (kept for a cross-check, never canonical, no declared
consumer for a raw-ounce series).
"""
from __future__ import annotations
import requests

URL = ("https://api.imf.org/external/sdmx/2.1/data/IMF.STA,IRFCL/"
       ".IRFCLDT1_IRFCL56V_FTO.S1XS1311.M")
SOURCE = "IMF IRFCL"
OZ_TO_TONNES = 31.1034768 / 1_000_000
SERIES_PREFIX = "cb_gold_"
SERIES_SUFFIX = "_tonnes"


def fetch_json(url: str = URL, timeout: int = 90) -> dict:
    r = requests.get(url, timeout=timeout,
                     headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.json()


def _month_end(time_period: str) -> str:
    """"2026-M07" -> "2026-07-31" (canon's monthly convention: as_of = last
    calendar day of the reported month, matching macro_ism_mfg.json etc.)."""
    import calendar
    year_s, month_s = time_period.split("-M")
    year, month = int(year_s), int(month_s)
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{last_day:02d}"


def parse(payload: dict) -> dict:
    """SDMX-JSON -> {country_code: [(as_of, oz), ...]}, each list sorted
    chronologically by as_of (NOT by the dict's own key/index order -- see
    module docstring). country_code is IMF's own COUNTRY id (ISO3, or EZB/
    G163 for the two aggregates)."""
    ds = payload["dataSets"][0]
    struct = payload["structure"]
    sdims = struct["dimensions"]["series"]
    country_pos = next(i for i, d in enumerate(sdims) if d["id"] == "COUNTRY")
    time_values = struct["dimensions"]["observation"][0]["values"]

    out: dict[str, list[tuple[str, float]]] = {}
    for key, s in ds["series"].items():
        country_idx = int(key.split(":")[country_pos])
        code = sdims[country_pos]["values"][country_idx]["id"]
        pairs = []
        for obs_idx, obs in s["observations"].items():
            val = obs[0]
            if val is None:
                continue
            period = time_values[int(obs_idx)]["id"]        # e.g. "2026-M07"
            pairs.append((_month_end(period), float(val)))
        pairs.sort()
        out[code] = pairs
    return out


def to_records(parsed: dict) -> dict:
    """{country_code: [(as_of, oz)]} -> {series_id: {"ok": True, "records": [...]}}
    -- tonnes only, the canonical unit for every gold-tonnage series in this
    observatory (mirrors etf_gld_tonnes)."""
    out = {}
    for code, pairs in parsed.items():
        sid = f"{SERIES_PREFIX}{code.lower()}{SERIES_SUFFIX}"
        out[sid] = {"ok": True, "records": [
            {"as_of": d, "value": round(oz * OZ_TO_TONNES, 6), "source": SOURCE}
            for d, oz in pairs
        ]}
    return out


def fetch(url: str = URL) -> dict:
    return to_records(parse(fetch_json(url)))

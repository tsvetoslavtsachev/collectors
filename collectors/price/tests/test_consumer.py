# -*- coding: utf-8 -*-
"""P6 verify gate -- proves collectors/price/consumer.py (the base-first canonical reader)
against a TEMPORARY archive root. NO network: synthetic px_* bars are written through the SAME
archive.append path the live citizen uses, then read back through the consumer.

The gates:
  c1 reconstruction -- read_base_ohlcv reproduces auto_adjust=True: Close == value_tr,
                       O/H/L == split-adj OHLC * (value_tr/close), Volume == raw volume
  c2 drift bar      -- a bar with value_tr != close scales O/H/L by the right factor
  c3 source map     -- a served symbol -> SRC_BASE; an unmapped ticker (^VIX) -> SRC_UNMAPPED;
                       a mapped-but-absent series -> left out of base (a fallback candidate)
  c4 period window  -- period/start/end clip the returned bars
  c5 no root        -- root=None + DATACORE_ROOT unset -> empty frames, empty source_map
                       (NEVER a root=None read against the data-core base)
  c6 fallback       -- load_ohlcv_base_first: base serves SPY, the injected fallback serves the
                       rest; merged frame carries BOTH; source_map base/fetch is correct
  c7 all-fallback   -- archive unreachable -> the WHOLE universe routes through the fallback
  c8 dead symbol    -- a symbol the fallback also cannot serve is dropped from source_map
  c9 clip-to-empty  -- a mapped series clipped out of the window is a fallback candidate, not base
  c10 GBX normalize -- normalize_currency: GBX (pence) /100 -> GBP on O/H/L/Close, Volume untouched
  c11 EUR untouched -- a non-GBX series is divisor 1.0 (unchanged) even under normalize
  c12 uniform merge -- load_ohlcv_base_first normalizes base + fetch UNIFORMLY (no mixed units);
                       default (no flag) keeps raw pence (backward compat)

Run:
  PYTHONPATH=<data-core>;<collectors> python collectors/price/tests/test_consumer.py
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pandas as pd
import yaml

from datacore import archive
from collectors.price import register_catalog, consumer

_REC = "2026-06-26"  # fixed recorded_on -> deterministic
PRICE_DIR = Path(register_catalog.__file__).resolve().parent
CFG = yaml.safe_load((PRICE_DIR / "config.yaml").read_text(encoding="utf-8"))


class Gate:
    def __init__(self):
        self.total = 0
        self.fails: list[str] = []

    def check(self, name, cond, detail=""):
        self.total += 1
        print(("  [PASS] " if cond else "  [FAIL] ") + name + (f" -- {detail}" if detail else ""))
        if not cond:
            self.fails.append(name)


def _seed_temp_root() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="px_p6_"))
    (tmp / "catalog").mkdir(parents=True)
    (tmp / "catalog" / "catalog.json").write_text(
        json.dumps({"catalog_schema_version": 1,
                    "series": {"px_probe_daily": {"description": "seed probe",
                                                  "source": "synthetic-probe",
                                                  "schema_version": 1}}},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    register_catalog.register(CFG, tmp)
    return tmp


def _bar(as_of, *, close, value_tr=None, open_=None, high=None, low=None, volume=1000,
         split_factor=1.0, dividend=0.0):
    """A px bar with INDEPENDENT close vs value_tr so the reconstruction factor is exercised."""
    vtr = close if value_tr is None else value_tr
    return {"as_of": as_of, "value": close,
            "open": close if open_ is None else open_,
            "high": close if high is None else high,
            "low": close if low is None else low,
            "close": close, "value_tr": vtr, "volume": volume,
            "split_factor": split_factor, "dividend": dividend, "source": "test"}


def _write(root, sid, bars):
    archive.append(sid, bars, root=str(root), recorded_on=_REC)


def _fake_fallback(returned: dict):
    """Build a download_ohlcv-shaped callable that serves only ``returned`` {ticker: close_val}.
    Records the tickers it was asked for so the test can assert base-first (no over-fetch)."""
    calls = {}

    def fb(tickers, period=None):
        calls["tickers"] = list(tickers)
        idx = pd.to_datetime(["2026-06-24", "2026-06-25"]).normalize()
        fields = {f: {} for f in ("Open", "High", "Low", "Close", "Volume")}
        for t in tickers:
            if t not in returned:
                continue
            v = returned[t]
            for f in ("Open", "High", "Low", "Close"):
                fields[f][t] = pd.Series([v, v], index=idx)
            fields["Volume"][t] = pd.Series([10, 10], index=idx)
        return {f: (pd.DataFrame(cols) if cols else pd.DataFrame()) for f, cols in fields.items()}

    return fb, calls


def main() -> int:
    g = Gate()
    consumer.symbol_to_series.cache_clear()
    consumer.quote_basis_map.cache_clear()
    tmp = _seed_temp_root()
    old_env = os.environ.get("DATACORE_ROOT")
    try:
        # --- seed SPY: a drift bar (value_tr<close) + a tip bar (value_tr==close) ---
        _write(tmp, "px_spy_daily", [
            _bar("2026-06-24", close=100.0, value_tr=90.0, open_=100.0, high=110.0, low=95.0,
                 volume=5000),
            _bar("2026-06-25", close=200.0, value_tr=200.0, open_=198.0, high=205.0, low=196.0,
                 volume=6000),
        ])

        # c1 + c2 reconstruction + drift -------------------------------------------------
        ohlcv, src = consumer.read_base_ohlcv(["SPY"], root=tmp, period="max")
        cl = ohlcv["Close"]["SPY"]
        op = ohlcv["Open"]["SPY"]
        vol = ohlcv["Volume"]["SPY"]
        d24 = pd.Timestamp("2026-06-24")
        d25 = pd.Timestamp("2026-06-25")
        g.check("c1 Close==value_tr", abs(cl[d24] - 90.0) < 1e-9 and abs(cl[d25] - 200.0) < 1e-9,
                f"{cl[d24]},{cl[d25]}")
        # factor 2026-06-24 = 90/100 = 0.9 -> Open 100*0.9=90 ; 2026-06-25 factor 1 -> Open 198
        g.check("c2 Open scaled by value_tr/close",
                abs(op[d24] - 90.0) < 1e-9 and abs(op[d25] - 198.0) < 1e-9, f"{op[d24]},{op[d25]}")
        g.check("c1b Volume raw (unadjusted)", vol[d24] == 5000 and vol[d25] == 6000,
                f"{vol[d24]},{vol[d25]}")
        g.check("c1c High/Low scaled", abs(ohlcv["High"]["SPY"][d24] - 99.0) < 1e-9
                and abs(ohlcv["Low"]["SPY"][d24] - 85.5) < 1e-9)

        # c3 source map ------------------------------------------------------------------
        ohlcv2, src2 = consumer.read_base_ohlcv(["SPY", "QQQ", "^VIX"], root=tmp, period="max")
        g.check("c3 SPY base", src2.get("SPY") == consumer.SRC_BASE, str(src2.get("SPY")))
        g.check("c3 ^VIX unmapped", src2.get("^VIX") == consumer.SRC_UNMAPPED, str(src2.get("^VIX")))
        g.check("c3 QQQ mapped-but-absent omitted (fallback candidate)",
                "QQQ" not in src2 and "QQQ" not in ohlcv2["Close"].columns)

        # c4 period window ---------------------------------------------------------------
        _write(tmp, "px_qqq_daily", [
            _bar("2024-01-02", close=10.0), _bar("2025-06-02", close=20.0),
            _bar("2026-06-25", close=30.0),
        ])
        oc, _ = consumer.read_base_ohlcv(["QQQ"], root=tmp, period="6mo", end="2026-06-26")
        idx = list(oc["Close"]["QQQ"].dropna().index)
        g.check("c4 6mo window keeps only recent bar", idx == [pd.Timestamp("2026-06-25")], str(idx))
        oc2, _ = consumer.read_base_ohlcv(["QQQ"], root=tmp, start="2025-01-01", end="2026-06-26")
        idx2 = sorted(str(x.date()) for x in oc2["Close"]["QQQ"].dropna().index)
        g.check("c4b start/end window", idx2 == ["2025-06-02", "2026-06-25"], str(idx2))

        # c5 no root ---------------------------------------------------------------------
        os.environ.pop("DATACORE_ROOT", None)
        oc3, src3 = consumer.read_base_ohlcv(["SPY"], root=None, period="max")
        g.check("c5 no root -> empty frames", oc3["Close"].empty and src3 == {})

        # c6 base-first + CLOSED fallback ------------------------------------------------
        # IWM is mapped (px_iwm_daily) but never written to this temp root -> a fallback
        # candidate; ^VIX is unmapped -> also a fallback candidate. SPY is base-served.
        fb, calls = _fake_fallback({"IWM": 333.0, "^VIX": 17.0})
        oc4, src4 = consumer.load_ohlcv_base_first(
            ["SPY", "IWM", "^VIX"], fetch_fallback=fb, root=tmp, period="max")
        g.check("c6 SPY from base", src4.get("SPY") == consumer.SRC_BASE)
        g.check("c6 IWM from fetch", src4.get("IWM") == consumer.SRC_FETCH)
        g.check("c6 ^VIX from fetch", src4.get("^VIX") == consumer.SRC_FETCH)
        g.check("c6 merged frame carries base+fallback cols",
                {"SPY", "IWM", "^VIX"}.issubset(set(oc4["Close"].columns)), str(list(oc4["Close"].columns)))
        g.check("c6 base-first: fallback asked ONLY for non-base symbols",
                set(calls["tickers"]) == {"IWM", "^VIX"}, str(calls["tickers"]))

        # c7 archive unreachable -> all fallback -----------------------------------------
        fb2, calls2 = _fake_fallback({"SPY": 1.0, "QQQ": 2.0})
        oc5, src5 = consumer.load_ohlcv_base_first(
            ["SPY", "QQQ"], fetch_fallback=fb2, root=None, period="max")
        g.check("c7 all symbols via fallback when no root",
                src5.get("SPY") == consumer.SRC_FETCH and src5.get("QQQ") == consumer.SRC_FETCH)

        # c8 dead symbol (fallback serves nothing for it) --------------------------------
        fb3, _ = _fake_fallback({"IWM": 5.0})  # ZZZZ not served, and ZZZZ is unmapped anyway
        oc6, src6 = consumer.load_ohlcv_base_first(
            ["SPY", "IWM", "ZZZZ"], fetch_fallback=fb3, root=tmp, period="max")
        g.check("c8 dead symbol dropped from source_map",
                "ZZZZ" not in src6 and src6.get("SPY") == consumer.SRC_BASE
                and src6.get("IWM") == consumer.SRC_FETCH, str(src6))

        # c9 PR-1: a mapped series whose only bars fall OUTSIDE the requested window clips to empty
        # -> must NOT be stamped SRC_BASE (else an all-NaN col + suppressed fallback). VTI is mapped
        # (px_vti_daily) and only has a 2019 bar; a 6mo window in 2026 clips it to zero.
        _write(tmp, "px_vti_daily", [_bar("2019-01-02", close=50.0)])
        oc7, src7 = consumer.read_base_ohlcv(["VTI"], root=tmp, period="6mo", end="2026-06-26")
        g.check("c9 clip-to-empty NOT base (fallback candidate)",
                "VTI" not in src7 and "VTI" not in oc7["Close"].columns, str(src7))
        fb4, _ = _fake_fallback({"VTI": 99.0})
        oc8, src8 = consumer.load_ohlcv_base_first(
            ["VTI"], fetch_fallback=fb4, root=tmp, period="6mo", end="2026-06-26")
        g.check("c9b stale-windowed symbol falls back to fetch",
                src8.get("VTI") == consumer.SRC_FETCH and "VTI" in oc8["Close"].columns, str(src8))

        # c10..c12 P8c GBX currency normalization ----------------------------------------
        # HSBA.L (px_hsba_l_daily) + AAL.L (px_aal_l_daily) are GBX (London pence); SAP.DE
        # (px_sap_de_daily) is EUR. Write synthetic RAW bars (pence / euros) and read with the
        # P8c normalize_currency flag. The archive stays RAW (decision 4a); /100 is the consumer step.
        _write(tmp, "px_hsba_l_daily", [
            _bar("2026-06-24", close=5000.0, value_tr=5000.0, open_=4900.0, high=5100.0,
                 low=4800.0, volume=12345),
            _bar("2026-06-25", close=5200.0, value_tr=5200.0, open_=5150.0, high=5250.0,
                 low=5050.0, volume=22222),
        ])
        _write(tmp, "px_sap_de_daily", [
            _bar("2026-06-24", close=120.0, value_tr=120.0, volume=7000),
            _bar("2026-06-25", close=122.0, value_tr=122.0, volume=8000),
        ])
        # c10a raw by default -> pence preserved (the archive contract + auto_adjust parity)
        raw_g, _ = consumer.read_base_ohlcv(["HSBA.L"], root=tmp, period="max")
        g.check("c10a GBX raw by default (pence, no /100)",
                abs(raw_g["Close"]["HSBA.L"][d25] - 5200.0) < 1e-9, str(raw_g["Close"]["HSBA.L"][d25]))
        # c10b/c/d normalized -> /100 to GBP on EVERY price field, Volume untouched
        nrm, _ = consumer.read_base_ohlcv(["HSBA.L"], root=tmp, period="max", normalize_currency=True)
        g.check("c10b GBX normalized Close /100 (-> GBP)",
                abs(nrm["Close"]["HSBA.L"][d25] - 52.0) < 1e-9, str(nrm["Close"]["HSBA.L"][d25]))
        g.check("c10c GBX normalized O/H/L /100",
                abs(nrm["Open"]["HSBA.L"][d25] - 51.5) < 1e-9
                and abs(nrm["High"]["HSBA.L"][d25] - 52.5) < 1e-9
                and abs(nrm["Low"]["HSBA.L"][d25] - 50.5) < 1e-9,
                f"O={nrm['Open']['HSBA.L'][d25]} H={nrm['High']['HSBA.L'][d25]} L={nrm['Low']['HSBA.L'][d25]}")
        g.check("c10d GBX normalized VOLUME untouched (share count, never /100)",
                nrm["Volume"]["HSBA.L"][d25] == 22222, str(nrm["Volume"]["HSBA.L"][d25]))
        # c11 a EUR series is divisor 1.0 -- untouched even with the flag on
        eur, _ = consumer.read_base_ohlcv(["SAP.DE"], root=tmp, period="max", normalize_currency=True)
        g.check("c11 EUR series untouched under normalize (divisor 1.0)",
                abs(eur["Close"]["SAP.DE"][d25] - 122.0) < 1e-9, str(eur["Close"]["SAP.DE"][d25]))
        # c12 UNIFORM across provenance: HSBA.L base-served + AAL.L (GBX) fetch-served (raw pence
        # from the fallback, yfinance shape) -> BOTH /100 from the one merged frame (the core
        # mixed-units hazard the post-merge placement exists to prevent).
        fb5, _ = _fake_fallback({"AAL.L": 2500.0})
        oc9, src9 = consumer.load_ohlcv_base_first(
            ["HSBA.L", "AAL.L"], fetch_fallback=fb5, root=tmp, period="max", normalize_currency=True)
        g.check("c12a base+fetch GBX normalized uniformly /100 (no mixed units)",
                abs(oc9["Close"]["HSBA.L"][d25] - 52.0) < 1e-9
                and abs(oc9["Close"]["AAL.L"][d25] - 25.0) < 1e-9,
                f"HSBA={oc9['Close']['HSBA.L'][d25]} AAL={oc9['Close']['AAL.L'][d25]}")
        g.check("c12b provenance still correct under normalize",
                src9.get("HSBA.L") == consumer.SRC_BASE and src9.get("AAL.L") == consumer.SRC_FETCH,
                str(src9))
        fb6, _ = _fake_fallback({"AAL.L": 2500.0})
        oc10, _ = consumer.load_ohlcv_base_first(
            ["HSBA.L", "AAL.L"], fetch_fallback=fb6, root=tmp, period="max")
        g.check("c12c default (no normalize) keeps raw pence (backward compat)",
                abs(oc10["Close"]["HSBA.L"][d25] - 5200.0) < 1e-9
                and abs(oc10["Close"]["AAL.L"][d25] - 2500.0) < 1e-9,
                f"HSBA={oc10['Close']['HSBA.L'][d25]} AAL={oc10['Close']['AAL.L'][d25]}")
    finally:
        if old_env is not None:
            os.environ["DATACORE_ROOT"] = old_env
        else:
            os.environ.pop("DATACORE_ROOT", None)
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{g.total - len(g.fails)}/{g.total} passed"
          + (f" -- FAILED: {g.fails}" if g.fails else " -- ALL GREEN"))
    return 1 if g.fails else 0


if __name__ == "__main__":
    raise SystemExit(main())

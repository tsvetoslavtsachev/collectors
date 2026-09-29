"""Plausibility for the IMF IRFCL gold tonnage -- repair what the source
provably got wrong, then gate what is about to reach the canon (29.09.2026,
after the ЗЛТ4 reader s9_gold had to drop 86 points under its deviation О2).

Root cause (verified on the live payload 29.09.2026): the bad points are in
OBS_VALUE itself, not in parse(). No attribute marks them -- SCALE is the
series-level "6" on every series (display only, ЗЛТ3), and the only
observation attribute with a value is DERIVATION_TYPE="O" everywhere. The
collector cannot read its way out; it has to recognise the two defects:

1. Decimal slip x10^3 by the reporter. BRA 2026-M03..M08 = 5,544,278,722.99
   right after 5,544,278.72 in 2026-M02 (ratio 1000 to 13 digits); AGO in
   every month 2020-M10..2026-M07 (592,900,000 "oz" = 18,441 t, above the
   US). Proof it is a slip, not a level: the same reporter's USD value of the
   same gold (indicator IRFCLDT1_IRFCL56_USD) over those ounces gives
   $1.88/oz for AGO 2020-M10 and $4.61/oz for BRA 2026-M03 -- 1000x under
   the price every other reporter implies that month. Divided by 10^3 both
   sit on the market (0.8-1.0x the cross-reporter median).
2. A gap coded as zero. Ten single '0' observations sandwiched between two
   non-zero months, mostly at the same level on both sides (DNK 2002-M11:
   66.574 t -> 0 -> 66.574 t). A central bank does not sell and rebuy its
   whole stock inside one month; this is a missing report. Runs of 2+ zeros
   (MLT/CHL/SVN) are NOT touched -- the levels on either side differ there
   and a real zero-holding period cannot be ruled out.

repair() fixes only these two and only with evidence: the sandwiched zero is
dropped (= missing), a level above the US is divided by 10^k only when the
USD field confirms the rescaled ounces at the market price. Everything else
passes through untouched and meets check()/gate() -- the last look before
datacore.write, which refuses the whole series on any violation (the canon
keeps its previous file).

Why the implied price is evidence, not the detector: several reporters carry
gold at book value (USA at the statutory $42.22/oz, also SAU/SGP/TUN/KOR),
so their USD/oz legitimately sits 10-100x under market. A detector built on
it would flag them; as confirmation of one specific x10^k slip on a point
that is already impossible (above the US) it is decisive.
"""
from __future__ import annotations
import statistics

from .fetch_imf import SERIES_PREFIX, SERIES_SUFFIX
from .register_catalog import AGGREGATES

US = "USA"
# Decimal slips: x10^3 (BRA/AGO, 2026) and x10^6 (the SCALE="6" trap, ЗЛТ3).
SLIP_FACTORS = (1_000, 1_000_000)
# A month-to-month ratio within 1% of a slip factor is a slip signature. The
# largest non-slip ratios in the 29.09.2026 payload are 749 (SVN 2001) and
# 56.9 (ARG 2004) -- nowhere near 1000 +/- 1%.
SLIP_TOL = 0.01
# After a rescale, USD value / ounces must land within this band of the
# cross-reporter median $/oz for the same month.
PRICE_BAND = (2 / 3, 1.5)


def _sandwiched_zero(vals: list, i: int) -> bool:
    return (vals[i] == 0 and 0 < i < len(vals) - 1
            and vals[i - 1] > 0 and vals[i + 1] > 0)


def _slip_factor(a: float, b: float) -> int | None:
    """Consecutive non-zero levels a -> b: the slip factor their ratio sits
    on (either direction), else None."""
    if a <= 0 or b <= 0:
        return None
    r = max(a / b, b / a)
    return next((f for f in SLIP_FACTORS if abs(r / f - 1) < SLIP_TOL), None)


def market_prices(oz: dict, usd: dict) -> dict:
    """{as_of: median USD/oz over every reporter with both fields > 0}."""
    by_date: dict[str, list[float]] = {}
    for code, pairs in oz.items():
        u = dict(usd.get(code, ()))
        for d, v in pairs:
            if v > 0 and u.get(d, 0) > 0:
                by_date.setdefault(d, []).append(u[d] / v)
    return {d: statistics.median(ps) for d, ps in by_date.items()}


def _confirmed_slip(v: float, usd_value, market, ceiling: float) -> int | None:
    if not usd_value or not market:
        return None
    for f in SLIP_FACTORS:
        fixed = v / f
        if fixed <= ceiling and PRICE_BAND[0] <= usd_value / fixed / market <= PRICE_BAND[1]:
            return f
    return None


def repair(oz: dict, usd: dict) -> tuple[dict, list[str]]:
    """{code: [(as_of, oz)]} + the same shape for the USD value field ->
    ({code: [(as_of, oz)]} repaired, one note per repaired run/point)."""
    ceiling = max((v for _, v in oz.get(US, ())), default=None)
    prices = market_prices(oz, usd)
    out, notes = {}, []
    for code in sorted(oz):
        pairs = oz[code]
        vals = [v for _, v in pairs]
        u = dict(usd.get(code, ()))
        kept, zeros, slips = [], [], {}
        for i, (d, v) in enumerate(pairs):
            if _sandwiched_zero(vals, i):
                zeros.append(d)
                continue
            if ceiling is not None and code not in AGGREGATES and v > ceiling:
                f = _confirmed_slip(v, u.get(d), prices.get(d), ceiling)
                if f:
                    slips.setdefault(f, []).append(d)
                    v = v / f
            kept.append((d, v))
        out[code] = kept
        notes += [f"{code} {d[:7]}: 0 between non-zero months dropped (gap coded as zero)"
                  for d in zeros]
        notes += [f"{code} {ds[0][:7]}..{ds[-1][:7]}: /{f:,} over {len(ds)} month(s) "
                  f"(decimal slip, confirmed by the USD value field)"
                  for f, ds in slips.items()]
    return out, notes


def us_ceiling(raw: dict) -> float | None:
    """Highest US level (tonnes) in this batch -- no central bank holds more."""
    block = raw.get(f"{SERIES_PREFIX}{US.lower()}{SERIES_SUFFIX}") or {}
    recs = block.get("records") if block.get("ok") else None
    return max((r["value"] for r in recs or ()), default=None)


def check(series_id: str, records: list, ceiling: float | None) -> list[str]:
    """Violations in the records about to be written for one series; [] = pass."""
    code = series_id[len(SERIES_PREFIX):-len(SERIES_SUFFIX)].upper()
    recs = sorted(records, key=lambda r: r["as_of"])
    vals = [r["value"] for r in recs]
    bad = []
    if code not in AGGREGATES:          # the Euro Area sum legitimately tops the US
        if ceiling is None:
            bad.append("no US level in this batch to bound it")
        else:
            above = [r["as_of"] for r in recs if r["value"] > ceiling]
            if above:
                bad.append(f"{len(above)} month(s) above the US level {ceiling:,.1f} t "
                           f"({above[0]}..{above[-1]})")
    zeros = [recs[i]["as_of"] for i in range(len(recs)) if _sandwiched_zero(vals, i)]
    if zeros:
        bad.append(f"{len(zeros)} zero(s) between non-zero months ({', '.join(zeros[:3])})")
    steps = [(recs[i]["as_of"], f) for i in range(1, len(recs))
             if (f := _slip_factor(vals[i - 1], vals[i]))]
    if steps:
        bad.append(f"{len(steps)} x{steps[0][1]:,} step(s) (first at {steps[0][0]})")
    return bad


def gate(raw: dict) -> dict:
    """{series_id: [violations]} for every series in raw that fails check()."""
    ceiling = us_ceiling(raw)
    out = {}
    for sid, block in raw.items():
        recs = block.get("records") if block.get("ok") else None
        if recs and (bad := check(sid, recs, ceiling)):
            out[sid] = bad
    return out

"""Mock COT markets for offline end-to-end runs (no network).

Returns the citizen shape {key: {"ok", "rows"}}. Covers the three cases the
identity guard must handle:
  - gold   : clean single-contract history -> writes.
  - natgas : DECLARED splice (NATURAL GAS -> HENRY HUB rename) -> marked + written
             whole (mark-don't-clean).
  - silver : clean -> writes.
The undeclared-splice REJECT case is exercised by tampered_mixed() below.
"""
from __future__ import annotations


def _series(name, start_net, n, step=500):
    rows = []
    for i in range(n):
        net = start_net + (i % 20) * step
        # invariant-respecting synthetic cohorts (long - short == net), so the
        # offline run exercises the full S13c record shape, not just primary_net.
        p_short = 20_000
        s_net, s_short = -net // 2, 15_000
        t_net, t_short = net // 4, 8_000
        rows.append({"date": f"2016-{i:04d}", "market_name": name,
                     "open_interest": 100_000 + i,
                     "primary_long": p_short + net, "primary_short": p_short, "primary_net": net,
                     "secondary_long": s_short + s_net, "secondary_short": s_short, "secondary_net": s_net,
                     "tertiary_long": t_short + t_net, "tertiary_short": t_short, "tertiary_net": t_net})
    return rows


def raw(cfg=None) -> dict:
    gold = "GOLD - COMMODITY EXCHANGE INC."
    natgas_old = "NATURAL GAS - NEW YORK MERCANTILE EXCHANGE"
    natgas_new = "HENRY HUB - NEW YORK MERCANTILE EXCHANGE"
    silver = "SILVER - COMMODITY EXCHANGE INC."
    return {
        "gold": {"ok": True, "rows": _series(gold, 50_000, 60)},
        "silver": {"ok": True, "rows": _series(silver, -10_000, 60)},
        # declared splice: 40 old-contract weeks then 20 new-contract weeks
        "natgas": {"ok": True,
                   "rows": _series(natgas_old, -30_000, 40) + _series(natgas_new, 20_000, 20)},
    }


def tampered_mixed() -> dict:
    """A CLEAN key (gold) whose rows secretly carry two identities — must REJECT."""
    a = "GOLD - COMMODITY EXCHANGE INC."
    b = "GOLD MINI - SOME OTHER EXCHANGE"
    return {"gold": {"ok": True, "rows": _series(a, 50_000, 30) + _series(b, 10_000, 10)}}

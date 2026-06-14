"""Синтетични данни, приближаващи състоянието към 11 юни 2026 — за тест/демо."""
from __future__ import annotations
import datetime as dt
import math
import random

random.seed(11)


def _days(n: int):
    today = dt.date.today()
    return [(today - dt.timedelta(days=n - i)).strftime("%Y-%m-%d") for i in range(n)]


def _fridays(n: int):
    d = dt.date.today()
    while d.weekday() != 4:
        d -= dt.timedelta(days=1)
    return [(d - dt.timedelta(weeks=n - 1 - i)).strftime("%Y-%m-%d") for i in range(n)]


def raw(cfg: dict) -> dict:
    days = _days(240)
    # WTI: предвоенна база ~65, военен режим 80–117, компресия към ~89
    wti, level = [], 65.0
    for i, d in enumerate(days):
        if i < 80:
            level = 65 + random.uniform(-1.5, 1.5)
        elif i < 110:
            level = min(112.0, level + random.uniform(0, 3.5))
        else:
            anchor = 112 - (i - 110) * 0.18
            level = max(86.0, anchor + 9 * math.sin(i / 9) + random.uniform(-2, 2))
        wti.append((d, round(level, 2)))
    wti[-1] = (wti[-1][0], 88.96)

    spread = [(d, round(max(0.2, 0.4 + (0 if i < 80 else min(1.05, (i - 80) * 0.02))
                            + random.uniform(-0.12, 0.12)), 3))
              for i, (d, _) in enumerate(wti)]

    weeks = _fridays(16)
    pct = [88, 84, 22, 9, 6, 11, 18, 24, 30, 34, 38, 41, 43, 44, 46, 44]
    weekly_pct = list(zip(weeks, [float(v) for v in pct]))

    dev_weeks = _fridays(26)
    dev = [round(random.uniform(-1, 1), 2) for _ in range(12)] + \
          [-2.1, -3.4, -2.8, -4.2, -3.1, -3.8, -2.9, -4.5, -3.6, -3.2, -4.1, -3.3, -3.9, -3.4][:14]
    deviations = list(zip(dev_weeks, dev))

    cot_weeks = _fridays(52)
    pctl = [round(55 + 25 * math.sin(i / 6), 1) for i in range(40)] + \
           [38.0, 30.0, 24.0, 18.0, 14.0, 12.0, 13.0, 15.0, 14.0, 16.0, 15.0, 17.0]
    pctile_series = list(zip(cot_weeks, pctl))

    bw = []
    for i, (d, _) in enumerate(wti):
        if i < 80:
            v = 3.5 + random.uniform(-0.3, 0.3)
        elif i < 130:
            v = 3.5 + (i - 80) * 0.15 + random.uniform(-0.5, 0.5)
        else:
            v = max(4.6, 11.0 - (i - 130) * 0.05 + random.uniform(-0.4, 0.4))
        bw.append((d, round(v, 2)))
    bw[-1] = (bw[-1][0], 5.70)

    return {
        "prices": {"ok": True, "wti_closes": wti, "wti_last": 88.96,
                   "bw_spread_series": bw, "bw_last": 5.70,
                   "m1_ticker": "BZQ26.NYM", "m2_ticker": "BZU26.NYM",
                   "spread_series": spread, "spread_last": spread[-1][1],
                   "brent_m1_last": 93.4},
        "hormuz": {"ok": True, "baseline_tankers_per_day": 76.0,
                   "last_7d_pct": 44.0, "weekly_pct": weekly_pct,
                   "daily_tail": []},
        "eia": {"ok": True, "crude_last_mbbl": 388.4, "cushing_last_mbbl": 21.7,
                "last_change_mbbl": -7.23, "deviations_mbbl": deviations,
                "consecutive_draws": 7},
        "cot": {"ok": True, "net_last": 142387, "pctile_last": 17.0,
                "pctile_2w_ago": 14.0, "pctile_4w_ago": 15.0,
                "pctile_series": pctile_series,
                "report_date": _fridays(1)[0]},
    }

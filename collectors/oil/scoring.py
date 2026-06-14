"""Скоринг: превръща данните в състояния ✅/⚪/❌ и композитна присъда.

Състояния на серия: BULL (✅ доказателство), BEAR (❌ опровержение),
NEUTRAL (⚪ изчакване), NODATA (—).
Композит: BULL при >= bull_needed от активните серии, задължително
включващи S1 или S2; BEAR при >= bear_needed; иначе NEUTRAL.
Ценовите фалсификатори имат последната дума.
"""
from __future__ import annotations

BG = {"BULL": "✅ доказателство", "BEAR": "❌ опровержение",
      "NEUTRAL": "⚪ изчакване", "NODATA": "— няма данни"}


def score_s1(prices: dict, cfg: dict) -> dict:
    c = cfg["series1_spread"]
    if not prices.get("ok"):
        return {"state": "NODATA", "detail": prices.get("error", "")}
    series = [v for _, v in prices["spread_series"]]
    last_n = series[-c["persistence_days"]:]
    spread = prices["spread_last"]
    if len(last_n) >= c["persistence_days"] and min(last_n) >= c["bull_threshold"]:
        state = "BULL"
    elif spread <= c["bear_threshold"]:
        state = "BEAR"
    else:
        state = "NEUTRAL"
    return {"state": state,
            "value": f"M1−M2 = {spread:+.2f} $/б ({prices['m1_ticker']}−{prices['m2_ticker']})",
            "detail": f"праг ✅ ≥ {c['bull_threshold']:.2f} устойчиво {c['persistence_days']} дни; ❌ ≤ {c['bear_threshold']:.2f}"}


def score_s2(hz: dict, cfg: dict) -> dict:
    c = cfg["series2_hormuz"]
    if not hz.get("ok"):
        return {"state": "NODATA", "detail": hz.get("error", "")}
    weekly = [v for _, v in hz["weekly_pct"]]
    state = "NEUTRAL"
    if len(weekly) >= c["bull_weeks"] and all(v < c["bull_pct_of_baseline"] for v in weekly[-c["bull_weeks"]:]):
        state = "BULL"
    elif len(weekly) >= c["bear_weeks"] and all(v > c["bear_pct_of_baseline"] for v in weekly[-c["bear_weeks"]:]):
        state = "BEAR"
    return {"state": state,
            "value": f"7-дн. транзити: {hz['last_7d_pct']:.0f}% от предвоенната база ({hz['baseline_tankers_per_day']:.0f} танкера/ден)",
            "detail": f"✅ < {c['bull_pct_of_baseline']}% за {c['bull_weeks']} седм.; ❌ > {c['bear_pct_of_baseline']}% за {c['bear_weeks']} седм."}


def score_s3(eia: dict, cfg: dict) -> dict:
    c = cfg["series3_eia"]
    if not eia.get("ok"):
        return {"state": "NODATA", "detail": eia.get("error", "")}
    dev = [v for _, v in eia["deviations_mbbl"]]
    w = c["bull_window_weeks"]
    avg_dev = sum(dev[-w:]) / w if len(dev) >= w else 0.0
    builds = 0
    for v in reversed(dev):
        if v > 0:
            builds += 1
        else:
            break
    if len(dev) >= w and avg_dev <= c["bull_avg_deviation_mbbl"]:
        state = "BULL"
    elif builds >= c["bear_consecutive_builds"]:
        state = "BEAR"
    else:
        state = "NEUTRAL"
    return {"state": state,
            "value": (f"посл. промяна {eia['last_change_mbbl']:+.1f} млн.; "
                      f"{eia['consecutive_draws']} поредни изтегляния; "
                      f"откл. от нормата ({w} седм. ср.): {avg_dev:+.1f} млн./седм."),
            "detail": f"✅ ср. отклонение ≤ {c['bull_avg_deviation_mbbl']:.0f} млн.; ❌ {c['bear_consecutive_builds']} поредни builds над нормата. Cushing: {eia['cushing_last_mbbl']:.1f} млн. б."}


def score_s5(cot: dict, cfg: dict, others_tight: bool) -> dict:
    c = cfg["series5_cot"]
    if not cot.get("ok"):
        return {"state": "NODATA", "detail": cot.get("error", "")}
    p_now, p_2w, p_4w = cot["pctile_last"], cot["pctile_2w_ago"], cot["pctile_4w_ago"]
    state = "NEUTRAL"
    if (p_2w is not None and p_2w < c["bull_from_below_pctile"]
            and p_now - p_2w >= c["bull_pctile_jump"]):
        state = "BULL"
    elif (others_tight and p_4w is not None
          and abs(p_now - p_4w) < c["bear_flat_band"]):
        state = "BEAR"
    return {"state": state,
            "value": f"MM net {cot['net_last']:,} к-та; {p_now:.0f}-и персентил (преди 2 седм.: {p_2w:.0f}-и)".replace(",", " "),
            "detail": f"✅ скок ≥ +{c['bull_pctile_jump']} п.п. за 2 седм. от база < {c['bull_from_below_pctile']}-и; ❌ флат при физическо затягане. Доклад: {cot['report_date']}"}


def score_s6(prices: dict, cfg: dict) -> dict:
    c = cfg["series6_canary"]
    if not prices.get("ok") or "bw_spread_series" not in prices:
        return {"state": "NODATA", "detail": prices.get("error", "няма Brent флат данни")}
    series = [v for _, v in prices["bw_spread_series"]]
    last_n = series[-c["persistence_days"]:]
    spr = prices["bw_last"]
    if len(last_n) >= c["persistence_days"] and min(last_n) >= c["bull_threshold"]:
        state = "BULL"
    elif spr <= c["bear_threshold"]:
        state = "BEAR"
    else:
        state = "NEUTRAL"
    return {"state": state,
            "value": f"Brent−WTI = {spr:+.2f} $/б (предвоенно ~3.5; априлски пик ~11)",
            "detail": f"✅ ≥ {c['bull_threshold']:.0f} устойчиво {c['persistence_days']} дни — вносният регион се задушава; ❌ ≤ {c['bear_threshold']:.0f} — атлантическият басейн се справя"}


def falsifier(prices: dict, cfg: dict) -> dict:
    f = cfg["falsifier"]
    if not prices.get("ok"):
        return {"dead": False, "confirm": False, "text": "няма ценови данни"}
    closes = [v for _, v in prices["wti_closes"]]
    below = _streak(closes, lambda v: v < f["dead_below"])
    above = _streak(closes, lambda v: v > f["confirm_above"])
    dead = below >= f["dead_consecutive_closes"]
    confirm = above >= f["confirm_consecutive_closes"]
    text = (f"WTI {prices['wti_last']:.2f}. Под {f['dead_below']:.0f}: {below}/{f['dead_consecutive_closes']} затваряния · "
            f"Над {f['confirm_above']:.0f}: {above}/{f['confirm_consecutive_closes']} затваряния")
    return {"dead": dead, "confirm": confirm, "text": text}


def _streak(vals: list, pred) -> int:
    n = 0
    for v in reversed(vals):
        if pred(v):
            n += 1
        else:
            break
    return n


def composite(scores: dict, fals: dict, cfg: dict) -> dict:
    c = cfg["composite"]
    active = {k: v for k, v in scores.items() if v["state"] != "NODATA"}
    bulls = [k for k, v in active.items() if v["state"] == "BULL"]
    bears = [k for k, v in active.items() if v["state"] == "BEAR"]

    if fals["dead"]:
        verdict, label = "DEAD", "ТЕЗАТА ОПРОВЕРГАНА — военната премия се топи"
    elif len(bulls) >= c["bull_needed"] and any(k in bulls for k in c["bull_must_include"]):
        verdict, label = "PROVEN", "ТЕЗАТА ДОКАЗАНА — очаквай гап, не тренд"
    elif len(bears) >= c["bear_needed"]:
        verdict, label = "DEAD", "ТЕЗАТА ОПРОВЕРГАНА — диапазонът се разпада"
    else:
        verdict, label = "WAIT", "ИЗЧАКВАНЕ — пружината се навива"

    if fals["confirm"] and verdict == "WAIT":
        label += " · ценово потвърждение в ход (WTI > 95)"
    return {"verdict": verdict, "label": label,
            "bulls": len(bulls), "bears": len(bears),
            "active": len(active), "nodata": len(scores) - len(active)}

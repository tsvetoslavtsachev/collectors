# -*- coding: utf-8 -*-
"""export_signals: signals/current.json за signals-registry (M2, 25.09.2026; SR1 решенията, 25.09.2026).

Чете data/state/vrm_overlay.json и data/state/vrm_ks_state.json от DATACORE_ROOT (последния
запис на всеки) и пише DATACORE_ROOT/signals/current.json: масив по схемата 0.1 на регистъра:
  1. vrm-core        state = режимът, persistence = поредни седмици в него
  2. vrm-mid         state = „N/8“ (alignment_score), score 0-8; допълнително поле tercile_label
                     по правилото Б2р от SR1 (терцили от миналите седмици на режима, поне 26,
                     иначе фиксирани 0-2 / 3-5 / 6-8); регистърът го пази, не го проверява
  3. kill-switch     state = ON/OFF от active
  4. dollar-correlation (ако има PRICE_ARCHIVE_ROOT): 60-дневна корелация на двудневните лог
                     доходности DXY и SPY от price-archive; праг 0,2; новото състояние влиза
                     след 4 поредни седмици; обикновената дневна мярка се носи като corr_60d_daily
                     (решение на Ц. 25.09.2026 по доклада SR1, вариант 2)

Правила:
- Само чете state и цени. Не пише числа в канона, не ходи в мрежата, не ползва writer lib-а.
- Детерминистичен спрямо входа; единственото променливо поле е generated_at.
- Никога не проваля рън-а на храната: при грешка в доларовия обект пише трите VRM обекта и
  предупреждава; при грешка във VRM обектите не пише нищо, предупреждава и излиза с 0 (старият
  файл остава). С --strict (локална проверка) всяка грешка е изход 1.

Run:  python -m collectors.vrm.export_signals [--root PATH] [--price-archive PATH] [--out PATH] [--strict]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "0.1"
OVERLAY_REL = Path("data") / "state" / "vrm_overlay.json"
KS_REL = Path("data") / "state" / "vrm_ks_state.json"
OUT_REL = Path("signals") / "current.json"
SOURCE_BASE = "https://github.com/tsvetoslavtsachev/data-core/blob/main/data/state/"
REGIMES = ("GROWTH", "REFLATION", "STAGNATION", "CRISIS", "DEFLATION")
REGIME_BG = {"GROWTH": "Растеж", "REFLATION": "Рефлация", "STAGNATION": "Стагнация",
             "CRISIS": "Криза", "DEFLATION": "Дефлация"}
FLAG_ORDER = ("OK", "WATCH", "UNDER", "OVER")

# MID: правилото Б2р от SR1 (mid/rules.py: bin_b1, tertile_cuts, b2_rt)
TERCILE_MIN_PAST = 26
CONTRADICTS, MIXED, CONFIRMS = "противоречи", "смесено", "потвърждава"

# Доларът: SR1 dollar/corr.py (roll_corr_2d, to_friday) и dollar/regimes.py (raw_state, 4w)
DOLLAR_WINDOW = 60
DOLLAR_THRESHOLD = 0.2
DOLLAR_PERSIST_WEEKS = 4
SAFE, NEUT, RISK = "УБЕЖИЩЕ", "НЕУТРАЛНО", "РИСКОВ АКТИВ"
PA_DXY_REL = Path("archive") / "px_dxy_daily"
PA_SPY_REL = Path("archive") / "px_spy_daily"
PA_URL = "https://github.com/tsvetoslavtsachev/price-archive"
PA_SOURCE = "price-archive px_dxy_daily (DX-Y.NYB) + px_spy_daily (SPY), yfinance"


# ---------------------------------------------------------------- общи

def load_records(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list) and data:
        return data
    raise ValueError(f"{path}: празен или неочакван JSON")


def trailing_run(records: list, key: str) -> int:
    """Колко последни записа (вкл. последния) имат същата стойност на key."""
    if not records:
        return 0
    last = records[-1].get(key)
    n = 0
    for rec in reversed(records):
        if rec.get(key) != last:
            break
        n += 1
    return n


def fmt_pct(value) -> str:
    return "n/a" if value is None else f"{value:+.2f}%".replace(".", ",")


def fmt_num(value, digits=2) -> str:
    return "n/a" if value is None else f"{value:+.{digits}f}".replace(".", ",")


# ---------------------------------------------------------------- MID: Б2р

def bin_fixed(score: int) -> str:
    return CONTRADICTS if score <= 2 else (MIXED if score <= 5 else CONFIRMS)


def tertile_cuts(scores) -> tuple[int, int]:
    """Долен праг a (най-близкият до долната третина отдолу) и горен b (до горната третина отгоре).
    Същата дефиниция като SR1 mid/rules.py tertile_cuts (numpy-версията), без numpy."""
    x = [int(v) for v in scores]
    n = len(x)
    if n == 0:
        raise ValueError("tertile_cuts: празен списък")

    def share_le(c):
        return sum(1 for v in x if v <= c) / n

    def share_ge(c):
        return sum(1 for v in x if v >= c) / n

    a = min(range(0, 8), key=lambda c: abs(share_le(c) - 1 / 3))
    b = min(range(a + 1, 9), key=lambda c: abs(share_ge(c) - 1 / 3))
    return a, b


def tercile_label(overlay: list) -> dict:
    """Кошчето по Б2р за последния запис: праговете само от миналите седмици на същия режим
    (поне 26), иначе фиксираните кошчета. Описателно поле, не state."""
    last = overlay[-1]
    score, regime = last.get("alignment_score"), last.get("regime")
    if not isinstance(score, int) or isinstance(score, bool):
        return {"tercile_label": None, "tercile_cuts": None, "tercile_rule": "n/a"}
    past = [r.get("alignment_score") for r in overlay[:-1]
            if r.get("regime") == regime and isinstance(r.get("alignment_score"), int)]
    if len(past) < TERCILE_MIN_PAST:
        return {"tercile_label": bin_fixed(score), "tercile_cuts": None,
                "tercile_rule": f"фиксирани 0-2 / 3-5 / 6-8 (под {TERCILE_MIN_PAST} минали седмици в режима)"}
    a, b = tertile_cuts(past)
    label = CONTRADICTS if score <= a else (CONFIRMS if score >= b else MIXED)
    return {"tercile_label": label, "tercile_cuts": [a, b],
            "tercile_rule": f"Б2р: терцили от {len(past)} минали седмици в режима {regime}"}


# ---------------------------------------------------------------- VRM обектите

def build_signals(overlay: list, ks: list, generated_at: str) -> list[dict]:
    ov = overlay[-1]
    k = ks[-1]
    regime = ov.get("regime")
    if regime not in REGIMES:
        raise ValueError(f"vrm_overlay: непознат режим {regime!r}")
    as_of = ov.get("as_of")
    if not isinstance(as_of, str):
        raise ValueError("vrm_overlay: липсва as_of")
    persistence = trailing_run(overlay, "regime")
    flags = ov.get("alignment_flags") or {}
    counts = {f: sum(1 for val in flags.values() if val == f) for f in FLAG_ORDER}
    align = ov.get("alignment_score")
    gms = ov.get("gms") or {}
    c4 = ov.get("cumulative_4w") or {}
    terc = tercile_label(overlay)

    vrm_core = {
        "module": "vrm-core",
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of,
        "generated_at": generated_at,
        "state": regime,
        "score": None,
        "scale": None,
        "confidence": None,
        "falsified": False,
        "persistence": persistence,
        "notes": f"Режим {REGIME_BG[regime]}, {persistence} поредни седмици към {as_of}; "
                 f"съгласуваност на пазара {align}/8.",
        "source_url": SOURCE_BASE + "vrm_overlay.json",
        "z_method": ov.get("z_method"),
        "provisional": bool(ov.get("provisional", False)),
        "velocity": ov.get("velocity"),
    }

    vrm_mid = {
        "module": "vrm-mid",
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of,
        "generated_at": generated_at,
        # Решение на Ц. (25.09.2026, SR1 вариант 1): state е самото число като „N/8“;
        # кошчето по Б2р е описателно поле по-долу.
        "state": f"{align}/8" if isinstance(align, int) else "n/a",
        "score": align if isinstance(align, int) else None,
        "scale": [0, 8],
        "confidence": None,
        "falsified": False,
        "persistence": None,
        "notes": f"Съгласуваност {align}/8 при режим {REGIME_BG[regime]}: "
                 f"{counts['OK']} съвпадат, {counts['WATCH']} гранични, {counts['UNDER']} по-слаби, "
                 f"{counts['OVER']} по-силни от очакваното; кошче по Б2р: {terc['tercile_label']}.",
        "source_url": SOURCE_BASE + "vrm_overlay.json",
        "regime": regime,
        "alignment_flags": flags,
        "gms": {"score": gms.get("score"), "max": gms.get("max"), "tier": gms.get("tier")},
        **terc,
    }

    active = bool(k.get("active"))
    applicable = k.get("applicable")
    ks_as_of = k.get("as_of") if isinstance(k.get("as_of"), str) else as_of
    spy = k.get("spy_pct", c4.get("spy_pct"))
    threshold = c4.get("threshold_pct")
    margin = c4.get("margin_pct")
    if applicable is False:
        ks_note = f"Kill Switch не се прилага в режим {REGIME_BG.get(k.get('regime'), k.get('regime'))}."
    else:
        ks_note = (f"Kill Switch {'активен' if active else 'неактивен'}; SPY за 4 седмици {fmt_pct(spy)} "
                   f"при праг {fmt_pct(threshold)}, резерв {fmt_pct(margin)}.")
    kill_switch = {
        "module": "kill-switch",
        "schema_version": SCHEMA_VERSION,
        "as_of": ks_as_of,
        "generated_at": generated_at,
        "state": "ON" if active else "OFF",
        "score": None,
        "scale": None,
        "confidence": None,
        "falsified": False,
        "persistence": k.get("weeks_in") if isinstance(k.get("weeks_in"), int) else None,
        "notes": ks_note,
        "source_url": SOURCE_BASE + "vrm_ks_state.json",
        "applicable": applicable,
        "variant": k.get("variant"),
        "phase": k.get("phase"),
        "weeks_in": k.get("weeks_in"),
        "since": k.get("since"),
        "regime": k.get("regime"),
        "spy_4w_pct": spy,
        "threshold_pct": threshold,
        "margin_pct": margin,
    }
    return [vrm_core, vrm_mid, kill_switch]


# ---------------------------------------------------------------- доларът

def load_price_archive_series(root: Path, rel: Path):
    """Дневна серия от price-archive (archive/<series>/<година>.jsonl): без provisional редове,
    последният запис за всяка дата (bitemporal), само положителни цени. Както SR1 load_pa."""
    import pandas as pd

    files = sorted((root / rel).glob("*.jsonl"))
    if not files:
        raise FileNotFoundError(f"{root / rel}: няма *.jsonl файлове")
    rows = []
    for f in files:
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rows.append(json.loads(line))
    df = pd.DataFrame(rows)
    df = df[~df["provisional"].astype(bool)]
    df = df.sort_values(["as_of", "recorded_on"]).drop_duplicates("as_of", keep="last")
    s = pd.Series(df["value"].astype(float).values, index=pd.to_datetime(df["as_of"]))
    return s[s > 0].sort_index()


def rolling_corr(a, b, window: int, step: int):
    """Подвижна корелация на лог доходностите със стъпка step дни (1 = дневни, 2 = двудневни,
    припокриващи се), върху общите търговски дни. Както SR1 roll_corr / roll_corr_2d."""
    import numpy as np
    import pandas as pd

    j = pd.concat([a, b], axis=1, join="inner").dropna()
    r = np.log(j).diff(step).dropna()
    return r.iloc[:, 0].rolling(window, min_periods=window).corr(r.iloc[:, 1])


def to_friday(series, fridays):
    """Стойността към всеки петък = последната налична на или преди петъка; по-стара от 7 дни
    остава празна. Както SR1 to_friday."""
    import numpy as np
    import pandas as pd

    s = series.dropna()
    idx = s.index.searchsorted(fridays, side="right") - 1
    vals = np.where(idx >= 0, s.values[np.clip(idx, 0, None)], np.nan)
    last_date = np.where(idx >= 0, s.index.values[np.clip(idx, 0, None)], np.datetime64("NaT", "ns"))
    out = pd.Series(vals, index=fridays)
    stale = (fridays.values - last_date) > np.timedelta64(7, "D")
    out[stale] = np.nan
    return out


def raw_state(c: float, p: float = DOLLAR_THRESHOLD) -> str:
    return SAFE if c < -p else (RISK if c > p else NEUT)


def apply_persist_4w(raw: list) -> list:
    """Новото сурово състояние влиза след 4 поредни седмици. Както SR1 apply_persist(mode='4w')."""
    out = [raw[0]]
    run_x, run_n = raw[0], 1
    for t in range(1, len(raw)):
        run_n = run_n + 1 if raw[t] == run_x else 1
        run_x = raw[t]
        out.append(run_x if run_n >= DOLLAR_PERSIST_WEEKS else out[-1])
    return out


def dollar_signal(price_archive: Path, generated_at: str) -> dict:
    import numpy as np
    import pandas as pd

    dxy = load_price_archive_series(price_archive, PA_DXY_REL)
    spy = load_price_archive_series(price_archive, PA_SPY_REL)
    c2 = rolling_corr(dxy, spy, DOLLAR_WINDOW, 2)
    c1 = rolling_corr(dxy, spy, DOLLAR_WINDOW, 1)
    valid = c2.dropna()
    if valid.empty:
        raise ValueError("dollar-correlation: няма пълен 60-дневен прозорец")
    fridays = pd.date_range(valid.index[0], valid.index[-1], freq="W-FRI")
    w2 = to_friday(c2, fridays).dropna()
    if w2.empty:
        raise ValueError("dollar-correlation: няма петък с пресен прозорец")
    w1 = to_friday(c1, fridays)
    raw = [raw_state(float(c)) for c in w2.values]
    states = apply_persist_4w(raw)
    t = len(states) - 1
    start = t
    while start > 0 and states[start - 1] == states[t]:
        start -= 1
    as_of_ts = w2.index[-1]
    as_of = str(as_of_ts.date())
    score = round(float(w2.iloc[-1]), 4)
    daily = w1.get(as_of_ts, np.nan)
    daily = None if daily is None or np.isnan(daily) else round(float(daily), 4)
    last_day = valid.index[valid.index <= as_of_ts][-1]
    persistence = t - start + 1
    state = states[t]
    return {
        "module": "dollar-correlation",
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of,
        "generated_at": generated_at,
        "state": state,
        "score": score,
        "scale": [-1, 1],
        "confidence": None,
        "falsified": False,
        "persistence": persistence,
        "notes": f"Корелация DXY и S&P 500 за 60 дни (двудневни доходности) {fmt_num(score)}: {state.lower()} "
                 f"от {persistence} седмици. Под -0,2 доларът върви срещу акциите, над +0,2 с тях; новото "
                 f"състояние влиза след 4 поредни седмици. Дневната мярка: {fmt_num(daily)}.",
        "source_url": PA_URL,
        "window_days": DOLLAR_WINDOW,
        "returns": "двудневни лог доходности, припокриващи се, общи търговски дни",
        "threshold": DOLLAR_THRESHOLD,
        "persistence_rule": f"{DOLLAR_PERSIST_WEEKS} поредни седмици в новото сурово състояние",
        "raw_state": raw[-1],
        "since": str(w2.index[start].date()),
        "corr_60d_daily": daily,
        "n_days_in_window": DOLLAR_WINDOW,
        "last_price_day": str(last_day.date()),
        "price_source": PA_SOURCE,
        "decision": "Ц., 25.09.2026, по SR1 (вариант 2: data-core, двудневни доходности, праг 0,2, 4 седмици)",
    }


# ---------------------------------------------------------------- запис

def export(root: Path, out: Path | None = None, now: datetime | None = None,
           price_archive: Path | None = None) -> tuple[Path, list[str]]:
    """Пише файла; връща (път, предупреждения). Грешка във VRM обектите се вдига нагоре;
    грешка в доларовия обект само се записва като предупреждение."""
    overlay = load_records(root / OVERLAY_REL)
    ks = load_records(root / KS_REL)
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    signals = build_signals(overlay, ks, stamp)
    warnings: list[str] = []
    if price_archive is None:
        warnings.append("dollar-correlation пропуснат: няма PRICE_ARCHIVE_ROOT / --price-archive")
    else:
        try:
            signals.append(dollar_signal(Path(price_archive), stamp))
        except Exception as exc:  # noqa: BLE001  (доларът не бива да спира VRM обектите)
            warnings.append(f"dollar-correlation пропуснат: {type(exc).__name__}: {exc}")
    target = out or (root / OUT_REL)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(signals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return target, warnings


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Пише signals/current.json за signals-registry от VRM state и price-archive.")
    ap.add_argument("--root", default=os.environ.get("DATACORE_ROOT"), help="коренът на data-core (DATACORE_ROOT)")
    ap.add_argument("--price-archive", default=os.environ.get("PRICE_ARCHIVE_ROOT"),
                    help="коренът на price-archive (PRICE_ARCHIVE_ROOT); без него доларовият обект се пропуска")
    ap.add_argument("--out", help="друг изходен път (по подразбиране <root>/signals/current.json)")
    ap.add_argument("--strict", action="store_true", help="грешка или предупреждение = изход 1 (за локална проверка)")
    args = ap.parse_args(argv)
    try:
        if not args.root:
            raise ValueError("няма DATACORE_ROOT и няма --root")
        target, warnings = export(Path(args.root), Path(args.out) if args.out else None,
                                  price_archive=Path(args.price_archive) if args.price_archive else None)
        signals = json.loads(target.read_text(encoding="utf-8"))
        summary = " · ".join(f"{s['module']}={s['state']}" for s in signals)
        print(f"export_signals: {target} ({summary}, as_of {signals[0]['as_of']})")
        for w in warnings:
            print(f"::warning::export_signals: {w}")
        return 1 if (warnings and args.strict) else 0
    except Exception as exc:  # noqa: BLE001  (никога не събаряме храната на VRM)
        print(f"::warning::export_signals пропусна записа: {type(exc).__name__}: {exc}")
        return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())

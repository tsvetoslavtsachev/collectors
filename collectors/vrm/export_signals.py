# -*- coding: utf-8 -*-
"""export_signals: signals/current.json за signals-registry (M2, 25.09.2026).

Чете data/state/vrm_overlay.json и data/state/vrm_ks_state.json от DATACORE_ROOT (последния
запис на всеки) и пише DATACORE_ROOT/signals/current.json: масив от три обекта по схемата 0.1
на регистъра (vrm-core, vrm-mid, kill-switch). Регистърът дърпа файла в събота; това repo не
знае нищо за него и не държи секрети за него.

Правила:
- Само чете state. Не пише числа в канона, не ходи в мрежата, не ползва writer lib-а.
- Детерминистичен спрямо входа; единственото променливо поле е generated_at.
- Никога не проваля рън-а на храната: при грешка печата ::warning:: и излиза с 0, старият
  файл остава. С --strict (локална проверка) грешката е изход 1.

Run:  python -m collectors.vrm.export_signals [--root PATH] [--out PATH] [--strict]
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
        # Категоричен етикет за MID още няма (праговете чакат автора); дотогава state носи
        # самата стойност като „N/8“, за да е четимо и да не измисля прагове.
        "state": f"{align}/8" if isinstance(align, int) else "n/a",
        "score": align if isinstance(align, int) else None,
        "scale": [0, 8],
        "confidence": None,
        "falsified": False,
        "persistence": None,
        "notes": f"Съгласуваност {align}/8 при режим {REGIME_BG[regime]}: "
                 f"{counts['OK']} съвпадат, {counts['WATCH']} гранични, {counts['UNDER']} по-слаби, "
                 f"{counts['OVER']} по-силни от очакваното.",
        "source_url": SOURCE_BASE + "vrm_overlay.json",
        "regime": regime,
        "alignment_flags": flags,
        "gms": {"score": gms.get("score"), "max": gms.get("max"), "tier": gms.get("tier")},
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


def export(root: Path, out: Path | None = None, now: datetime | None = None) -> Path:
    overlay = load_records(root / OVERLAY_REL)
    ks = load_records(root / KS_REL)
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    signals = build_signals(overlay, ks, stamp)
    target = out or (root / OUT_REL)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(signals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return target


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Пише signals/current.json за signals-registry от VRM state.")
    ap.add_argument("--root", default=os.environ.get("DATACORE_ROOT"), help="коренът на data-core (DATACORE_ROOT)")
    ap.add_argument("--out", help="друг изходен път (по подразбиране <root>/signals/current.json)")
    ap.add_argument("--strict", action="store_true", help="грешка = изход 1 (за локална проверка)")
    args = ap.parse_args(argv)
    try:
        if not args.root:
            raise ValueError("няма DATACORE_ROOT и няма --root")
        target = export(Path(args.root), Path(args.out) if args.out else None)
        signals = json.loads(target.read_text(encoding="utf-8"))
        summary = " · ".join(f"{s['module']}={s['state']}" for s in signals)
        print(f"export_signals: {target} ({summary}, as_of {signals[0]['as_of']})")
        return 0
    except Exception as exc:  # noqa: BLE001  (никога не събаряме храната на VRM)
        print(f"::warning::export_signals пропусна записа: {type(exc).__name__}: {exc}")
        return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())

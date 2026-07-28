# -*- coding: utf-8 -*-
"""Мандат №44 (26.07.2026) — пазачът на ценовия слой (write-boundary).

Болестите, доказани при №42/№44 диагностиката (data-core 7f48416 <-> a1a3a21):

(а) ФРОНТОВИЯТ РЕД се разминава между пусковете — петъчното VNQ close 100.81 ->
    98.69 (-2.1%) между двата съботни пуска: лош първи принт, после сетълнат от
    източника. Точно това обърна публикувания VRM ред 24.07 (alignment 4->5,
    REAL_ESTATE OVER->WATCH) и роди PIT freeze-а на деривата (№42).
(б) value_tr (adjusted/TR базата) се РЕСКЕЙЛВА по цялата история — истински при
    всеки нов дивидент, но и с епсилон-шум в 5-6-ия знак при всеки пуск
    (21.399103 -> 21.399094) -> ~24k-редови дифове, в които реалните промени
    се давят и нито една не е декларирана.

Пазачът (ползван от vrm И oil to_datacore) НЕ замразява сурови факти — канонът
остава „последната истина на източника" (дериватният канон се пази от №42).
Той прави три неща:

1. ROUND  — детерминистично закръгляне до PRICE_DECIMALS на write границата:
            убива епсилон-шума; тихият пуск става байт-идентичен; реалните
            дивидентни рескейли (3-4-ти знак и нагоре) остават видими.
2. DECLARE — всяка промяна спрямо съществуващия канон се декларира в ledger-а
            health/price_revisions_<collector>.json (чиста функция на
            (стар канон, нов fetch), без wallclock; пътува в git с канона).
            Ledger-ът е ЛИЦЕТО на последния пуск и се презаписва — затова
            декларациите се дописват и в append-only журнала
            health/price_revisions_<collector>_journal.jsonl (по един ред на
            пуск С декларации, с wallclock): flip-flop-ът 28.07.2026 показа,
            че следващият тих пуск изтрива доказателството от лицето и то
            оцелява само в git историята. Видове декларации:
            - в SETTLE_DAYS от върха: "settled" — нормалното доуточняване на
              пресния бар (поименно);
            - извън прозореца, поле value: поименно — историческа ревизия на
              суров факт е СЪБИТИЕ, не шум;
            - извън прозореца, поле value_tr: обобщение {n, max_rel} —
              рескейлът е очакван по конструкция; интересен е мащабът;
            - серии от клас recomputed-by-construction (recomputed=True; напр.
              COT персентил върху плъзгащ се прозорец — всяка нова седмица
              мести цялата историческа опашка с ~0.1-0.2 п.п.): обобщение
              {n, max_abs} вместо поименен списък — преизчислението е свойство
              на конструкцията, като value_tr рескейла; реална историческа
              ревизия личи по извънреден max_abs.
3. SPLIT-SUSPECT — >= SPLIT_SUSPECT_MIN_ROWS исторически value промени в един
            пуск миришат на сплит/ребейс на източника -> флаг за човешко око.
            НЕ важи за recomputed-by-construction серии — там масовата
            историческа промяна е очакваният режим, не аномалия.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path

PRICE_DECIMALS = 4          # 0.1 цент — под всяка аналитична резолюция на организма
SETTLE_DAYS = 7             # календарни дни назад от върха, в които барът „сетълва"
SPLIT_SUSPECT_MIN_ROWS = 20  # толкова исторически value промени = сплит/ребейс съмнение
NUMERIC_FIELDS = ("value", "value_tr")
_LEDGER_MAX_NAMED = 50      # поименен таван в ledger-а; останалото е в брояча
# Епсилон deadband: живият пуск №1 (26.07) показа, че само закръгляне НЕ стига —
# adjusted факторите на Yahoo варират в 5-6-ия знак ПО ЗАЯВКА, и редове до
# границата на закръгляне пляскат (7-315 реда/серия, max_rel ~e-6). Разлика
# |Δ| <= max(EPSILON_ABS, |old|*EPSILON_REL) е под-аналитична: канонът пази
# старата стойност. Реалните движения (сетълване -2%, дивидент ~1%, сплит ×2)
# минават невредими.
EPSILON_REL = 1e-4
EPSILON_ABS = 1e-4


def round_records(records):
    """Deterministic write-boundary rounding of the numeric price fields."""
    out = []
    for r in records:
        r2 = dict(r)
        for f in NUMERIC_FIELDS:
            v = r2.get(f)
            if isinstance(v, float):
                r2[f] = round(v, PRICE_DECIMALS)
        out.append(r2)
    return out


def apply_deadband(existing, records):
    """Sub-analytic source jitter must not move the canon: for rows present in
    both, a numeric change within the epsilon deadband keeps the OLD value
    (re-rounded to write precision). Deterministic given (existing, records)."""
    if not existing:
        return records
    ex = {r["as_of"]: r for r in existing}
    out = []
    for r in records:
        e = ex.get(r["as_of"])
        if e is None:
            out.append(r)
            continue
        r2 = dict(r)
        for f in NUMERIC_FIELDS:
            old, new = e.get(f), r2.get(f)
            if (isinstance(old, (int, float)) and isinstance(new, (int, float))
                    and abs(new - old) <= max(EPSILON_ABS, abs(old) * EPSILON_REL)):
                r2[f] = round(float(old), PRICE_DECIMALS)
        out.append(r2)
    return out


def declare_revisions(existing, records, recomputed=False):
    """Pure function (existing canon rows, incoming ROUNDED rows) -> declaration
    dict, or None when nothing changed. Existing values are compared at the same
    precision, so the one-off 4dp migration does not read as a mass revision.

    recomputed=True marks a recomputed-by-construction series (e.g. a percentile
    ranked over a sliding window): historical value changes are then a summary
    {n, max_abs} — expected by construction, like the value_tr rescale — and the
    SPLIT-SUSPECT heuristic does not apply. A real historical revision still
    surfaces: it reads as an outsized max_abs. The settle window stays named —
    the frontier is where genuine settling happens."""
    if not existing or not records:
        return None
    ex = {r["as_of"]: r for r in existing}
    tip = max(r["as_of"] for r in records)
    settle_from = (_dt.date.fromisoformat(tip)
                   - _dt.timedelta(days=SETTLE_DAYS)).isoformat()
    settled, hist_value, tr_rel, rec_abs = [], [], [], []
    for r in records:
        e = ex.get(r["as_of"])
        if e is None:
            continue
        for f in NUMERIC_FIELDS:
            if f not in r and f not in e:
                continue
            old, new = e.get(f), r.get(f)
            if isinstance(old, float):
                old = round(old, PRICE_DECIMALS)
            if old == new:
                continue
            item = {"as_of": r["as_of"], "field": f, "old": old, "new": new}
            if r["as_of"] >= settle_from:
                settled.append(item)
            elif f == "value":
                if recomputed:
                    rec_abs.append(abs(new - old)
                                   if isinstance(old, (int, float))
                                   and isinstance(new, (int, float)) else 0.0)
                else:
                    hist_value.append(item)
            else:
                tr_rel.append(abs(new - old) / abs(old) if old else 0.0)
    if not settled and not hist_value and not tr_rel and not rec_abs:
        return None
    decl = {"tip": tip, "settle_window_from": settle_from}
    if settled:
        decl["settled"] = settled[:_LEDGER_MAX_NAMED]
        decl["settled_n"] = len(settled)
    if hist_value:
        decl["historical_value"] = hist_value[:_LEDGER_MAX_NAMED]
        decl["historical_value_n"] = len(hist_value)
        if len(hist_value) >= SPLIT_SUSPECT_MIN_ROWS:
            decl["split_suspect"] = True
    if rec_abs:
        decl["value_recomputed"] = {"n": len(rec_abs),
                                    "max_abs": round(max(rec_abs), 8)}
    if tr_rel:
        decl["value_tr_rescale"] = {"n": len(tr_rel),
                                    "max_rel": round(max(tr_rel), 8)}
    return decl


def datacore_base():
    """The data-core root the writers target (mirrors to_datacore.assert_safe_root:
    DATACORE_ROOT env, else the installed datacore package's repo)."""
    env = os.environ.get("DATACORE_ROOT")
    if env:
        return Path(env).resolve()
    import datacore
    return Path(datacore.__file__).resolve().parent.parent


def write_ledger(collector, declarations, base=None, now=None):
    """health/price_revisions_<collector>.json — written on every real run; an
    empty {} is a meaningful declaration ('nothing moved'). Deterministic: pure
    content, sorted keys, no wallclock.

    Освен лицето: пуск С декларации се дописва като един JSONL ред в
    health/price_revisions_<collector>_journal.jsonl — журналът ПОМНИ
    (лицето се презаписва: flip-flop-ът 28.07.2026 оцеля само в git
    историята, защото следващият тих пуск го изтри от лицето). Тихите
    пускове не пипат журнала, така че остават байт-идентични. Журналът
    е събитиен запис, затова редът носи wallclock (UTC); `now` е
    инжектируем за тестове."""
    base = Path(base) if base else datacore_base()
    health = base / "health"
    health.mkdir(parents=True, exist_ok=True)
    path = health / "price_revisions_{}.json".format(collector)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(declarations, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    if declarations:
        stamp = (now or _dt.datetime.now(_dt.timezone.utc)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        entry = {"run_at": stamp, "declarations": declarations}
        jpath = health / "price_revisions_{}_journal.jsonl".format(collector)
        with open(jpath, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def summarize(decl):
    """One log line per series for the CI log (loud, greppable)."""
    bits = []
    if decl.get("settled_n"):
        bits.append("settled {}".format(decl["settled_n"]))
    if decl.get("historical_value_n"):
        bits.append("HISTORICAL value {}{}".format(
            decl["historical_value_n"],
            " SPLIT-SUSPECT" if decl.get("split_suspect") else ""))
    if decl.get("value_recomputed"):
        v = decl["value_recomputed"]
        bits.append("recomputed n={} max_abs={}".format(v["n"], v["max_abs"]))
    if decl.get("value_tr_rescale"):
        v = decl["value_tr_rescale"]
        bits.append("tr-rescale n={} max_rel={}".format(v["n"], v["max_rel"]))
    return " · ".join(bits)

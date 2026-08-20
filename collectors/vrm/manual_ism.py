"""FEED 4 — ISM manual slot (macro_ism_mfg, macro_ism_services).

ISM PMI is licensed; there is no free FRED equivalent (THE gap, S3). Цветослав
enters the monthly prints by hand (Bloomberg) into a slot file; the collector
reads it, never fetches. A missing/empty slot -> that series skipped (never a
silent zero), so a stale ISM shows red in Health rather than a fake number.

Slot file (collector dir, gitignored — licensed data, not redistributed):
    ism_manual.json = {"macro_ism_mfg":      [{"as_of":"YYYY-MM-DD","value":n}, ...],
                       "macro_ism_services": [...]}

A series may name its own slot via opts.slot_file (same row format, same gitignore
rationale); without it, the shared man["slot_file"] is read. First non-ISM tenant:
macro_mn_ore_cny in manganese_manual.json (INIT-27 план А — the unmarketized gauge,
month-end snap of the weekly Bloomberg prints, vrm_role=observation).

D8 contract, amended 2026-07-02 (session C1+2, decision Д1.2 - "историята е
финална"): provisional=true ONLY on the NEWEST record of each series (the freshly
pasted print, the one at risk of a typo or a late correction); every older print is
final (provisional=false). The old contract flagged EVERY record provisional, which
saturated the flag on all downstream regime state (ISM feeds GROWTH every month) and
made it carry zero information - REVIEW-01 §B2. This is the documented
"historical prints are deemed final" splice (mark, don't clean) the S6c README
anticipated. Set opts.provisional=false in config to force all-final (unchanged).

Provenance: ISM keeps bloomberg_era (= as_of <= the S5 cut), UNLIKE the FRED feed.
A live FRED re-pull is FRED-sourced, so stamping bloomberg_era there would mislabel
provenance (the honest omission, see INVENTORY) -- but ISM is STILL the Bloomberg
paste (hand-entered, source=manual_bloomberg), so dropping bloomberg_era would be the
mislabel here. Stamping it keeps ISM byte-faithful to the frozen S6b canonical.
"""
from __future__ import annotations
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_slot(name: str, cache: dict) -> dict:
    """Parse one slot file once; {"__slot_error__": msg} instead of raising."""
    if name not in cache:
        slot = HERE / name
        if not slot.exists():
            cache[name] = {"__slot_error__": f"manual slot not found: {slot.name}"}
        else:
            try:
                cache[name] = json.loads(slot.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                cache[name] = {"__slot_error__": f"slot parse error: {type(e).__name__}: {e}"}
    return cache[name]


def load_ism(cfg: dict) -> dict:
    """{series_id: {ok, records}} for every manual series from its slot file."""
    man = cfg["manual"]
    cut = cfg.get("settings", {}).get("bloomberg_era_cut")   # S5 provenance boundary
    out: dict = {}
    cache: dict = {}

    for sid, opts in man["series"].items():
        data = _load_slot(opts.get("slot_file", man["slot_file"]), cache)
        if "__slot_error__" in data:
            out[sid] = {"ok": False, "error": data["__slot_error__"]}
            continue
        rows = data.get(sid, [])
        if not rows:
            out[sid] = {"ok": False, "error": "no rows in manual slot"}
            continue
        try:
            newest = max(r["as_of"] for r in rows)   # Д1.2: only the newest print is provisional
            recs = [{"as_of": r["as_of"], "value": float(r["value"]),
                     "source": "manual_bloomberg", "resolution": "monthly",
                     "bloomberg_era": bool(cut) and r["as_of"] <= cut,
                     "provisional": bool(opts.get("provisional", True)) and r["as_of"] == newest}
                    for r in rows]
        except (KeyError, ValueError, TypeError) as e:  # one bad hand-entered row
            out[sid] = {"ok": False, "error": f"bad ISM row: {type(e).__name__}: {e}"}
            continue
        out[sid] = {"ok": True, "records": recs}
    return out

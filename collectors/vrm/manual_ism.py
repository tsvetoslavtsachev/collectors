"""FEED 4 — ISM manual slot (macro_ism_mfg, macro_ism_services).

ISM PMI is licensed; there is no free FRED equivalent (THE gap, S3). Цветослав
enters the monthly prints by hand (Bloomberg) into a slot file; the collector
reads it, never fetches. A missing/empty slot -> that series skipped (never a
silent zero), so a stale ISM shows red in Health rather than a fake number.

Slot file (collector dir, gitignored — licensed data, not redistributed):
    ism_manual.json = {"macro_ism_mfg":      [{"as_of":"YYYY-MM-DD","value":n}, ...],
                       "macro_ism_services": [...]}

D8 contract: provisional=true on every ISM record (preserves the backtested
series; the regime engine already treats these as provisional).

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


def load_ism(cfg: dict) -> dict:
    """{series_id: {ok, records}} for the two ISM series from the manual slot."""
    man = cfg["manual"]
    slot = HERE / man["slot_file"]
    cut = cfg.get("settings", {}).get("bloomberg_era_cut")   # S5 provenance boundary
    out: dict = {}

    if not slot.exists():
        for sid in man["series"]:
            out[sid] = {"ok": False, "error": f"manual slot not found: {slot.name}"}
        return out

    try:
        data = json.loads(slot.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        for sid in man["series"]:
            out[sid] = {"ok": False, "error": f"slot parse error: {type(e).__name__}: {e}"}
        return out

    for sid, opts in man["series"].items():
        rows = data.get(sid, [])
        if not rows:
            out[sid] = {"ok": False, "error": "no rows in manual slot"}
            continue
        try:
            recs = [{"as_of": r["as_of"], "value": float(r["value"]),
                     "source": "manual_bloomberg", "resolution": "monthly",
                     "bloomberg_era": bool(cut) and r["as_of"] <= cut,
                     "provisional": bool(opts.get("provisional", True))}
                    for r in rows]
        except (KeyError, ValueError, TypeError) as e:  # one bad hand-entered row
            out[sid] = {"ok": False, "error": f"bad ISM row: {type(e).__name__}: {e}"}
            continue
        out[sid] = {"ok": True, "records": recs}
    return out

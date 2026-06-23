"""One-time bootstrap of the ISM manual slot from the frozen canonical.

ISM Manufacturing + Services PMI are licensed (Bloomberg); there is no free FRED
equivalent (THE gap, S3). They cannot be fetched and must NEVER be fabricated. But
the frozen canonical already holds Цветослав's verified Bloomberg ISM history (the
VRM2 paste that S6b landed: macro_ism_mfg / macro_ism_services, month-end, provisional).

This bootstrap reads those existing verified prints (READ-ONLY on whatever base
DATACORE_ROOT points at) and writes them into the gitignored slot file. The point:
seed the manual feed from Цветослав's own already-verified numbers, so the manual
path can re-assert them through datacore.write — no number is invented here.

Going forward Цветослав appends each new monthly print to ism_manual.json by hand;
this script only exists to bootstrap the back-history (and to re-bootstrap if needed).

Run (point DATACORE_ROOT at the TEMP base — its ISM is a byte copy of the frozen real):
    $env:DATACORE_ROOT="C:\\Projects\\_vrm_tmp_datacore"
    python -m collectors.vrm.seed_ism_slot

Cardinal rule: this writes ONLY the slot file (in this repo), never data-core.
"""
from __future__ import annotations
import json
import os
from pathlib import Path

import yaml
from datacore import storage

HERE = Path(__file__).resolve().parent


def seed() -> tuple[dict, Path]:
    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))
    series = list(cfg["manual"]["series"])           # macro_ism_mfg, macro_ism_services
    slot: dict = {}
    for sid in series:
        recs = storage.read_canonical(sid)            # reads DATACORE_ROOT (read-only)
        rows = sorted(
            ({"as_of": r["as_of"], "value": r["value"]} for r in recs),
            key=lambda r: r["as_of"],
        )
        slot[sid] = rows
    out = HERE / "ism_manual.json"
    out.write_text(json.dumps(slot, indent=2), encoding="utf-8")
    return slot, out


def main() -> int:
    root = os.environ.get("DATACORE_ROOT", "(data-core repo default)")
    print(f"seed_ism_slot: reading frozen ISM from DATACORE_ROOT = {root}")
    slot, out = seed()
    for sid, rows in slot.items():
        if rows:
            print(f"  {sid}: {len(rows)} months  {rows[0]['as_of']} .. {rows[-1]['as_of']}"
                  f"  (last value {rows[-1]['value']})")
        else:
            print(f"  {sid}: 0 months (canonical empty?)")
    print(f"wrote slot -> {out}  (gitignored — licensed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

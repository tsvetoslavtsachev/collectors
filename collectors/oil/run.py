"""Oil 'Two Clocks' collector -- first citizen of data-core (INIT-22 E2).

Run:  python -m collectors.oil.run [--mock]

Flow: fetch -> WRITE each series through the data-core gate (identity + schema +
health) -> score -> render the светофар. The numbers live in data-core; this repo
holds only the fetch logic, the scoring rules, and the published face.
"""
from __future__ import annotations
import sys
import json
import datetime as dt
from pathlib import Path
import yaml

from . import scoring, report, to_datacore

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent          # collectors repo root
DOCS = REPO / "docs"


def _safe(fn, *args) -> dict:
    try:
        return fn(*args)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def main() -> int:
    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))

    if "--mock" in sys.argv:
        from . import mockdata
        raw = mockdata.raw(cfg)
    else:
        from .fetch_prices import fetch_prices
        from .fetch_portwatch import fetch_hormuz
        from .fetch_eia import fetch_eia
        from .fetch_cot import fetch_cot
        raw = {
            "prices": _safe(fetch_prices, cfg),
            "hormuz": _safe(fetch_hormuz, cfg),
            "eia": _safe(fetch_eia, cfg),
            "cot": _safe(fetch_cot, cfg),
        }

    # --- citizen step: every number goes through the data-core gate ---
    pushed = to_datacore.push(raw)

    # --- scoring (reads the same raw; later E5 inverts to read from data-core) ---
    s1 = scoring.score_s1(raw["prices"], cfg)
    s2 = scoring.score_s2(raw["hormuz"], cfg)
    s3 = scoring.score_s3(raw["eia"], cfg)
    others_tight = "BULL" in (s1["state"], s3["state"])
    s5 = scoring.score_s5(raw["cot"], cfg, others_tight)
    s6 = scoring.score_s6(raw["prices"], cfg)
    scores = {"S1": s1, "S2": s2, "S3": s3, "S5": s5, "S6": s6}

    fals = scoring.falsifier(raw["prices"], cfg)
    comp = scoring.composite(scores, fals, cfg)

    state = {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "raw": raw, "scores": scores, "falsifier": fals, "composite": comp,
        "datacore": pushed,
    }

    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "index.html").write_text(report.build_html(state), encoding="utf-8")
    (DOCS / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )

    print(f"[{state['generated_at']}] {comp['label']}")
    for k, v in scores.items():
        print(f"  {k}: {scoring.BG[v['state']]} | {v.get('value', v.get('detail', ''))}")
    print("  -> data-core:")
    for r in pushed:
        if r.get("rows") is not None:
            print(f"     {r['series_id']}: {r['rows']} rows, as_of {r['as_of']}")
        else:
            print(f"     {r['series_id']}: SKIP ({r.get('skipped')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

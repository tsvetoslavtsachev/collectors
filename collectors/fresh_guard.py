"""The daily freshness guard (ЗЛТ8, 21.11.2026) -- shared by cot and gold.

The weekly Saturday runs missed what the sources publish later: CFTC put the 10.11
report up on Mon 16.11 20:30 UTC (Veterans Day week) and SPDR adds Friday's row after
the Saturday run. Each evening the collectors now run with `--if-newer`: they look at
the newest date the source has, compare it with the newest canonical as_of of the
same series, and write only when the source is ahead. Otherwise they exit 0 without
touching data-core (no push, no health write -> no commit).

The Saturday weekly run keeps its full path and its fail-loud freshness verdict; the
daily path never goes red on a quiet day or a source hiccup (it warns), because a
red light every night would teach everyone to ignore it.
"""
from __future__ import annotations

import os
import sys


def wants_if_newer(argv: list | None = None) -> bool:
    return "--if-newer" in (sys.argv if argv is None else argv)


def canonical_last(series_ids, read) -> str | None:
    """Newest as_of across the given canonical series (None if none exist)."""
    last = None
    for sid in series_ids:
        try:
            rows = read(sid) or []
        except Exception:  # noqa: BLE001 -- a missing series is just "no date"
            rows = []
        for r in rows:
            d = str(r.get("as_of") or "")[:10]
            if d and (last is None or d > last):
                last = d
    return last


def decide(source_last: str | None, canon_last: str | None, name: str) -> tuple:
    """(write?, message). Source unknown -> no write (warn); canon empty -> write."""
    if source_last is None:
        return False, f"{name}: source date unknown this run -- nothing written (daily path)"
    if canon_last is None or source_last > canon_last:
        return True, f"{name}: source has {source_last}, canonical {canon_last} -> writing"
    return False, (f"{name}: nothing newer (source {source_last}, canonical "
                   f"{canon_last}) -- no write")


def warn(title: str, msg: str) -> None:
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::warning title={title}::{msg}")

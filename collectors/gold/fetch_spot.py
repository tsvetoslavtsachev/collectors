"""Gold spot XAU/USD from Swissquote's public quote feed (ЗЛТ8, 21.11.2026).

One point per run, not a history: the feed answers only "the quote now". So the
series is the canonical history carried as-is plus this run's point.

    GET forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD
    -> [{"topo": {"platform": ...}, "spreadProfilePrices": [{"spreadProfile",
         "bid", "ask", ...}], "ts": <epoch ms>}, ...]

value = (bid + ask) / 2 of the "premium" profile on the SwissquoteCapitalMarkets
platform (the mid is the same across profiles; measured 21.11.2026). bid, ask and
quote_ts ride along on the row, so anyone can see WHEN the quote was taken.

as_of is the COMEX/CME trading date the quote belongs to: the session for day D runs
18:00 ET (D-1) to 17:00 ET (D), so a quote at or after 18:00 ET counts for the next
day and a weekend rolls to Monday. Friday's close is stamped 17:00:00 ET -> Friday.

Honest limit (told to Ц. before the build): GitHub starts the evening job at roughly
01:00-05:00 UTC, so on weekdays the point is an early-session quote of the new day,
not a close. Only the weekend runs see a true close (Friday 17:00 ET). A later quote
for the same as_of replaces the earlier one (that is how Friday's close lands).
"""
from __future__ import annotations

import datetime as dt
import zoneinfo

import requests

SERIES = "mkt_gold_spot_usd"
URL = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"
PLATFORM = "SwissquoteCapitalMarkets"
PROFILE = "premium"
SOURCE = "Swissquote XAU/USD premium bid/ask mid"
SANE = (100.0, 100_000.0)          # USD/oz outside this is a broken feed, not a market
NY = zoneinfo.ZoneInfo("America/New_York")
ROLL_HOUR = 18                     # CME gold: new trading day opens 18:00 ET


class FormatError(ValueError):
    """The Swissquote JSON no longer has the shape this reader was built on."""


def parse(payload) -> dict:
    """Payload -> {bid, ask, mid, quote_ts}. Any shape change raises FormatError."""
    if not isinstance(payload, list) or not payload:
        raise FormatError(f"expected a non-empty list, got {type(payload).__name__}")
    blocks = [b for b in payload if isinstance(b, dict)]
    blocks.sort(key=lambda b: (b.get("topo") or {}).get("platform") != PLATFORM)
    for b in blocks:
        prof = [p for p in b.get("spreadProfilePrices") or []
                if isinstance(p, dict) and p.get("spreadProfile") == PROFILE]
        if not prof:
            continue
        p, ts = prof[0], b.get("ts")
        try:
            bid, ask, ts = float(p["bid"]), float(p["ask"]), int(ts)
        except (KeyError, TypeError, ValueError) as e:
            raise FormatError(f"bid/ask/ts not numeric in {PROFILE}: {e!r}") from e
        if not (SANE[0] < bid <= ask < SANE[1]):
            raise FormatError(f"implausible quote bid={bid} ask={ask}")
        when = dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc)
        return {"bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 3),
                "quote_ts": when.strftime("%Y-%m-%dT%H:%M:%SZ")}
    raise FormatError(f"no '{PROFILE}' spreadProfilePrices in any block")


def trading_date(quote_ts: str) -> str:
    t = dt.datetime.strptime(quote_ts, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc).astimezone(NY)
    d = t.date() + dt.timedelta(days=1 if t.hour >= ROLL_HOUR else 0)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d.isoformat()


def _carry(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in ("series_id", "schema_version")}


def extend(existing: list, quote: dict) -> dict:
    """Canonical rows + this quote. Same as_of: the later quote_ts wins."""
    row = {"as_of": trading_date(quote["quote_ts"]), "value": quote["mid"],
           "source": SOURCE, "bid": quote["bid"], "ask": quote["ask"],
           "quote_ts": quote["quote_ts"]}
    rows = {r["as_of"]: _carry(r) for r in existing or []}
    old = rows.get(row["as_of"])
    if old is None or str(old.get("quote_ts") or "") < row["quote_ts"]:
        rows[row["as_of"]] = row
        note = f"{SOURCE}: {row['as_of']} = {row['value']} (quote {row['quote_ts']})"
    else:
        note = f"{SOURCE}: quote {row['quote_ts']} not newer than stored {old.get('quote_ts')}"
    return {"ok": True, "records": [rows[d] for d in sorted(rows)], "note": note}


def fetch(get=requests.get, timeout: int = 30):
    r = get(URL, timeout=timeout, headers={"User-Agent": "collectors-bot/1.0"})
    r.raise_for_status()
    return r.json()

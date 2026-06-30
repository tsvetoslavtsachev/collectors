# -*- coding: utf-8 -*-
"""collectors.price.reconcile -- fetch-time CURRENCY reconciliation (INIT-22 P8c).

A multi-currency stock family (STOXX600, P7a-2) carries a per-series ``currency`` + ``quote_basis``
SNAPSHOT in config.yaml / the catalog (decision 4a). Those are static metadata captured once; the
VENDOR's live quoting can DRIFT -- a London name re-denominated from pence (GBp -> quote_basis
"GBX") to pounds ("GBP"), or sterling to dollars. Either silently corrupts the RAW store by a
factor (pence vs pounds = 100x) because the archive stores the vendor number verbatim and decision
4a deliberately keeps it raw.

This module reconciles the LIVE yfinance ``fast_info.currency`` against the catalog/config
``quote_basis`` BEFORE a STOXX bar is written (mandate P8c step 1, F3). The two outcomes:

  * DEFINITE contradiction (both known, canon differs) -> FAIL-LOUD: the series is dropped from the
    write batch with a loud reason (never a silent corrupt bar). This is the simulated-GBX/GBP-flip
    gate -- a pence series that starts reporting pounds, or sterling that becomes dollars, fires.
  * UNREACHABLE fast_info (a metadata throttle / offline) -> SOFT: WARN and ALLOW. The price bar is
    still split-adjusted-correct; a transient metadata blip must not nuke a daily of good bars, and
    the guard self-heals on the next run that reaches fast_info. Only a CONFIRMED change drops a
    symbol.

GBX / GBp / GBP. yfinance reports London pence as ``GBp`` (lowercase p) -- the SAME economic basis
as our ``GBX``. ``GBP`` (uppercase, pounds) is a DIFFERENT basis (100x). So GBp/GBX reconcile as
equal; a GBX series whose live currency is GBP has been re-denominated and MUST fire.

NETWORK: ``fast_info`` is a light metadata call (no history), injected (``info_fn``) so the offline
tests drive it without yfinance. Never run on --mock.
"""
from __future__ import annotations


def canon_currency(cur):
    """Canonical basis label for a raw currency string (vendor or catalog).

    'GBp' (yfinance London pence) -> 'GBX'; 'GBX' -> 'GBX'; everything else upper-cased -- so
    'GBP' (pounds) stays 'GBP', DISTINCT from GBX/pence by 100x, and 'eur' -> 'EUR'. None -> None.
    The 'GBp' check is exact and BEFORE upper-casing (``'GBp'.upper() == 'GBP'`` would lose the
    pence/pounds distinction that the whole reconcile turns on)."""
    if cur is None:
        return None
    c = str(cur).strip()
    if c == "GBp" or c.upper() == "GBX":
        return "GBX"
    return c.upper()


def _yf_currency(symbol):
    """Live yfinance ``fast_info`` currency for a symbol, or None if unreachable (the SOFT path).

    Any error (throttle / offline / missing field) -> None, so a transient metadata failure is a
    WARN-and-allow, never a hard drop. fast_info supports both mapping and attribute access across
    yfinance versions -- try both."""
    try:
        import yfinance as yf
        fi = yf.Ticker(symbol).fast_info
        try:
            cur = fi["currency"]
        except (KeyError, TypeError):
            cur = getattr(fi, "currency", None)
        return cur
    except Exception:  # noqa: BLE001 -- any failure is the SOFT (unverified) path
        return None


def reconcile(cfg, sids, *, info_fn=_yf_currency, log=print):
    """Reconcile live currency vs catalog quote_basis for the currency-bearing series in ``sids``.

    Returns ``(mismatched, unverified)``:
      * ``mismatched`` -- {sid: (expected_quote_basis, live_currency)} for a DEFINITE contradiction
        (both known, canon differs). FAIL-LOUD: the caller drops these from the write batch.
      * ``unverified`` -- [sid, ...] where ``info_fn`` returned None (metadata unreachable). SOFT:
        logged, but written (the price bar is still correct; the guard self-heals next run).

    A series with no ``quote_basis`` in config (ETF / SP500 USD) is skipped -- nothing to
    reconcile, so an ETF/SP500-only run makes ZERO fast_info calls.
    """
    price = cfg.get("price", {})
    mismatched: dict = {}
    unverified: list = []
    for sid in sids:
        m = price.get(sid) or {}
        expected = m.get("quote_basis")
        if not expected:
            continue                          # single-currency family -> nothing to reconcile
        live = info_fn(m["symbol"])
        if live is None:
            unverified.append(sid)
            continue
        if canon_currency(live) != canon_currency(expected):
            mismatched[sid] = (expected, live)
    if mismatched:
        log(f"  CURRENCY RECONCILE: {len(mismatched)} series DROPPED -- live currency contradicts "
            f"catalog quote_basis (re-denomination?): "
            f"{[(s, exp, got) for s, (exp, got) in list(mismatched.items())[:6]]}")
    if unverified:
        log(f"  currency reconcile: {len(unverified)} series unverified (fast_info unreachable -- "
            f"WARN, written anyway; self-heals next run): e.g. {unverified[:6]}")
    return mismatched, unverified


def neutralize(raw, mismatched, *, reason_prefix="currency mismatch"):
    """Mark each mismatched series ok=False in a fetch ``raw`` dict so push / split-heal SKIP it.

    Loud, never silent: the series becomes a recorded skip carrying the expected-vs-live reason.
    Mutates ``raw`` in place (it is the run's own transient fetch result) and returns it."""
    for sid, (expected, live) in mismatched.items():
        raw[sid] = {
            "ok": False,
            "error": (f"{reason_prefix}: catalog quote_basis={expected!r} but live "
                      f"currency={live!r} (canon {canon_currency(expected)} != "
                      f"{canon_currency(live)}) -- refusing to write a possibly re-denominated bar"),
        }
    return raw

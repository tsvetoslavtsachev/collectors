# -*- coding: utf-8 -*-
"""collectors.price.identity -- stable internal stock identity + recycling/splice
guard + AUTOMATIC rename-continuity (INIT-22 P7b, SCAFFOLD).

WHY THIS EXISTS
---------------
P7a/P7a-2 made ~1112 current stocks (503 SP500 + 609 STOXX) citizens of the price
archive, each series KEYED ON THE TICKER (px_aapl_daily, px_sap_de_daily). A ticker
is the mutable human NAME, not a stable identity:

  * RECYCLE (poison curve): a ticker freed by a delisting is later reassigned to a
    DIFFERENT company. One px_<ticker> series would splice bars of two unrelated
    firms -> 50/200-DMA, returns, vol computed across a non-existent seam.
    (V = Vivendi->Visa 2008; S = Sears->Sprint->SentinelOne. program R4.)
  * RENAME (fragmented track record): the SAME company, a new ticker -> px_fb and
    px_meta are different keys -> a 6y record split into two curves with a seam.
    (FB->META, ANTM->ELV, FISV->FI, SQ->XYZ, DUFN->AVOL.)

This module is the exact COT lesson carried to stocks. COT broke on a "LIKE-rename"
trap (resolve-by-human-name -> splice/truncate for ~17 markets) and was fixed by
pinning a stable cftc_code SEPARATE from the series_id (markets.py: canonical +
cftc_code as two independent fields; derive.py: detect_splice / mark-don't-clean;
verify_splice.py: synthetic offline proof). P7b is the stock analog:

  * OPTION A (decision a): KEEP series_id = px_<ticker>_daily. Identity is an
    ADDITIVE side field (stable_id). NO re-key -> zero rewrite of the immutable
    store, zero consumer break, the P7a-2 "0-collision in 1244" invariant survives.
  * INTERNAL ID (decision b): a free, offline, deterministic sequential SEC-NNNNNN,
    minted-on-first-sight in SORTED order ONCE, then only ever APPENDED (never
    renumbered). COT had a free authoritative CFTC code; 1112 stocks do not, so the
    id is internal. The map (stock_identity.json) IS the authority (like the
    hand-frozen markets.py registry).
  * SPLICE GUARD (decision d): an effective-dated lifecycle. The key is
    (ticker AND an OPEN epoch), never the ticker alone. Seen -> mint, effective_to
    NULL. Disappears -> close_epoch (set effective_to). Re-seen -> MINT A NEW id,
    never reattach the retired one. A recycled ticker therefore cannot splice
    company A onto company B. Fail-closed by default.
  * RENAME-CONTINUITY (decision e', ADDENDUM -- AUTOMATIC): cross-snapshot diff +
    shareClassFIGI (figi.py) -> a confirmed same-company ticker change UPGRADES the
    default "2 fragments" to "1 continued stable_id" (continuation_of link). FIGI
    must MATCH to link; conflict / FIGI-offline + only-fuzzy -> review_flag, never a
    silent merge and never a splice. Different company on the same ticker -> new id.

SCAFFOLD (P7b): the machinery for CURRENT members + forward rename-tracking, against
a TEMPORARY archive root. The real catalog registration is P8; full point-in-time
membership history + WEIGHTS is P11 (paid source). Record-level stable_id stamping is
P8. This module writes only <root>/catalog/stock_identity.json (a TEMP root).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

IDENTITY_SCHEMA_VERSION = 1
ID_PREFIX = "SEC-"
ID_WIDTH = 6
_ID_RE = re.compile(r"^SEC-\d{6}$")

# Yahoo Finance exchange suffix -> OpenFIGI / Bloomberg exchCode.
#   US tickers carry NO dot suffix (a dash share-class like BRK-B / BF-B is still
#   US) -> "US". A dotted suffix maps to its primary-listing exchCode.
#   .DE -> GR (XETRA, the primary electronic venue); the Frankfurt floor (GF) is a
#   separate venue -- we pin XETRA because the iShares EXSA constituents trade
#   primary there. shareClassFIGI is exchange-independent anyway, so a slightly off
#   exchCode still resolves the share-class FIGI; the exch_code is mainly a stable
#   human label on the epoch. An UNKNOWN suffix degrades to the upper-cased suffix
#   (never crashes; only the FIGI lookup for that one venue may miss).
_SUFFIX_EXCH = {
    "L": "LN", "PA": "FP", "DE": "GR", "SW": "SW", "ST": "SS", "MI": "IM",
    "AS": "NA", "MC": "SM", "CO": "DC", "OL": "NO", "HE": "FH", "WA": "PW",
    "BR": "BB", "VI": "AV", "IR": "ID", "LS": "PL", "NYB": "US",
}


def exch_code(symbol: str) -> str:
    """FIGI/Bloomberg exchCode for a yahoo symbol, from its suffix.

    BRK-B -> US (dash share-class, no dot). SAP.DE -> GR. HSBA.L -> LN. An unknown
    suffix returns the upper-cased suffix itself (graceful; identity stays stable).
    """
    if "." not in symbol:
        return "US"
    return _SUFFIX_EXCH.get(symbol.rsplit(".", 1)[1].upper(),
                            symbol.rsplit(".", 1)[1].upper())


# --------------------------------------------------------------------------- #
# Map model + persistence  (the map IS the authority, like markets.py)
# --------------------------------------------------------------------------- #
def empty_map() -> dict:
    return {"identity_schema_version": IDENTITY_SCHEMA_VERSION, "epochs": []}


def _path(root) -> Path:
    return Path(root) / "catalog" / "stock_identity.json"


def load(root) -> dict:
    """Load <root>/catalog/stock_identity.json, or an empty map if absent.

    Absent-map is the BACKWARD-COMPATIBLE path: a P7a-era caller of
    register_catalog.register (no map seeded) gets an empty map -> zero stable_id
    stamps -> the ETF/P7a catalog stays byte-identical.
    """
    p = _path(root)
    if not p.exists():
        return empty_map()
    m = json.loads(p.read_text(encoding="utf-8"))
    m.setdefault("epochs", [])
    m.setdefault("identity_schema_version", IDENTITY_SCHEMA_VERSION)
    return m


def save(m: dict, root) -> Path:
    """Persist the map deterministically (epochs in append/mint order -> stable
    bytes; only genuinely-new epochs ever change the tail)."""
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def assert_temp_archive(root) -> None:
    """P7b cardinal-rule guard: write ONLY a TEMP root, never the live archive.

    The vendored P1 ``assert_safe_root`` protects the data-core repo, but the real
    PRICE store (price-archive) is a SIBLING directory it does not cover (LENS 5).
    So this adds a CONTENT-based refusal: a root whose catalog.json already holds
    real (non-probe) px_ series IS the live price-archive -> refuse. A fresh temp
    root (empty / probe-only catalog) passes. ``DATACORE_ALLOW_REAL=1`` overrides --
    the same explicit opt-in P8 uses for the real registration after sign-off.
    """
    import os
    from datacore.archive import assert_safe_root
    assert_safe_root(root)                                # data-core protection (P1)
    if os.environ.get("DATACORE_ALLOW_REAL") == "1":
        return
    cat = Path(root) / "catalog" / "catalog.json"
    if not cat.exists():
        return
    try:
        series = json.loads(cat.read_text(encoding="utf-8")).get("series", {})
    except Exception:
        return
    real_px = [s for s in series if s.startswith("px_") and s != "px_probe_daily"]
    if real_px:
        raise SystemExit(
            f"REFUSED: {cat} holds {len(real_px)} real px_ series -> this is the LIVE "
            f"price-archive, not a TEMP root. P7b writes only a temp root (real "
            f"registration is P8). Set DATACORE_ALLOW_REAL=1 to override.")


# --------------------------------------------------------------------------- #
# Id arithmetic (append-only, never renumber)
# --------------------------------------------------------------------------- #
def _id_num(internal_id: str) -> int:
    return int(internal_id[len(ID_PREFIX):])


def _fmt_id(n: int) -> str:
    return f"{ID_PREFIX}{n:0{ID_WIDTH}d}"


def _max_id_num(m: dict) -> int:
    return max((_id_num(e["internal_id"]) for e in m["epochs"]), default=0)


def _by_id(m: dict) -> dict:
    return {e["internal_id"]: e for e in m["epochs"]}


# --------------------------------------------------------------------------- #
# Epoch lifecycle  (decision d: key = ticker AND an OPEN epoch)
# --------------------------------------------------------------------------- #
def active_epoch(m: dict, ticker: str, exch: str | None = None):
    """The single OPEN epoch (effective_to is None) for a ticker, or None.

    The map invariant (check_invariants) guarantees at most one open epoch per
    ticker; this returns it (the last one if a violation ever existed).
    """
    found = None
    for e in m["epochs"]:
        if (e["ticker"] == ticker and e["effective_to"] is None
                and (exch is None or e["exch_code"] == exch)):
            found = e
    return found


def epochs_for(m: dict, ticker: str) -> list:
    return [e for e in m["epochs"] if e["ticker"] == ticker]


def _new_epoch(internal_id, ticker, exch, date, name, source_note) -> dict:
    return {
        "internal_id": internal_id,
        "ticker": ticker,
        "exch_code": exch,
        "first_seen": date,        # == effective_from (the open boundary)
        "effective_to": None,
        "name": name,
        "isin": None,
        "share_class_figi": None,
        "continuation_of": None,
        "review_flag": None,
        "source_note": source_note,
    }


def mint_or_resolve(m: dict, ticker: str, exch: str, date: str, *,
                    name: str = "", source_note: str = "") -> str:
    """Resolve a ticker to its ACTIVE internal_id, else MINT a new epoch on first
    sight. Appends to the map; never renumbers. Returns the epoch internal_id.

    This is the splice-refuse CORE: a ticker with no OPEN epoch (a prior epoch was
    closed when the ticker disappeared) mints a NEW id -- it never reattaches a
    retired company's identity (program R4, COT mark-don't-clean spirit).
    """
    e = active_epoch(m, ticker, exch)
    if e is not None:
        return e["internal_id"]
    new_id = _fmt_id(_max_id_num(m) + 1)
    m["epochs"].append(_new_epoch(new_id, ticker, exch, date, name, source_note))
    return new_id


def close_epoch(m: dict, ticker: str, date: str, exch: str | None = None):
    """Close the active epoch for a ticker (set effective_to). Returns the closed
    internal_id, or None if no active epoch (idempotent)."""
    e = active_epoch(m, ticker, exch)
    if e is None:
        return None
    e["effective_to"] = date
    return e["internal_id"]


# --------------------------------------------------------------------------- #
# Stable identity resolution (follow the continuation chain to its ROOT)
# --------------------------------------------------------------------------- #
def _chain_root(m: dict, epoch: dict) -> str:
    """The root internal_id of an epoch's continuation chain. A renamed company
    (META continuation_of FB) resolves to FB's id -> the stable identity is the
    chain head, so pre- and post-rename tickers share ONE stable_id."""
    by_id = _by_id(m)
    seen = set()
    cur = epoch
    while cur.get("continuation_of") and cur["continuation_of"] in by_id:
        if cur["internal_id"] in seen:      # defensive: never loop on a bad cycle
            break
        seen.add(cur["internal_id"])
        cur = by_id[cur["continuation_of"]]
    return cur["internal_id"]


def stable_id(m: dict, ticker: str, exch: str | None = None):
    """The STABLE identity for a ticker = the ROOT of its active epoch's
    continuation chain, or None if the ticker has no active epoch. This is the
    value register_catalog stamps as ``stable_id``."""
    e = active_epoch(m, ticker, exch)
    if e is None:
        return None
    return _chain_root(m, e)


# --------------------------------------------------------------------------- #
# Splice / identity introspection (COT derive.py analog)
# --------------------------------------------------------------------------- #
def distinct_identities(m: dict, ticker: str) -> list:
    """Distinct internal_ids ever minted for a ticker (COT distinct_identities
    analog). >1 means the ticker was recycled across identities -- UNLESS those
    ids are linked by continuation_of (the same company re-appearing)."""
    seen = []
    for e in m["epochs"]:
        if e["ticker"] == ticker and e["internal_id"] not in seen:
            seen.append(e["internal_id"])
    return seen


def detect_splice(m: dict, ticker: str):
    """Return a seam dict if a ticker carries >1 internal_id, else None.

    Mirrors derive.detect_splice (mark-don't-clean): the seam is reported, never
    cleaned. ``flag`` distinguishes the two cases exactly like COT's
    name_rebrand vs contract_splice:
      * ticker_recycle_splice -- the ids are UNLINKED (a recycled ticker spanning
        two unrelated companies). The real hazard (program R4 verify gate).
      * name_rebrand          -- the ids are continuation-linked (the SAME company
        re-appeared under the ticker, FIGI-confirmed). Benign.
    A different-ticker rename (FB->META) is NOT a splice on either ticker: each
    ticker holds exactly one id; the continuation lives across the two tickers.
    """
    eps = sorted(epochs_for(m, ticker),
                 key=lambda e: (e["first_seen"], _id_num(e["internal_id"])))
    ids = []
    for e in eps:
        if e["internal_id"] not in ids:
            ids.append(e["internal_id"])
    if len(ids) < 2:
        return None
    # Linked iff every later epoch continues one of the earlier epochs of THIS
    # ticker (same company re-appearing) -> benign name_rebrand; else recycle.
    prior_ids: set = set()
    all_linked = True
    for e in eps:
        if prior_ids and e.get("continuation_of") not in prior_ids:
            all_linked = False
            break
        prior_ids.add(e["internal_id"])
    return {
        "flag": "name_rebrand" if all_linked else "ticker_recycle_splice",
        "ticker": ticker,
        "from_identity": ids[-2],
        "to_identity": ids[-1],
        "seam_date": (eps[-1]["first_seen"] or "")[:10],
        "last_clean_date": (eps[-2].get("effective_to") or "")[:10] if eps[-2].get("effective_to") else None,
        "linked": all_linked,
    }


# --------------------------------------------------------------------------- #
# Invariants (verify gate)
# --------------------------------------------------------------------------- #
def check_invariants(m: dict) -> list:
    """Return a list of invariant violations (empty list == healthy map)."""
    problems = []
    ids = [e["internal_id"] for e in m["epochs"]]
    if len(ids) != len(set(ids)):
        dup = [i for i, c in Counter(ids).items() if c > 1]
        problems.append(f"duplicate internal_id: {dup[:5]}")
    for e in m["epochs"]:
        if not _ID_RE.match(e["internal_id"]):
            problems.append(f"bad id format: {e['internal_id']}")
            break
    open_by_ticker = Counter(e["ticker"] for e in m["epochs"] if e["effective_to"] is None)
    multi = [t for t, c in open_by_ticker.items() if c > 1]
    if multi:
        problems.append(f"ticker(s) with >1 active epoch: {multi[:5]}")
    idset = set(ids)
    by_id = _by_id(m)
    for e in m["epochs"]:
        co = e.get("continuation_of")
        if co and co not in idset:
            problems.append(f"dangling continuation_of: {e['internal_id']} -> {co}")
            break
    # continuation_of must form a DAG -- never a cycle (A continues B continues A).
    for e in m["epochs"]:
        seen, cur, cycle = set(), e, False
        while cur.get("continuation_of"):
            if cur["internal_id"] in seen:
                cycle = True
                break
            seen.add(cur["internal_id"])
            cur = by_id.get(cur["continuation_of"])
            if cur is None:
                break
        if cycle:
            problems.append(f"continuation cycle through {e['internal_id']}")
            break
    # ids dense + 1-based (sorted seed, append-only -> no holes from a renumber bug)
    nums = sorted(_id_num(i) for i in ids)
    if nums and (nums[0] != 1 or nums[-1] != len(nums)):
        problems.append(f"id sequence not dense 1..N (min={nums[0]}, max={nums[-1]}, n={len(nums)})")
    return problems


def unstamped_stocks(cfg: dict, m: dict) -> list:
    """Config stock series whose symbol has NO active epoch in the map -> they would
    register WITHOUT a stable_id (the silent-omit gap LENS 3 surfaced). Empty in the
    pure seed->register scaffold flow; a non-empty result is a real coverage gap that
    P8 forward-tracking must fail-closed on (a delisted-but-still-configured stock).
    """
    return [sid for sid, mk in stock_universe(cfg)
            if stable_id(m, mk["symbol"], exch_code(mk["symbol"])) is None]


# --------------------------------------------------------------------------- #
# Seed (decision b: sorted seed -> SEC-NNNNNN, ONCE; re-run appends only)
# --------------------------------------------------------------------------- #
def stock_universe(cfg: dict) -> list:
    """[(series_id, market_dict)] for the STOCK family, sorted by series_id.

    Sorted -> the SEC-NNNNNN assignment is deterministic regardless of config
    ordering (P7a snapshot discipline). ETFs are excluded (decision h: ETFs survive,
    no splice/survivorship risk -> no stable_id in the scaffold)."""
    return sorted(((sid, m) for sid, m in cfg["price"].items()
                   if m.get("family") == "stock"), key=lambda kv: kv[0])


def seed(cfg: dict, root, date: str, *, source_tag: str = "seed") -> tuple:
    """Mint one active epoch per CURRENT stock series, in sorted-series_id order,
    APPEND-ONLY against any existing map (a re-run mints only genuinely-new
    tickers, never renumbers). Writes <root>/catalog/stock_identity.json.

    Returns (map, minted_count). Idempotent: a second seed mints 0.
    """
    assert_temp_archive(root)                            # cardinal: temp root only (LENS 5)
    m = load(root)
    minted = 0
    for _sid, mk in stock_universe(cfg):
        sym = mk["symbol"]
        ex = exch_code(sym)
        if active_epoch(m, sym, ex) is None:
            minted += 1
        mint_or_resolve(m, sym, ex, date, name=mk.get("name", ""),
                        source_note=f"{source_tag}:{mk.get('category', '')}")
    save(m, root)
    return m, minted


# --------------------------------------------------------------------------- #
# AUTOMATIC rename-continuity (decision e', ADDENDUM): cross-snapshot diff
# --------------------------------------------------------------------------- #
def _name_key(name: str) -> str:
    """Loose normalization for the LAST-resort fuzzy name signal (flag only, never
    an auto-merge): upper, strip common suffixes/punctuation."""
    s = (name or "").upper()
    for junk in (" PLC", " AG", " SA", " NV", " SE", " INC", " CORP", " CO", " LTD",
                 " GROUP", " HOLDINGS", " CLASS A", " CLASS B", ".", ",", "-"):
        s = s.replace(junk, " ")
    return " ".join(s.split())


def apply_snapshot(m: dict, snapshot: list, date: str, *,
                   figi_lookup=None, source_note: str = "snapshot") -> dict:
    """Apply one universe snapshot to the map: mint new tickers, close disappeared
    ones, and AUTO-LINK confirmed renames.

    snapshot     : [{"ticker", "exch_code", "name", "isin"?, "share_class_figi"?}]
                   for the CURRENT universe of this snapshot.
    figi_lookup  : {(ticker, exch_code): shareClassFIGI or None} from figi.py.
                   Pass None to mean "OpenFIGI UNREACHABLE" -> rename matching falls
                   back to ISIN, then to a flagged-only fuzzy name (graceful, no crash).

    RENAME RULE (safety ordering): a disappeared X and an appeared Y are linked
    (Y.continuation_of = X.internal_id, Y resolves to X's stable_id) ONLY when their
    shareClassFIGI matches (primary) or, failing that, their ISIN matches (secondary)
    -- and the match is UNIQUE. Ambiguity / FIGI-offline-with-only-a-name-hit / a
    name-only hit -> review_flag, NEVER a silent merge and NEVER a splice. FIGI must
    MATCH to link, so two DIFFERENT companies are never merged. Reuse stays
    fail-closed: an unmatched appearance mints a fresh id.

    Returns {minted, closed, continued, flagged, resolved}.
    """
    figi_offline = figi_lookup is None
    figi_lookup = figi_lookup or {}

    snap_by_ticker = {s["ticker"]: s for s in snapshot}
    snap_tickers = set(snap_by_ticker)
    active = {e["ticker"]: e for e in m["epochs"] if e["effective_to"] is None}
    active_tickers = set(active)

    disappeared = sorted(active_tickers - snap_tickers)
    appeared = sorted(snap_tickers - active_tickers)
    stayed = sorted(snap_tickers & active_tickers)

    report = {"minted": [], "closed": [], "continued": [], "flagged": [], "resolved": 0}

    def snap_figi(t):
        s = snap_by_ticker[t]
        return figi_lookup.get((t, s["exch_code"])) or s.get("share_class_figi")

    # (0) CONTINUOUS-HANDOVER RECYCLE (the V = Vivendi->Visa hazard). A ticker present
    # in BOTH the map and this snapshot, but whose incoming shareClassFIGI/ISIN
    # CONTRADICTS the cached identity, is a recycle with NO intervening absent snapshot
    # -- set-arithmetic alone would file it under `stayed` and silently keep the old id.
    # Evidence-based + fail-closed: a HARD FIGI/ISIN conflict (both sides present and
    # different) closes the retired epoch, mints a NEW id, and flags it -> the splice is
    # refused even without an absent snapshot. A name change alone is NOT a conflict
    # (benign rebrand) -> it never false-fires.
    contradicted = []
    for t in stayed:
        ep, s = active[t], snap_by_ticker[t]
        inf, ini = snap_figi(t), s.get("isin")
        cf, ci = ep.get("share_class_figi"), ep.get("isin")
        if (cf and inf and cf != inf) or (ci and ini and ci != ini):
            cid = close_epoch(m, t, date)
            if cid:
                report["closed"].append(cid)
            mint_or_resolve(m, t, s["exch_code"], date, name=s.get("name", ""),
                            source_note=source_note)
            nep = active_epoch(m, t, s["exch_code"])
            if inf:
                nep["share_class_figi"] = inf
            if ini:
                nep["isin"] = ini
            nep["review_flag"] = "ticker_recycle_contradiction"
            report["minted"].append(nep["internal_id"])
            report["flagged"].append((t, "ticker_recycle_contradiction"))
            contradicted.append(t)
    stayed = [t for t in stayed if t not in contradicted]
    report["resolved"] = len(stayed)

    # (1) RENAMES: an appeared Y is linked to a disappeared X by FIGI (primary) / ISIN
    # (secondary) ONLY on a UNIQUE, unclaimed match. The guard is SYMMETRIC: ambiguity
    # in EITHER direction -> review_flag, never a silent merge and never a silent
    # fragment. 1:N (one X, several appeared Y sharing its FIGI -- a GOOG/GOOGL dual
    # share class) links the first Y and FLAGS the rest (rename_multi_class_figi)
    # rather than fragmenting them invisibly.
    # How many appeared Y carry each FIGI -> a count > 1 is a 1:N dual-share-class
    # cluster, in which even the LINKED winner is an order-dependent guess (flag it).
    appeared_figi_count = Counter(snap_figi(y) for y in appeared if snap_figi(y))
    matched: set = set()
    for y in appeared:
        s = snap_by_ticker[y]
        ex = s["exch_code"]
        yf, yi = snap_figi(y), s.get("isin")
        link_x, link_via, flag = None, None, None

        figi_all = [x for x in disappeared if yf and active[x].get("share_class_figi") == yf]
        isin_all = [x for x in disappeared if yi and active[x].get("isin") == yi]
        cands_figi = [x for x in figi_all if x not in matched]
        cands_isin = [x for x in isin_all if x not in matched]
        if len(cands_figi) == 1:
            link_x, link_via = cands_figi[0], "figi"     # primary: unique FIGI match
        elif len(cands_figi) > 1:
            flag = "rename_ambiguous_figi"               # N:1 conflict -> flag, no merge
        elif figi_all:                                   # 1:N -- FIGI matched an already
            flag = "rename_multi_class_figi"             # claimed X (dual share class) -> flag
        elif len(cands_isin) == 1:
            link_x, link_via = cands_isin[0], "isin"     # secondary: unique ISIN match
        elif len(cands_isin) > 1:
            flag = "rename_ambiguous_isin"
        elif isin_all:
            flag = "rename_multi_class_isin"
        else:
            name_cands = [x for x in disappeared if x not in matched
                          and _name_key(active[x]["name"]) == _name_key(s.get("name", ""))]
            if name_cands:                               # last resort: flag ONLY, never link
                flag = "rename_figi_offline" if figi_offline else "rename_name_only"

        # A FIGI link is auditable only if FIGI and a present ISIN AGREE on the same X;
        # a disagreement (stale cached FIGI vs a different ISIN) must surface, not vanish.
        if link_via == "figi" and isin_all and link_x not in isin_all:
            flag = "rename_figi_isin_disagree"

        mint_or_resolve(m, y, ex, date, name=s.get("name", ""), source_note=source_note)
        ep = active_epoch(m, y, ex)
        if yi:
            ep["isin"] = yi
        if yf:
            ep["share_class_figi"] = yf
        if link_x is not None:
            ep["continuation_of"] = active[link_x]["internal_id"]
            matched.add(link_x)
            report["continued"].append((link_x, y))
            # 1:N winner OR a FIGI/ISIN disagreement -> the link is a guess; flag it so a
            # P8 reviewer can never promote it as a clean 1:1 (symmetry: losers + winner).
            win_flag = flag if flag == "rename_figi_isin_disagree" else (
                "rename_multi_class_winner"
                if link_via == "figi" and appeared_figi_count.get(yf, 0) > 1 else None)
            if win_flag:
                ep["review_flag"] = win_flag
                report["flagged"].append((y, win_flag))
        elif flag:
            ep["review_flag"] = flag
            report["flagged"].append((y, flag))
        report["minted"].append(ep["internal_id"])

    for x in disappeared:
        cid = close_epoch(m, x, date)                    # matched X closes too; link lives on Y
        if cid:
            report["closed"].append(cid)

    for t in stayed:                                     # cache FIGI/ISIN on first sight
        ep, s = active[t], snap_by_ticker[t]
        new_name = s.get("name", "")
        inf, ini = snap_figi(t), s.get("isin")
        first_figi = bool(inf) and not ep.get("share_class_figi")
        name_changed = (new_name and _name_key(ep["name"])
                        and _name_key(ep["name"]) != _name_key(new_name))
        # A FIRST-time identity (FIGI) assignment WHILE the name materially changes is an
        # ambiguous continuous-handover (rebrand vs. recycle -- no absent snapshot AND no
        # prior FIGI to contradict, so block 0 cannot fire). Mark for review rather than
        # SILENTLY grafting a new identity onto the epoch (the LENS 1 graft residual). We
        # still cache the FIGI so a LATER snapshot can detect a hard conflict (block 0).
        if first_figi and name_changed and not ep.get("review_flag"):
            ep["review_flag"] = "stayed_identity_review"
            report["flagged"].append((t, "stayed_identity_review"))
        if new_name:                                     # refresh the (cosmetic) name --
            ep["name"] = new_name                        # identity is the id, not the name.
        if first_figi:
            ep["share_class_figi"] = inf
        if not ep.get("isin") and ini:
            ep["isin"] = ini

    return report


def enrich_figi(m: dict, *, api_key=None, only_missing: bool = True) -> int:
    """Fetch shareClassFIGI for active epochs missing it and cache into the map
    (offline thereafter). Returns the count populated. Graceful: figi.py returns
    None for any unreachable job, so this never raises."""
    from collectors.price import figi as _figi
    targets = [(e["ticker"], e["exch_code"]) for e in m["epochs"]
               if e["effective_to"] is None and (not only_missing or not e.get("share_class_figi"))]
    if not targets:
        return 0
    lookup = _figi.map_share_class_figi(targets, api_key=api_key)
    n = 0
    for e in m["epochs"]:
        if e["effective_to"] is None and not e.get("share_class_figi"):
            f = lookup.get((e["ticker"], e["exch_code"]))
            if f:
                e["share_class_figi"] = f
                n += 1
    return n


# --------------------------------------------------------------------------- #
# CLI: seed the map against a TEMP root (cardinal -- never the real archive)
# --------------------------------------------------------------------------- #
def main() -> int:
    import os
    from datetime import date as _date
    import yaml

    env = os.environ.get("DATACORE_ROOT")
    if not env:
        raise SystemExit("REFUSED: set DATACORE_ROOT to the price-archive checkout (TEMP for P7b)")
    root = Path(env).resolve()
    assert_temp_archive(root)                            # cardinal: temp root only (P1 + price-archive)
    here = Path(__file__).resolve().parent
    cfg = yaml.safe_load((here / "config.yaml").read_text(encoding="utf-8"))
    today = _date.today().isoformat()
    m, minted = seed(cfg, root, today)
    problems = check_invariants(m)
    gaps = unstamped_stocks(cfg, m)
    if gaps:
        print(f"WARNING: {len(gaps)} config stock(s) have no active epoch (would be "
              f"unstamped): e.g. {gaps[:5]}")
    active = sum(1 for e in m["epochs"] if e["effective_to"] is None)
    print(f"stock_identity @ {root}: {minted} minted this run, "
          f"{len(m['epochs'])} epochs total, {active} active")
    if problems:
        print("INVARIANT VIOLATIONS:")
        for p in problems:
            print("  -", p)
        return 1
    print("invariants OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

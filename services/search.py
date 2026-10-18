"""Game search and autocomplete.

The index is loaded on the first search, not at import, so pages that never
search (most of them, on a cold serverless start) don't pay for it.

Source, in order of preference:
1. ``data/search_index.json.gz`` — games only, with a popularity score,
   built by ``scripts/build_data.py index``.
2. ``games.json`` — Steam's old full app list, filtered with :func:`is_junk_name`.

Matching keeps the original approach (prefix, substring, acronym and
RapidFuzz scoring) and adds word-level candidates — so "witcher" finds
"The Witcher 3" — plus a popularity boost so well-known games rank first.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict
from typing import Any

from rapidfuzz import fuzz, process

from config import Config

log = logging.getLogger(__name__)

INDEX_PATH = os.path.join(Config.DATA_DIR, "search_index.json.gz")

_norm_re = re.compile(r"[^a-z0-9]+")

# Entries in Steam's full app list that aren't games people search for.
# Checked against the normalised name: whole words, then phrases.
_JUNK_WORDS = frozenset({
    "soundtrack", "soundtracks", "ost", "sdk", "demo", "playtest", "dlc",
    "artbook", "wallpaper", "wallpapers", "benchmark", "valvetestapp",
})
_JUNK_PHRASES = (
    "dedicated server", "season pass", "art book", "public test", "test server",
    "supporter pack", "bonus content", "original soundtrack",
)

# Until the built index exists, these well-known games get a popularity boost.
_LEGACY_POPULAR = {
    730, 570, 440, 578080, 1172470, 252490, 1091500, 1174180, 1245620, 1086940,
    292030, 72850, 489830, 413150, 105600, 892970, 367520, 1145360, 646570,
    427520, 271590, 359550, 1938090, 2358720, 814380, 374320, 620, 400, 220,
    4000, 550, 1222670, 1238810, 381210, 945360, 1966720, 1623730, 275850,
    322330, 242760, 346110, 588650, 1794680, 632360, 250900, 1942280, 281990,
    394360, 289070, 8930, 236390, 1599340, 291550, 1343400, 1551360, 1293830,
    1203220, 230410, 444090, 1085660, 990080, 1716740, 2050650, 1817070,
    601150, 883710, 1196590, 952060, 438740, 268500, 261570, 1057090, 48000,
    304430, 1150690, 824600, 1506830, 1814990, 851150, 1113560, 570940,
    1151640, 1030840, 1097150, 728880, 304930, 1144200, 236430, 431960,
    1422450, 1867240, 3393110, 322170, 2246340, 2139460, 1172620, 2357570,
}


def is_junk_name(name: str) -> bool:
    return _is_junk_norm(_normalize(name))


def _is_junk_norm(norm: str) -> bool:
    if not norm or not _JUNK_WORDS.isdisjoint(norm.split()):
        return True
    return any(p in norm for p in _JUNK_PHRASES)


# Roman numerals as whole words become digits, so "civ 6" finds
# "Civilization VI" and "gta5" finds "Grand Theft Auto V".  ("i" and "x" are
# left alone: they're usually letters.)
_ROMAN = {
    "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8",
    "ix": "9", "xi": "11", "xii": "12", "xiii": "13", "xiv": "14", "xv": "15", "xvi": "16",
}


def _normalize(s: str) -> str:
    s = (s or "").lower().replace("'", "").replace("’", "")
    return " ".join(_ROMAN.get(w, w) for w in _norm_re.sub(" ", s).split())


def _acronym(norm: str) -> str:
    return "".join(w[0] for w in norm.split() if w)


# Entry tuple layout (tuples keep 200k+ entries compact and fast).
APPID, NAME, NORM, COMPACT, ACRO, POP = range(6)


class _Index:
    def __init__(self, entries: list[tuple]):
        self.entries = entries
        self.by_appid = {e[APPID]: i for i, e in enumerate(entries)}
        self.buckets: dict[str, list[int]] = defaultdict(list)
        for i, e in enumerate(entries):
            keys = {w[:2] for w in e[NORM].split()}
            keys.add(e[COMPACT][:2])
            if len(e[ACRO]) >= 2:
                keys.add("@" + e[ACRO][:2])
            for k in keys:
                if k:
                    self.buckets[k].append(i)
        # Popular games, for one-letter queries.
        self.popular = sorted(
            (i for i, e in enumerate(entries) if e[POP] >= 3.0),
            key=lambda i: -entries[i][POP],
        )[:5000]


_index: _Index | None = None
_lock = threading.Lock()


def _entry(appid: int, name: str, pop: float, norm: str | None = None) -> tuple:
    norm = _normalize(name) if norm is None else norm
    return (int(appid), name, norm, norm.replace(" ", ""), _acronym(norm), float(pop))


def _load_entries() -> list[tuple]:
    if os.path.exists(INDEX_PATH):
        with gzip.open(INDEX_PATH, "rt", encoding="utf-8") as f:
            rows = json.load(f)
        return [_entry(a, n, p) for a, n, p in rows if n]

    log.info("search: built index missing, using legacy games.json")
    with open(Config.LEGACY_GAMES_JSON, encoding="utf-8") as f:
        apps = json.load(f)["applist"]["apps"]
    seen: set[int] = set()
    entries = []
    for a in apps:
        name, appid = a.get("name"), a.get("appid")
        if not name or appid is None or appid in seen:
            continue
        norm = _normalize(name)
        if _is_junk_norm(norm):
            continue
        seen.add(appid)
        entries.append(_entry(appid, name, 4.5 if appid in _LEGACY_POPULAR else 0.0, norm))
    return entries


_warming = False


def warm() -> None:
    """Start building the index in the background (no-op once started).

    Called when a page is served, so the index is usually ready by the time
    someone types in the search box.
    """
    global _warming
    if _index is None and not _warming:
        _warming = True
        from services.http import executor

        executor.submit(_get_index)


def _get_index() -> _Index:
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                t = time.perf_counter()
                _index = _Index(_load_entries())
                log.info("search: indexed %d games in %.2fs", len(_index.entries), time.perf_counter() - t)
    return _index


# ══════════════════════════════════════════════════════════
#  Public API
# ══════════════════════════════════════════════════════════

def find_matches(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Up to *limit* games for *query*, best first: ``{"appid", "name", "score"}``."""
    q_norm = _normalize(query)
    if not q_norm:
        return []
    q_compact = q_norm.replace(" ", "")
    q_tokens = q_norm.split()
    idx = _get_index()
    entries = idx.entries

    # 1) Candidates.
    if len(q_compact) == 1:
        cand = [i for i in idx.popular if entries[i][NORM].startswith(q_compact)]
    else:
        longest = max(q_tokens, key=len)
        cand_set = set(idx.buckets.get(longest[:2], ()))
        if len(q_tokens) == 1:
            # "counterstrike" should still find "Counter-Strike".
            cand_set.update(idx.buckets.get(q_compact[:2], ()))
        if len(q_compact) <= 6:
            # Short queries may be acronyms: "bg3", "gta 5", "ff7".
            cand_set.update(idx.buckets.get("@" + q_compact[:2], ()))
        cand = list(cand_set)
    if not cand:
        return []

    # 2) Fuzzy scores in one C-level pass.
    fuzzy = {
        cand[j]: s
        for _, s, j in process.extract(
            q_norm,
            [entries[i][NORM] for i in cand],
            scorer=fuzz.token_set_ratio,
            limit=300,
            score_cutoff=60,
        )
    }

    # 3) Cheap string checks over every candidate, then combine.
    scored: list[tuple[float, int]] = []
    for i in cand:
        e = entries[i]
        norm, compact, acro = e[NORM], e[COMPACT], e[ACRO]
        exact = norm == q_norm or compact == q_compact
        prefix = norm.startswith(q_norm) or compact.startswith(q_compact)
        words = norm.split()
        word_prefix = all(any(w.startswith(t) for w in words) for t in q_tokens)
        substr = q_compact in compact
        acro_hit = len(q_compact) >= 2 and (acro == q_compact or (len(q_compact) >= 3 and acro.startswith(q_compact)))
        fz = fuzzy.get(i, 0.0)
        if not (exact or prefix or word_prefix or substr or acro_hit or fz):
            continue

        # Popularity (log10 of review count, 0–7) is weighted so a well-known
        # game outranks an obscure one with the exact same short name.
        score = fz + 12.0 * e[POP]
        if exact:
            score += 40
        if prefix:
            score += 60
        elif word_prefix:
            score += 45
        elif substr:
            score += 28
        if acro_hit:
            score += 110 if acro == q_compact else 40
        scored.append((score, i))

    scored.sort(key=lambda t: (-t[0], len(entries[t[1]][NAME])))
    return [
        {"appid": entries[i][APPID], "name": entries[i][NAME], "score": round(s, 1)}
        for s, i in scored[:limit]
    ]


def search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Local matches, topped up with Steam's store search when they're weak.

    Steam's search knows releases newer than our index; its hits slot in
    after our strong matches.
    """
    from services import steam
    from services.http import UpstreamError

    local = find_matches(query, limit)
    strong = [r for r in local if r["score"] >= 100]
    if len(strong) >= min(limit, 3) or len(query.strip()) < 3:
        return local
    try:
        items = steam.store_search(query.strip())
    except UpstreamError:
        return local
    seen = {r["appid"] for r in local}
    extra = [
        {"appid": int(it["id"]), "name": it["name"], "score": 0.0, "image": it.get("tiny_image")}
        for it in items
        if it.get("type") == "app" and it.get("id") and int(it["id"]) not in seen
    ]
    weak = [r for r in local if r["score"] < 100]
    return (strong + extra + weak)[:limit]


def find_appid_by_name(query: str) -> int | None:
    """Best matching app ID for a free-text name, or ``None``."""
    hits = find_matches(query, limit=1)
    return hits[0]["appid"] if hits else None


def name_for(appid: int) -> str | None:
    idx = _get_index()
    i = idx.by_appid.get(int(appid))
    return idx.entries[i][NAME] if i is not None else None

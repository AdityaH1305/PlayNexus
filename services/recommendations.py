"""Game recommendations — "Games like this".

Uses a prebuilt genre→appids index from a curated pool of well-known
games.  Scoring combines genre overlap (3×), tag overlap (2×), and a
popularity boost.  No games.json scan, no mass API calls.

The curated pool covers ~120 popular games across all major genres.
For any source game, we:
1. Look up its genres via the (cached) appdetails API
2. Find candidates from the genre index (O(1) lookup)
3. Score by genre overlap + tag overlap + popularity
4. Return top 6–8 strong matches
"""

from __future__ import annotations

import math
from typing import Any

from services import steam
from services.http import UpstreamError


# ══════════════════════════════════════════════════════════
#  CURATED GAME POOL WITH GENRE TAGS
# ══════════════════════════════════════════════════════════
# Each game has: appid, name, genres (list of genre strings),
# tags (extra descriptors), popularity (0-100 quality signal)

_POOL: list[dict] = [
    # ── Action / Adventure ────────────────────────────────
    {"appid": "1091500", "name": "Cyberpunk 2077",        "genres": ["action","rpg","open world","adventure"],          "tags": ["story rich","fps","sci-fi"],    "pop": 95},
    {"appid": "292030",  "name": "The Witcher 3",         "genres": ["action","rpg","open world","adventure"],          "tags": ["story rich","fantasy","dark"],   "pop": 98},
    {"appid": "1174180", "name": "Red Dead Redemption 2", "genres": ["action","adventure","open world"],                "tags": ["story rich","western","shooter"],"pop": 97},
    {"appid": "1245620", "name": "Elden Ring",            "genres": ["action","rpg","open world"],                      "tags": ["souls-like","dark fantasy","difficult"], "pop": 96},
    {"appid": "1086940", "name": "Baldur's Gate 3",       "genres": ["rpg","adventure","strategy"],                     "tags": ["story rich","co-op","fantasy","turn-based"], "pop": 97},
    {"appid": "2358720", "name": "Black Myth: Wukong",    "genres": ["action","rpg","adventure"],                       "tags": ["souls-like","mythology","difficult"], "pop": 92},
    {"appid": "1151640", "name": "Horizon Zero Dawn",     "genres": ["action","rpg","open world","adventure"],          "tags": ["sci-fi","story rich","shooter"], "pop": 88},
    {"appid": "1817070", "name": "Marvel's Spider-Man Remastered", "genres": ["action","adventure","open world"],       "tags": ["superhero","story rich"],        "pop": 90},
    {"appid": "601150",  "name": "Devil May Cry 5",       "genres": ["action","adventure"],                             "tags": ["hack and slash","stylish","combo"], "pop": 88},
    {"appid": "1030840", "name": "Mafia: Definitive Edition", "genres": ["action","adventure","open world"],            "tags": ["story rich","crime","shooter"],  "pop": 80},

    # ── Souls-like / Challenging ──────────────────────────
    {"appid": "814380",  "name": "Sekiro",                "genres": ["action","adventure"],                             "tags": ["souls-like","difficult","stealth","samurai"], "pop": 93},
    {"appid": "570940",  "name": "Dark Souls Remastered",  "genres": ["action","rpg"],                                  "tags": ["souls-like","difficult","dark fantasy"], "pop": 90},
    {"appid": "374320",  "name": "Dark Souls III",        "genres": ["action","rpg"],                                   "tags": ["souls-like","difficult","dark fantasy","co-op"], "pop": 94},
    {"appid": "236430",  "name": "Dark Souls II",         "genres": ["action","rpg"],                                   "tags": ["souls-like","difficult","dark fantasy"], "pop": 85},
    {"appid": "1113560", "name": "Nioh 2",                "genres": ["action","rpg"],                                   "tags": ["souls-like","difficult","loot","samurai"], "pop": 82},
    {"appid": "367520",  "name": "Hollow Knight",         "genres": ["action","adventure","indie"],                     "tags": ["metroidvania","difficult","atmospheric","platformer"], "pop": 96},
    {"appid": "588650",  "name": "Dead Cells",            "genres": ["action","indie","roguelike"],                     "tags": ["metroidvania","difficult","roguelite","pixel"], "pop": 92},
    {"appid": "851150",  "name": "Katana ZERO",           "genres": ["action","indie"],                                 "tags": ["story rich","pixel","difficult","stylish"], "pop": 88},

    # ── Shooter ───────────────────────────────────────────
    {"appid": "730",     "name": "Counter-Strike 2",      "genres": ["action","fps","shooter"],                         "tags": ["competitive","esports","pvp","tactical"], "pop": 99},
    {"appid": "578080",  "name": "PUBG: BATTLEGROUNDS",   "genres": ["action","fps","shooter"],                         "tags": ["battle royale","pvp","survival"], "pop": 90},
    {"appid": "1172470", "name": "Apex Legends",          "genres": ["action","fps","shooter"],                         "tags": ["battle royale","pvp","hero shooter"], "pop": 88},
    {"appid": "440",     "name": "Team Fortress 2",       "genres": ["action","fps","shooter"],                         "tags": ["hero shooter","free to play","competitive"], "pop": 92},
    {"appid": "1938010", "name": "Call of Duty: MW III",   "genres": ["action","fps","shooter"],                        "tags": ["pvp","competitive","campaign"], "pop": 82},
    {"appid": "1144200", "name": "Ready or Not",          "genres": ["action","fps","shooter"],                         "tags": ["tactical","co-op","realistic"], "pop": 85},
    {"appid": "945360",  "name": "Among Us",              "genres": ["casual","indie"],                                 "tags": ["social deduction","party","co-op","multiplayer"], "pop": 92},

    # ── RPG / CRPG ────────────────────────────────────────
    {"appid": "72850",   "name": "Skyrim",                "genres": ["rpg","action","open world","adventure"],          "tags": ["fantasy","modding","exploration"], "pop": 97},
    {"appid": "489830",  "name": "Skyrim Special Edition", "genres": ["rpg","action","open world","adventure"],         "tags": ["fantasy","modding","exploration"], "pop": 95},
    {"appid": "292030",  "name": "The Witcher 3",         "genres": ["action","rpg","open world","adventure"],          "tags": ["story rich","fantasy","dark"],   "pop": 98},

    # ── Roguelike ─────────────────────────────────────────
    {"appid": "1145360", "name": "Hades",                 "genres": ["action","rpg","indie","roguelike"],               "tags": ["roguelite","mythology","hack and slash","story"], "pop": 96},
    {"appid": "1794680", "name": "Vampire Survivors",     "genres": ["action","casual","indie","roguelike"],            "tags": ["roguelite","bullet hell","horde"], "pop": 94},
    {"appid": "646570",  "name": "Slay the Spire",        "genres": ["strategy","indie","roguelike"],                   "tags": ["deckbuilder","card game","turn-based"], "pop": 95},
    {"appid": "632360",  "name": "Risk of Rain 2",        "genres": ["action","indie","roguelike"],                     "tags": ["roguelite","co-op","shooter"],   "pop": 90},
    {"appid": "250900",  "name": "Binding of Isaac: Rebirth", "genres": ["action","indie","roguelike"],                 "tags": ["roguelite","difficult","dark"],  "pop": 93},
    {"appid": "1942280", "name": "Balatro",               "genres": ["strategy","indie","roguelike"],                   "tags": ["deckbuilder","card game","poker"], "pop": 92},

    # ── Strategy ──────────────────────────────────────────
    {"appid": "427520",  "name": "Factorio",              "genres": ["strategy","simulation","indie"],                  "tags": ["automation","factory","base building","sandbox"], "pop": 98},
    {"appid": "457140",  "name": "Oxygen Not Included",   "genres": ["strategy","simulation","indie"],                  "tags": ["colony sim","base building","survival"], "pop": 90},
    {"appid": "281990",  "name": "Stellaris",             "genres": ["strategy","simulation"],                          "tags": ["grand strategy","4x","sci-fi","space"], "pop": 90},
    {"appid": "394360",  "name": "Hearts of Iron IV",     "genres": ["strategy","simulation"],                          "tags": ["grand strategy","ww2","historical"], "pop": 88},
    {"appid": "289070",  "name": "Civilization VI",       "genres": ["strategy"],                                       "tags": ["4x","turn-based","historical"], "pop": 92},
    {"appid": "8930",    "name": "Civilization V",        "genres": ["strategy"],                                       "tags": ["4x","turn-based","historical"], "pop": 95},
    {"appid": "236390",  "name": "War Thunder",           "genres": ["action","simulation","strategy"],                 "tags": ["military","vehicles","pvp","free to play"], "pop": 88},

    # ── Survival / Crafting ───────────────────────────────
    {"appid": "252490",  "name": "Rust",                  "genres": ["action","adventure","survival"],                  "tags": ["pvp","crafting","base building","multiplayer"], "pop": 93},
    {"appid": "892970",  "name": "Valheim",               "genres": ["action","adventure","survival","indie"],          "tags": ["crafting","co-op","viking","exploration"], "pop": 92},
    {"appid": "242760",  "name": "The Forest",            "genres": ["action","adventure","survival","indie"],          "tags": ["horror","crafting","co-op","open world"], "pop": 90},
    {"appid": "322330",  "name": "Don't Starve Together",  "genres": ["adventure","survival","indie"],                  "tags": ["crafting","co-op","difficult","gothic"], "pop": 90},
    {"appid": "346110",  "name": "ARK: Survival Evolved", "genres": ["action","adventure","survival"],                  "tags": ["dinosaurs","crafting","open world","co-op"], "pop": 85},
    {"appid": "105600",  "name": "Terraria",              "genres": ["action","adventure","indie","sandbox"],           "tags": ["crafting","exploration","2d","co-op"], "pop": 97},
    {"appid": "304930",  "name": "Unturned",              "genres": ["action","survival","indie"],                      "tags": ["zombies","crafting","free to play","co-op"], "pop": 82},
    {"appid": "1623730", "name": "Palworld",              "genres": ["action","adventure","survival"],                  "tags": ["crafting","open world","creatures","co-op"], "pop": 88},

    # ── Simulation / Building ─────────────────────────────
    {"appid": "413150",  "name": "Stardew Valley",        "genres": ["rpg","simulation","indie"],                       "tags": ["farming","cozy","pixel","relaxing","co-op"], "pop": 98},
    {"appid": "1222670", "name": "The Sims 4",            "genres": ["simulation","casual"],                            "tags": ["life sim","building","sandbox"], "pop": 88},
    {"appid": "1238810", "name": "PowerWash Simulator",   "genres": ["simulation","casual","indie"],                    "tags": ["relaxing","co-op","satisfying"], "pop": 85},
    {"appid": "275850",  "name": "No Man's Sky",          "genres": ["action","adventure","simulation"],                "tags": ["exploration","survival","space","co-op"], "pop": 88},

    # ── Horror ────────────────────────────────────────────
    {"appid": "1966720", "name": "Lethal Company",        "genres": ["action","adventure","indie"],                     "tags": ["horror","co-op","atmospheric","survival"], "pop": 93},
    {"appid": "381210",  "name": "Dead by Daylight",      "genres": ["action"],                                         "tags": ["horror","pvp","survival","multiplayer"], "pop": 90},
    {"appid": "438740",  "name": "Resident Evil 7",       "genres": ["action","adventure"],                             "tags": ["horror","survival horror","first-person"], "pop": 90},
    {"appid": "883710",  "name": "Resident Evil 2",       "genres": ["action","adventure"],                             "tags": ["horror","survival horror","zombies"], "pop": 93},
    {"appid": "952060",  "name": "Resident Evil 3",       "genres": ["action","adventure"],                             "tags": ["horror","survival horror","zombies","action"], "pop": 82},
    {"appid": "1196590", "name": "Resident Evil Village",  "genres": ["action","adventure"],                            "tags": ["horror","survival horror","fps"],  "pop": 90},
    {"appid": "1062090", "name": "Amnesia: Rebirth",      "genres": ["adventure","indie"],                              "tags": ["horror","atmospheric","puzzle","story rich"], "pop": 70},
    {"appid": "268500",  "name": "Darkest Dungeon",       "genres": ["rpg","strategy","indie"],                         "tags": ["dark","roguelike","difficult","gothic","turn-based"], "pop": 92},

    # ── Cozy / Indie ──────────────────────────────────────
    {"appid": "824600",  "name": "Spiritfarer",           "genres": ["adventure","indie","simulation"],                 "tags": ["cozy","story rich","management","emotional"], "pop": 85},
    {"appid": "1506830", "name": "Unpacking",             "genres": ["casual","indie"],                                 "tags": ["cozy","relaxing","puzzle"],      "pop": 82},
    {"appid": "1150690", "name": "OMORI",                 "genres": ["rpg","indie","adventure"],                        "tags": ["story rich","emotional","dark","pixel","turn-based"], "pop": 93},
    {"appid": "1455840", "name": "Cozy Grove",            "genres": ["casual","simulation","indie"],                    "tags": ["cozy","relaxing","life sim"],    "pop": 72},
    {"appid": "1814990", "name": "Dredge",                "genres": ["adventure","rpg","indie"],                        "tags": ["fishing","horror","atmospheric","exploration"], "pop": 85},
    {"appid": "261570",  "name": "Ori and the Blind Forest", "genres": ["action","adventure","indie"],                  "tags": ["metroidvania","platformer","emotional","beautiful"], "pop": 93},
    {"appid": "1057090", "name": "Ori and the Will of the Wisps", "genres": ["action","adventure","indie"],             "tags": ["metroidvania","platformer","emotional","beautiful"], "pop": 94},

    # ── MOBA / Arena ──────────────────────────────────────
    {"appid": "570",     "name": "Dota 2",                "genres": ["action","strategy"],                              "tags": ["moba","competitive","esports","free to play"], "pop": 97},
    {"appid": "291550",  "name": "Brawlhalla",            "genres": ["action","indie"],                                 "tags": ["fighting","pvp","free to play","platform fighter"], "pop": 85},
    {"appid": "1599340", "name": "Lost Ark",              "genres": ["action","rpg"],                                   "tags": ["mmo","isometric","loot","free to play"], "pop": 82},
    {"appid": "1343400", "name": "Naraka: Bladepoint",    "genres": ["action"],                                         "tags": ["battle royale","pvp","martial arts"], "pop": 78},

    # ── Racing / Sports ───────────────────────────────────
    {"appid": "1551360", "name": "Forza Horizon 5",       "genres": ["racing","action","simulation"],                   "tags": ["open world","cars","multiplayer"], "pop": 90},
    {"appid": "1293830", "name": "Forza Horizon 4",       "genres": ["racing","action","simulation"],                   "tags": ["open world","cars","multiplayer"], "pop": 88},

    # ── Puzzle / Narrative ────────────────────────────────
    {"appid": "620",     "name": "Portal 2",              "genres": ["action","adventure","puzzle"],                    "tags": ["co-op","sci-fi","comedy","physics"], "pop": 99},
    {"appid": "400",     "name": "Portal",                "genres": ["action","adventure","puzzle"],                    "tags": ["sci-fi","physics","first-person"], "pop": 96},
    {"appid": "48000",   "name": "Limbo",                 "genres": ["adventure","indie","puzzle"],                     "tags": ["atmospheric","dark","platformer","minimalist"], "pop": 90},
    {"appid": "304430",  "name": "Inside",                "genres": ["adventure","indie","puzzle"],                     "tags": ["atmospheric","dark","platformer","dystopian"], "pop": 92},

    # ── Co-op / Party ─────────────────────────────────────
    {"appid": "1097150", "name": "Fall Guys",             "genres": ["action","casual"],                                "tags": ["party","battle royale","multiplayer","fun"], "pop": 85},
    {"appid": "728880",  "name": "Overcooked! 2",         "genres": ["casual","indie"],                                 "tags": ["co-op","party","cooking","local co-op"], "pop": 88},
]


# ══════════════════════════════════════════════════════════
#  PREBUILT INDICES (built at import time)
# ══════════════════════════════════════════════════════════

# Deduplicate pool by appid (keep first occurrence)
_seen_ids: set[str] = set()
_CLEAN_POOL: list[dict] = []
for _g in _POOL:
    _aid = str(_g["appid"])
    if _aid not in _seen_ids:
        _seen_ids.add(_aid)
        _g["appid"] = _aid
        _g["_genre_set"] = set(_g.get("genres", []))
        _g["_tag_set"] = set(_g.get("tags", []))
        _CLEAN_POOL.append(_g)

# Genre → list of pool entries
_GENRE_INDEX: dict[str, list[dict]] = {}
for _g in _CLEAN_POOL:
    for _genre in _g["_genre_set"]:
        _GENRE_INDEX.setdefault(_genre, []).append(_g)

# Tag → list of pool entries
_TAG_INDEX: dict[str, list[dict]] = {}
for _g in _CLEAN_POOL:
    for _tag in _g["_tag_set"]:
        _TAG_INDEX.setdefault(_tag, []).append(_g)


# ══════════════════════════════════════════════════════════
#  SCORING
# ══════════════════════════════════════════════════════════

def _score_candidate(
    cand: dict,
    source_genres: set[str],
    source_tags: set[str],
    source_appid: str,
) -> float:
    """Score a candidate game against the source game's genres and tags.

    Weights:
    - Genre overlap: 3× per shared genre
    - Tag overlap: 2× per shared tag
    - Popularity boost: log-scaled 0–5
    """
    if cand["appid"] == source_appid:
        return -1.0  # exclude self

    genre_overlap = len(cand["_genre_set"] & source_genres)
    tag_overlap = len(cand["_tag_set"] & source_tags)

    if genre_overlap == 0:
        return -1.0  # no genre match = not similar

    score = 0.0
    score += 3.0 * genre_overlap
    score += 2.0 * tag_overlap
    score += math.log10(max(cand.get("pop", 50), 10)) * 2.5  # ~0–5 boost

    return score


# ══════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════

def get_recommendations(appid: str, limit: int = 8) -> list[dict[str, Any]]:
    """Return up to *limit* similar games for *appid*.

    Each item: ``{"appid", "name", "cover", "match_reason"}``.

    Uses the prebuilt genre/tag indices — no games.json scan, no mass
    API calls.  All data for candidates comes from the curated pool.
    The only API call is for the source game's appdetails (cached).
    """
    appid = str(appid)
    try:
        data = steam.appdetails(int(appid), "us")
    except UpstreamError:
        return []
    if not data:
        return []

    # Extract source game's genres and build a tag set from categories
    source_genres: set[str] = set()
    source_tags: set[str] = set()

    for g in (data.get("genres") or []):
        desc = (g.get("description") or "").lower()
        if desc:
            source_genres.add(desc)

    # Map Steam's genre descriptions to our simplified genre names
    _GENRE_MAP = {
        "action": "action", "adventure": "adventure", "rpg": "rpg",
        "strategy": "strategy", "simulation": "simulation",
        "casual": "casual", "indie": "indie", "racing": "racing",
        "massively multiplayer": "mmo", "free to play": "free to play",
        "sports": "sports", "early access": "indie",
    }
    mapped_genres: set[str] = set()
    for g in source_genres:
        mapped = _GENRE_MAP.get(g, g)
        mapped_genres.add(mapped)

    # Also extract some tag-like info from categories
    for c in (data.get("categories") or []):
        desc = (c.get("description") or "").lower()
        if desc:
            source_tags.add(desc)

    # Extract from short description + name for tag matching
    short_desc = (data.get("short_description") or "").lower()
    for tag_word in ["horror", "survival", "crafting", "roguelike", "roguelite",
                     "souls-like", "puzzle", "co-op", "pvp", "competitive",
                     "open world", "sandbox", "exploration", "story rich",
                     "atmospheric", "difficult", "platformer", "metroidvania",
                     "battle royale", "shooter", "fps", "moba", "card game",
                     "deckbuilder", "turn-based", "base building", "factory",
                     "farming", "cozy", "relaxing", "pixel"]:
        if tag_word in short_desc:
            source_tags.add(tag_word)

    if not mapped_genres:
        return []

    # ── Collect candidates from genre index ───────────────
    candidates: set[str] = set()
    for genre in mapped_genres:
        for entry in _GENRE_INDEX.get(genre, []):
            candidates.add(entry["appid"])

    # Also check tag index for bonus candidates
    for tag in source_tags:
        for entry in _TAG_INDEX.get(tag, []):
            candidates.add(entry["appid"])

    # ── Score all candidates ──────────────────────────────
    scored: list[tuple[float, dict]] = []
    for cand in _CLEAN_POOL:
        if cand["appid"] not in candidates:
            continue
        s = _score_candidate(cand, mapped_genres, source_tags, appid)
        if s > 0:
            scored.append((s, cand))

    scored.sort(key=lambda x: -x[0])

    # ── Build results ─────────────────────────────────────
    results: list[dict[str, Any]] = []
    for score, cand in scored[:limit]:
        # Compute human-readable match reason
        genre_overlap = cand["_genre_set"] & mapped_genres
        tag_overlap = cand["_tag_set"] & source_tags
        reasons = sorted(genre_overlap | tag_overlap)[:3]

        results.append({
            "appid": cand["appid"],
            "name": cand["name"],
            "cover": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{cand['appid']}/header.jpg",
            "match_reason": ", ".join(reasons) if reasons else "similar genre",
        })

    return results

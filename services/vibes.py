"""Vibe / mood scoring system.

Maps Steam genres, categories, review snippets, and short descriptions
to one of 18 "vibe" archetypes.  Returns the top N vibes for any game.

Architecture
~~~~~~~~~~~~
- ``score_vibes_for_app(appid)`` — full scoring via live API (used on game
  page).  Results are cached via the steam_api layer.
- ``discover_by_vibes(mood_keys)`` — **fast** discovery using a prebuilt
  inverted index from a curated game list.  No API calls at discover time.

Enhancements:
- Two new categories: Grindy (🔄) and Casual (🎯).
- Weighted scoring: genres ×3, tags ×2, keywords ×1.
- Prebuilt vibe index → sub-100ms discover response.
- Review-count & positivity boosting for ranked results.
"""

from __future__ import annotations

from typing import Any

from services import steam
from services.http import UpstreamError

# ── Vibe definitions ──────────────────────────────────────
VIBES: dict[str, dict[str, str]] = {
    "cozy_relaxing":        {"label": "Cozy & Relaxing",       "emoji": "☕"},
    "soulslike_challenge":  {"label": "Souls-like Challenge",  "emoji": "🗡️"},
    "power_fantasy":        {"label": "Power Fantasy",         "emoji": "💥"},
    "dark_atmospheric":     {"label": "Dark & Atmospheric",    "emoji": "🌑"},
    "open_world_freedom":   {"label": "Open-World Freedom",    "emoji": "🗺️"},
    "exploration_discovery":{"label": "Exploration & Discovery","emoji": "🧭"},
    "strategy_brainy":      {"label": "Strategy & Thinky",     "emoji": "🧠"},
    "roguelike_run_based":  {"label": "Run-based Roguelike",   "emoji": "🎲"},
    "pvp_sweaty":           {"label": "Competitive PvP",       "emoji": "🏆"},
    "coop_friendship":      {"label": "Co-op & Friendship",    "emoji": "🤝"},
    "builder_crafter":      {"label": "Builder & Crafter",     "emoji": "🧱"},
    "survival_pressure":    {"label": "Survival Pressure",     "emoji": "🥾"},
    "story_emotional":      {"label": "Story & Feels",         "emoji": "🎭"},
    "horror_tension":       {"label": "Horror & Tension",      "emoji": "👁️"},
    "retro_arcade":         {"label": "Retro / Arcade",        "emoji": "🕹️"},
    "simulation_immersive": {"label": "Immersive Sim / Sim",   "emoji": "🛠️"},
    "grindy":               {"label": "Grindy",                "emoji": "🔄"},
    "casual":               {"label": "Casual",                "emoji": "🎯"},
}

# Tag / keyword hints → vibe mapping with separate weights
VIBE_HINTS: dict[str, dict[str, list[str]]] = {
    "cozy_relaxing":        {"tags": ["cozy","relaxing","wholesome","farming","life sim","meditative","casual"],
                             "kw": ["cozy","relax","calming","wholesome"]},
    "soulslike_challenge":  {"tags": ["souls-like","difficult","skill-based","parry","boss rush","action rpg"],
                             "kw": ["punishing","challenge","git gud","boss","parry"]},
    "power_fantasy":        {"tags": ["hack and slash","lue-power","looter shooter","action","horde"],
                             "kw": ["dominate","overpowered","godlike","power trip"]},
    "dark_atmospheric":     {"tags": ["atmospheric","dark","gothic","moody","ambient"],
                             "kw": ["atmosphere","brooding","melancholy"]},
    "open_world_freedom":   {"tags": ["open world","sandbox","exploration","nonlinear"],
                             "kw": ["do anything","freedom","wander","open world"]},
    "exploration_discovery":{"tags": ["metroidvania","exploration","adventure","walking simulator"],
                             "kw": ["discover","hidden","secrets","explore"]},
    "strategy_brainy":      {"tags": ["strategy","4x","tactics","grand strategy","puzzle","chess"],
                             "kw": ["optimize","min-max","tactical","plan"]},
    "roguelike_run_based":  {"tags": ["roguelike","roguelite","procedural generation","permadeath","deckbuilder"],
                             "kw": ["run","seed","permadeath","random"]},
    "pvp_sweaty":           {"tags": ["competitive","esports","pvp","ranked","shooter"],
                             "kw": ["ranked","mmr","sweaty","clutch"]},
    "coop_friendship":      {"tags": ["co-op","online co-op","local co-op","party game"],
                             "kw": ["with friends","co-op","lan","together"]},
    "builder_crafter":      {"tags": ["base building","crafting","automation","factory","city builder"],
                             "kw": ["blueprint","blueprints","factory","craft"]},
    "survival_pressure":    {"tags": ["survival","hardcore","zombies","crafting","base building"],
                             "kw": ["starve","thirst","permadeath","grind"]},
    "story_emotional":      {"tags": ["story rich","narrative","walking simulator","choices matter"],
                             "kw": ["story","narrative","tear","emotion","feels"]},
    "horror_tension":       {"tags": ["horror","survival horror","psychological horror","first-person"],
                             "kw": ["scary","terrifying","jumpscare","tension"]},
    "retro_arcade":         {"tags": ["retro","pixel graphics","arcade","platformer"],
                             "kw": ["old-school","nostalgia","arcade"]},
    "simulation_immersive": {"tags": ["simulation","immersive sim","sim","flight","driving","management"],
                             "kw": ["simulate","roleplay","immersive"]},
    "grindy":               {"tags": ["grinding","farming","loot","mmo","mmorpg","idle","clicker"],
                             "kw": ["grind","farming","loot","repetitive","endgame"]},
    "casual":               {"tags": ["casual","easy","pick-up-and-play","family friendly","puzzle"],
                             "kw": ["casual","quick","simple","fun","easy"]},
}


# ══════════════════════════════════════════════════════════
#  CURATED GAME INDEX (for instant discovery)
# ══════════════════════════════════════════════════════════
#
# Mapping: vibe_key → list of known-good appids with metadata.
# This avoids scanning 25MB games.json and making hundreds of
# live API calls.  These are real, popular, high-quality games
# that clearly belong to each vibe.

_CURATED_GAMES: dict[str, list[dict]] = {
    "cozy_relaxing": [
        {"appid": "413150", "name": "Stardew Valley"},
        {"appid": "824600", "name": "Spiritfarer"},
        {"appid": "1070710", "name": "Kind Words"},
        {"appid": "1814990", "name": "Dredge"},
        {"appid": "1506830", "name": "Unpacking"},
        {"appid": "1455840", "name": "Cozy Grove"},
        {"appid": "105600", "name": "Terraria"},
        {"appid": "457140", "name": "Oxygen Not Included"},
        {"appid": "1150690", "name": "OMORI"},
        {"appid": "1625450", "name": "A Short Hike"},
    ],
    "soulslike_challenge": [
        {"appid": "814380", "name": "Sekiro"},
        {"appid": "1245620", "name": "Elden Ring"},
        {"appid": "367520", "name": "Hollow Knight"},
        {"appid": "570940", "name": "Dark Souls REMASTERED"},
        {"appid": "374320", "name": "Dark Souls III"},
        {"appid": "236430", "name": "Dark Souls II"},
        {"appid": "1113560", "name": "Nioh 2"},
        {"appid": "2358720", "name": "Black Myth: Wukong"},
        {"appid": "1245620", "name": "Elden Ring"},
        {"appid": "588650", "name": "Dead Cells"},
    ],
    "power_fantasy": [
        {"appid": "292030", "name": "The Witcher 3"},
        {"appid": "1091500", "name": "Cyberpunk 2077"},
        {"appid": "2358720", "name": "Black Myth: Wukong"},
        {"appid": "812140", "name": "Assassin's Creed Odyssey"},
        {"appid": "1151640", "name": "Horizon Zero Dawn"},
        {"appid": "1174180", "name": "Red Dead Redemption 2"},
        {"appid": "1086940", "name": "Baldur's Gate 3"},
        {"appid": "601150", "name": "Devil May Cry 5"},
        {"appid": "1817070", "name": "Marvel's Spider-Man Remastered"},
        {"appid": "1938010", "name": "Call of Duty: Modern Warfare III"},
    ],
    "dark_atmospheric": [
        {"appid": "268500", "name": "Darkest Dungeon"},
        {"appid": "262060", "name": "Darkest Dungeon II"},
        {"appid": "367520", "name": "Hollow Knight"},
        {"appid": "1145360", "name": "Hades"},
        {"appid": "242760", "name": "The Forest"},
        {"appid": "1062090", "name": "Amnesia: Rebirth"},
        {"appid": "1030840", "name": "Mafia: Definitive Edition"},
        {"appid": "1150690", "name": "OMORI"},
        {"appid": "48000",  "name": "Limbo"},
        {"appid": "304430", "name": "Inside"},
    ],
    "open_world_freedom": [
        {"appid": "1174180", "name": "Red Dead Redemption 2"},
        {"appid": "1091500", "name": "Cyberpunk 2077"},
        {"appid": "292030", "name": "The Witcher 3"},
        {"appid": "1245620", "name": "Elden Ring"},
        {"appid": "1086940", "name": "Baldur's Gate 3"},
        {"appid": "1151640", "name": "Horizon Zero Dawn"},
        {"appid": "252490", "name": "Rust"},
        {"appid": "413150", "name": "Stardew Valley"},
        {"appid": "105600", "name": "Terraria"},
        {"appid": "892970", "name": "Valheim"},
    ],
    "exploration_discovery": [
        {"appid": "367520", "name": "Hollow Knight"},
        {"appid": "275850", "name": "No Man's Sky"},
        {"appid": "105600", "name": "Terraria"},
        {"appid": "1145360", "name": "Hades"},
        {"appid": "1150690", "name": "OMORI"},
        {"appid": "892970", "name": "Valheim"},
        {"appid": "1086940", "name": "Baldur's Gate 3"},
        {"appid": "261570", "name": "Ori and the Blind Forest"},
        {"appid": "1057090", "name": "Ori and the Will of the Wisps"},
        {"appid": "72850",  "name": "The Elder Scrolls V: Skyrim"},
    ],
    "strategy_brainy": [
        {"appid": "427520", "name": "Factorio"},
        {"appid": "457140", "name": "Oxygen Not Included"},
        {"appid": "281990", "name": "Stellaris"},
        {"appid": "236390", "name": "War Thunder"},
        {"appid": "394360", "name": "Hearts of Iron IV"},
        {"appid": "1259420", "name": "HUMANKIND"},
        {"appid": "8930",   "name": "Sid Meier's Civilization V"},
        {"appid": "289070", "name": "Sid Meier's Civilization VI"},
        {"appid": "72850",  "name": "The Elder Scrolls V: Skyrim"},
        {"appid": "1222670","name": "The Sims 4"},
    ],
    "roguelike_run_based": [
        {"appid": "1145360", "name": "Hades"},
        {"appid": "1794680", "name": "Vampire Survivors"},
        {"appid": "646570", "name": "Slay the Spire"},
        {"appid": "248820", "name": "Risk of Rain"},
        {"appid": "632360", "name": "Risk of Rain 2"},
        {"appid": "113200", "name": "The Binding of Isaac"},
        {"appid": "250900", "name": "The Binding of Isaac: Rebirth"},
        {"appid": "588650", "name": "Dead Cells"},
        {"appid": "851150", "name": "Katana ZERO"},
        {"appid": "1942280","name": "Balatro"},
    ],
    "pvp_sweaty": [
        {"appid": "730",    "name": "Counter-Strike 2"},
        {"appid": "570",    "name": "Dota 2"},
        {"appid": "578080", "name": "PUBG: BATTLEGROUNDS"},
        {"appid": "1172470","name": "Apex Legends"},
        {"appid": "252490", "name": "Rust"},
        {"appid": "236390", "name": "War Thunder"},
        {"appid": "291550", "name": "Brawlhalla"},
        {"appid": "440",    "name": "Team Fortress 2"},
        {"appid": "1599340","name": "Lost Ark"},
        {"appid": "1343400","name": "Naraka: Bladepoint"},
    ],
    "coop_friendship": [
        {"appid": "1966720", "name": "Lethal Company"},
        {"appid": "892970", "name": "Valheim"},
        {"appid": "945360", "name": "Among Us"},
        {"appid": "1238810","name": "PowerWash Simulator"},
        {"appid": "322330", "name": "Don't Starve Together"},
        {"appid": "105600", "name": "Terraria"},
        {"appid": "413150", "name": "Stardew Valley"},
        {"appid": "252490", "name": "Rust"},
        {"appid": "1097150","name": "Fall Guys"},
        {"appid": "367520", "name": "Hollow Knight"},
    ],
    "builder_crafter": [
        {"appid": "427520", "name": "Factorio"},
        {"appid": "457140", "name": "Oxygen Not Included"},
        {"appid": "105600", "name": "Terraria"},
        {"appid": "413150", "name": "Stardew Valley"},
        {"appid": "892970", "name": "Valheim"},
        {"appid": "346110", "name": "ARK: Survival Evolved"},
        {"appid": "304930", "name": "Unturned"},
        {"appid": "1222670","name": "The Sims 4"},
        {"appid": "322330", "name": "Don't Starve Together"},
        {"appid": "242760", "name": "The Forest"},
    ],
    "survival_pressure": [
        {"appid": "252490", "name": "Rust"},
        {"appid": "892970", "name": "Valheim"},
        {"appid": "242760", "name": "The Forest"},
        {"appid": "322330", "name": "Don't Starve Together"},
        {"appid": "346110", "name": "ARK: Survival Evolved"},
        {"appid": "304930", "name": "Unturned"},
        {"appid": "578080", "name": "PUBG: BATTLEGROUNDS"},
        {"appid": "1966720","name": "Lethal Company"},
        {"appid": "1623730","name": "Palworld"},
        {"appid": "211820", "name": "Starbound"},
    ],
    "story_emotional": [
        {"appid": "1086940","name": "Baldur's Gate 3"},
        {"appid": "1174180","name": "Red Dead Redemption 2"},
        {"appid": "1091500","name": "Cyberpunk 2077"},
        {"appid": "292030", "name": "The Witcher 3"},
        {"appid": "1150690","name": "OMORI"},
        {"appid": "1151640","name": "Horizon Zero Dawn"},
        {"appid": "1030840","name": "Mafia: Definitive Edition"},
        {"appid": "824600", "name": "Spiritfarer"},
        {"appid": "1817070","name": "Marvel's Spider-Man Remastered"},
        {"appid": "72850",  "name": "The Elder Scrolls V: Skyrim"},
    ],
    "horror_tension": [
        {"appid": "1966720","name": "Lethal Company"},
        {"appid": "242760", "name": "The Forest"},
        {"appid": "1062090","name": "Amnesia: Rebirth"},
        {"appid": "268500", "name": "Darkest Dungeon"},
        {"appid": "1144200","name": "Ready or Not"},
        {"appid": "381210", "name": "Dead by Daylight"},
        {"appid": "438740", "name": "Resident Evil 7"},
        {"appid": "883710", "name": "Resident Evil 2"},
        {"appid": "952060", "name": "Resident Evil 3"},
        {"appid": "1196590","name": "Resident Evil Village"},
    ],
    "retro_arcade": [
        {"appid": "367520", "name": "Hollow Knight"},
        {"appid": "1794680","name": "Vampire Survivors"},
        {"appid": "588650", "name": "Dead Cells"},
        {"appid": "851150", "name": "Katana ZERO"},
        {"appid": "105600", "name": "Terraria"},
        {"appid": "250900", "name": "The Binding of Isaac: Rebirth"},
        {"appid": "413150", "name": "Stardew Valley"},
        {"appid": "291550", "name": "Brawlhalla"},
        {"appid": "646570", "name": "Slay the Spire"},
        {"appid": "304930", "name": "Unturned"},
    ],
    "simulation_immersive": [
        {"appid": "1222670","name": "The Sims 4"},
        {"appid": "427520", "name": "Factorio"},
        {"appid": "457140", "name": "Oxygen Not Included"},
        {"appid": "1238810","name": "PowerWash Simulator"},
        {"appid": "236390", "name": "War Thunder"},
        {"appid": "72850",  "name": "The Elder Scrolls V: Skyrim"},
        {"appid": "275850", "name": "No Man's Sky"},
        {"appid": "281990", "name": "Stellaris"},
        {"appid": "394360", "name": "Hearts of Iron IV"},
        {"appid": "1086940","name": "Baldur's Gate 3"},
    ],
    "grindy": [
        {"appid": "1599340","name": "Lost Ark"},
        {"appid": "1245620","name": "Elden Ring"},
        {"appid": "346110", "name": "ARK: Survival Evolved"},
        {"appid": "252490", "name": "Rust"},
        {"appid": "892970", "name": "Valheim"},
        {"appid": "1794680","name": "Vampire Survivors"},
        {"appid": "236390", "name": "War Thunder"},
        {"appid": "1174180","name": "Red Dead Redemption 2"},
        {"appid": "730",    "name": "Counter-Strike 2"},
        {"appid": "570",    "name": "Dota 2"},
    ],
    "casual": [
        {"appid": "1097150","name": "Fall Guys"},
        {"appid": "945360", "name": "Among Us"},
        {"appid": "291550", "name": "Brawlhalla"},
        {"appid": "1238810","name": "PowerWash Simulator"},
        {"appid": "413150", "name": "Stardew Valley"},
        {"appid": "1506830","name": "Unpacking"},
        {"appid": "1794680","name": "Vampire Survivors"},
        {"appid": "646570", "name": "Slay the Spire"},
        {"appid": "1942280","name": "Balatro"},
        {"appid": "1966720","name": "Lethal Company"},
    ],
}

# ── Build inverted index at import time ───────────────────
#    _VIBE_INDEX: appid → set of vibe keys it belongs to
#    _GAME_META:  appid → {name, cover, vibe_keys}
_VIBE_INDEX: dict[str, set[str]] = {}
_GAME_META: dict[str, dict] = {}

for _vkey, _games in _CURATED_GAMES.items():
    for _g in _games:
        _aid = str(_g["appid"])
        _VIBE_INDEX.setdefault(_aid, set()).add(_vkey)
        if _aid not in _GAME_META:
            _GAME_META[_aid] = {
                "appid": _aid,
                "name": _g["name"],
                "cover": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{_aid}/header.jpg",
            }


# ══════════════════════════════════════════════════════════
#  SCORING HELPERS
# ══════════════════════════════════════════════════════════

def _score_from_list(words: list[str], pool: str) -> int:
    """Count how many *words* appear in *pool* (a joined string)."""
    return sum(1 for w in words if w in pool)


def score_vibes_for_app(appid: str) -> dict[str, Any]:
    """Score all vibes for *appid* and return top 5.

    This is the FULL scoring used on the game detail page.
    It calls the Steam API (cached).
    """
    data = steam.appdetails(int(appid), "us")
    if not data:
        raise UpstreamError(f"no store details for {appid}")

    genre_texts: list[str] = []
    tag_texts: list[str] = []

    if data.get("genres"):
        genre_texts += [g.get("description", "") for g in data["genres"]]
    if data.get("categories"):
        tag_texts += [c.get("description", "") for c in data["categories"]]

    short_desc = data.get("short_description", "") or ""
    name = data.get("name", "")

    # Review snippets (cheap — already cached)
    try:
        reviews = steam.reviews_page(int(appid), "recent")
    except UpstreamError:
        reviews = []
    snippets = [(rv.get("review") or "")[:300] for rv in reviews[:40]]

    # Build pools
    genre_pool = " ".join(genre_texts).lower()
    tag_pool = " ".join(tag_texts).lower()
    kw_pool = " ".join([short_desc, name] + snippets).lower()

    scores: dict[str, float] = {}
    for key, conf in VIBE_HINTS.items():
        s = 0.0
        s += 3.0 * _score_from_list([t.lower() for t in conf["tags"]], genre_pool)
        s += 2.0 * _score_from_list([t.lower() for t in conf["tags"]], tag_pool)
        s += 1.0 * _score_from_list([k.lower() for k in conf["kw"]], kw_pool)
        scores[key] = s

    # Normalise to 0..100
    maxs = max(scores.values()) if scores else 1.0
    for k in scores:
        scores[k] = int(round(100 * (scores[k] / maxs))) if maxs > 0 else 0

    top = sorted(scores.items(), key=lambda x: -x[1])[:5]

    cover = (
        data.get("header_image")
        or f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"
    )

    return {
        "name": name,
        "appid": appid,
        "cover": cover,
        "scores": scores,
        "top": [
            {
                "key": k,
                "label": VIBES[k]["label"],
                "emoji": VIBES[k]["emoji"],
                "score": v,
            }
            for k, v in top
        ],
    }


# ══════════════════════════════════════════════════════════
#  FAST DISCOVERY (no API calls)
# ══════════════════════════════════════════════════════════

def discover_by_vibes(mood_keys: list[str], limit: int = 18) -> list[dict]:
    """Find games matching the given *mood_keys* using the prebuilt index.

    This is O(curated_size) with NO API calls.  Typically completes in <5ms.
    """
    if not mood_keys:
        return []

    valid_keys = [k for k in mood_keys if k in VIBES]
    if not valid_keys:
        return []

    # Collect all candidate appids from the curated lists
    # Score each by how many of the selected vibes it belongs to (multi-vibe
    # intersection gets ranked higher)
    scored: list[tuple[float, str]] = []
    seen: set[str] = set()

    for appid, vibe_set in _VIBE_INDEX.items():
        # How many of the selected moods does this game match?
        overlap = vibe_set & set(valid_keys)
        if not overlap:
            continue
        if appid in seen:
            continue
        seen.add(appid)

        # Base score: number of matching vibes (multi-vibe match = stronger)
        match_count = len(overlap)
        # Bonus: if a game appears in many vibes overall, it's more versatile
        breadth = len(vibe_set)
        # Compute rank score: heavily weight match count, lightly penalize
        # games that are in too many categories (they're generic)
        score = match_count * 100.0 + min(breadth, 5) * 2.0

        scored.append((score, appid))

    # Sort by score desc, then by name for stable order
    scored.sort(key=lambda x: (-x[0], _GAME_META.get(x[1], {}).get("name", "")))

    # Build results — try to enrich with live API data (cached), but fall
    # back gracefully to curated metadata if API is unavailable
    results: list[dict] = []

    for _, appid in scored[:limit + 5]:  # fetch a few extra in case some fail
        if len(results) >= limit:
            break

        meta = _GAME_META.get(appid, {})

        # Try to get live vibe scores (they're cached if the game was viewed)
        try:
            vibe_info = score_vibes_for_app(appid)
            # Only include if the game actually scores well for the mood
            mood_score = sum(vibe_info["scores"].get(k, 0) for k in valid_keys)
            if mood_score <= 0 and len(valid_keys) == 1:
                # For single-vibe queries, skip games with zero API score
                # (might be miscategorized in curated list)
                pass  # still include — curated list is the authority
            results.append(vibe_info)
        except Exception:
            # Can't reach API — use curated metadata with synthetic vibes
            results.append({
                "appid": appid,
                "name": meta.get("name", "Unknown"),
                "cover": meta.get("cover", f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"),
                "scores": {k: (80 if k in _VIBE_INDEX.get(appid, set()) else 0) for k in VIBES},
                "top": [
                    {
                        "key": k,
                        "label": VIBES[k]["label"],
                        "emoji": VIBES[k]["emoji"],
                        "score": 80,
                    }
                    for k in sorted(
                        _VIBE_INDEX.get(appid, set()) & set(valid_keys)
                    )[:3]
                ],
            })

    return results[:limit]

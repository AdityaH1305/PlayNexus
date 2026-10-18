"""Vibes: what a game feels like, from the tags Steam players give it.

Each vibe is a weighted set of real Steam user tags (negative weights push a
vibe away: a horror game isn't cozy).  A game's tags come with vote counts;
we scale them so its strongest tag is 1.0, then add up the vibe's weights.
Scores are absolute — a game can be 90 on two vibes and 10 on the rest —
and only vibes above :data:`SHOW_AT` are shown, with the tags that earned it.
"""

from __future__ import annotations

from typing import Any, Iterable

from services import steam, tags
from services.http import UpstreamError

SHOW_AT = 35  # minimum score to call a game a vibe
FULL = 1.4  # raw score that counts as 100

# key: (label, one-line description, {tag name: weight})
VIBES: dict[str, dict[str, Any]] = {
    "cozy": {
        "label": "Cozy and calm",
        "blurb": "Low stakes, warm colours, nothing chasing you.",
        "tags": {"Cozy": 1.0, "Relaxing": 0.9, "Wholesome": 0.8, "Farming Sim": 0.7, "Life Sim": 0.6,
                 "Cute": 0.5, "Family Friendly": 0.4, "Colorful": 0.3, "Beautiful": 0.3, "Casual": 0.3,
                 "Horror": -0.8, "Gore": -0.6, "Violent": -0.4, "Difficult": -0.3, "Psychological Horror": -0.8},
    },
    "souls_like": {
        "label": "Souls-like challenge",
        "blurb": "Hard, fair fights you learn by dying.",
        "tags": {"Souls-like": 1.0, "Difficult": 0.7, "Boss Rush": 0.4, "Dark Fantasy": 0.4, "Action RPG": 0.3,
                 "Metroidvania": 0.2},
    },
    "power_fantasy": {
        "label": "Power fantasy",
        "blurb": "Feel unstoppable: big combos, big loot, big explosions.",
        "tags": {"Character Action Game": 0.9, "Hack and Slash": 0.8, "Superhero": 0.8, "Looter Shooter": 0.5,
                 "Action RPG": 0.4, "Bullet Time": 0.4, "Destruction": 0.4, "Swordplay": 0.3, "Combat": 0.2},
    },
    "dark": {
        "label": "Dark and atmospheric",
        "blurb": "Moody worlds that stay with you.",
        "tags": {"Dark": 0.9, "Atmospheric": 0.8, "Gothic": 0.8, "Dark Fantasy": 0.6, "Psychological Horror": 0.5,
                 "Post-apocalyptic": 0.4, "Lore-Rich": 0.2, "Cute": -0.5, "Wholesome": -0.5, "Colorful": -0.3},
    },
    "open_world": {
        "label": "Open-world freedom",
        "blurb": "A big map and no one telling you where to go.",
        "tags": {"Open World": 1.0, "Sandbox": 0.7, "Nonlinear": 0.5, "Exploration": 0.4,
                 "Open World Survival Craft": 0.4},
    },
    "exploration": {
        "label": "Exploration and discovery",
        "blurb": "Secrets, maps and the urge to see what's next.",
        "tags": {"Exploration": 1.0, "Metroidvania": 0.7, "Mystery": 0.5, "Walking Simulator": 0.4,
                 "Atmospheric": 0.2, "Space": 0.2, "Beautiful": 0.2},
    },
    "strategy": {
        "label": "Strategy and tactics",
        "blurb": "Think ahead, plan, outsmart.",
        "tags": {"Turn-Based Strategy": 0.9, "Turn-Based Tactics": 0.9, "4X": 0.9, "Grand Strategy": 0.9,
                 "RTS": 0.9, "Tactical": 0.7, "Real Time Tactics": 0.7, "Strategy": 0.6, "Wargame": 0.6,
                 "Tower Defense": 0.5, "Resource Management": 0.4, "Card Game": 0.3},
    },
    "roguelike": {
        "label": "Roguelike runs",
        "blurb": "Die, unlock, try again — every run is different.",
        "tags": {"Roguelike": 1.0, "Roguelite": 1.0, "Action Roguelike": 1.0, "Deckbuilding": 0.6,
                 "Replay Value": 0.3, "Dungeon Crawler": 0.3, "Bullet Hell": 0.2},
    },
    "competitive": {
        "label": "Competitive multiplayer",
        "blurb": "Ranked matches, other people, sweaty hands.",
        "tags": {"Competitive": 1.0, "eSports": 0.9, "MOBA": 0.9, "PvP": 0.8, "Battle Royale": 0.8,
                 "Hero Shooter": 0.7, "Team-Based": 0.5, "Fighting": 0.5, "Extraction Shooter": 0.5},
    },
    "coop": {
        "label": "Co-op with friends",
        "blurb": "Better with people you like.",
        "tags": {"Online Co-Op": 1.0, "Co-op": 0.9, "Co-op Campaign": 0.8, "Local Co-Op": 0.8,
                 "Party Game": 0.7, "Team-Based": 0.2},
    },
    "builder": {
        "label": "Building and crafting",
        "blurb": "Factories, bases, cities — make something.",
        "tags": {"Automation": 1.0, "City Builder": 1.0, "Base Building": 0.9, "Colony Sim": 0.9,
                 "Crafting": 0.8, "Building": 0.7, "Open World Survival Craft": 0.5, "Sandbox": 0.4,
                 "Resource Management": 0.4, "Mining": 0.3},
    },
    "survival": {
        "label": "Survival pressure",
        "blurb": "Hunger, cold and something in the dark.",
        "tags": {"Survival": 1.0, "Open World Survival Craft": 0.8, "Survival Horror": 0.5, "Zombies": 0.4,
                 "Post-apocalyptic": 0.4, "Crafting": 0.2, "Hunting": 0.2},
    },
    "story": {
        "label": "Story-driven",
        "blurb": "Characters and choices you'll remember.",
        "tags": {"Story Rich": 1.0, "Choices Matter": 0.8, "Emotional": 0.7, "Visual Novel": 0.6,
                 "Multiple Endings": 0.5, "Lore-Rich": 0.4, "Narrative": 0.4, "Mystery": 0.3,
                 "Detective": 0.3, "Walking Simulator": 0.3},
    },
    "horror": {
        "label": "Horror and dread",
        "blurb": "Lights off, sound up.",
        "tags": {"Horror": 1.0, "Psychological Horror": 0.9, "Survival Horror": 0.9, "Zombies": 0.3,
                 "Gore": 0.3, "Dark": 0.2},
    },
    "retro": {
        "label": "Retro and arcade",
        "blurb": "Pixels, high scores and tight controls.",
        "tags": {"Retro": 0.9, "Pixel Graphics": 0.8, "Arcade": 0.8, "Shoot 'Em Up": 0.7, "Beat 'em up": 0.7,
                 "Score Attack": 0.5, "Bullet Hell": 0.4, "2D Platformer": 0.4},
    },
    "simulation": {
        "label": "Simulation",
        "blurb": "Run it, drive it, fly it, manage it.",
        "tags": {"Flight": 0.8, "Simulation": 0.8, "Management": 0.7, "Space Sim": 0.7, "Driving": 0.6,
                 "Immersive Sim": 0.6, "Life Sim": 0.5, "Economy": 0.4, "Trading": 0.4, "Physics": 0.3},
    },
    "grind": {
        "label": "Grind and loot",
        "blurb": "Numbers go up. One more run for that drop.",
        "tags": {"Loot": 0.9, "Looter Shooter": 0.9, "MMORPG": 0.8, "Idler": 0.6, "Clicker": 0.6,
                 "Massively Multiplayer": 0.5, "Dungeon Crawler": 0.3, "Inventory Management": 0.2},
    },
    "casual": {
        "label": "Pick up and play",
        "blurb": "Easy to start, easy to stop.",
        "tags": {"Casual": 1.0, "Puzzle": 0.5, "Family Friendly": 0.5, "Short": 0.4, "Party Game": 0.4,
                 "Funny": 0.3, "Arcade": 0.3, "Difficult": -0.5, "Grand Strategy": -0.5, "Souls-like": -0.5},
    },
}

# Old keys (used in links and saved URLs) → new keys.
LEGACY_KEYS = {
    "cozy_relaxing": "cozy", "soulslike_challenge": "souls_like", "dark_atmospheric": "dark",
    "open_world_freedom": "open_world", "exploration_discovery": "exploration", "strategy_brainy": "strategy",
    "roguelike_run_based": "roguelike", "pvp_sweaty": "competitive", "coop_friendship": "coop",
    "builder_crafter": "builder", "survival_pressure": "survival", "story_emotional": "story",
    "horror_tension": "horror", "retro_arcade": "retro", "simulation_immersive": "simulation",
    "grindy": "grind",
}


def normalize_key(key: str) -> str | None:
    key = LEGACY_KEYS.get(key, key)
    return key if key in VIBES else None


def vibe_list() -> list[dict[str, str]]:
    return [{"key": k, "label": v["label"], "blurb": v["blurb"]} for k, v in VIBES.items()]


def score_tags(item_tags: Iterable[tuple[int, int]], names: dict[int, str] | None = None) -> dict[str, dict[str, Any]]:
    """Score every vibe for one game's ``(tagid, votes)`` tags.

    Returns ``{vibe: {"score": 0-100, "why": [tag names]}}``.
    """
    pairs = list(item_tags)
    if not pairs:
        return {}
    names = names if names is not None else tags.names()
    top = max(w for _, w in pairs) or 1
    rel = {names[tid]: w / top for tid, w in pairs if tid in names}

    out: dict[str, dict[str, Any]] = {}
    for key, vibe in VIBES.items():
        contrib = {t: weight * rel[t] for t, weight in vibe["tags"].items() if t in rel}
        raw = sum(contrib.values())
        score = max(0, min(100, round(100 * raw / FULL)))
        why = [t for t, c in sorted(contrib.items(), key=lambda kv: -kv[1]) if c > 0][:3]
        out[key] = {"score": score, "why": why}
    return out


def top_vibes(scores: dict[str, dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    """The strongest vibes at or above :data:`SHOW_AT`, best first."""
    ranked = sorted(scores.items(), key=lambda kv: -kv[1]["score"])
    return [
        {"key": k, "label": VIBES[k]["label"], "score": s["score"], "why": "Tagged " + ", ".join(s["why"])}
        for k, s in ranked
        if s["score"] >= SHOW_AT
    ][:limit]


def score_vibes_for_app(appid: int) -> dict[str, Any]:
    """Vibes for one game, from its live Steam tags."""
    item = steam.store_items([int(appid)]).get(int(appid))
    if not item:
        raise UpstreamError(f"no store item for {appid}")
    scores = score_tags(item["tags"])
    return {
        "appid": int(appid),
        "name": item["name"],
        "cover": item["header"],
        "scores": {k: v["score"] for k, v in scores.items()},
        "top": top_vibes(scores),
    }

import pytest

from services import search

GAMES = [
    (1245620, "ELDEN RING", 5.9),
    (2778580, "ELDEN RING Shadow of the Erdtree", 4.9, 1),
    (367520, "Hollow Knight", 5.6),
    (2261140, "Hollow", 0.8),
    (1086940, "Baldur's Gate 3", 5.8),
    (2956320, "BG3 Toolkit Data", 0.5),
    (289070, "Sid Meier's Civilization VI", 5.3),
    (292030, "The Witcher 3: Wild Hunt", 6.0),
    (271590, "Grand Theft Auto V Legacy", 6.3),
    (730, "Counter-Strike 2", 6.9),
    (666220, "CS2D", 3.1),
    (1172470, "Apex Legends", 5.9),
    (630630, "Apex", 1.2),
]


@pytest.fixture(autouse=True)
def small_index(monkeypatch):
    monkeypatch.setattr(search, "_index", None)
    monkeypatch.setattr(search, "_load_entries", lambda: [search._entry(*g) for g in GAMES])
    yield
    search._index = None


def top(q):
    hits = search.find_matches(q, 3)
    return hits[0]["name"] if hits else None


@pytest.mark.parametrize("query,expected", [
    ("hollow", "Hollow Knight"),            # popularity beats an obscure exact match
    ("bg3", "Baldur's Gate 3"),             # acronym, apostrophe dropped
    ("baldurs gate", "Baldur's Gate 3"),
    ("civ 6", "Sid Meier's Civilization VI"),  # roman numerals
    ("witcher", "The Witcher 3: Wild Hunt"),   # word in the middle of the name
    ("gta 5", "Grand Theft Auto V Legacy"),
    ("cs2", "Counter-Strike 2"),
    ("counterstrike", "Counter-Strike 2"),
    ("apex", "Apex Legends"),
])
def test_ranking(query, expected):
    assert top(query) == expected


def test_empty_and_unknown_queries():
    assert search.find_matches("   ") == []
    assert search.find_matches("zzzzqqq") == []


def test_find_appid_by_name_uses_ranking():
    assert search.find_appid_by_name("hollow") == 367520


def test_base_game_outranks_its_dlc_unless_the_dlc_is_named():
    base = search.find_matches("elden ring", 2)
    assert [h["name"] for h in base] == ["ELDEN RING", "ELDEN RING Shadow of the Erdtree"]
    assert base[0]["dlc"] is False and base[1]["dlc"] is True
    assert top("shadow of the erdtree") == "ELDEN RING Shadow of the Erdtree"


@pytest.mark.parametrize("name,minor", [
    ("ELDEN RING Shadow of the Erdtree", False),
    ("Cyberpunk 2077: Phantom Liberty", False),
    ("The Witcher 3: Wild Hunt - Blood and Wine", False),
    ("Sid Meier's Civilization® VI: Gathering Storm", False),
    ("The Sims™ 4 Cottage Living Expansion Pack", False),
    ("Dead by Daylight - Resident Evil Chapter", False),
    ("ELDEN RING Bonus Gesture", True),
    ("Hollow Knight Original Soundtrack", True),
    ("Civilization VI - Babylon Pack", True),
    ("Sims 4 Grunge Revival Kit", True),
    ("DOOM Eternal: Year One Pass", False),
    ("Game X - Digital Deluxe Upgrade", True),
    ("Game X Season Pass", True),
    ("Game X - Adventure Pack: Desert", True),
    ("Game X - Legendary Skin Bundle", True),
    ("Game X Artbook", True),
    ("Game X - 1000 Coins", True),
])
def test_minor_dlc_filter(name, minor):
    assert search.is_minor_dlc_name(name) is minor


@pytest.mark.parametrize("name,junk", [
    ("Hollow Knight Original Soundtrack", True),
    ("Counter-Strike 2 Dedicated Server", True),
    ("ELDEN RING Demo", True),
    ("Demolition Inc.", False),
    ("Half-Life 2: Deathmatch", False),
])
def test_junk_filter(name, junk):
    assert search.is_junk_name(name) is junk

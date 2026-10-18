import pytest

from services import search

GAMES = [
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


@pytest.mark.parametrize("name,junk", [
    ("Hollow Knight Original Soundtrack", True),
    ("Counter-Strike 2 Dedicated Server", True),
    ("ELDEN RING Demo", True),
    ("Demolition Inc.", False),
    ("Half-Life 2: Deathmatch", False),
])
def test_junk_filter(name, junk):
    assert search.is_junk_name(name) is junk

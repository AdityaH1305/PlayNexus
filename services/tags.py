"""Steam user-tag names, live with a bundled fallback."""

from __future__ import annotations

import json
import os
from functools import lru_cache

from config import Config
from services import steam
from services.http import UpstreamError

TAGS_PATH = os.path.join(Config.DATA_DIR, "tags.json")


@lru_cache(maxsize=1)
def bundled() -> dict[int, str]:
    """Tag names shipped in data/tags.json (refreshed by build_data.py)."""
    try:
        with open(TAGS_PATH, encoding="utf-8") as f:
            return {int(k): v for k, v in json.load(f).items()}
    except FileNotFoundError:
        return {}


def names() -> dict[int, str]:
    try:
        return steam.tag_names()
    except UpstreamError:
        return bundled()


def top_names(item_tags: list[tuple[int, int]], n: int = 3) -> list[str]:
    """Names of the *n* strongest tags in a store item's ``tags`` list."""
    if not item_tags:
        return []
    lookup = names()
    ordered = sorted(item_tags, key=lambda t: -t[1])
    return [lookup[tid] for tid, _ in ordered if tid in lookup][:n]

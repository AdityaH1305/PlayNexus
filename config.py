"""Central configuration, read from environment variables (and .env locally)."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    # ── Site ──────────────────────────────────────────────
    SITE_NAME: str = "PlayNexus"
    SITE_URL: str = os.getenv("SITE_URL", "").rstrip("/")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "playnexus-dev")
    DEBUG: bool = os.getenv("FLASK_DEBUG", "0") == "1"
    # Vercel sets VERCEL=1. Its Web Analytics script only exists there.
    ON_VERCEL: bool = bool(os.getenv("VERCEL"))

    # ── Upstream APIs ─────────────────────────────────────
    STEAM_API_KEY: str = os.getenv("STEAM_API_KEY", "")
    HTTP_TIMEOUT: float = float(os.getenv("HTTP_TIMEOUT", "6"))
    MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "16"))
    # CheapShark rejects generic user agents, so say who we are.
    USER_AGENT: str = (
        "PlayNexus/2.0 (+"
        + (SITE_URL or "https://github.com/AdityaH1305/PlayNexus")
        + ")"
    )

    # ── Price history (optional) ──────────────────────────
    # Postgres connection string; Neon from the Vercel Marketplace sets it.
    # Without it, price history is simply left out.
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    # Vercel sends this as a Bearer token when it runs the daily cron job.
    CRON_SECRET: str = os.getenv("CRON_SECRET", "")

    # ── Data files ────────────────────────────────────────
    DATA_DIR: str = os.path.join(BASE_DIR, "data")
    # Legacy full app list; used for search until data/search_index.json.gz exists.
    LEGACY_GAMES_JSON: str = os.path.join(BASE_DIR, os.getenv("GAMES_JSON", "games.json"))

    # ── Regions for price comparison ──────────────────────
    # `cc` is the store country Steam prices for; `code` is the label we show.
    # Steam has no "eu" store: cc=eu silently returns US dollars, so the euro
    # area uses Germany's store, which carries the shared euro price.
    REGIONS: list[dict[str, str]] = [
        {"cc": "us", "code": "US", "name": "United States"},
        {"cc": "ca", "code": "CA", "name": "Canada"},
        {"cc": "mx", "code": "MX", "name": "Mexico"},
        {"cc": "br", "code": "BR", "name": "Brazil"},
        {"cc": "ar", "code": "AR", "name": "Argentina"},
        {"cc": "gb", "code": "GB", "name": "United Kingdom"},
        {"cc": "de", "code": "EU", "name": "Euro area"},
        {"cc": "pl", "code": "PL", "name": "Poland"},
        {"cc": "tr", "code": "TR", "name": "Türkiye"},
        {"cc": "in", "code": "IN", "name": "India"},
        {"cc": "cn", "code": "CN", "name": "China"},
        {"cc": "jp", "code": "JP", "name": "Japan"},
        {"cc": "kr", "code": "KR", "name": "South Korea"},
        {"cc": "id", "code": "ID", "name": "Indonesia"},
        {"cc": "au", "code": "AU", "name": "Australia"},
        {"cc": "ae", "code": "AE", "name": "UAE"},
    ]

    # Countries that pay the euro-area price.
    EUROZONE: frozenset[str] = frozenset({
        "AT", "BE", "HR", "CY", "EE", "FI", "FR", "DE", "GR", "IE",
        "IT", "LV", "LT", "LU", "MT", "NL", "PT", "SK", "SI", "ES",
    })

    @classmethod
    def region(cls, code_or_cc: str) -> dict[str, str] | None:
        key = (code_or_cc or "").lower()
        for r in cls.REGIONS:
            if r["cc"] == key or r["code"].lower() == key:
                return r
        return None

    @classmethod
    def region_for_country(cls, country: str) -> str | None:
        """Our region code for an ISO country code, if we price it."""
        country = (country or "").upper()
        if country in cls.EUROZONE:
            return "EU"
        r = cls.region(country)
        return r["code"] if r and r["code"] != "EU" else None

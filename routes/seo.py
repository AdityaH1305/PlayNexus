"""robots.txt and sitemap.xml, so search engines can find every game page."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from flask import Blueprint, Response, render_template, request, url_for

from config import Config
from routes.caching import cdn_cache, mark_degraded
from services import charts, pool

bp = Blueprint("seo", __name__)


def absolute_url(path: str) -> str:
    """*path* on the public site: under ``SITE_URL``, or under the host that
    was asked for if that isn't set."""
    return (Config.SITE_URL or request.host_url.rstrip("/")) + path


def w3c_time(ts: float | None) -> str | None:
    """Unix timestamp → W3C datetime, the format sitemaps use."""
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@bp.route("/robots.txt")
@cdn_cache(s_maxage=86400)
def robots():
    lines = ["User-agent: *", "Disallow: /api/", "", f"Sitemap: {absolute_url(url_for('seo.sitemap'))}"]
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


@bp.route("/sitemap.xml")
@cdn_cache(s_maxage=86400)
def sitemap():
    """The main pages, every game in the discovery pool and this week's top
    sellers. One cached chart call; nothing is looked up per game."""
    now = w3c_time(time.time())
    built = w3c_time(pool.built())

    # Pages built from live Steam data change every day. Compare and My Steam
    # only change when we deploy, so they get no date rather than a wrong one.
    entries = [(url_for("pages.home"), now), (url_for("pages.charts_page"), now)]
    entries += [(url_for("pages.charts_page", tab=tab), now) for tab in charts.CHARTS if tab != "playing"]
    entries += [
        (url_for("pages.deals_page"), now),
        (url_for("pages.regions_page"), now),
        (url_for("pages.discover_page"), now),
        (url_for("pages.compare_page"), None),
        (url_for("pages.me_page"), None),
    ]

    # Game pages are dated by the pool snapshot; top sellers outside the pool
    # by the week of the chart that put them here.
    games = {g["appid"]: built for g in pool.games()}
    sellers = charts.safe(charts.top_sellers)
    if sellers.get("error"):
        mark_degraded()  # try again soon instead of leaving them out for a day
    week = w3c_time(sellers["updated"])
    for row in sellers["rows"]:
        games.setdefault(row["appid"], week)
    entries += [(url_for("pages.game_page", appid=appid), lastmod) for appid, lastmod in games.items()]

    urls = [{"loc": absolute_url(path), "lastmod": lastmod} for path, lastmod in entries]
    return Response(render_template("sitemap.xml", urls=urls), mimetype="application/xml")

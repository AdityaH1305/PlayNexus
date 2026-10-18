"""Server-rendered pages."""

from __future__ import annotations

from flask import Blueprint, abort, redirect, render_template, request, url_for

from routes.caching import cdn_cache
from services import charts
from services import game as game_service
from services import search
from services.http import UpstreamError
from services.search import find_appid_by_name
from services.vibes import VIBES

bp = Blueprint("pages", __name__)


@bp.before_request
def _warm_search():
    search.warm()


@bp.route("/")
@cdn_cache(s_maxage=900)
def home():
    playing = charts.safe(charts.playing_now, limit=30)
    return render_template("index.html", playing=playing["rows"][:8])


@bp.route("/game", methods=["POST"])
def game_post():
    """Search form fallback (no JS): resolve a name or ID, then redirect."""
    picked = (request.form.get("game_input_id") or "").strip()
    typed = (request.form.get("game_input") or "").strip()
    appid = None
    if picked.isdigit():
        appid = int(picked)
    elif typed.isdigit():
        appid = int(typed)
    elif typed:
        appid = find_appid_by_name(typed)
    if not appid:
        return render_template(
            "error.html", code=404, title="No game by that name",
            message=f"We couldn't find “{typed}” on Steam. Check the spelling or try its App ID.",
        ), 404
    return redirect(url_for("pages.game_page", appid=appid))


@bp.route("/game/<int:appid>")
@cdn_cache(s_maxage=1800, swr=86400)
def game_page(appid: int):
    try:
        game = game_service.load_game(appid)
    except UpstreamError:
        abort(503)
    if game is None:
        abort(404)

    # Regional table: cheapest first, then regions without a price.
    regions = game["prices"]["regions"]
    priced = sorted((r for r in regions if r.get("usd")), key=lambda r: r["usd"])
    unpriced = [r for r in regions if not r.get("usd")]
    top = max((r["usd"] for r in priced), default=0) * 1.08 or 1
    strip = {r["code"]: round(r["usd"] / top * 100, 2) for r in priced}
    us_x = round(game["prices"]["us_usd"] / top * 100, 2) if game["prices"]["us_usd"] else None

    page_data = {
        "appid": game["appid"],
        "name": game["name"],
        "header": game["header_image"],
        "is_free": game["is_free"],
        "us_usd": game["prices"]["us_usd"],
        "regions": [
            {k: r.get(k) for k in ("code", "name", "available", "final_formatted", "usd", "discount")}
            for r in regions
        ],
        "value": game["value"],
    }
    return render_template(
        "game.html", game=game, price_rows=priced + unpriced, strip=strip, us_x=us_x, page_data=page_data,
    )


@bp.route("/charts")
@cdn_cache(s_maxage=900)
def charts_page():
    tab = request.args.get("tab", "playing")
    if tab not in charts.CHARTS:
        tab = "playing"
    title, fn = charts.CHARTS[tab]
    data = charts.safe(fn)
    return render_template("charts.html", tab=tab, title=title, tabs=charts.CHARTS, chart=data)


@bp.route("/discover")
@cdn_cache(s_maxage=86400)
def discover_page():
    vibes = sorted(({"key": k, "label": v["label"]} for k, v in VIBES.items()), key=lambda m: m["label"])
    return render_template("discover.html", vibes=vibes)


@bp.route("/compare")
@cdn_cache(s_maxage=86400)
def compare_page():
    return render_template("compare.html")


# Old addresses, kept working.
@bp.route("/trending")
def trending_redirect():
    return redirect(url_for("pages.charts_page"), 301)


@bp.route("/moods")
def moods_redirect():
    return redirect(url_for("pages.discover_page"), 301)


@bp.route("/favicon.ico")
@cdn_cache(s_maxage=86400 * 30)
def favicon():
    return redirect(url_for("static", filename="img/favicon.svg"), 301)

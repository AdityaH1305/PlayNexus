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
    return render_template("game.html", game=game)


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

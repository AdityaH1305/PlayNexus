"""Server-rendered pages."""

from __future__ import annotations

from flask import Blueprint, abort, redirect, render_template, request, url_for

from routes.caching import cdn_cache
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
@cdn_cache(s_maxage=3600)
def home():
    return render_template("index.html")


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
        return render_template("error.html", code=404,
                               title="No game by that name",
                               message=f"We couldn't find “{typed}” on Steam. Check the spelling or try its App ID."), 404
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


@bp.route("/moods")
@cdn_cache(s_maxage=86400)
def moods_page():
    moods = sorted(
        ({"key": k, "label": v["label"], "emoji": v["emoji"]} for k, v in VIBES.items()),
        key=lambda m: m["label"],
    )
    return render_template("moods.html", moods=moods)


@bp.route("/trending")
@cdn_cache(s_maxage=3600)
def trending_page():
    return render_template("trending.html")


@bp.route("/compare")
@cdn_cache(s_maxage=86400)
def compare_page():
    return render_template("compare.html")

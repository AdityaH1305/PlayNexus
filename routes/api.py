"""JSON API used by the front end."""

from __future__ import annotations

from flask import Blueprint, abort, jsonify, redirect, request

from routes.caching import cdn_cache
from services import charts
from services import game as game_service
from services import pricing, steam
from services.http import UpstreamError
from services.recommendations import get_recommendations
from services.search import search
from services.vibes import VIBES, discover_by_vibes, score_vibes_for_app

bp = Blueprint("api", __name__, url_prefix="/api")


def _int_arg(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(request.args.get(name, default))))
    except (TypeError, ValueError):
        return default


def _game_or_404(appid: int) -> dict:
    try:
        game = game_service.load_game(appid)
    except UpstreamError:
        abort(503)
    if game is None:
        abort(404)
    return game


@bp.route("/search")
@cdn_cache(s_maxage=86400, swr=604800)
def api_search():
    q = (request.args.get("q") or "").strip()[:80]
    return jsonify(search(q, limit=_int_arg("limit", 8, 1, 20)))


@bp.route("/game/<int:appid>")
@cdn_cache(s_maxage=1800)
def api_game(appid: int):
    g = _game_or_404(appid)
    prices = g["prices"]
    cheapest = next((r for r in prices["regions"] if r.get("is_cheapest")), None)
    return jsonify({
        "appid": g["appid"],
        "name": g["name"],
        "header": g["header_image"],
        "store_url": g["store_url"],
        "is_free": g["is_free"],
        "coming_soon": g["coming_soon"],
        "prices": prices,
        "cheapest_region": cheapest,
        "usd_price": g["usd_price"],
        "reviews": g["reviews"],
        "value": g["value"],
        "vibes": {"top": [
            {"key": v["key"], "label": v["label"], "score": v["score"]} for v in g["vibes"].get("top", [])
        ]},
    })


@bp.route("/prices/<int:appid>")
@cdn_cache(s_maxage=3 * 3600)
def api_prices(appid: int):
    try:
        details = steam.appdetails(appid, "us")
    except UpstreamError:
        abort(503)
    if not details:
        abort(404)
    return jsonify(pricing.regional_prices(appid, is_free=bool(details.get("is_free"))))


@bp.route("/recommendations/<int:appid>")
@cdn_cache(s_maxage=6 * 3600)
def api_recommendations(appid: int):
    return jsonify(get_recommendations(str(appid)))


@bp.route("/vibes/<int:appid>")
@cdn_cache(s_maxage=6 * 3600)
def api_vibes(appid: int):
    try:
        return jsonify(score_vibes_for_app(appid))
    except UpstreamError:
        abort(503)


@bp.route("/discover")
@cdn_cache(s_maxage=6 * 3600)
def api_discover():
    keys = [m for m in request.args.getlist("m") if m in VIBES][:3]
    return jsonify(discover_by_vibes(keys, limit=18) if keys else [])


_ART_KEYS = {"header": "header", "capsule": "small_capsule", "hero": "hero", "library": "library_capsule"}


@bp.route("/art/<int:appid>")
@cdn_cache(s_maxage=7 * 86400, swr=30 * 86400, error_s_maxage=3600)
def api_art(appid: int):
    """Redirect to a game's real art URL (newer games use hashed paths)."""
    key = _ART_KEYS.get(request.args.get("kind", "header"), "header")
    item = steam.store_items([appid]).get(appid)
    url = item and (item.get(key) or item.get("header"))
    if not url:
        abort(404)
    return redirect(url, 302)


@bp.route("/charts/<name>")
@cdn_cache(s_maxage=15 * 60)
def api_chart(name: str):
    if name not in charts.CHARTS:
        abort(404)
    return jsonify(charts.safe(charts.CHARTS[name][1]))

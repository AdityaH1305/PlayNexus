"""JSON API used by the front end."""

from __future__ import annotations

from flask import Blueprint, abort, jsonify, request

from routes.caching import cdn_cache
from services import game as game_service
from services import pricing, steam, tags
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
    return jsonify({
        "appid": g["appid"],
        "name": g["name"],
        "cover_url": g["header_image"],
        "store_url": g["store_url"],
        "is_free": g["is_free"],
        "prices": g["prices"],
        "usd_price": g["usd_price"],
        "reviews": g["reviews"],
        "value_score": g["value"]["score"],
        "value_label": g["value"]["label"],
        "value_breakdown": g["value"]["breakdown"],
        "vibes": g["vibes"],
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


@bp.route("/trending")
@cdn_cache(s_maxage=15 * 60)
def api_trending():
    """Top 20 games by players right now, from Steam's live chart."""
    try:
        ranks = steam.concurrent_chart().get("ranks") or []
    except UpstreamError:
        abort(503)
    appids = [int(r["appid"]) for r in ranks[:24]]
    items = steam.store_items(appids)
    out = []
    for r in ranks[:24]:
        it = items.get(int(r["appid"]))
        if not it or not it["name"]:
            continue
        price = "Free to play" if it["is_free"] else ((it["price"] or {}).get("final_formatted") or "")
        out.append({
            "appid": it["appid"],
            "name": it["name"],
            "cover": it["header"],
            "short_description": it["short_description"][:120],
            "price": price,
            "players": r.get("concurrent_in_game"),
            "genres": tags.top_names(it["tags"], 3),
        })
    return jsonify(out[:20])

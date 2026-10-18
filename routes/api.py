"""JSON API used by the front end."""

from __future__ import annotations

import re

from flask import Blueprint, abort, jsonify, redirect, request

from config import Config
from routes.caching import cdn_cache, mark_degraded, no_store
from services import charts, cheapshark, discover, steam_account
from services import game as game_service
from services import fx, pricing, steam
from services.http import UpstreamError, executor
from services.recommendations import get_recommendations
from services.search import search
from services.vibes import score_vibes_for_app

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
        "is_dlc": g["is_dlc"],
        "base_game": g["base_game"],
        "prices": prices,
        "cheapest_region": cheapest,
        "usd_price": g["usd_price"],
        "reviews": g["reviews"],
        "value": g["value"],
        "vibes": {"top": [
            {"key": v["key"], "label": v["label"], "score": v["score"]} for v in g["vibes"].get("top", [])
        ]},
    })


@bp.route("/game/<int:appid>/live")
@cdn_cache(s_maxage=300, swr=600)
def api_game_live(appid: int):
    """Players right now, plus rank and today's peak if it's in the top 100."""
    f_players = executor.submit(_or_none, steam.current_players, appid)
    f_chart = executor.submit(_or_none, steam.concurrent_chart)
    players, chart = f_players.result(), f_chart.result() or {}
    entry = next((r for r in chart.get("ranks") or [] if int(r["appid"]) == appid), None)
    return jsonify({
        "players": players,
        "rank": entry.get("rank") if entry else None,
        "peak_today": entry.get("peak_in_game") if entry else None,
    })


@bp.route("/game/<int:appid>/reviews")
@cdn_cache(s_maxage=12 * 3600)
def api_game_reviews(appid: int):
    """Review counts over time (monthly), and the last 30 days."""
    try:
        h = steam.review_histogram(appid)
    except UpstreamError:
        abort(503)
    points = [
        {"t": r["date"], "up": r.get("recommendations_up", 0), "down": r.get("recommendations_down", 0)}
        for r in h.get("rollups") or []
    ]
    recent = h.get("recent") or []
    return jsonify({
        "rollup": h.get("rollup_type") or "month",
        "points": points,
        "recent": {
            "up": sum(r.get("recommendations_up", 0) for r in recent),
            "down": sum(r.get("recommendations_down", 0) for r in recent),
            "days": len(recent),
        },
    })


@bp.route("/game/<int:appid>/deals")
@cdn_cache(s_maxage=6 * 3600)
def api_game_deals(appid: int):
    """All-time low and current prices at other stores (CheapShark, USD)."""
    try:
        deals = cheapshark.game_deals(appid)
    except UpstreamError:
        abort(503)
    return jsonify(deals or {"cheapest_ever": None, "deals": []})


@bp.route("/whereami")
@no_store
def api_whereami():
    """Suggest a price region from the visitor's country (Vercel sets the header)."""
    country = (request.headers.get("X-Vercel-IP-Country") or "").upper() or None
    return jsonify({"country": country, "region": Config.region_for_country(country) if country else None})


@bp.route("/me/resolve")
@no_store
def api_me_resolve():
    try:
        profile = steam_account.resolve(request.args.get("q", "")[:200])
    except steam_account.ProfileError as exc:
        return jsonify({"error": str(exc)}), 400
    except UpstreamError:
        return jsonify({"error": "Steam isn't answering right now. Try again in a moment."}), 503
    return jsonify({**profile, "library_available": steam_account.has_key()})


def _valid_steamid(steamid: str) -> None:
    if not steam_account.STEAMID_RE.match(steamid):
        abort(404)


@bp.route("/me/<steamid>/wishlist")
@no_store
def api_me_wishlist(steamid: str):
    _valid_steamid(steamid)
    region = Config.region(request.args.get("region", "US")) or Config.region("US")
    try:
        return jsonify(steam_account.wishlist(steamid, region["cc"].upper()))
    except UpstreamError:
        return jsonify({"error": "Couldn't load the wishlist from Steam just now."}), 503


@bp.route("/me/<steamid>/library")
@no_store
def api_me_library(steamid: str):
    _valid_steamid(steamid)
    try:
        lib = steam_account.library(steamid)
    except steam_account.ProfileError as exc:
        return jsonify({"error": str(exc), "reason": "no_key"}), 501
    except UpstreamError:
        return jsonify({"error": "Couldn't load the library from Steam just now."}), 503
    if lib is None:
        return jsonify({
            "error": "This profile's game details are private.",
            "reason": "private",
        }), 403
    return jsonify(lib)


def _or_none(fn, *args):
    try:
        return fn(*args)
    except UpstreamError:
        return None


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


MAX_BATCH_IDS = 100  # two Steam calls; public/static/js/prices.js sends no more
_APPID_RE = re.compile(r"[1-9][0-9]{0,9}")


@bp.route("/prices/batch")
@cdn_cache(s_maxage=3600)
def api_prices_batch():
    """One region's prices for many apps: ``?ids=570,730&region=IN``.

    List pages are cached for everyone, so they show US prices and swap these
    in for visitors elsewhere.  ``prices`` maps app IDs to Steam's formatted
    local price, the discount and the US-dollar equivalent, or to null when
    that store doesn't sell the app.  Apps Steam didn't answer for are left
    out, and the response is then only cached for a minute.
    """
    region = Config.region(request.args.get("region", ""))
    if not region:
        codes = ", ".join(r["code"] for r in Config.REGIONS)
        return jsonify({"error": f"Unknown region. Use one of {codes}."}), 400
    parts = [p.strip() for p in request.args.get("ids", "").split(",") if p.strip()]
    if not parts or not all(_APPID_RE.fullmatch(p) for p in parts):
        return jsonify({"error": "Pass ids as comma-separated Steam app IDs."}), 400
    ids = list(dict.fromkeys(int(p) for p in parts))
    if len(ids) > MAX_BATCH_IDS:
        return jsonify({"error": f"At most {MAX_BATCH_IDS} app IDs per request."}), 400

    prices, failed = pricing.local_prices(ids, region["cc"])
    if failed:
        mark_degraded()
    return jsonify({
        "region": region["code"],
        "prices": {str(a): prices[a] for a in ids if a in prices},
        "fx": fx.meta(),
    })


@bp.route("/recommendations/<int:appid>")
@cdn_cache(s_maxage=6 * 3600)
def api_recommendations(appid: int):
    return jsonify(get_recommendations(appid))


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
    """Games by vibe (?v=cozy&v=story, or the old ?m=…) and filters."""
    keys = request.args.getlist("v") + request.args.getlist("m")
    try:
        max_price = float(request.args["max"]) if request.args.get("max") else None
    except ValueError:
        max_price = None
    return jsonify(discover.discover(
        keys,
        max_price=max_price,
        min_rating=_int_arg("rating", 0, 0, 100) or None,
        players=request.args.get("players") if request.args.get("players") in discover.PLAYERS else None,
        length=request.args.get("length") if request.args.get("length") in discover.LENGTHS else None,
        deck=request.args.get("deck") == "1",
        limit=_int_arg("limit", 36, 1, 60),
    ))


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

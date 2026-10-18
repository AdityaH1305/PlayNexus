"""PlayNexus — Flask entry point.

Vercel finds the ``app`` object in this file and serves it as one function.
Static files live in ``public/`` so Vercel's CDN serves them directly; Flask
serves the same folder when running locally.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from flask import Flask, render_template, url_for

from config import Config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public", "static")

NAV_ITEMS = [
    {"label": "Charts", "href": "/charts", "icon": "chart"},
    {"label": "Discover", "href": "/discover", "icon": "compass"},
    {"label": "Compare", "href": "/compare", "icon": "columns"},
]


@lru_cache(maxsize=256)
def _file_version(filename: str) -> str:
    try:
        return format(int(os.path.getmtime(os.path.join(STATIC_DIR, filename))), "x")
    except OSError:
        return "0"


def asset_url(filename: str) -> str:
    """URL for a static file, versioned so it can be cached forever."""
    version = os.getenv("VERCEL_GIT_COMMIT_SHA", "")[:10]
    if not version:
        if Config.DEBUG:
            _file_version.cache_clear()
        version = _file_version(filename)
    return url_for("static", filename=filename, v=version)


def create_app() -> Flask:
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
    app.config.from_object(Config)

    from routes.api import bp as api_bp
    from routes.pages import bp as pages_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp)

    from services import fmt

    # Globals (not a context processor) so imported macros can use them too.
    app.jinja_env.globals.update(
        asset_url=asset_url,
        art_url=fmt.art_url,
        site_name=Config.SITE_NAME,
        site_url=Config.SITE_URL,
        nav_items=NAV_ITEMS,
        config_regions=Config.REGIONS,
    )

    app.add_template_filter(fmt.comma, "comma")
    app.add_template_filter(fmt.compact, "compact")
    app.add_template_filter(fmt.usd, "usd")
    app.add_template_filter(fmt.date, "date")
    app.add_template_filter(fmt.hours, "hours")

    @app.errorhandler(404)
    def not_found(_e):
        return render_template(
            "error.html", code=404, title="Page not found",
            message="That page doesn't exist. It may have moved, or the link has a typo.",
        ), 404

    @app.errorhandler(503)
    def unavailable(_e):
        return render_template(
            "error.html", code=503, title="Steam isn't answering",
            message="We couldn't reach Steam just now. Wait a moment and try again.",
        ), 503

    @app.errorhandler(500)
    def server_error(_e):
        return render_template(
            "error.html", code=500, title="Something broke on our side",
            message="The error has been logged. Try again, or head back to search.",
        ), 500

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=Config.DEBUG)

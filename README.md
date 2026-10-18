# PlayNexus

Is that Steam game worth the money? PlayNexus looks up any Steam game and tells you what it costs in 16 regions, how much play you get per dollar, what it feels like to play, and whether now is a good time to buy.

It runs as a single Flask app on Vercel, with no database and no build step.

## What's in it

**Game pages**
- A 0–100 **value score** built from typical playtime, review quality and review count, with the price at which the game becomes "great value" and its cost per hour.
- **Prices in 16 regions**, converted at today's exchange rates, with a strip chart showing each region against the US price. Your region is detected (or picked), highlighted, and used to re-score value at your price.
- **Price check**: the all-time low at any store and current prices at other stores (via CheapShark).
- **Live players** and chart rank, **Steam Deck** status, Metacritic, top user tags.
- A **review history** chart, month by month.
- **What it feels like**: vibes worked out from the tags Steam players give the game, with the tags that earned each one.
- **Similar games** by tag similarity.

**Around the site**
- **Charts**: playing now, top sellers this week, new releases, coming soon.
- **Deals**: today's Steam sales ranked by a deal score you can read, plus the value score at the sale price.
- **Where is Steam cheapest?**: a regional price index across Steam's most popular paid games.
- **Discover**: pick up to three vibes, filter by price, reviews, players, length and Steam Deck, and let it pick one for you.
- **Compare**: up to four games side by side, best value per row highlighted, shareable links.
- **My Steam**: paste your profile to see which wishlist games are on sale and worth it; with an API key, library stats, a backlog picker and suggestions.
- Search from anywhere with **Ctrl K** (⌘K on Mac) or **/**. Dark and light themes.

## How the numbers work

**Value score (0–100)**

```
55 × hours of play per dollar   (log scale, full marks at 20 h per $)
+ 35 × how far reviews sit above 60% positive
+ 10 × confidence from the number of reviews (1,000+ = full)
```

Playtime is the typical playtime of people who reviewed the game. Labels: 85+ excellent, 70+ great, 55+ good, 40+ fair. You can't overpay for a free game, so for free games the first 55 points come from how positive reviews are instead (none at 50%, all of them from 90%).

**Deal score (0–100)**: 40 for the discount (up to 90% off), 45 for how far reviews sit above 50% positive, 15 for review volume (100,000+ = full).

**Vibes**: each vibe is a weighted set of real Steam user tags (`services/vibes.py`). A game's tag votes are scaled so its strongest tag counts as 1.0; some tags count against a vibe (a horror game isn't cozy). Scores are absolute, and a vibe only shows from 35 up.

**Regional index**: each game's regional price in US dollars divided by its US price; a region's index is the median across the basket, so one odd price can't skew it.

## Running it locally

Needs Python 3.11 or newer.

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

(On macOS or Linux: `source .venv/bin/activate`.)

```bash
pip install -r requirements-dev.txt
```

```bash
cp .env.example .env
```

```bash
python app.py
```

Then open http://127.0.0.1:5000.

Checks:

```bash
pytest
```

```bash
python scripts/check_contrast.py
```

The tests never touch the network: anything that tries to fails on purpose.

## Settings

Set these in `.env` locally and in Vercel under Project → Settings → Environment Variables.

| Variable | Needed for |
|---|---|
| `STEAM_API_KEY` | Optional. Library tools on My Steam, and rebuilding the games-only search index. Free from [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey) (your Steam account must have spent $5 on the store). Never commit it. |
| `SITE_URL` | The public address, e.g. `https://your-site.vercel.app`. Used for link previews and the User-Agent we send to APIs. |
| `FLASK_DEBUG` | `1` locally for auto-reload. Leave unset in production. |
| `HTTP_TIMEOUT`, `MAX_WORKERS` | Optional tuning (defaults 6 seconds, 16 threads). |

## Data files

Snapshots in `data/` ship with the app so pages never wait on slow sources. Refresh them with `scripts/build_data.py`:

| Command | Writes | Key? |
|---|---|---|
| `python scripts/build_data.py fx` | `data/fx_fallback.json`: exchange rates used if the live feed is down | No |
| `python scripts/build_data.py tags` | `data/tags.json`: Steam tag names | No |
| `python scripts/build_data.py pool` | `data/pool.json`: ~1,000 well-reviewed games with tags and measured playtime, used by Discover, Similar games and the value scores on Deals | No |
| `python scripts/build_data.py index` | `data/search_index.json.gz`: games-only search index with popularity (replaces the old 25 MB `games.json`) | Yes |

Steam rate-limits review requests, so `pool` measures playtime gently and stops when Steam pushes back. Run it again later to fill in the rest. It resumes from `.cache/`.

## Deploying

The site deploys on Vercel straight from GitHub: pushes to `main` go to production, other branches get a preview URL. Vercel detects the Flask app in `app.py` automatically.

- Files in `public/` (CSS, JS, images) are served by Vercel's CDN. Their URLs carry the commit hash, so `vercel.json` lets browsers cache them for a year.
- Pages and API responses send `s-maxage` headers, so the CDN answers most visitors without running Python or calling Steam. My Steam data is sent `private, no-store` and never cached.
- There's no database. Live data is cached in memory per instance, and the snapshots above cover the rest.

## Project layout

```
app.py                  Flask app, template helpers, error pages
config.py               Settings and the 16 price regions
routes/                 pages.py (HTML), api.py (JSON), caching.py (CDN headers)
services/               steam, pricing, fx, reviews, value_score, game,
                        charts, deals, price_index, cheapshark, search,
                        vibes, pool, discover, recommendations, steam_account
templates/              Jinja templates; macros.html holds shared pieces
public/static/          css/ (tokens, base, components, pages), js/ (ES modules), img/
data/                   Snapshots (see above)
scripts/                build_data.py, check_contrast.py, seed_games.json
tests/                  pytest suite
```

## Data sources

Prices, reviews, tags and charts come from Steam's public store and Web APIs. Store deals and all-time lows come from [CheapShark](https://www.cheapshark.com), exchange rates from [ExchangeRate-API](https://www.exchangerate-api.com), and the discovery pool's starting list from [SteamSpy](https://steamspy.com). Icons are from [Lucide](https://lucide.dev) (ISC); fonts are Barlow Condensed and IBM Plex (SIL Open Font License).

PlayNexus isn't affiliated with Valve or Steam.

## Author

Built by Aditya Harikrishnan.

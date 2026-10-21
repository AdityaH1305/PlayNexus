# PlayNexus

Is that Steam game worth the money? PlayNexus looks up any Steam game and tells you what it costs in 16 regions, how much play you get per dollar, what it feels like to play, and whether now is a good time to buy.

It runs as a single Flask app on Vercel, with no build step. An optional free Postgres database keeps a daily price history.

## What's in it

**Game pages**
- A 0–100 **value score** built from typical playtime, review quality and review count, with the price at which the game becomes "great value" and its cost per hour.
- **Prices in 16 regions**, converted at today's exchange rates, with a strip chart showing each region against the US price. Your region is detected (or picked), highlighted, and used to re-score value at your price.
- **Before you buy**: what Steam says about free demos, third-party accounts and DRM, in-app purchases and Early Access, plus a warning when the last 30 days of reviews are much better or worse than the rest.
- **Price check**: the all-time low at any store and current prices at other stores (via CheapShark).
- **Buy now or wait?** (with an IsThereAnyDeal key): whether a lower price is likely soon, from the game's own Steam price history, Steam's seasonal sales and Steam's discount rules, with the facts behind it.
- **Price history**: two years of US Steam prices from IsThereAnyDeal, with big Steam sales shaded (or, with only a database, our own daily records), the lowest of the last 90 days, and a note when today's price is at or near it.
- **Live players** and chart rank, **Steam Deck** status, Metacritic, top user tags.
- A **review history** chart, month by month.
- **What it feels like**: vibes worked out from the tags Steam players give the game, with the tags that earned each one.
- **Similar games** by tag similarity.

**Around the site**
- **Charts**: playing now, top sellers this week, new releases, coming soon.
- **Deals**: today's Steam sales ranked by a deal score you can read, plus the value score at the sale price and a flag on sales at their lowest US price in 90 days.
- **Where is Steam cheapest?**: a regional price index across Steam's most popular paid games.
- **Discover**: pick up to three vibes, filter by price, reviews, players, length and Steam Deck, and let it pick one for you.
- **Compare**: up to four games side by side, best value per row highlighted, shareable links.
- **My Steam**: paste your profile to see which wishlist games are on sale and worth it; with an API key, library stats, a backlog picker and suggestions.
- **Prices in your currency** on charts, deals, Discover, Compare, My Steam and the home page, with a region switch in the nav. Deals show your store's price where it has the same sale, otherwise the US one marked USD. Value scores outside game pages use US prices.
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

**Buy now or wait?** (`services/timing.py`) rests on four things:
- the game's US Steam price history from IsThereAnyDeal;
- Steam's four seasonal sales (`data/steam_sales.json`);
- Steam's discount rules: no discount in the 30 days after a release or a base-price rise, and 30 days between discounts, except that seasonal sales are exempt either side;
- the end date Steam gives for the current discount.

It looks at the last four seasonal sales the game could have joined (it "joined" one if it was discounted for at least half of it), and expects the next one to repeat the most recent cut, since Valve tells developers to deepen discounts over time. The first rule that fits decides:
1. **Buy now** if it's on sale within 2% of its lowest price ever on Steam.
2. **Buy now** if a seasonal sale is running and it's at least as cheap as in its last one.
3. **Wait for the <sale>** if it joined 3 of its last 4 seasonal sales, the next starts within 60 days, and the expected price is at least 15% lower.
4. **Wait for a sale** if it went on sale at least 15% below today's price 8 or more times in the past year, and Steam allows its next ordinary discount within 45 days.
5. **Buy now** if it's on sale within 5% of its lowest price in the past year.
6. **No need to wait** if it has never been discounted in a year or more of records (**No sale yet** for newer games).
7. **Either is fine** if it has been 15%+ cheaper but that isn't likely within 60 days, or if its discounts are small.
8. **Buy now** if it's rarely discounted: no sale in the past year.

Without price history it says **Can't tell yet** and gives only the certain facts.

**How often it's right.** `python scripts/backtest_timing.py` replays the rules on real prices: for 400 games from the discovery pool, every Monday of the past two years, using only what was known that day. A fall of 15% counts as cheaper. "Buy now" is right if the price didn't fall that far in the next 60 days; "Wait" is right if it did, by the end of the sale it named. Over 34,257 replayed days (October 2026):

| Verdict | Share of days | Right |
|---|---|---|
| Wait for the next big sale | 45% | 96% |
| Wait for a sale | 10% | 90% |
| Buy now (and No need to wait) | 28% | 95% |
| Either is fine | 16% | n/a: the price fell 15%+ after 45% of these days |
| No sale yet | 1% | n/a: the price fell 15%+ after 23% of these days |

When waiting was right it saved 68% on average, and the expected sale price was off by 0% (median). For comparison, the price fell 15% or more soon after 61% of all days tested. The thresholds in `services/timing.py` were chosen with this script; waits of up to 75 or 90 days scored the same but ask people to wait much longer, so the window stays at 60 days.

**Recent reviews**: the game page flags the last 30 days of reviews (from Steam's review histogram) when there are at least 50 of them, they're no more than a quarter of all reviews, they sit 15+ points from the histogram's all-time figure, and they're 10+ points from the overall score in the same direction. The histogram doesn't count quite the same reviews as the overall score, so both checks have to agree.

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
| `ITAD_API_KEY` | Optional. "Buy now or wait?" and two years of price history on every game page. Free: register an app on [isthereanydeal.com](https://isthereanydeal.com) and verify your email for the full rate limit (1,000 requests per 5 minutes). Never commit it. |
| `SITE_URL` | The public address, e.g. `https://your-site.vercel.app`. Used for link previews, the links in `robots.txt`, the sitemap and JSON-LD (which otherwise use the requested host), and the User-Agent we send to APIs. |
| `DATABASE_URL` | Optional. Postgres for [price history](#price-history). Neon from the Vercel Marketplace sets it; copy it into `.env` to use the same database locally. Without it, price history is left out. |
| `CRON_SECRET` | With `DATABASE_URL`. A random string of 16+ characters that Vercel sends with its daily cron call; `/api/cron/snapshot` refuses requests without it. Only needed on Vercel. |
| `FLASK_DEBUG` | `1` locally for auto-reload. Leave unset in production. |
| `HTTP_TIMEOUT`, `MAX_WORKERS` | Optional tuning (defaults 6 seconds, 16 threads). |

## Data files

Snapshots in `data/` ship with the app so pages never wait on slow sources. Refresh them with `scripts/build_data.py`:

| Command | Writes | Key? |
|---|---|---|
| `python scripts/build_data.py fx` | `data/fx_fallback.json`: exchange rates used if the live feed is down | No |
| `python scripts/build_data.py tags` | `data/tags.json`: Steam tag names | No |
| `python scripts/build_data.py pool` | `data/pool.json`: ~1,000 well-reviewed games with tags and measured playtime, used by Discover, Similar games and the value scores on Deals | No |
| `python scripts/build_data.py index` | `data/search_index.json.gz`: every game plus real expansions (DLC with 500+ reviews; soundtracks, cosmetics and packs left out), ranked by popularity | Yes |
| `python scripts/build_data.py gems` | `data/gems.json`: indie games with 94%+ positive and 1,000–70,000 reviews, for the home page's "Try …" suggestions. Run after `index` | No |
| Edited by hand | `data/steam_sales.json`: Steam's seasonal sale dates. Add next year's once a year from Valve's [Upcoming Steam Events](https://partner.steamgames.com/doc/marketing/upcoming_events) page | No |

Steam rate-limits review requests, so `pool` measures playtime gently and stops when Steam pushes back. Run it again later to fill in the rest. It resumes from `.cache/`. The `index` step takes about half an hour and also resumes if interrupted.

## Price history

With a Postgres database connected, PlayNexus records the US Steam price of each tracked game once a day. Game pages get a price chart and the lowest price of the last 90 days, with a note when today's price is at that low, within 10% of it, or hasn't moved. Deals get a "90-day low" flag. Without `DATABASE_URL` all of it is left out, and nothing else changes.

- **What's tracked**: every paid game in `data/pool.json` plus this week's 100 best sellers, about 1,000 games. One table, `price_snapshots`, holds one row per game per day (`appid`, `cc`, `day`, `final_cents`, `initial_cents`, `discount`, `currency`). That's roughly 35 MB a year. `services/history.py` creates the table on the first snapshot.
- **When**: a Vercel Cron job (`vercel.json`) calls `/api/cron/snapshot` daily at 22:00 UTC; on Hobby it runs some time within that hour. That's hours after Steam's 10 a.m. Pacific price changes. It makes about 20 batched Steam calls and one database write, so it finishes in seconds. Running it again the same day refreshes that day's rows instead of adding more.
- **Lows you can trust**: a "90-day low" needs 90 days of data. Until a game has been tracked that long, its page says "lowest since" the first day we have, and Deals doesn't flag it.
- **Locked down**: the snapshot endpoint only answers requests with `Authorization: Bearer <CRON_SECRET>`, which Vercel adds to its cron calls. Without `CRON_SECRET` set it refuses everyone.
- **Reads are cached**: game pages fetch `/api/game/<id>/history` after the page loads, and the CDN keeps it for an hour, so a sleeping database never slows a page down.

**Setting it up on Vercel**

1. Project → Storage → Create Database → Neon. Pick the Free plan and the region nearest your functions (Washington, D.C. unless you changed it). Connect it to the project for Development, Preview and Production with no environment-variable prefix, so the variable is named `DATABASE_URL`.
2. Project → Settings → Environment Variables: add `CRON_SECRET` for Production.
3. Push or redeploy. The job then shows under Project → Settings → Cron Jobs, where **Run** takes a snapshot straight away.

To take a snapshot from your machine (it writes to whatever database `DATABASE_URL` in `.env` points at):

```bash
python -c "from services import history; print(history.snapshot())"
```

## Deploying

The site deploys on Vercel straight from GitHub: pushes to `main` go to production, other branches get a preview URL. Vercel detects the Flask app in `app.py` automatically.

- Files in `public/` (CSS, JS, images) are served by Vercel's CDN. Their URLs carry the commit hash, so `vercel.json` lets browsers cache them for a year.
- Pages and API responses send `s-maxage` headers, so the CDN answers most visitors without running Python or calling Steam. My Steam data is sent `private, no-store` and never cached.
- Because pages are shared by everyone, they're rendered with US prices. For visitors elsewhere, `public/static/js/prices.js` fetches their store's prices from `/api/prices/batch?ids=…&region=…` (up to 100 apps, also CDN-cached) and swaps them in; CSS sets aside room for them first, so nothing shifts.
- The home page's "Try …" line should change on every visit, so the cached page carries 20 sets of suggestions (`services/suggestions.py`: mostly indie gems, sometimes a big recent release or an all-time favorite) and a few lines of inline script show a random one before the line is painted.
- The only database is the optional price history (above). Live data is cached in memory per instance, and the snapshots in `data/` cover the rest.

### Search engines and analytics

- `/robots.txt` lets crawlers in everywhere except `/api/` and points them to `/sitemap.xml`, which lists the main pages, every game in the discovery pool and this week's top sellers. Both come from `routes/seo.py`, are built from data already in memory (no per-game Steam calls) and are cached for a day.
- Game pages carry schema.org `VideoGame` JSON-LD. It has no `aggregateRating` on purpose: the reviews are Steam's, and Google doesn't allow third-party ratings marked up as your own.
- Vercel Web Analytics (free on Hobby, no cookies) is switched on in the Vercel dashboard: Project → Analytics → Enable. Its script tag is only rendered when Vercel's `VERCEL` variable is set, so local runs don't ask for a script that doesn't exist.

## Project layout

```
app.py                  Flask app, template helpers, error pages
config.py               Settings and the 16 price regions
routes/                 pages.py (HTML), api.py (JSON), seo.py (robots.txt, sitemap),
                        caching.py (CDN headers)
services/               steam, pricing, fx, reviews, value_score, game,
                        charts, deals, price_index, cheapshark, search,
                        vibes, pool, discover, recommendations, steam_account,
                        history (price history), itad (IsThereAnyDeal),
                        timing (buy now or wait), steam_sales (sale calendar)
templates/              Jinja templates; macros.html holds shared pieces
public/static/          css/ (tokens, base, components, pages), js/ (ES modules), img/
data/                   Snapshots (see above)
scripts/                build_data.py, check_contrast.py, seed_games.json
tests/                  pytest suite
```

## Data sources

Prices, reviews, tags and charts come from Steam's public store and Web APIs. Steam price history comes from the [IsThereAnyDeal API](https://docs.isthereanydeal.com), and game pages that use it say so and link to the game there, as its terms ask. Store deals and all-time lows come from [CheapShark](https://www.cheapshark.com), exchange rates from [ExchangeRate-API](https://www.exchangerate-api.com), and the discovery pool's starting list from [SteamSpy](https://steamspy.com). Sale dates and discount rules are Valve's, from the Steamworks documentation. Icons are from [Lucide](https://lucide.dev) (ISC); fonts are Barlow Condensed and IBM Plex (SIL Open Font License).

PlayNexus isn't affiliated with Valve or Steam.

## Author

Built by Aditya Harikrishnan.

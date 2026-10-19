import {
  $,
  $$,
  artImg,
  compareList,
  favorites,
  fmtInt,
  fmtUSD,
  getJSON,
  html,
  pageData,
  recent,
  safeUrl,
  setHTML,
  setRegion,
  toast,
  watchRegion,
} from "pn/core";
import { dayLabel, priceHistory, reviewHistory } from "pn/charts";

const data = pageData();
const game = { appid: data.appid, name: data.name, cover: data.header };
recent.add(game);

/* ── Favorite and compare ──────────────────────────────── */

const favBtn = $("[data-fav]");
function syncFav() {
  const on = favorites.has(game.appid);
  favBtn.setAttribute("aria-pressed", String(on));
  favBtn.setAttribute("aria-label", on ? "Remove from favorites" : "Save to favorites");
}
favBtn.addEventListener("click", () => {
  toast(favorites.toggle(game) ? "Saved to favorites" : "Removed from favorites");
  syncFav();
});
syncFav();

$("[data-compare]").addEventListener("click", () => {
  const result = compareList.add(game);
  if (result === "full") toast("Compare holds four games. Remove one on the compare page first.");
  else if (result === "exists") toast(`${game.name} is already in your comparison.`);
  else toast(`Added to compare (${compareList.all().length} of 4)`);
});

/* ── Value explainer ───────────────────────────────────── */

const explainBtn = $("[data-explain]");
explainBtn?.addEventListener("click", () => {
  const panel = $("#value-explain");
  const open = panel.hidden;
  panel.hidden = !open;
  explainBtn.setAttribute("aria-expanded", String(open));
});

/* ── Your region ───────────────────────────────────────── */

const select = $("[data-region-select]");
const usRow = data.regions.find((r) => r.code === "US");
const base = {
  score: data.value.score,
  label: data.value.label,
  cph: data.value.breakdown?.cost_per_hour,
};

function setText(sel, text) {
  const el = $(sel);
  if (el) el.textContent = text;
}

function applyRegion(code) {
  if (select) select.value = code || "";
  $$(".price-table tr[data-region]").forEach((tr) => tr.classList.toggle("is-mine", tr.dataset.region === code));
  if (data.is_free) return;

  const row = data.regions.find((r) => r.code === code && r.available);
  const shown = row || (usRow?.available ? usRow : null);
  if (shown) {
    setText("[data-your-label]", shown.name);
    setText("[data-your-price]", shown.final_formatted);
  }

  const meter = $("[data-meter]");
  if (!meter || base.score == null) return;
  const regional = row && row.code !== "US" ? data.value.by_region?.[row.code] : null;
  const v = regional || base;
  const cph = regional ? regional.cost_per_hour : base.cph;
  setText("[data-v-score]", v.score);
  setText("[data-v-label]", v.label);
  setText("[data-v-cph]", cph ? fmtUSD(cph) : "—");
  setText(
    "[data-v-basis]",
    regional
      ? `At your price in ${row.name}, ${row.final_formatted} (≈ ${fmtUSD(row.usd)})`
      : `At the US price of ${fmtUSD(data.us_usd)}`,
  );
  meter.style.setProperty("--score", v.score);
  meter.setAttribute("aria-label", `Value score ${v.score} out of 100`);
}

// Shares the saved region with the switch in the site nav (see main.js).
select?.addEventListener("change", () => setRegion(select.value || null));
watchRegion(applyRegion);

/* ── Live players ──────────────────────────────────────── */

getJSON(`/api/game/${game.appid}/live`, { ttl: 300_000 })
  .then((live) => {
    if (live.players == null || live.players === 0) return;
    setText("[data-live-count]", fmtInt(live.players));
    if (live.rank) setText("[data-live-rank]", ` · #${live.rank} on Steam`);
    $("[data-live]").hidden = false;
  })
  .catch(() => {});

/* ── Other stores and all-time low ─────────────────────── */

const dealsEl = $("[data-deals]");
if (dealsEl) {
  getJSON(`/api/game/${game.appid}/deals`, { ttl: 3_600_000 })
    .then(({ cheapest_ever: low, deals }) => {
      if (!low && !deals.length) {
        setHTML(dealsEl, html`<p class="small muted">No other stores we track sell this game.</p>`);
        return;
      }
      const when = low?.date
        ? new Date(low.date * 1000).toLocaleDateString("en-GB", { month: "short", year: "numeric" })
        : "";
      setHTML(
        dealsEl,
        html`${low
            ? html`<div class="deals__low">
                <span class="small text-2">Lowest ever, any store</span>
                <span><strong class="num">${fmtUSD(low.price)}</strong> <span class="small muted">${when}</span></span>
              </div>`
            : ""}
          ${deals.length
            ? html`<p class="eyebrow" style="margin-top: 12px">Right now</p>
              <ul class="deals__list">
                ${deals.slice(0, 5).map(
                  (d) => html`<li><a class="deal" href="${safeUrl(d.url)}" target="_blank" rel="noopener sponsored">
                    <img src="${safeUrl(d.icon)}" alt="" loading="lazy">
                    <span class="deal__store">${d.store}</span>
                    ${d.savings ? html`<span class="discount">−${d.savings}%</span>` : html`<span></span>`}
                    <span class="num">${fmtUSD(d.price)}</span></a></li>`,
                )}
              </ul>`
            : ""}
          <p class="tiny muted" style="margin-top: 8px">US prices from CheapShark.</p>`,
      );
    })
    .catch(() => setHTML(dealsEl, html`<p class="small muted">Couldn't check other stores just now.</p>`));
}

/* ── Price history ─────────────────────────────────────── */

const priceChartEl = $("[data-price-history]");
const priceLowEl = $("[data-price-low]");

function priceLowMarkup({ low, now, status }) {
  const span = low.full ? "in 90 days" : `since ${dayLabel(low.since)}`;
  const hint = {
    low: html`${low.full ? html`<span class="flag-note flag-note--accent">90-day low</span> ` : ""}Today's US price is the lowest ${span}.`,
    near: html`Today's US price is within ${fmtUSD((now - low.final) / 100)} of that.`,
    flat: html`The US price hasn't changed ${span}.`,
  }[status];
  return html`<div class="deals__low">
      <span class="small text-2">${low.full ? "Lowest US price, last 90 days" : `Lowest US price since ${dayLabel(low.since)}`}</span>
      <span><strong class="num">${fmtUSD(low.final / 100)}</strong>${status === "flat" ? "" : html` <span class="small muted">${dayLabel(low.day)}</span>`}</span>
    </div>
    ${hint ? html`<p class="small price-low__hint">${hint}</p>` : ""}`;
}

if (priceChartEl || priceLowEl) {
  getJSON(`/api/game/${game.appid}/history`, { ttl: 3_600_000 })
    .then((h) => {
      // One day isn't a history yet: show nothing until there are two.
      const pts = h.points || [];
      if (pts.length < 2) return;
      if (priceChartEl) {
        $("[data-price-history-panel]").hidden = false; // first, so the chart can measure its width
        priceHistory(priceChartEl, h);
        const first = dayLabel(pts[0].day);
        const last = pts[pts.length - 1].day;
        const stale = Date.now() - Date.parse(`${last}T00:00:00Z`) > 3 * 86_400_000; // no longer tracked
        setText("[data-price-history-summary]", stale ? `${first} to ${dayLabel(last)}` : `Since ${first}`);
      }
      if (priceLowEl && h.low) {
        setHTML(priceLowEl, priceLowMarkup(h));
        priceLowEl.hidden = false;
      }
    })
    .catch(() => {}); // price history is optional: it just stays hidden
}

/* ── Review history ────────────────────────────────────── */

const historyEl = $("[data-history]");
if (historyEl) {
  getJSON(`/api/game/${game.appid}/reviews`, { ttl: 3_600_000 })
    .then((h) => {
      reviewHistory(historyEl, h);
      const { up, down, days } = h.recent || {};
      if (up + down >= 10) {
        setText("[data-history-summary]", `Last ${days} days: ${Math.round((up / (up + down)) * 100)}% positive of ${fmtInt(up + down)}`);
      }
    })
    .catch(() => setHTML(historyEl, html`<p class="small muted">Couldn't load review history just now.</p>`));
}

/* ── Similar games ─────────────────────────────────────── */

const similarEl = $("[data-similar]");
getJSON(`/api/recommendations/${game.appid}`, { ttl: 3_600_000 })
  .then((items) => {
    if (!items.length) {
      setHTML(similarEl, html`<p class="small muted">No close matches found.</p>`);
      return;
    }
    setHTML(
      similarEl,
      items.slice(0, 6).map(
        (g) => html`<a class="grow" href="/game/${g.appid}">
          <span class="grow__art">${artImg(g.appid, g.capsule || null, "capsule")}</span>
          <span><span class="grow__title">${g.name}</span><span class="grow__meta">${g.match_reason || ""}</span></span></a>`,
      ),
    );
  })
  .catch(() => setHTML(similarEl, html`<p class="small muted">Couldn't load similar games just now.</p>`));

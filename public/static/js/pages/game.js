import {
  $,
  $$,
  KEYS,
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
  store,
  toast,
} from "pn/core";
import { reviewHistory } from "pn/charts";

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

const EUROZONE = new Set("AT BE HR CY EE FI FR DE GR IE IT LV LT LU MT NL PT SK SI ES".split(" "));
const select = $("[data-region-select]");
const regionCodes = new Set(data.regions.map((r) => r.code));
const usRow = data.regions.find((r) => r.code === "US");
const base = {
  score: data.value.score,
  label: data.value.label,
  cph: data.value.breakdown?.cost_per_hour,
};

function regionFromCountry(cc) {
  if (!cc) return null;
  cc = cc.toUpperCase();
  if (EUROZONE.has(cc)) return "EU";
  return regionCodes.has(cc) ? cc : null;
}

async function detectRegion() {
  const saved = store.read(KEYS.region, null);
  if (saved && regionCodes.has(saved)) return saved;
  try {
    const cached = sessionStorage.getItem("playnexus_whereami");
    const where = cached ? JSON.parse(cached) : await getJSON("/api/whereami");
    if (!cached) sessionStorage.setItem("playnexus_whereami", JSON.stringify(where));
    if (where.region && regionCodes.has(where.region)) return where.region;
  } catch {
    /* fall through to the browser language */
  }
  const m = (navigator.language || "").match(/-([A-Za-z]{2})$/);
  return regionFromCountry(m && m[1]);
}

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

select?.addEventListener("change", () => {
  store.write(KEYS.region, select.value || null);
  applyRegion(select.value || null);
});
detectRegion().then(applyRegion);

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

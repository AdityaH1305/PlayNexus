/* Local prices on list pages.

   Pages are cached for everyone, so they're rendered with US prices. For a
   visitor elsewhere this fetches their own store's prices from
   /api/prices/batch and swaps them in. Mark a price with data-lp="<appid>"
   and data-lp-usd="<US price>"; whatever is inside is the US version, which
   comes back if the visitor switches to the US. data-lp-mode says what to show:

     price     the local price and discount (charts, Discover)
     sale      the local sale if the visitor's store has the game on sale too,
               otherwise the US sale price marked USD (home page deals)
     sale-row  the same, with the regular price struck through (Deals)

   Room for local prices is set aside in CSS under :root[data-region], which
   base.html sets before first paint when it already knows the region, so the
   swap doesn't move anything. */

import { $$, fmtUSD, getJSON, html, regionName, setHTML, watchRegion } from "pn/core";

const MAX_IDS = 100; // per request: routes/api.py allows no more

const usMarkup = new WeakMap(); // element -> the server-rendered US price
const swapped = new WeakSet();
let shown = { region: null, prices: new Map() };
let run = 0;

async function fetchPrices(ids, region) {
  // Sorted, so everyone on the same page asks for the same URLs and the CDN can answer.
  const sorted = [...new Set(ids)].sort((a, b) => a - b);
  const requests = [];
  for (let i = 0; i < sorted.length; i += MAX_IDS) {
    const chunk = sorted.slice(i, i + MAX_IDS).join(",");
    requests.push(getJSON(`/api/prices/batch?ids=${chunk}&region=${encodeURIComponent(region)}`, { ttl: 1_800_000 }));
  }
  const prices = new Map();
  for (const r of await Promise.allSettled(requests)) {
    if (r.status !== "fulfilled") continue;
    for (const [id, entry] of Object.entries(r.value.prices || {})) prices.set(Number(id), entry);
  }
  return prices;
}

function restore(el) {
  if (!swapped.has(el)) return;
  el.innerHTML = usMarkup.get(el);
  swapped.delete(el);
}

function render(el, entry, region) {
  restore(el);
  const mode = el.dataset.lpMode || "price";
  const name = regionName(region);
  swapped.add(el);

  // No local price to show: keep the US one and say so.
  if (!entry || (mode !== "price" && !entry.discount)) {
    const why =
      entry === undefined
        ? `Couldn't load ${name} prices just now, so this is the US price.`
        : entry === null
          ? `Steam has no ${name} price for this game, so this is the US price.`
          : `Not on sale in the ${name} store right now, so this is the US sale price.`;
    const flag = html` <span class="flag-note flag-note--quiet lp-usd" title="${why}">USD</span>`;
    (el.querySelector(".deal-price__now, .num") || el).insertAdjacentHTML("afterend", String(flag));
    return;
  }

  const usPrice = `US price ${fmtUSD(Number(el.dataset.lpUsd))}.`;
  const title = entry.usd == null ? usPrice : `About ${fmtUSD(entry.usd)} at today's exchange rate. ${usPrice}`;
  const badge = entry.discount ? html`<span class="discount">−${entry.discount}%</span>` : "";
  setHTML(
    el,
    mode === "sale-row"
      ? html`${badge}<span class="deal-price__now" title="${title}">${entry.final_formatted}</span>${
          entry.initial_formatted ? html`<s class="muted small">${entry.initial_formatted}</s>` : ""
        }`
      : html`${badge}${badge ? " " : ""}<span class="num" title="${title}">${entry.final_formatted}</span>`,
  );
}

async function apply(code) {
  const id = ++run;
  const region = code && code !== "US" ? code : null;
  const root = document.documentElement;
  if (region) root.dataset.region = region;
  else delete root.dataset.region;
  $$("[data-region-name]").forEach((el) => (el.textContent = regionName(region || "US")));

  const els = $$("[data-lp]");
  for (const el of els) if (!usMarkup.has(el)) usMarkup.set(el, el.innerHTML);
  if (!region) {
    shown = { region: null, prices: new Map() };
    els.forEach(restore);
    return;
  }
  if (id > 1) {
    // Arrowing through the region picker fires a change per key; fetch only where it stops.
    await new Promise((r) => setTimeout(r, 250));
    if (id !== run) return;
  }
  const prices = els.length ? await fetchPrices(els.map((el) => Number(el.dataset.lp)), region) : new Map();
  if (id !== run) return; // switched again while this was loading
  shown = { region, prices };
  els.forEach((el) => render(el, prices.get(Number(el.dataset.lp)), region));
}

/** An app's price as text: the visitor's local one if it's loaded, else the US one. */
export function priceText(appid, usText) {
  if (!shown.region || !usText) return usText;
  const entry = shown.prices.get(Number(appid));
  return entry ? entry.final_formatted : `${usText} USD`;
}

export function initLocalPrices() {
  watchRegion(apply);
}

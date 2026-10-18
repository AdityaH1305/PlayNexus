/* Compare up to four games. The URL (?ids=…) is the source of truth so a
   comparison can be shared; the list is mirrored to localStorage so the nav
   count and "add to compare" buttons elsewhere stay in sync. */

import {
  $,
  MAX_COMPARE,
  artImg,
  compareList,
  fmtInt,
  fmtUSD,
  getJSON,
  html,
  icon,
  reviewTone,
  setHTML,
  toast,
} from "pn/core";
import { Combobox } from "pn/search";

const body = $("#compare-body");
const cache = new Map(); // appid -> bundle | "error"

function idsFromUrl() {
  const raw = new URLSearchParams(location.search).get("ids");
  if (!raw) return null;
  return [...new Set(raw.split(",").map(Number).filter((n) => Number.isInteger(n) && n > 0))].slice(0, MAX_COMPARE);
}

function syncUrl() {
  const ids = compareList.all().map((g) => g.appid);
  const url = new URL(location.href);
  if (ids.length) url.searchParams.set("ids", ids.join(","));
  else url.searchParams.delete("ids");
  history.replaceState(null, "", url);
}

// The URL wins on first load (a shared link), keeping names we already know.
const fromUrl = idsFromUrl();
if (fromUrl) {
  const known = new Map(compareList.all().map((g) => [g.appid, g]));
  compareList.save(fromUrl.map((id) => known.get(id) || { appid: id, name: `App ${id}` }));
}
syncUrl();

new Combobox($("#compare-search"), {
  onChoose(item) {
    const result = compareList.add({ appid: item.appid, name: item.name, cover: item.image || null });
    if (result === "full") toast(`You can compare up to ${MAX_COMPARE} games. Remove one first.`);
    else if (result === "exists") toast(`${item.name} is already in the comparison.`);
    const input = $("#compare-search .combo__input");
    input.value = "";
    input.dispatchEvent(new Event("input"));
    syncUrl();
    render();
  },
});

$("[data-clear-compare]").addEventListener("click", () => {
  compareList.clear();
  syncUrl();
  render();
});

$("[data-copy-link]").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(location.href);
    toast("Link copied");
  } catch {
    toast("Couldn't copy. Copy the address bar instead.");
  }
});

body.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-remove]");
  if (!btn) return;
  compareList.remove(Number(btn.dataset.remove));
  syncUrl();
  render();
});

/* Rows: label, how to read a value, how to show it, and which way is better. */
const ROWS = [
  {
    label: "Price (US)",
    value: (g) => (g.is_free ? 0 : g.usd_price),
    show: (g) => (g.is_free ? "Free" : g.usd_price == null ? "Not for sale" : fmtUSD(g.usd_price)),
    better: "low",
  },
  {
    label: "Cheapest region",
    value: (g) => g.cheapest_region?.usd ?? null,
    show: (g) =>
      g.cheapest_region
        ? html`<span class="chip-region">${g.cheapest_region.code}</span> <span class="num">${g.cheapest_region.final_formatted}</span>
            <span class="muted small num">≈ ${fmtUSD(g.cheapest_region.usd)}</span>`
        : html`<span class="muted">—</span>`,
    better: "low",
  },
  {
    label: "Value score",
    value: (g) => g.value?.score ?? null,
    show: (g) =>
      g.value?.score == null
        ? html`<span class="muted">—</span>`
        : html`<span class="num score">${g.value.score}</span> <span class="small text-2">${g.value.label}</span>`,
    better: "high",
  },
  {
    label: "Cost per hour",
    value: (g) => g.value?.breakdown?.cost_per_hour ?? null,
    show: (g) => (g.value?.breakdown?.cost_per_hour == null ? "—" : `${fmtUSD(g.value.breakdown.cost_per_hour)} / h`),
    better: "low",
  },
  {
    label: "Great value at",
    value: (g) => g.value?.worth_it?.great ?? null,
    show: (g) => {
      if (g.is_free) return "Free";
      const p = g.value?.worth_it?.great;
      return p == null ? html`<span class="muted">Not at any price</span>` : `${fmtUSD(p)} or less`;
    },
    better: "high",
  },
  {
    label: "Reviews",
    value: (g) => (g.reviews?.total_reviews >= 10 ? g.reviews.positivity * 100 : null),
    show: (g) => {
      const r = g.reviews;
      if (!r || r.total_reviews < 10) return html`<span class="muted">Too few reviews</span>`;
      const pct = Math.round(r.positivity * 100);
      return html`<span class="review review--${reviewTone(pct)}"><span class="review__label">${r.review_label || "Reviews"}</span>
        <span class="review__pct num">${pct}%</span></span><div class="small muted num">${fmtInt(r.total_reviews)} reviews</div>`;
    },
    better: "high",
  },
  {
    label: "Typical playtime",
    value: (g) => g.value?.breakdown?.effective_hours ?? null,
    show: (g) => (g.value?.breakdown?.effective_hours ? `${fmtInt(g.value.breakdown.effective_hours)} h` : "—"),
    better: "high",
  },
  {
    label: "Vibes",
    value: () => null,
    show: (g) =>
      g.vibes?.top?.length
        ? html`<div class="tags">${g.vibes.top.slice(0, 3).map((v) => html`<span class="tag">${v.label}</span>`)}</div>`
        : html`<span class="muted">—</span>`,
  },
];

function bestIndexes(row, games) {
  if (!row.better) return new Set();
  const vals = games.map((g) => row.value(g));
  const nums = vals.filter((v) => typeof v === "number");
  if (nums.length < 2) return new Set();
  const target = row.better === "low" ? Math.min(...nums) : Math.max(...nums);
  if (nums.every((v) => v === target)) return new Set();
  return new Set(vals.map((v, i) => (v === target ? i : -1)).filter((i) => i >= 0));
}

function table(games) {
  return html`<div class="table-wrap compare-wrap">
    <table class="table compare-table">
      <thead><tr>
        <th scope="col"><span class="sr-only">Measure</span></th>
        ${games.map(
          (g) => html`<th scope="col">
            <a class="compare-game" href="/game/${g.appid}">
              <span class="compare-game__art">${artImg(g.appid, g.header, "header")}</span>
              <span class="compare-game__name">${g.name}</span>
            </a>
            <button class="btn btn--sm btn--quiet compare-remove" type="button" data-remove="${g.appid}" aria-label="Remove ${g.name}">
              ${icon("x", "icon--sm")}Remove</button>
          </th>`,
        )}
      </tr></thead>
      <tbody>
        ${ROWS.map((row) => {
          const best = bestIndexes(row, games);
          return html`<tr><th scope="row">${row.label}</th>
            ${games.map((g, i) => html`<td class="${best.has(i) ? "is-best" : ""}">${row.show(g)}${best.has(i) ? html`<span class="best-mark">Best</span>` : ""}</td>`)}
          </tr>`;
        })}
      </tbody>
    </table></div>`;
}

async function load(appid) {
  if (!cache.has(appid)) {
    try {
      cache.set(appid, await getJSON(`/api/game/${appid}`, { ttl: 600_000 }));
    } catch {
      cache.set(appid, "error");
    }
  }
  return cache.get(appid);
}

let renderId = 0;

async function render() {
  const id = ++renderId;
  const list = compareList.all();
  if (list.length < 2) {
    setHTML(
      body,
      html`<div class="empty">
        <p class="empty__title">${list.length ? `Add one more game to compare with ${list[0].name}` : "Add two or more games"}</p>
        <p class="empty__body">Search above, or use “Compare” on any game's page.</p>
        ${list.length ? html`<button class="btn btn--sm" type="button" data-remove="${list[0].appid}">Remove ${list[0].name}</button>` : ""}
      </div>`,
    );
    return;
  }
  setHTML(body, html`<div class="skel-list">${list.map(() => html`<div class="skel skel--row"></div>`)}</div>`);
  const bundles = await Promise.all(list.map((g) => load(g.appid)));
  if (id !== renderId) return;
  const games = bundles.filter((b) => b && b !== "error");
  const failed = list.filter((_, i) => bundles[i] === "error");
  // Fill in names for entries that arrived by shared link.
  if (games.length) {
    compareList.save(list.map((g) => {
      const b = games.find((x) => x.appid === g.appid);
      return b ? { appid: b.appid, name: b.name, cover: b.header } : g;
    }));
  }
  setHTML(
    body,
    html`${failed.length ? html`<p class="notice">${icon("info")}Couldn't load ${failed.map((g) => g.name).join(", ")}. Steam may be busy; try again shortly.</p>` : ""}
      ${games.length >= 2 ? table(games) : ""}`,
  );
}

render();

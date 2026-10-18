import { $, $$, artImg, getJSON, html, setHTML, toast } from "pn/core";

const MAX = 3;
const status = $("#discover-status");
const results = $("#discover-results");
const toggles = $$("[data-vibe]");

function selected() {
  return toggles.filter((b) => b.getAttribute("aria-pressed") === "true").map((b) => b.dataset.vibe);
}

function card(g, picked) {
  const matches = (g.top || []).filter((v) => picked.includes(v.key)).map((v) => v.label);
  return html`<a class="gcard" href="/game/${g.appid}">
    <span class="gcard__art">${artImg(g.appid, g.cover, "header")}</span>
    <span class="gcard__title">${g.name}</span>
    ${matches.length ? html`<span class="gcard__meta">${matches.join(" · ")}</span>` : ""}</a>`;
}

let requestId = 0;

async function refresh() {
  const picked = selected();
  if (!picked.length) {
    status.textContent = "Choose a vibe to start.";
    setHTML(results, "");
    return;
  }
  const id = ++requestId;
  status.textContent = "Finding games…";
  setHTML(results, Array.from({ length: 6 }, () => html`<div class="skel skel--card" aria-hidden="true"></div>`));
  try {
    const params = new URLSearchParams(picked.map((k) => ["m", k]));
    const games = await getJSON(`/api/discover?${params}`, { ttl: 600_000 });
    if (id !== requestId) return;
    status.textContent = games.length ? `${games.length} games` : "Nothing matches that combination. Try dropping a vibe.";
    setHTML(results, games.map((g) => card(g, picked)));
  } catch {
    if (id !== requestId) return;
    status.textContent = "Couldn't load games just now. Try again in a moment.";
    setHTML(results, "");
  }
}

toggles.forEach((btn) =>
  btn.addEventListener("click", () => {
    const on = btn.getAttribute("aria-pressed") === "true";
    if (!on && selected().length >= MAX) {
      toast(`Pick up to ${MAX} vibes. Turn one off first.`);
      return;
    }
    btn.setAttribute("aria-pressed", String(!on));
    refresh();
  }),
);

import { $, $$, KEYS, artImg, html, pageData, setHTML, store, toast } from "pn/core";

const MAX_VIBES = 3;
const form = $("[data-discover-form]");
const data = pageData() || { results: [], picked: [] };

/* ── Filters apply as soon as they change ──────────────── */

function vibeBoxes() {
  return $$('input[name="v"]', form);
}

function limitVibes() {
  const checked = vibeBoxes().filter((b) => b.checked).length;
  vibeBoxes().forEach((b) => {
    b.disabled = !b.checked && checked >= MAX_VIBES;
    b.closest(".toggle-check")?.classList.toggle("is-disabled", b.disabled);
  });
}

form?.addEventListener("change", () => {
  limitVibes();
  const params = new URLSearchParams(new FormData(form));
  for (const [k, v] of [...params]) if (!v) params.delete(k);
  location.search = params.toString();
});
limitVibes();

/* ── Not interested ────────────────────────────────────── */

const hidden = () => new Set((store.read(KEYS.hidden, []) || []).map(Number));

function applyHidden() {
  const set = hidden();
  let n = 0;
  $$(".dcard[data-appid]").forEach((card) => {
    const hide = set.has(Number(card.dataset.appid));
    card.hidden = hide;
    if (hide) n += 1;
  });
  const note = $("[data-hidden-note]");
  if (note) {
    note.hidden = n === 0;
    setHTML(note, n ? html`${n} game${n === 1 ? "" : "s"} you marked “not interested” ${n === 1 ? "is" : "are"} hidden. <button class="btn btn--sm btn--quiet" type="button" data-unhide>Show them again</button>` : "");
  }
}

document.addEventListener("click", (e) => {
  const hideBtn = e.target.closest("[data-hide]");
  if (hideBtn) {
    const id = Number(hideBtn.dataset.hide);
    store.write(KEYS.hidden, [...hidden(), id].slice(-500));
    applyHidden();
    toast("Hidden. We won't suggest it here again.");
  }
  if (e.target.closest("[data-unhide]")) {
    store.write(KEYS.hidden, []);
    applyHidden();
  }
});
applyHidden();

/* ── Pick for me ───────────────────────────────────────── */

const picker = $("[data-picker]");
let lastPick = null;

function pick() {
  const set = hidden();
  const pool = data.results.filter((g) => !set.has(g.appid) && g.appid !== lastPick);
  if (!pool.length) {
    toast("Nothing left to pick from. Loosen the filters or show hidden games.");
    return;
  }
  // Earlier results fit better, so they're more likely, but anything can come up.
  const weights = pool.map((_, i) => (pool.length - i) ** 1.3);
  let r = Math.random() * weights.reduce((a, b) => a + b, 0);
  let g = pool[pool.length - 1];
  for (let i = 0; i < pool.length; i++) {
    r -= weights[i];
    if (r <= 0) {
      g = pool[i];
      break;
    }
  }
  lastPick = g.appid;

  const reasons = [];
  if (g.vibes.length) reasons.push(`${g.fits_all ? "Fits" : "Leans"} ${g.vibes.map((v) => v.label.toLowerCase()).join(" and ")}`);
  if (g.review_pct != null) reasons.push(`${g.review_pct}% positive`);
  if (g.hours) reasons.push(`about ${Math.round(g.hours)} hours`);
  const price = g.is_free ? "Free" : g.price ? g.price.final_formatted : "";

  setHTML(
    picker,
    html`<div class="picker__card">
      <a class="picker__art" href="/game/${g.appid}">${artImg(g.appid, g.header, "header")}</a>
      <div class="picker__body">
        <p class="eyebrow">Tonight, try</p>
        <h2><a href="/game/${g.appid}">${g.name}</a></h2>
        <p class="text-2">${reasons.join(" · ")}${price ? html` · <span class="num">${price}</span>` : ""}</p>
        <div class="row row--wrap">
          <a class="btn btn--primary btn--sm" href="/game/${g.appid}">Open game</a>
          <button class="btn btn--sm" type="button" data-pick>Pick again</button>
          <button class="btn btn--sm btn--quiet" type="button" data-hide="${g.appid}">Not interested</button>
        </div>
      </div>
    </div>`,
  );
  picker.hidden = false;
  picker.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

document.addEventListener("click", (e) => {
  if (e.target.closest("[data-pick]")) pick();
});

import {
  $,
  KEYS,
  artImg,
  debounce,
  detectRegion,
  fmtInt,
  fmtUSD,
  html,
  icon,
  pageData,
  regionName,
  reviewTone,
  safeUrl,
  setHTML,
  store,
  toast,
  watchRegion,
} from "pn/core";

const form = $("[data-me-form]");
const statusEl = $("[data-me-status]");
const page = pageData() || {};
const asRegion = (code) => (code && code !== "US" ? code : null);
let region = null; // the visitor's price region; null for the US
let wishFor = null; // SteamID whose wishlist is showing
let wishRun = 0;

function status(msg, tone = "") {
  setHTML(statusEl, msg ? html`<p class="notice ${tone}">${icon("info")}${msg}</p>` : "");
}

async function api(url) {
  const r = await fetch(url, { headers: { Accept: "application/json" }, cache: "no-store" });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(body.error || "Something went wrong. Try again.");
    err.reason = body.reason;
    err.status = r.status;
    throw err;
  }
  return body;
}

/* ── Profile ───────────────────────────────────────────── */

function renderProfile(p) {
  const box = $("[data-me-profile]");
  setHTML(
    box,
    html`<div class="me-profile__card">
      ${p.avatar ? html`<img class="me-profile__avatar" src="${safeUrl(p.avatar)}" alt="" width="48" height="48">` : ""}
      <div>
        <p class="me-profile__name">${p.name || "Steam user"}</p>
        <p class="small muted num">${p.steamid}${p.public ? "" : " · profile is private"}</p>
      </div>
      <button class="btn btn--sm btn--quiet" type="button" data-forget>Not you? Look up someone else</button>
    </div>`,
  );
  box.hidden = false;
}

document.addEventListener("click", (e) => {
  if (!e.target.closest("[data-forget]")) return;
  store.write(KEYS.steam, null);
  wishFor = null;
  wishRun += 1;
  ["[data-me-profile]", "[data-me-wishlist]", "[data-me-library]"].forEach((s) => ($(s).hidden = true));
  history.replaceState(null, "", location.pathname);
  form.q.value = "";
  form.q.focus();
  status("");
});

/* ── Wishlist ──────────────────────────────────────────── */

function priceCell(g) {
  if (g.is_free) return html`<span class="muted">Free</span>`;
  if (!g.price) return html`<span class="muted">${g.coming_soon ? "Not out yet" : "—"}</span>`;
  return html`${g.discount ? html`<span class="discount">−${g.discount}%</span> ` : ""}<span class="num">${g.price.final_formatted}</span>`;
}

function renderWishlist(w) {
  const section = $("[data-me-wishlist]");
  section.hidden = false;
  $("[data-wish-summary]").textContent = w.count
    ? `${w.on_sale} of ${w.count} on sale now`
    : "";
  if (!w.count) {
    setHTML($("[data-wish-body]"), html`<p class="text-2">This wishlist is empty, or it's private.</p>`);
    return;
  }
  const rows = (list) =>
    list.map(
      (g) => html`<tr>
        <td><a class="chart-game" href="/game/${g.appid}">
          <span class="chart-game__art">${artImg(g.appid, g.capsule, "capsule")}</span>
          <span class="chart-game__text"><span class="chart-game__name">${g.name}</span></span></a></td>
        <td class="hide-sm">${g.review_pct != null
          ? html`<span class="review review--${reviewTone(g.review_pct)}"><span class="review__label">${g.review_label}</span><span class="review__pct num">${g.review_pct}%</span></span>`
          : html`<span class="muted small">Too few reviews</span>`}</td>
        <td class="num nowrap">${priceCell(g)}</td>
        <td class="num hide-sm">${g.value ? html`<span class="num">${g.value.score}</span> <span class="tiny muted">${g.value.label}</span>` : html`<span class="muted">—</span>`}</td>
      </tr>`,
    );
  const first = w.games.slice(0, 25);
  const rest = w.games.slice(25);
  setHTML(
    $("[data-wish-body]"),
    html`<div class="table-wrap"><table class="table">
      <thead><tr><th scope="col">Game</th><th scope="col" class="hide-sm">Reviews</th><th scope="col" class="num">Price</th><th scope="col" class="num hide-sm">Value</th></tr></thead>
      <tbody>${rows(first)}</tbody>
      <tbody data-wish-rest hidden>${rows(rest)}</tbody>
    </table></div>
    ${rest.length ? html`<button class="btn btn--sm" type="button" data-wish-more>Show all ${w.count}</button>` : ""}
    ${region
      ? html`<p class="small muted notes">Prices are from the ${regionName(region)} Steam store. Value scores use US prices; a game's own page scores it at your price.</p>`
      : ""}`,
  );
}

function loadWishlist(steamid) {
  const run = ++wishRun;
  wishFor = steamid;
  api(`/api/me/${steamid}/wishlist?region=${encodeURIComponent(region || "US")}`)
    .then((w) => run === wishRun && renderWishlist(w))
    .catch((err) => {
      if (run !== wishRun) return;
      $("[data-me-wishlist]").hidden = false;
      setHTML($("[data-wish-body]"), html`<p class="text-2">${err.message}</p>`);
    });
}

// Picking another region in the nav reloads the wishlist in its prices
// (once the picker settles: arrowing through it fires a change per key).
watchRegion(
  debounce((code) => {
    if (asRegion(code) === region) return;
    region = asRegion(code);
    if (wishFor) loadWishlist(wishFor);
  }, 250),
);

document.addEventListener("click", (e) => {
  const more = e.target.closest("[data-wish-more]");
  if (!more) return;
  $("[data-wish-rest]").hidden = false;
  more.remove();
});

/* ── Library ───────────────────────────────────────────── */

let backlog = [];

function renderLibrary(lib) {
  const section = $("[data-me-library]");
  section.hidden = false;
  const top = lib.most_played[0]?.hours || 1;
  setHTML(
    $("[data-lib-body]"),
    html`<dl class="stat-tiles">
        <div><dt>Games</dt><dd class="num">${fmtInt(lib.count)}</dd></div>
        <div><dt>Hours played</dt><dd class="num">${fmtInt(lib.hours)}</dd></div>
        <div><dt>Never played</dt><dd class="num">${fmtInt(lib.never_played)}</dd></div>
        <div><dt>Store value today</dt><dd class="num">${fmtUSD(lib.current_value_usd)}</dd></div>
      </dl>
      <p class="tiny muted">Store value is what your paid games cost on the US store today (${fmtInt(lib.priced_count)} games with a price), not what you paid.</p>

      <div class="lib-grid">
        <div class="panel">
          <div class="panel__head"><h3>Most played</h3></div>
          <ol class="bars">${lib.most_played.map(
            (g) => html`<li><a href="/game/${g.appid}">${g.name}</a>
              <span class="bars__bar"><span style="width: ${Math.max(2, (g.hours / top) * 100)}%"></span></span>
              <span class="num small">${fmtInt(g.hours)} h</span></li>`,
          )}</ol>
        </div>
        <div class="panel">
          <div class="panel__head"><h3>Your backlog</h3><span class="small muted">${fmtInt(lib.backlog.length)} unplayed</span></div>
          <div class="panel__body">
            ${lib.backlog.length
              ? html`<p class="text-2 small">Can't decide? We'll pick an unplayed game, favouring the best-reviewed ones.</p>
                <button class="btn btn--primary btn--sm" type="button" data-backlog-pick>${icon("dice", "icon--sm")}Pick from my backlog</button>
                <div class="backlog-pick" data-backlog-result></div>`
              : html`<p class="text-2">Nothing unplayed. Impressive.</p>`}
          </div>
        </div>
      </div>

      ${lib.suggestions.length
        ? html`<div class="section-head" style="margin-top: 32px"><h3>Because you play…</h3></div>
          <div class="card-grid card-grid--6">${lib.suggestions.map(
            (g) => html`<a class="gcard" href="/game/${g.appid}">
              <span class="gcard__art">${artImg(g.appid, g.header, "header")}</span>
              <span class="gcard__title">${g.name}</span>
              <span class="gcard__meta">${g.because}</span></a>`,
          )}</div>`
        : ""}`,
  );
  backlog = lib.backlog;
}

document.addEventListener("click", (e) => {
  if (!e.target.closest("[data-backlog-pick]") || !backlog.length) return;
  const weights = backlog.map((g) => ((g.review_pct ?? 60) / 100) ** 3);
  let r = Math.random() * weights.reduce((a, b) => a + b, 0);
  let pick = backlog[backlog.length - 1];
  for (let i = 0; i < backlog.length; i++) {
    r -= weights[i];
    if (r <= 0) {
      pick = backlog[i];
      break;
    }
  }
  setHTML(
    $("[data-backlog-result]"),
    html`<a class="grow" href="/game/${pick.appid}">
      <span class="grow__art">${artImg(pick.appid, pick.header, "header")}</span>
      <span><span class="grow__title">${pick.name}</span>
      <span class="grow__meta">${[pick.review_pct != null ? `${pick.review_pct}% positive` : null, pick.hours ? `about ${Math.round(pick.hours)} h` : null].filter(Boolean).join(" · ")}</span></span></a>`,
  );
});

/* ── Look up ───────────────────────────────────────────── */

async function lookUp(q, { remember = false } = {}) {
  status("Looking up your profile…");
  let profile;
  try {
    profile = await api(`/api/me/resolve?q=${encodeURIComponent(q)}`);
  } catch (err) {
    status(err.message);
    return;
  }
  if (remember) store.write(KEYS.steam, profile.steamid);
  history.replaceState(null, "", `${location.pathname}?id=${profile.steamid}`);
  renderProfile(profile);
  status("");

  region = asRegion(await detectRegion());
  loadWishlist(profile.steamid);

  if (page.library_available) {
    $("[data-me-library]").hidden = false;
    setHTML($("[data-lib-body]"), html`<p class="small muted">Loading your library…</p>`);
    api(`/api/me/${profile.steamid}/library`)
      .then(renderLibrary)
      .catch((err) => {
        const help = err.reason === "private"
          ? " In Steam, open your profile, choose Edit profile, then Privacy settings, and set Game details to Public."
          : "";
        setHTML($("[data-lib-body]"), html`<p class="notice">${icon("info")}${err.message}${help}</p>`);
      });
  } else {
    $("[data-me-library]").hidden = false;
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = form.q.value.trim();
  if (!q) {
    toast("Enter your profile link or SteamID first.");
    return;
  }
  lookUp(q, { remember: form.remember.checked });
});

const fromUrl = new URLSearchParams(location.search).get("id");
const saved = store.read(KEYS.steam, null);
if (fromUrl || saved) {
  form.q.value = fromUrl || saved;
  form.remember.checked = Boolean(saved);
  lookUp(fromUrl || saved);
}

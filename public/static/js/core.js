/* Shared helpers for every page: escaping, fetching, storage, toasts, theme. */

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/* ── Safe HTML ─────────────────────────────────────────────
   html`<p>${name}</p>` escapes every interpolated value. Wrap trusted markup
   in raw() to insert it as-is; arrays are joined. Use setHTML() to render. */

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);

class Raw {
  constructor(s) {
    this.s = s;
  }
  toString() {
    return this.s;
  }
}
export const raw = (s) => new Raw(String(s ?? ""));

function render(v) {
  if (v == null || v === false) return "";
  if (v instanceof Raw) return v.s;
  if (Array.isArray(v)) return v.map(render).join("");
  return escapeHtml(v);
}

export function html(strings, ...values) {
  let out = strings[0];
  values.forEach((v, i) => {
    out += render(v) + strings[i + 1];
  });
  return new Raw(out);
}

export function setHTML(el, fragment) {
  if (el) el.innerHTML = render(fragment);
}

/** Only http(s) and same-site URLs; anything else becomes "#". */
export function safeUrl(u) {
  const s = String(u ?? "");
  return /^(https?:\/\/|\/(?!\/))/i.test(s) ? s : "#";
}

export function icon(name, cls = "") {
  return raw(`<svg class="icon ${escapeHtml(cls)}" aria-hidden="true" focusable="false"><use href="#i-${escapeHtml(name)}"></use></svg>`);
}

/* ── Fetching ──────────────────────────────────────────── */

const responseCache = new Map();

export async function getJSON(url, { ttl = 60_000, signal } = {}) {
  const hit = responseCache.get(url);
  if (hit && Date.now() - hit.t < ttl) return hit.v;
  const r = await fetch(url, { signal, headers: { Accept: "application/json" } });
  if (!r.ok) {
    const err = new Error(`Request failed (${r.status})`);
    err.status = r.status;
    throw err;
  }
  const v = await r.json();
  responseCache.set(url, { v, t: Date.now() });
  return v;
}

export function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

/* ── Formatting ────────────────────────────────────────── */

const nf = new Intl.NumberFormat("en-US");
export const fmtInt = (n) => (n == null ? "—" : nf.format(Math.round(n)));
export const fmtUSD = (n) => (n == null ? "—" : `$${Number(n).toFixed(2)}`);
export const fmtCompact = (n) =>
  n == null ? "—" : new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n);
export const fmtPct = (n, digits = 0) => (n == null ? "—" : `${Number(n).toFixed(digits)}%`);

export function reviewTone(pct) {
  if (pct == null) return "none";
  if (pct >= 70) return "pos";
  if (pct >= 40) return "mixed";
  return "neg";
}

/* ── Steam art ─────────────────────────────────────────── */

const ART_FILES = {
  header: "header.jpg",
  capsule: "capsule_231x87.jpg",
  hero: "library_hero.jpg",
  library: "library_600x900.jpg",
};

/** Legacy CDN path; base.html swaps in /api/art/<id> if it 404s (newer games). */
export function artUrl(appid, kind = "header") {
  return `https://cdn.cloudflare.steamstatic.com/steam/apps/${encodeURIComponent(appid)}/${ART_FILES[kind] || ART_FILES.header}`;
}

export function artImg(appid, src, kind = "header", alt = "") {
  return html`<img class="art" src="${safeUrl(src || artUrl(appid, kind))}" alt="${alt}" data-appid="${appid}" data-art="${kind}" loading="lazy" decoding="async">`;
}

/* ── Local storage ─────────────────────────────────────── */

export const KEYS = {
  recent: "playnexus_recent",
  favorites: "playnexus_favorites",
  compare: "playnexus_compare",
  theme: "playnexus_theme",
  region: "playnexus_region",
  hidden: "playnexus_not_interested",
  steam: "playnexus_steam_profile",
};

function read(key, fallback) {
  try {
    const v = JSON.parse(localStorage.getItem(key));
    return v ?? fallback;
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try {
    if (value == null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage full or blocked: features degrade quietly */
  }
}

export const store = { read, write };

/** Keep only well-formed {appid, name, cover} entries. */
function cleanGames(list) {
  if (!Array.isArray(list)) return [];
  const seen = new Set();
  const out = [];
  for (const g of list) {
    const appid = Number(g && g.appid);
    if (!Number.isInteger(appid) || appid <= 0 || seen.has(appid)) continue;
    seen.add(appid);
    out.push({ appid, name: String(g.name || `App ${appid}`), cover: g.cover ? String(g.cover) : null });
  }
  return out;
}

/** Move data saved under the old "steamdb_*" keys, dropping bad entries. */
export function migrateStorage() {
  const legacy = { steamdb_recent: KEYS.recent, steamdb_favorites: KEYS.favorites, steamdb_compare: KEYS.compare };
  for (const [oldKey, newKey] of Object.entries(legacy)) {
    let old;
    try {
      old = localStorage.getItem(oldKey);
    } catch {
      return;
    }
    if (old == null) continue;
    if (read(newKey, null) == null) write(newKey, cleanGames(read(oldKey, [])));
    write(oldKey, null);
  }
}

function gameList(key, cap) {
  return {
    all: () => cleanGames(read(key, [])),
    has(appid) {
      return this.all().some((g) => g.appid === Number(appid));
    },
    save(list) {
      write(key, cleanGames(list).slice(0, cap));
      window.dispatchEvent(new CustomEvent("pn:storage", { detail: key }));
    },
    remove(appid) {
      this.save(this.all().filter((g) => g.appid !== Number(appid)));
    },
    clear() {
      this.save([]);
    },
  };
}

export const recent = {
  ...gameList(KEYS.recent, 20),
  add(game) {
    this.save([game, ...this.all().filter((g) => g.appid !== Number(game.appid))]);
  },
};

export const favorites = {
  ...gameList(KEYS.favorites, 100),
  toggle(game) {
    if (this.has(game.appid)) {
      this.remove(game.appid);
      return false;
    }
    this.save([game, ...this.all()]);
    return true;
  },
};

export const MAX_COMPARE = 4;

export const compareList = {
  ...gameList(KEYS.compare, MAX_COMPARE),
  /** Returns "added", "exists" or "full". */
  add(game) {
    const list = this.all();
    if (list.some((g) => g.appid === Number(game.appid))) return "exists";
    if (list.length >= MAX_COMPARE) return "full";
    this.save([...list, game]);
    return "added";
  },
};

/* ── Price region ──────────────────────────────────────── */

/** The store regions from config.py ({cc, code, name}), embedded by base.html. */
export const REGIONS = pageData("pn-regions") || [];
const REGION_CODES = new Set(REGIONS.map((r) => r.code));
const EUROZONE = new Set("AT BE HR CY EE FI FR DE GR IE IT LV LT LU MT NL PT SK SI ES".split(" "));

export const regionName = (code) => REGIONS.find((r) => r.code === code)?.name || code;

/** Our region code for an ISO country code, if we price it. */
export function regionFromCountry(cc) {
  if (!cc) return null;
  cc = cc.toUpperCase();
  if (EUROZONE.has(cc)) return "EU";
  return REGION_CODES.has(cc) && cc !== "EU" ? cc : null;
}

let detected;

async function detectCountry() {
  try {
    const cached = sessionStorage.getItem("playnexus_whereami");
    const where = cached ? JSON.parse(cached) : await getJSON("/api/whereami");
    if (!cached) sessionStorage.setItem("playnexus_whereami", JSON.stringify(where));
    if (where.region && REGION_CODES.has(where.region)) return where.region;
  } catch {
    /* fall through to the browser language */
  }
  const m = (navigator.language || "").match(/-([A-Za-z]{2})$/);
  return regionFromCountry(m && m[1]);
}

/** The visitor's region code: the one they picked, else their country's
    (Vercel's IP header), else their browser language's; null if none fits. */
export async function detectRegion() {
  const saved = store.read(KEYS.region, null);
  if (saved && REGION_CODES.has(saved)) return saved;
  detected ||= detectCountry();
  return detected;
}

/** Remember the visitor's pick (null forgets it) and tell the page. */
export function setRegion(code) {
  store.write(KEYS.region, code || null);
  window.dispatchEvent(new CustomEvent("pn:region", { detail: code || null }));
}

/** Call fn(code) with the visitor's region, then again whenever they pick
    one, here or in another tab. */
export function watchRegion(fn) {
  let picked = false;
  const changed = (code) => {
    picked = true;
    fn(code);
  };
  window.addEventListener("pn:region", (e) => changed(e.detail));
  window.addEventListener("storage", (e) => {
    if (e.key === KEYS.region) changed(store.read(KEYS.region, null));
  });
  detectRegion().then((code) => picked || fn(code));
}

/* ── Toasts ────────────────────────────────────────────── */

export function toast(message) {
  const box = document.getElementById("toasts");
  if (!box) return;
  const el = document.createElement("div");
  el.className = "toast";
  el.setAttribute("role", "status");
  el.textContent = message;
  box.append(el);
  requestAnimationFrame(() => el.classList.add("is-in"));
  setTimeout(() => {
    el.classList.remove("is-in");
    setTimeout(() => el.remove(), 250);
  }, 2600);
}

/* ── Theme ─────────────────────────────────────────────── */

export function initTheme() {
  const root = document.documentElement;
  const sync = () => {
    const next = root.dataset.theme === "light" ? "dark" : "light";
    $$("[data-theme-toggle]").forEach((b) => b.setAttribute("aria-label", `Switch to ${next} theme`));
  };
  $$("[data-theme-toggle]").forEach((btn) =>
    btn.addEventListener("click", () => {
      root.dataset.theme = root.dataset.theme === "light" ? "dark" : "light";
      try {
        // Stored as a bare string: base.html's inline script reads it before JSON is handy.
        localStorage.setItem(KEYS.theme, root.dataset.theme);
      } catch {
        /* ignore */
      }
      sync();
    }),
  );
  sync();
}

/** Server data embedded as <script type="application/json" id="...">. */
export function pageData(id = "page-data") {
  const el = document.getElementById(id);
  if (!el) return null;
  try {
    return JSON.parse(el.textContent);
  } catch {
    return null;
  }
}

export const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

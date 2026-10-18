/* Game search: an accessible combobox used inline (home, compare) and in the
   Ctrl/⌘K command palette. */

import { $, artImg, debounce, escapeHtml, getJSON, html, icon, raw, recent, setHTML } from "pn/core";

const PAGES = [
  { label: "Charts", href: "/charts", icon: "chart", hint: "Playing now, top sellers, new" },
  { label: "Deals", href: "/deals", icon: "percent", hint: "Steam sales ranked by value" },
  { label: "Discover", href: "/discover", icon: "compass", hint: "Find games by vibe" },
  { label: "Regional prices", href: "/regions", icon: "globe", hint: "Where Steam is cheapest" },
  { label: "Compare", href: "/compare", icon: "columns", hint: "Up to four games side by side" },
  { label: "My Steam", href: "/me", icon: "library", hint: "Wishlist sales and your backlog" },
];

let uid = 0;

/** Wrap each case-insensitive occurrence of `query`'s words in <mark>, escaping everything. */
function highlight(name, query) {
  const words = query.trim().split(/\s+/).filter(Boolean).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!words.length) return escapeHtml(name);
  const re = new RegExp(`(${words.join("|")})`, "ig");
  return name
    .split(re)
    .map((part, i) => (i % 2 ? `<mark>${escapeHtml(part)}</mark>` : escapeHtml(part)))
    .join("");
}

export class Combobox {
  /**
   * @param {HTMLElement} root  element containing .combo__input, .listbox, .combo__clear
   * @param {object} opts
   * @param {(item) => void} opts.onChoose  called with {type: "game"|"link", ...}
   * @param {() => Array} [opts.emptyItems] items to show when the query is empty
   * @param {boolean} [opts.alwaysOpen] keep the list visible (palette)
   */
  constructor(root, { onChoose, emptyItems = null, alwaysOpen = false } = {}) {
    this.root = root;
    this.input = $(".combo__input", root);
    this.list = $(".listbox", root);
    this.clearBtn = $(".combo__clear", root);
    this.onChoose = onChoose;
    this.emptyItems = emptyItems;
    this.alwaysOpen = alwaysOpen;
    this.items = [];
    this.active = -1;
    this.query = "";
    this.abort = null;
    this.idBase = `opt${++uid}`;

    this.input.setAttribute("aria-expanded", String(alwaysOpen));
    this.input.addEventListener("input", () => this.onInput());
    this.input.addEventListener("keydown", (e) => this.onKey(e));
    this.input.addEventListener("focus", () => {
      if (!this.input.value.trim() && this.emptyItems) this.showEmpty();
    });
    this.list.addEventListener("mousedown", (e) => e.preventDefault()); // keep focus in the input
    this.list.addEventListener("click", (e) => {
      const li = e.target.closest("[data-index]");
      if (li) this.choose(Number(li.dataset.index));
    });
    this.list.addEventListener("mousemove", (e) => {
      const li = e.target.closest("[data-index]");
      if (li && Number(li.dataset.index) !== this.active) this.setActive(Number(li.dataset.index), false);
    });
    if (this.clearBtn) {
      this.clearBtn.addEventListener("click", () => {
        this.reset();
        this.input.focus();
      });
    }
    if (!alwaysOpen) {
      document.addEventListener("click", (e) => {
        if (!root.contains(e.target)) this.close();
      });
    }
    this.search = debounce((q) => this.fetch(q), 140);
  }

  onInput() {
    const q = this.input.value.trim();
    if (this.clearBtn) this.clearBtn.hidden = !this.input.value;
    if (!q) {
      if (this.abort) this.abort.abort();
      return this.emptyItems ? this.showEmpty() : this.close();
    }
    this.search(q);
  }

  async fetch(q) {
    if (this.abort) this.abort.abort();
    this.abort = new AbortController();
    this.query = q;
    try {
      const results = await getJSON(`/api/search?q=${encodeURIComponent(q)}&limit=8`, {
        ttl: 300_000,
        signal: this.abort.signal,
      });
      if (q !== this.input.value.trim()) return; // a newer query is in flight
      this.render(results.map((r) => ({ type: "game", ...r })), q);
    } catch (err) {
      if (err.name !== "AbortError") this.hint("Search is unavailable right now. Try again in a moment.");
    }
  }

  showEmpty() {
    this.query = "";
    this.render(this.emptyItems(), "");
  }

  render(items, q) {
    this.items = items;
    this.active = -1;
    if (!items.length) {
      this.hint(q ? `No games match “${q}”.` : "Start typing a game's name.");
      return;
    }
    let lastGroup = null;
    const rows = items.map((it, i) => {
      const id = `${this.idBase}-${i}`;
      const group =
        it.group && it.group !== lastGroup ? html`<li class="listbox__group eyebrow" role="presentation">${it.group}</li>` : "";
      lastGroup = it.group || lastGroup;
      if (it.type === "link") {
        return html`${group}<li class="option option--link" role="option" id="${id}" data-index="${i}" aria-selected="false">
          ${icon(it.icon)}<span class="option__name">${it.label}</span><span class="option__aside">${it.hint || ""}</span></li>`;
      }
      return html`${group}<li class="option" role="option" id="${id}" data-index="${i}" aria-selected="false">
        <span class="option__art">${artImg(it.appid, it.image, "capsule")}</span>
        <span class="option__name">${raw(highlight(it.name, q))}</span>
        <span class="option__aside num">${it.appid}</span></li>`;
    });
    setHTML(this.list, rows);
    this.open();
    if (q) this.setActive(0, false);
  }

  hint(text) {
    this.items = [];
    this.active = -1;
    setHTML(this.list, html`<li class="listbox__hint" role="presentation">${text}</li>`);
    this.open();
  }

  open() {
    this.list.hidden = false;
    this.input.setAttribute("aria-expanded", "true");
  }

  close() {
    if (this.alwaysOpen) return;
    this.list.hidden = true;
    this.input.setAttribute("aria-expanded", "false");
    this.input.removeAttribute("aria-activedescendant");
    this.active = -1;
  }

  reset() {
    this.input.value = "";
    if (this.clearBtn) this.clearBtn.hidden = true;
    if (this.emptyItems) this.showEmpty();
    else this.close();
  }

  setActive(i, scroll = true) {
    const opts = this.list.querySelectorAll("[role=option]");
    if (!opts.length) return;
    this.active = (i + opts.length) % opts.length;
    opts.forEach((o, n) => o.setAttribute("aria-selected", String(n === this.active)));
    const el = opts[this.active];
    this.input.setAttribute("aria-activedescendant", el.id);
    if (scroll) el.scrollIntoView({ block: "nearest" });
  }

  onKey(e) {
    const listOpen = !this.list.hidden && this.items.length;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!listOpen && this.input.value.trim()) return this.onInput();
      this.setActive(this.active + 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      this.setActive(this.active - 1);
    } else if (e.key === "Enter") {
      if (listOpen) {
        e.preventDefault();
        this.choose(this.active >= 0 ? this.active : 0);
      }
    } else if (e.key === "Escape") {
      if (this.alwaysOpen) return; // the dialog handles it
      if (!this.list.hidden) {
        e.preventDefault();
        this.close();
      } else if (this.input.value) {
        this.reset();
      }
    }
  }

  choose(i) {
    const item = this.items[i];
    if (!item) return;
    this.onChoose(item);
  }
}

export function goToGame(appid) {
  window.location.href = `/game/${encodeURIComponent(appid)}`;
}

function paletteEmptyItems() {
  const items = PAGES.map((p) => ({ type: "link", group: "Jump to", ...p }));
  for (const g of recent.all().slice(0, 5)) {
    items.push({ type: "game", group: "Recently viewed", appid: g.appid, name: g.name, image: null });
  }
  return items;
}

let palette = null;

export function openPalette() {
  const dialog = document.getElementById("palette");
  if (!dialog) return;
  if (!palette) {
    palette = new Combobox(dialog, {
      alwaysOpen: true,
      emptyItems: paletteEmptyItems,
      onChoose: (item) => {
        if (item.type === "link") window.location.href = item.href;
        else goToGame(item.appid);
      },
    });
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) dialog.close(); // backdrop click
    });
  }
  if (!dialog.open) dialog.showModal();
  palette.reset();
  palette.input.focus();
}

export function initSearchShortcuts() {
  document.querySelectorAll("[data-open-palette]").forEach((b) => b.addEventListener("click", openPalette));
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
    if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      openPalette();
    } else if (e.key === "/" && !typing) {
      const primary = document.querySelector("[data-primary-search] .combo__input");
      e.preventDefault();
      if (primary) primary.focus();
      else openPalette();
    }
  });
}

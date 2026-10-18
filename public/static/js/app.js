/* ══════════════════════════════════════════════════════════
   SteamDB Clone — Shared JavaScript
   ══════════════════════════════════════════════════════════ */

// ── Utilities ────────────────────────────────────────────

function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, m =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m])
  );
}

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

// ── In-memory fetch cache ────────────────────────────────
// Prevents duplicate network calls when the same API is hit
// multiple times in one session (e.g. compare page re-renders).

const _fetchCache = new Map();
const CACHE_TTL_MS = 90_000; // 90 seconds

function cachedFetch(url) {
  const now = Date.now();
  const hit = _fetchCache.get(url);
  if (hit && now - hit.ts < CACHE_TTL_MS) return Promise.resolve(hit.data);

  return fetch(url)
    .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(data => { _fetchCache.set(url, { data, ts: now }); return data; });
}

// ── Toast Notifications ──────────────────────────────────

const toastContainer = (() => {
  let el = document.querySelector('.toast-container');
  if (!el) {
    el = document.createElement('div');
    el.className = 'toast-container';
    document.body.appendChild(el);
  }
  return el;
})();

function showToast(message, duration = 2500) {
  const t = document.createElement('div');
  t.className = 'toast';
  t.textContent = message;
  toastContainer.appendChild(t);
  requestAnimationFrame(() => t.classList.add('show'));
  setTimeout(() => {
    t.classList.add('fade-out');
    setTimeout(() => t.remove(), 300);
  }, duration);
}

// ── localStorage helpers ─────────────────────────────────

const STORAGE_KEYS = {
  RECENT: 'steamdb_recent',
  FAVORITES: 'steamdb_favorites',
  COMPARE: 'steamdb_compare',
};

function getStorageList(key) {
  try {
    return JSON.parse(localStorage.getItem(key)) || [];
  } catch {
    return [];
  }
}

function setStorageList(key, list) {
  localStorage.setItem(key, JSON.stringify(list));
}

// ── Recently Viewed ──────────────────────────────────────

function addRecentlyViewed(appid, name, cover) {
  const id = String(appid);
  let list = getStorageList(STORAGE_KEYS.RECENT);
  list = list.filter(g => String(g.appid) !== id);
  list.unshift({ appid: id, name, cover });
  if (list.length > 20) list = list.slice(0, 20);
  setStorageList(STORAGE_KEYS.RECENT, list);
}

function getRecentlyViewed() {
  return getStorageList(STORAGE_KEYS.RECENT);
}

// ── Favorites ────────────────────────────────────────────

function toggleFavorite(appid, name, cover) {
  const id = String(appid);
  let list = getStorageList(STORAGE_KEYS.FAVORITES);
  const idx = list.findIndex(g => String(g.appid) === id);
  if (idx >= 0) {
    list.splice(idx, 1);
    setStorageList(STORAGE_KEYS.FAVORITES, list);
    showToast('Removed from favorites');
    return false;
  } else {
    list.unshift({ appid: id, name, cover });
    if (list.length > 50) list = list.slice(0, 50);
    setStorageList(STORAGE_KEYS.FAVORITES, list);
    showToast('Added to favorites ❤️');
    return true;
  }
}

function isFavorite(appid) {
  const id = String(appid);
  return getStorageList(STORAGE_KEYS.FAVORITES).some(g => String(g.appid) === id);
}

function getFavorites() {
  return getStorageList(STORAGE_KEYS.FAVORITES);
}

// ── Compare List ─────────────────────────────────────────

function addToCompare(appid, name) {
  const id = String(appid);
  let list = getStorageList(STORAGE_KEYS.COMPARE);
  if (list.some(g => String(g.appid) === id)) {
    showToast('Already in compare list');
    return;
  }
  if (list.length >= 4) {
    showToast('Max 4 games to compare');
    return;
  }
  list.push({ appid: id, name });
  setStorageList(STORAGE_KEYS.COMPARE, list);
  showToast(`Added "${name}" to compare`);
  updateCompareCount();
}

function removeFromCompare(appid) {
  const id = String(appid);
  let list = getStorageList(STORAGE_KEYS.COMPARE);
  list = list.filter(g => String(g.appid) !== id);
  setStorageList(STORAGE_KEYS.COMPARE, list);
  updateCompareCount();
}

function getCompareList() {
  return getStorageList(STORAGE_KEYS.COMPARE);
}

function clearCompare() {
  setStorageList(STORAGE_KEYS.COMPARE, []);
  updateCompareCount();
}

function updateCompareCount() {
  const badge = document.getElementById('compare-count');
  if (!badge) return;
  const count = getCompareList().length;
  badge.textContent = count;
  badge.style.display = count > 0 ? 'inline-flex' : 'none';
}

// ── Skeleton Helpers ─────────────────────────────────────

function skeletonCards(count, className = 'skeleton-card') {
  return Array(count).fill(`<div class="skeleton ${className}"></div>`).join('');
}

function skeletonRows(count) {
  return Array(count).fill(`
    <div style="padding:10px 12px;display:flex;gap:12px;align-items:center;">
      <div class="skeleton" style="width:28px;height:28px;border-radius:50%;"></div>
      <div class="skeleton skeleton-text w-40" style="flex:1;"></div>
      <div class="skeleton skeleton-text w-60" style="width:80px;"></div>
    </div>
  `).join('');
}

// ── Autocomplete (used by index.html & base nav potentially) ─

function initSearchAutocomplete(inputEl, hiddenEl, listEl, clearEl, formEl) {
  if (!inputEl) return;

  let activeIndex = -1;
  let currentItems = [];
  let inflightAbort = null;

  inputEl.addEventListener('input', debounce(() => {
    hiddenEl.value = '';
    activeIndex = -1;
    clearEl.style.display = inputEl.value ? 'block' : 'none';
    const q = inputEl.value.trim();
    if (!q) { hideDropdown(); return; }
    fetchAutocomplete(q);
  }, 180));

  inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (inputEl.value) { inputEl.value = ''; hiddenEl.value = ''; }
      hideDropdown(); clearEl.style.display = 'none'; return;
    }
    if (listEl.style.display === 'none') {
      if (e.key === 'Enter' && inputEl.value.trim()) return;
      return;
    }
    if (e.key === 'ArrowDown') { e.preventDefault(); moveActive(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); moveActive(-1); }
    else if (e.key === 'Enter') {
      if (activeIndex >= 0 && currentItems[activeIndex]) {
        e.preventDefault(); chooseItem(currentItems[activeIndex]);
      } else if (currentItems.length) {
        e.preventDefault(); chooseItem(currentItems[0]);
      }
    }
  });

  document.addEventListener('click', (e) => {
    if (!inputEl.closest('.search-box').contains(e.target)) hideDropdown();
  });

  clearEl.addEventListener('click', () => {
    inputEl.value = ''; hiddenEl.value = ''; hideDropdown(); inputEl.focus();
    clearEl.style.display = 'none';
  });

  function fetchAutocomplete(q) {
    if (inflightAbort) inflightAbort.abort();
    inflightAbort = new AbortController();
    showHint('Searching…');
    fetch(`/api/search?q=${encodeURIComponent(q)}`, { signal: inflightAbort.signal })
      .then(r => r.json())
      .then(items => { currentItems = items || []; renderList(q, currentItems); })
      .catch(err => { if (err.name !== 'AbortError') showHint('Something went wrong'); })
      .finally(() => { inflightAbort = null; });
  }

  function renderList(query, items) {
    if (!items.length) { showHint('No results'); return; }
    // Build all DOM in a fragment then insert once (avoids reflows)
    const frag = document.createDocumentFragment();
    items.forEach((it, idx) => {
      const div = document.createElement('div');
      div.className = 'dropdown-item';
      div.dataset.index = idx;
      div.innerHTML = `
        <img src="https://cdn.cloudflare.steamstatic.com/steam/apps/${it.appid}/capsule_184x69.jpg"
             class="thumb" loading="lazy" onerror="this.style.display='none';">
        <div class="text-block">
          <strong>${highlight(it.name, query)}</strong>
          <span class="appid-tag">#${it.appid}</span>
        </div>`;
      div.addEventListener('click', () => chooseItem(it));
      frag.appendChild(div);
    });
    listEl.innerHTML = '';
    listEl.appendChild(frag);
    listEl.style.display = 'block';
    activeIndex = -1;
    updateActive();
  }

  function chooseItem(item) {
    inputEl.value = item.name;
    hiddenEl.value = item.appid;
    hideDropdown();
    if (formEl) formEl.submit();
  }

  function hideDropdown() {
    listEl.style.display = 'none'; listEl.innerHTML = '';
    currentItems = []; activeIndex = -1;
  }

  function moveActive(delta) {
    const items = listEl.querySelectorAll('.dropdown-item');
    if (!items.length) return;
    activeIndex += delta;
    if (activeIndex < 0) activeIndex = items.length - 1;
    if (activeIndex >= items.length) activeIndex = 0;
    updateActive();
    items[activeIndex]?.scrollIntoView({ block: 'nearest' });
  }

  function updateActive() {
    listEl.querySelectorAll('.dropdown-item').forEach((it, i) =>
      it.classList.toggle('active', i === activeIndex)
    );
  }

  function showHint(text) {
    listEl.innerHTML = `<div class="hint">${escapeHtml(text)}</div>`;
    listEl.style.display = 'block';
  }

  function highlight(name, query) {
    const q = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return name.replace(new RegExp(q, 'ig'), m => `<mark>${escapeHtml(m)}</mark>`);
  }
}

// ── Render home sections ─────────────────────────────────

function renderHomeSection(containerId, items, emptyMsg) {
  const el = document.getElementById(containerId);
  if (!el) return;
  if (!items.length) {
    el.closest('.home-section').style.display = 'none';
    return;
  }
  el.closest('.home-section').style.display = 'block';
  el.innerHTML = items.map(g => `
    <a href="/game/${g.appid}" class="home-card" title="${escapeHtml(g.name)}">
      <img src="${g.cover || `https://cdn.cloudflare.steamstatic.com/steam/apps/${g.appid}/header.jpg`}"
           loading="lazy"
           onerror="this.src='https://cdn.cloudflare.steamstatic.com/steam/apps/${g.appid}/header.jpg'"
           alt="${escapeHtml(g.name)}">
      <div class="home-card-name">${escapeHtml(g.name)}</div>
    </a>
  `).join('');
}

// ── Init on DOMContentLoaded ─────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  updateCompareCount();

  // Hotkey: "/" to focus search
  window.addEventListener('keydown', (e) => {
    if (e.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
      const input = document.getElementById('game_input');
      if (input) { e.preventDefault(); input.focus(); }
    }
  });
});

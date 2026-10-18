import { $, artImg, favorites, html, recent, setHTML } from "pn/core";
import { Combobox, goToGame } from "pn/search";

const form = $("[data-primary-search]");
if (form) {
  new Combobox(form, { onChoose: (item) => goToGame(item.appid) });
  form.addEventListener("submit", (e) => {
    // With a result highlighted, the combobox already navigated; a bare
    // App ID goes straight to its page; anything else falls back to the server.
    const q = $(".combo__input", form).value.trim();
    if (/^\d+$/.test(q)) {
      e.preventDefault();
      goToGame(q);
    }
  });
}

function card(g) {
  return html`<a class="gcard" href="/game/${g.appid}">
    <span class="gcard__art">${artImg(g.appid, g.cover, "header")}</span>
    <span class="gcard__title">${g.name}</span></a>`;
}

function renderList(sectionId, listId, games) {
  const section = document.getElementById(sectionId);
  if (!section) return;
  section.hidden = games.length === 0;
  setHTML(document.getElementById(listId), games.map(card));
}

function renderSaved() {
  renderList("recent-section", "recent-list", recent.all());
  renderList("fav-section", "fav-list", favorites.all());
}

renderSaved();
$("[data-clear-recent]")?.addEventListener("click", () => {
  recent.clear();
  renderSaved();
});
window.addEventListener("pageshow", renderSaved); // back/forward cache

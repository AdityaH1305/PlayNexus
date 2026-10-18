/* Runs on every page: theme, navigation, search shortcuts, compare count. */

import { $, $$, compareList, initTheme, isMac, migrateStorage } from "pn/core";
import { initSearchShortcuts } from "pn/search";

migrateStorage();
initTheme();
initSearchShortcuts();

if (isMac) $$("[data-shortcut-hint]").forEach((k) => (k.textContent = "⌘K"));

const toggle = $(".nav-toggle");
const sheet = $("#nav-sheet");
if (toggle && sheet) {
  toggle.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(open));
    sheet.hidden = !open;
  });
}

function updateCompareCount() {
  const n = compareList.all().length;
  $$("[data-compare-count]").forEach((el) => {
    el.textContent = n;
    el.hidden = n === 0;
  });
}
updateCompareCount();
window.addEventListener("pn:storage", updateCompareCount);
window.addEventListener("storage", updateCompareCount); // other tabs

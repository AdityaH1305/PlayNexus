/* Runs on every page: theme, navigation, search shortcuts, compare count,
   price region. */

import { $, $$, compareList, initTheme, isMac, migrateStorage, regionName, setRegion, watchRegion } from "pn/core";
import { initLocalPrices } from "pn/prices";
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

/* Price region: the switch in the nav shares the saved choice with the game
   page's picker. No region means US prices. */
const regionSwitch = $("[data-region-switch]");
if (regionSwitch) {
  const select = $("select", regionSwitch);
  const code = $("[data-region-code]", regionSwitch);
  select.addEventListener("change", () => setRegion(select.value));
  watchRegion((region) => {
    select.value = region || "US";
    code.textContent = select.value;
    select.title = `Prices for ${regionName(select.value)}`;
  });
}
initLocalPrices();

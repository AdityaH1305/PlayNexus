/* Small hand-drawn SVG charts. No chart library: these are simple enough to
   draw directly, and it keeps them in the site's own type and colours. */

import { escapeHtml, fmtInt, fmtUSD } from "pn/core";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY = 86_400_000;

const monthLabel = (t) => {
  const d = new Date(t * 1000);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
};

/** "2026-10-18" → that day's midnight UTC, in milliseconds. */
const dayTime = (s) => {
  const [y, m, d] = String(s).split("-").map(Number);
  return Date.UTC(y, m - 1, d);
};

/** "2026-10-18" → "18 Oct", with the year if it isn't this year (or always, with year: true). */
export function dayLabel(s, { year = false } = {}) {
  const d = new Date(dayTime(s));
  const y = d.getUTCFullYear();
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}${year || y !== new Date().getUTCFullYear() ? ` ${y}` : ""}`;
}

/**
 * Diverging monthly bars: positive reviews above the line, negative below,
 * on the same scale (so a quiet negative month really looks small).
 * @param {HTMLElement} el
 * @param {{points: {t:number, up:number, down:number}[]}} data
 */
export function reviewHistory(el, data, { months = 48 } = {}) {
  const pts = (data.points || []).slice(-months);
  if (!pts.length) {
    el.innerHTML = '<p class="small muted">Steam has no review history for this game yet.</p>';
    return;
  }

  const W = 640;
  const H = 190;
  const pad = { l: 4, r: 4, t: 6, b: 24 };
  const maxUp = Math.max(1, ...pts.map((p) => p.up));
  const maxDown = Math.max(0, ...pts.map((p) => p.down));
  const plotH = H - pad.t - pad.b;
  const scale = plotH / (maxUp + maxDown);
  const base = pad.t + maxUp * scale;
  const slot = (W - pad.l - pad.r) / pts.length;
  const bw = Math.max(1, Math.min(slot * 0.7, 22)); // short histories shouldn't turn into slabs

  const parts = [];
  pts.forEach((p, i) => {
    const x = pad.l + i * slot + (slot - bw) / 2;
    parts.push(`<rect class="hover-band" data-band="${i}" x="${pad.l + i * slot}" y="${pad.t}" width="${slot}" height="${plotH}" opacity="0"/>`);
    if (p.up) parts.push(`<rect class="bar-up" x="${x.toFixed(1)}" y="${(base - p.up * scale).toFixed(1)}" width="${bw.toFixed(1)}" height="${(p.up * scale).toFixed(1)}"/>`);
    if (p.down) parts.push(`<rect class="bar-down" x="${x.toFixed(1)}" y="${base.toFixed(1)}" width="${bw.toFixed(1)}" height="${(p.down * scale).toFixed(1)}"/>`);
  });
  parts.push(`<line class="axis" x1="${pad.l}" x2="${W - pad.r}" y1="${base}" y2="${base}"/>`);

  // Year labels on January, or every few months for short histories.
  const every = pts.length <= 18 ? 3 : 0;
  pts.forEach((p, i) => {
    const d = new Date(p.t * 1000);
    const show = every ? i % every === 0 : d.getUTCMonth() === 0;
    if (!show) return;
    const label = every ? `${MONTHS[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}` : d.getUTCFullYear();
    parts.push(`<text class="tick" x="${pad.l + i * slot}" y="${H - 6}">${label}</text>`);
  });
  pts.forEach((p, i) => {
    parts.push(`<rect class="hit" data-i="${i}" x="${pad.l + i * slot}" y="0" width="${slot}" height="${H}"/>`);
  });

  const last = pts[pts.length - 1];
  const summary = `Reviews per month over the last ${pts.length} months. Most recent month, ${monthLabel(last.t)}: ${fmtInt(last.up)} positive and ${fmtInt(last.down)} negative.`;
  el.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${escapeHtml(summary)}">${parts.join("")}</svg>
    <div class="history-tip" hidden></div>
    <div class="history-legend"><span class="up">Positive</span><span class="down">Negative</span></div>`;

  const svg = el.querySelector("svg");
  const tip = el.querySelector(".history-tip");
  let current = null;
  const show = (i) => {
    const p = pts[i];
    if (current != null) svg.querySelector(`[data-band="${current}"]`)?.setAttribute("opacity", "0");
    svg.querySelector(`[data-band="${i}"]`)?.setAttribute("opacity", "1");
    current = i;
    const total = p.up + p.down;
    tip.innerHTML = `<strong>${monthLabel(p.t)}</strong><br>
      <span class="pos">${fmtInt(p.up)}</span> positive · <span class="neg">${fmtInt(p.down)}</span> negative<br>
      <span class="muted">${total ? Math.round((p.up / total) * 100) + "% positive" : "No reviews"}</span>`;
    tip.hidden = false;
    const box = el.getBoundingClientRect();
    const scaleX = box.width / W;
    const x = (pad.l + (i + 0.5) * slot) * scaleX;
    tip.style.left = `${Math.min(Math.max(0, x - 75), box.width - 160)}px`;
    tip.style.top = `-8px`;
    tip.style.transform = "translateY(-100%)";
  };
  svg.addEventListener("mousemove", (e) => {
    const r = e.target.closest?.(".hit");
    if (r) show(Number(r.dataset.i));
  });
  svg.addEventListener("mouseleave", () => {
    tip.hidden = true;
    if (current != null) svg.querySelector(`[data-band="${current}"]`)?.setAttribute("opacity", "0");
    current = null;
  });
}

const cents = (c) => fmtUSD(c / 100);
const centsTick = (c) => (c % 100 ? cents(c) : `$${c / 100}`);

/** Gridline spacing in cents: a round step that gives about three lines. */
function gridStep(max) {
  const steps = [25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 25000, 50000];
  return steps.find((s) => s * 3 >= max) || steps[steps.length - 1];
}

/**
 * US price as a step line: each price holds until the next point (a daily
 * snapshot, or a price change from IsThereAnyDeal). The scale starts at zero,
 * so a half-price sale really halves the line. A dashed line marks the lowest
 * price of the last 90 days, and big Steam sales are shaded behind.
 * @param {HTMLElement} el
 * @param {{points: {day:string, final:number, initial:number, discount:number}[],
 *          low: ?{final:number, full:boolean, since:string},
 *          sales?: {name:string, start:string, end:string}[]}} data  prices in cents
 */
export function priceHistory(el, data) {
  const pts = (data.points || []).map((p) => ({ ...p, t: dayTime(p.day) }));
  if (pts.length < 2) {
    el.innerHTML = "";
    return;
  }

  // Drawn at the width it's shown (up to 640), so labels stay readable on phones.
  const W = Math.round(Math.min(640, Math.max(300, el.clientWidth || 640)));
  const H = 160;
  const pad = { l: 4, r: 4, t: 16, b: 24 };
  const first = pts[0].t;
  const end = pts[pts.length - 1];
  const days = Math.round((end.t - first) / DAY) + 1; // one slot per day, the last one included
  const slot = (W - pad.l - pad.r) / days;
  const xAt = (t) => pad.l + ((t - first) / DAY) * slot;
  const max = Math.max(1, ...pts.map((p) => p.final));
  const step = gridStep(max);
  const top = step * Math.ceil((max * 1.1) / step);
  const plotH = H - pad.t - pad.b;
  const yAt = (c) => pad.t + plotH * (1 - c / top);
  const base = yAt(0);
  const f = (n) => n.toFixed(1);

  const parts = [];
  // Big Steam sales, behind everything else. A sale's last day ends at 10 a.m.
  // Pacific, so the band stops at the start of its end date.
  let bands = 0;
  (data.sales || []).forEach((s) => {
    const x1 = xAt(Math.max(first, dayTime(s.start)));
    const x2 = xAt(Math.min(end.t + DAY, dayTime(s.end)));
    if (x2 - x1 < 0.5) return;
    bands += 1;
    parts.push(`<rect class="sale-band" x="${f(x1)}" y="${pad.t}" width="${f(x2 - x1)}" height="${f(plotH)}"><title>${escapeHtml(s.name)}</title></rect>`);
  });
  for (let v = step; v <= top; v += step) {
    parts.push(`<line class="grid" x1="${pad.l}" x2="${W - pad.r}" y1="${f(yAt(v))}" y2="${f(yAt(v))}"/>`);
    parts.push(`<text class="tick" x="${pad.l}" y="${f(yAt(v) - 4)}">${centsTick(v)}</text>`);
  }

  // Across each day at that day's price, then up or down to the next one.
  let line = "";
  pts.forEach((p, i) => {
    const next = i + 1 < pts.length ? xAt(pts[i + 1].t) : W - pad.r;
    line += `${i ? "V" : `M${f(xAt(p.t))},`}${f(yAt(p.final))}H${f(next)}`;
  });
  parts.push(`<path class="price-area" d="${line}V${f(base)}H${pad.l}Z"/>`);
  parts.push(`<line class="axis" x1="${pad.l}" x2="${W - pad.r}" y1="${f(base)}" y2="${f(base)}"/>`);
  const low = data.low;
  if (low) {
    const y = f(yAt(low.final));
    parts.push(`<line class="price-low" x1="${f(xAt(Math.max(first, dayTime(low.since))))}" x2="${W - pad.r}" y1="${y}" y2="${y}"/>`);
  }
  parts.push(`<path class="price-line" d="${line}"/>`);
  parts.push(`<circle class="price-dot" cx="${f(xAt(end.t) + slot / 2)}" cy="${f(yAt(end.final))}" r="3.5"/>`);

  // Dates along the bottom: about six for short histories, otherwise each
  // month, or each quarter once there's more than about a year.
  const ticks = [];
  if (days <= 45) {
    const every = Math.ceil(days / 6);
    for (let i = 0; i < days; i += every) ticks.push(first + i * DAY);
  } else {
    const months = days > 400 ? 3 : 1;
    const d0 = new Date(first);
    let m = Date.UTC(d0.getUTCFullYear(), d0.getUTCMonth() + 1, 1);
    while (new Date(m).getUTCMonth() % months) m = Date.UTC(new Date(m).getUTCFullYear(), new Date(m).getUTCMonth() + 1, 1);
    for (; m <= end.t; ) {
      ticks.push(m);
      const d = new Date(m);
      m = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + months, 1);
    }
  }
  ticks.forEach((t) => {
    const x = xAt(t);
    if (x > W - pad.r - 40) return; // no room for a label at the very end
    const d = new Date(t);
    const label = days <= 45 ? `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}` : d.getUTCMonth() ? MONTHS[d.getUTCMonth()] : d.getUTCFullYear();
    parts.push(`<text class="tick" x="${f(x)}" y="${H - 6}">${label}</text>`);
  });

  parts.push(`<line class="price-guide" y1="${pad.t}" y2="${f(base)}" visibility="hidden"/>`);
  parts.push(`<circle class="price-dot price-dot--hover" r="4.5" visibility="hidden"/>`);
  parts.push(`<rect class="hit" x="0" y="0" width="${W}" height="${H}"/>`);

  const lowest = pts.reduce((a, b) => (b.final <= a.final ? b : a));
  const summary =
    `US price from ${dayLabel(pts[0].day, { year: true })} to ${dayLabel(end.day, { year: true })}. ` +
    `Latest ${cents(end.final)}${end.discount ? `, ${end.discount}% off` : ""}. ` +
    `Lowest ${cents(lowest.final)}, on ${dayLabel(lowest.day, { year: true })}.`;
  const lowLabel = low && (low.full ? "Lowest in 90 days" : `Lowest since ${dayLabel(low.since)}`);
  el.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${escapeHtml(summary)}">${parts.join("")}</svg>
    <div class="history-tip" hidden></div>
    <div class="history-legend"><span class="price">US price</span>${low ? `<span class="low">${escapeHtml(lowLabel)}</span>` : ""}${bands ? '<span class="sale">Big Steam sales</span>' : ""}</div>`;

  const svg = el.querySelector("svg");
  const tip = el.querySelector(".history-tip");
  const guide = svg.querySelector(".price-guide");
  const dot = svg.querySelector(".price-dot--hover");
  const show = (clientX) => {
    const box = el.getBoundingClientRect();
    const day = Math.floor((((clientX - box.left) / box.width) * W - pad.l) / slot);
    const t = first + Math.min(Math.max(day, 0), days - 1) * DAY;
    // The snapshot in force that day: a missed day keeps the one before it.
    let p = pts[0];
    for (const q of pts) {
      if (q.t > t) break;
      p = q;
    }
    const x = xAt(p.t) + slot / 2;
    guide.setAttribute("x1", f(x));
    guide.setAttribute("x2", f(x));
    dot.setAttribute("cx", f(x));
    dot.setAttribute("cy", f(yAt(p.final)));
    guide.setAttribute("visibility", "visible");
    dot.setAttribute("visibility", "visible");
    tip.innerHTML = `<strong>${dayLabel(p.day, { year: true })}</strong><br>
      <span class="num">${cents(p.final)}</span>${p.discount ? ` <span class="pos">−${p.discount}%</span> <span class="muted">from ${cents(p.initial)}</span>` : ""}`;
    tip.hidden = false;
    tip.style.left = `${Math.min(Math.max(0, (x / W) * box.width - 75), box.width - 160)}px`;
    tip.style.top = "-8px";
    tip.style.transform = "translateY(-100%)";
  };
  const hide = () => {
    tip.hidden = true;
    guide.setAttribute("visibility", "hidden");
    dot.setAttribute("visibility", "hidden");
  };
  svg.addEventListener("pointermove", (e) => show(e.clientX));
  svg.addEventListener("pointerdown", (e) => show(e.clientX));
  // A finger lifting also "leaves", so a tapped tooltip stays until the next tap.
  svg.addEventListener("pointerleave", (e) => e.pointerType !== "touch" && hide());
}

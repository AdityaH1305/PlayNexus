/* Small hand-drawn SVG charts. No chart library: these are simple enough to
   draw directly, and it keeps them in the site's own type and colours. */

import { escapeHtml, fmtInt } from "pn/core";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const monthLabel = (t) => {
  const d = new Date(t * 1000);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
};

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

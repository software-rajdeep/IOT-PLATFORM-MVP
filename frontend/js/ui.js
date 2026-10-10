// Small DOM / formatting helpers used by the views.

export function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export function toast(message, ms = 2400) {
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = message;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), ms);
}

export function fmt(value, digits = 3) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(digits);
}

export function shortTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleTimeString(); } catch { return "—"; }
}

// Minimal dependency-free line chart (SVG). series = [{t, v}].
export function lineChart(series, opts = {}) {
  const points = (series || []).filter((p) => p && p.v != null);
  if (points.length < 2) return `<div class="empty">waiting for data…</div>`;
  const w = 600, h = 180;
  const vals = points.map((p) => p.v);
  let min = opts.min != null ? opts.min : Math.min(...vals);
  let max = opts.max != null ? opts.max : Math.max(...vals);
  if (max - min < 1e-6) { max = min + 1; }
  const span = max - min;
  const n = points.length;
  const path = points.map((p, i) => {
    const x = (i / (n - 1)) * w;
    const y = h - ((p.v - min) / span) * (h - 12) - 6;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polyline fill="none" stroke="var(--accent)" stroke-width="2"
      vector-effect="non-scaling-stroke" points="${path}" />
  </svg>`;
}

import { api } from "../api.js";
import { store } from "../store.js";
import { esc, fmt, shortTime } from "../ui.js";

// Step 7 — BLOCK 6 dashboard. Widget layout is metadata; values come from the API.
export async function renderDashboard(view, ctx, setCleanup, instanceId) {
  let instance;
  try { instance = await api.instance(instanceId); }
  catch (err) { view.innerHTML = `<div class="page narrow"><div class="error-box">${esc(err.message)}</div></div>`; return; }

  const widgets = (instance.template.dashboard && instance.template.dashboard.widgets) || [];

  view.innerHTML = `
    <div class="page">
      <div class="crumbs"><a href="#/apps">Applications</a><span class="sep">/</span><b>${esc(instance.name)}</b></div>
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
        <h1 style="margin:0">${esc(instance.name)}</h1>
        <span id="app-status" class="badge muted">${esc(instance.status)}</span>
      </div>
      <div id="widgets"><div class="spinner">loading…</div></div>
    </div>`;

  function thicknessChart(trend, upper, target, lower) {
    const pts = (trend || []).filter((p) => p["thickness.actual_mm"] != null);
    if (pts.length < 2) return `<div class="empty">waiting for measurements…</div>`;
    const w = 720, h = 200, pad = 8;
    const values = pts.map((p) => p["thickness.actual_mm"]);
    const bounds = [...values, upper, target, lower].filter((v) => v != null);
    let min = Math.min(...bounds), max = Math.max(...bounds);
    if (max - min < 1e-6) { max = min + 1; }
    const padY = (max - min) * 0.15; min -= padY; max += padY;
    const span = max - min;
    const X = (i) => (i / (pts.length - 1)) * w;
    const Y = (v) => h - ((v - min) / span) * (h - 12) - 6;
    const path = pts.map((p, i) => `${X(i).toFixed(1)},${Y(p["thickness.actual_mm"]).toFixed(1)}`).join(" ");
    const line = (v, color) => v == null ? "" :
      `<line x1="0" y1="${Y(v).toFixed(1)}" x2="${w}" y2="${Y(v).toFixed(1)}" stroke="${color}" stroke-width="1" stroke-dasharray="4 4" />`;
    const points = pts.map((p, i) => p["quality.status"] === "FAIL"
      ? `<circle cx="${X(i).toFixed(1)}" cy="${Y(p["thickness.actual_mm"]).toFixed(1)}" r="2.5" fill="var(--bad)" />` : "").join("");
    return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      ${line(upper, "var(--bad)")}${line(target, "var(--muted)")}${line(lower, "var(--bad)")}
      <polyline fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke" points="${path}" />
      ${points}
    </svg>`;
  }

  function render(data) {
    const c = data.current || {};
    const s = data.statistics || {};
    const q = data.quality || {};
    const h = data.device_health || {};
    const cfgQ = data.config || {};
    const status = c["quality.status"];
    const badge = status === "PASS" ? "good" : status === "FAIL" ? "bad" : "muted";
    document.getElementById("app-status").className = `badge ${badge}`;
    document.getElementById("app-status").textContent = status || data.status;

    const cards = widgets.map((widget) => {
      if (widget.type === "current_value") {
        return `<div class="stat primary" style="grid-column:span 4">
          <div class="k">${esc(widget.title || "Current thickness")}</div>
          <div class="v">${fmt(c["thickness.actual_mm"])}<span class="u">mm</span></div>
          <div class="meta"><span class="badge ${badge}">${esc(status || "—")}</span> · ${esc(shortTime(c.ts))}</div>
        </div>`;
      }
      if (widget.type === "target") {
        return `<div class="stat" style="grid-column:span 4">
          <div class="k">${esc(widget.title || "Target")}</div>
          <div class="v">${fmt(cfgQ["thickness.target_mm"], 3)}<span class="u">mm</span></div>
          <div class="meta">deviation ${fmt(c["thickness.deviation_mm"], 3)} mm</div>
        </div>`;
      }
      if (widget.type === "trend") {
        return `<div class="panel" style="grid-column:1 / -1">
          <h3>${esc(widget.title || "Thickness vs time")}</h3>
          ${thicknessChart(data.trend, cfgQ["thickness.upper_limit_mm"], cfgQ["thickness.target_mm"], cfgQ["thickness.lower_limit_mm"])}
          <div style="display:flex;gap:14px;margin-top:8px;font-size:11px;color:var(--muted)">
            <span>— actual</span><span style="color:var(--bad)">- - limits</span><span>- - target</span>
          </div>
        </div>`;
      }
      if (widget.type === "statistics") {
        return `<div class="stat" style="grid-column:span 4">
          <div class="k">${esc(widget.title || "Statistics")} (${s.samples || 0})</div>
          <div class="meta" style="margin-top:8px;line-height:1.9">
            avg <b>${fmt(s.average)}</b> · min <b>${fmt(s.minimum)}</b> · max <b>${fmt(s.maximum)}</b><br/>
            std dev <b>${fmt(s.std_dev, 4)}</b> mm</div>
        </div>`;
      }
      if (widget.type === "quality") {
        return `<div class="stat" style="grid-column:span 2">
          <div class="k">${esc(widget.title || "Quality")}</div>
          <div class="v" style="font-size:22px">${q.pass_percentage == null ? "—" : q.pass_percentage + "%"}</div>
          <div class="meta"><span class="badge good">${q.pass_count || 0} pass</span> <span class="badge bad">${q.fail_count || 0} fail</span></div>
        </div>`;
      }
      if (widget.type === "device_health") {
        const online = h.communication === "ONLINE";
        return `<div class="stat" style="grid-column:span 2">
          <div class="k">${esc(widget.title || "Device health")}</div>
          <div class="v" style="font-size:18px">${esc(h.model || "—")}</div>
          <div class="meta"><span class="badge ${online ? "good" : "bad"}">${esc(h.communication || "—")}</span>
            · ${h.seconds_since == null ? "no data" : h.seconds_since + "s ago"}</div>
        </div>`;
      }
      return "";
    }).join("");

    document.getElementById("widgets").innerHTML = `
      <div class="stat-grid" style="grid-template-columns:repeat(8,1fr)">${cards}</div>`;
  }

  async function refresh() {
    try { render(await api.dashboard(instanceId)); } catch { /* transient */ }
  }

  await refresh();
  const timer = setInterval(refresh, 1000);
  setCleanup(() => clearInterval(timer));
}

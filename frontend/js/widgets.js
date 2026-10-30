// Widget renderers. Each widget type (core.widget_types) renders from the generic dashboard
// bundle (current / trend / statistics / quality / capability / groups / health / alerts),
// using the template's label + unit — no widget knows it is "thickness".
import { esc, fmt, shortTime } from "./ui.js";

export const DEFAULT_TITLES = {
  current_value: (d) => `Current ${d.label.toLowerCase()}`,
  target_deviation: () => "Target & deviation",
  pass_fail: () => "Quality",
  statistics: () => "Statistics",
  capability: () => "Process capability",
  trend_band: (d) => `${d.label} over time`,
  deviation_trend: () => "Deviation over time",
  histogram: (d) => `${d.label} distribution`,
  recent_bars: () => "Recent readings",
  group_summary: () => "Per-product summary",
  speed_scatter: (d) => `${d.label} vs line speed`,
  device_health: () => "Source health",
  calibration_status: () => "Calibration",
  limits_editor: () => "Limits",
  alerts_list: () => "Alerts",
  history_table: () => "Reading history",
  sensor_list: () => "Sensor readings",
  sensor_thickness: () => "Per-sensor thickness",
  sensor_trends: (d) => `${d.label} per sensor`,
  raw_readings: () => "Raw readings",
  raw_current: () => "Current raw sensor readings",
};

// Widgets that hold user input are drawn once, not on every live refresh.
export const STATIC_WIDGETS = new Set(["limits_editor"]);

const cls = (status) => (status === "FAIL" ? "bad" : status === "PASS" ? "good" : "muted");
// What one sensor contributes to a "current" card: a reference sensor reports its error
// against the reference, the others report computed thickness and fall back to raw input.
const currentOf = (s) => (s.is_reference
  ? (s.error ?? s.thickness ?? s.distance ?? s.value)
  : (s.thickness ?? s.distance ?? s.value));

// Sensor picker shared by the current-value, calibration and limits cards. The board keeps
// the choice in the card's state and hands it back through renderWidget(..., state).
function sensorSelect(list, selected, allLabel = "All sensors", alwaysAll = false) {
  const opts = ((list.length > 1 || alwaysAll)
    ? `<option value=""${selected ? "" : " selected"}>${esc(allLabel)}</option>` : "")
    + list.map((s) => `<option value="${esc(s.key)}" data-field="${esc(s.limit_field || "")}"${s.key === selected ? " selected" : ""}>${esc(s.label || s.key)}</option>`).join("");
  return `<div class="raw-head"><label class="raw-select">Sensor <select data-sensor-select>${opts}</select></label></div>`;
}

const W = 720;

function bounds(values, extra = []) {
  const all = [...values, ...extra].filter((v) => v != null && Number.isFinite(v));
  let min = Math.min(...all), max = Math.max(...all);
  if (!all.length) { min = 0; max = 1; }
  if (max - min < 1e-6) { min -= 0.5; max += 0.5; }
  const pad = (max - min) * 0.08;
  return [min - pad, max + pad];
}

function hline(y, color, h) {
  return `<line x1="0" y1="${y.toFixed(1)}" x2="${W}" y2="${y.toFixed(1)}" stroke="${color}" stroke-width="1" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/>`;
}

function svg(h, inner, axis = "") {
  return `<svg class="wchart" viewBox="0 0 ${W} ${h}" preserveAspectRatio="none">${inner}</svg>${axis}`;
}

function lineChart(points, key, refs, h = 200) {
  const pts = points.filter((p) => p[key] != null);
  if (pts.length < 2) return `<div class="empty">waiting for data…</div>`;
  const [min, max] = bounds(pts.map((p) => p[key]), refs.map((r) => r.v));
  const X = (i) => (i / (pts.length - 1)) * W;
  const Y = (v) => h - ((v - min) / (max - min)) * h;
  const line = pts.map((p, i) => `${X(i).toFixed(1)},${Y(p[key]).toFixed(1)}`).join(" ");
  const dots = pts.map((p, i) => p.status === "FAIL"
    ? `<circle cx="${X(i).toFixed(1)}" cy="${Y(p[key]).toFixed(1)}" r="3" fill="var(--bad)"/>` : "").join("");
  const lines = refs.filter((r) => r.v != null).map((r) => hline(Y(r.v), r.color, h)).join("");
  const axis = `<div class="waxis"><span>${fmt(max, 3)}</span><span>${shortTime(pts[0].ts)} → ${shortTime(pts[pts.length - 1].ts)}</span><span>${fmt(min, 3)}</span></div>`;
  return svg(h, `${lines}<polyline fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke" points="${line}"/>${dots}`, axis);
}

function histogram(points, lower, upper, target, h = 200) {
  const vals = points.map((p) => p.value).filter((v) => v != null);
  if (vals.length < 5) return `<div class="empty">collecting samples…</div>`;
  const bins = 20;
  let [lo, hi] = bounds(vals, [lower, upper]);
  const span = hi - lo;
  const counts = new Array(bins).fill(0);
  vals.forEach((v) => { counts[Math.min(bins - 1, Math.max(0, Math.floor(((v - lo) / span) * bins)))]++; });
  const maxC = Math.max(...counts);
  const bw = W / bins;
  const bars = counts.map((c, i) => {
    const hh = (c / maxC) * (h - 6);
    const centre = lo + ((i + 0.5) / bins) * span;
    const ok = (lower == null || centre >= lower) && (upper == null || centre <= upper);
    return `<rect x="${(i * bw + 1).toFixed(1)}" y="${(h - hh).toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${hh.toFixed(1)}" fill="${ok ? "rgba(74,122,94,.75)" : "rgba(220,50,50,.8)"}" rx="1"/>`;
  }).join("");
  const vx = (v) => (((v - lo) / span) * W).toFixed(1);
  const marks = [[lower, "var(--bad)"], [upper, "var(--bad)"], [target, "var(--muted)"]].filter(([v]) => v != null)
    .map(([v, c]) => `<line x1="${vx(v)}" y1="0" x2="${vx(v)}" y2="${h}" stroke="${c}" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/>`).join("");
  return svg(h, bars + marks, `<div class="waxis"><span>${fmt(lo)}</span><span>${vals.length} samples</span><span>${fmt(hi)}</span></div>`);
}

function bars(points, lower, upper, target, h = 200) {
  const pts = points.filter((p) => p.value != null).slice(-60);
  if (pts.length < 2) return `<div class="empty">waiting for data…</div>`;
  const [min, max] = bounds(pts.map((p) => p.value), [lower, upper, target]);
  const Y = (v) => h - ((v - min) / (max - min)) * h;
  const bw = W / pts.length;
  const rects = pts.map((p, i) => `<rect x="${(i * bw + 0.5).toFixed(1)}" y="${Y(p.value).toFixed(1)}" width="${(bw - 1.5).toFixed(1)}"
    height="${(h - Y(p.value)).toFixed(1)}" fill="${p.status === "FAIL" ? "rgba(220,50,50,.85)" : "rgba(74,122,94,.8)"}" rx="1"/>`).join("");
  const refs = [[lower, "var(--bad)"], [upper, "var(--bad)"], [target, "var(--muted)"]].filter(([v]) => v != null).map(([v, c]) => hline(Y(v), c, h)).join("");
  return svg(h, refs + rects);
}

function scatter(points, lower, upper, h = 200) {
  const pts = points.filter((p) => p.value != null && p.speed != null);
  if (pts.length < 3) return `<div class="empty">waiting for speed data…</div>`;
  const [x0, x1] = bounds(pts.map((p) => p.speed));
  const [y0, y1] = bounds(pts.map((p) => p.value), [lower, upper]);
  const X = (v) => ((v - x0) / (x1 - x0)) * W;
  const Y = (v) => h - ((v - y0) / (y1 - y0)) * h;
  const dots = pts.map((p) => `<circle cx="${X(p.speed).toFixed(1)}" cy="${Y(p.value).toFixed(1)}" r="3.5" fill="${p.status === "FAIL" ? "var(--bad)" : "var(--accent)"}" opacity=".75"/>`).join("");
  const refs = [lower, upper].filter((v) => v != null).map((v) => hline(Y(v), "var(--bad)", h)).join("");
  return svg(h, refs + dots, `<div class="waxis"><span>${fmt(x0, 1)} m/min</span><span>line speed →</span><span>${fmt(x1, 1)} m/min</span></div>`);
}

function donut(pct) {
  const r = 30, c = 2 * Math.PI * r, v = pct == null ? 0 : pct / 100;
  return `<svg viewBox="0 0 80 80" class="donut"><circle cx="40" cy="40" r="${r}" fill="none" stroke="#f0f2f6" stroke-width="10"/>
    <circle cx="40" cy="40" r="${r}" fill="none" stroke="var(--good)" stroke-width="10" stroke-dasharray="${(c * v).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 40 40)"/>
    <text x="40" y="45" text-anchor="middle" font-size="15" fill="var(--text)" font-family="var(--mono)">${pct == null ? "—" : Math.round(pct) + "%"}</text></svg>`;
}

export function renderWidget(type, d, state = {}) {
  const c = d.current || {}, s = d.statistics || {}, q = d.quality || {}, cfg = d.config || {};
  const u = esc(d.unit || "");
  switch (type) {
    case "current_value": {
      // Several sources can feed the same input (Sensor 1, Sensor 2, ...). The card keeps
      // showing the overall value by default and lets the user pin one sensor; the choice
      // lives in the board item's state, so it survives the live refresh.
      const list = d.sensors || [];
      if (!list.length) return `<div class="big ${cls(c.status)}">${fmt(c.value)}<span class="u">${u}</span></div>`;
      const keys = list.map((s) => s.key);
      const selected = keys.includes(state.sensor) ? state.sensor : (list.length === 1 ? list[0].key : "");
      const sel = list.find((s) => s.key === selected);
      const shown = sel ? currentOf(sel) : c.value;
      const status = sel ? sel.status : c.status;
      const caption = sel
        ? `${sel.is_reference ? "reference error" : (sel.thickness != null ? "thickness" : "raw input")}`
        : (list.length > 1 ? `all ${list.length} sensors` : "");
      return `${sensorSelect(list, selected)}
        <div class="big ${cls(status)}">${fmt(shown)}<span class="u">${u}</span></div>
        ${caption ? `<div class="stat-sub">${esc(caption)}</div>` : ""}`;
    }
    case "target_deviation":
      return `<div class="big">${fmt(cfg.target)}<span class="u">${u}</span></div>`;
    case "pass_fail":
      return `<div class="pf">${donut(q.pass_percentage)}</div>`;
    case "statistics":
      return `<div class="kv"><span>avg</span><b>${fmt(s.average)}</b><span>min</span><b>${fmt(s.minimum)}</b>
        <span>max</span><b>${fmt(s.maximum)}</b><span>std dev</span><b>${fmt(s.std_dev, 4)}</b>
        <span>samples</span><b>${s.samples || 0}</b></div>`;
    case "capability": {
      const cp = d.capability || {};
      const grade = (v) => (v == null ? "muted" : v >= 1.33 ? "good" : v >= 1 ? "warn" : "bad");
      return `<div class="cap"><div><span class="k">Cp</span><span class="big ${grade(cp.cp)}">${fmt(cp.cp, 2)}</span></div>
        <div><span class="k">Cpk</span><span class="big ${grade(cp.cpk)}">${fmt(cp.cpk, 2)}</span></div></div>
        <div class="stat-sub">≥ 1.33 capable · 1.0–1.33 marginal · &lt; 1.0 not capable</div>`;
    }
    case "trend_band":
      return lineChart(d.trend || [], "value", [{ v: cfg.upper, color: "var(--bad)" }, { v: cfg.target, color: "var(--muted)" }, { v: cfg.lower, color: "var(--bad)" }]);
    case "deviation_trend": {
      const lo = cfg.lower != null && cfg.target != null ? cfg.lower - cfg.target : null;
      const hi = cfg.upper != null && cfg.target != null ? cfg.upper - cfg.target : null;
      return lineChart(d.trend || [], "deviation", [{ v: 0, color: "var(--muted)" }, { v: lo, color: "var(--bad)" }, { v: hi, color: "var(--bad)" }]);
    }
    case "histogram":
      return histogram(d.trend || [], cfg.lower, cfg.upper, cfg.target);
    case "recent_bars":
      return bars(d.trend || [], cfg.lower, cfg.upper, cfg.target);
    case "speed_scatter":
      return scatter(d.trend || [], cfg.lower, cfg.upper);
    case "group_summary":
      return (d.groups || []).length ? `<table class="wtable"><thead><tr><th>Product</th><th class="td-r">n</th>
        <th class="td-r">avg ${u}</th><th class="td-r">σ</th><th class="td-r">pass %</th></tr></thead><tbody>
        ${d.groups.map((g) => `<tr><td class="td-mono">${esc(g.group)}</td><td class="td-r">${g.count}</td><td class="td-r td-mono">${fmt(g.average)}</td>
          <td class="td-r td-mono">${fmt(g.std_dev, 4)}</td><td class="td-r">${g.pass_percentage == null ? "—" : g.pass_percentage + "%"}</td></tr>`).join("")}
        </tbody></table>` : `<div class="empty">no product codes yet</div>`;
    case "device_health": {
      const h = d.device_health || {};
      return `<div class="big" style="font-size:16px">${esc(h.model || h.device_id || "—")}</div>`;
    }
    case "calibration_status": {
      const gap = cfg["calibration.gap_distance"];
      const baselines = cfg["calibration.baselines"] || {};
      const configuredMode = cfg["calibration.mode"];
      // The calibration method belongs to the application template. Do not infer it from
      // whatever values happen to be in the config: a baseline app has no baselines yet
      // before its first calibration, but it is still a baseline app (not the legacy
      // two-point/linear formula). Config inference is only a fallback for old templates.
      const method = ((d.template || {}).commissioning || {}).calibration;
      const isGap = method === "gap" || method === "auto_gap"
        || (!method && (configuredMode === "gap" || configuredMode === "auto_gap" || gap != null));
      const isBaseline = !isGap && (method === "baseline"
        || (!method && (configuredMode === "baseline" || Object.keys(baselines).length > 0)));
      // Baseline calibration stores one baseline per sensor, so it can be done one sensor
      // at a time. The other methods calibrate the whole device, so they get no picker.
      const list = d.sensors || [];
      const perSensor = method === "baseline" && list.length > 1;
      const selected = perSensor && list.some((s) => s.key === state.sensor) ? state.sensor : "";
      const sel = list.find((s) => s.key === selected);
      const baselineCount = list.filter((s) => baselines[s.key] != null).length;
      const calibrated = !!cfg["calibration.calibrated_at"];
      // "All sensors" is only calibrated when every mapped sensor has a baseline; a
      // per-sensor capture sets calibrated_at even though the other sensors are still bare.
      const done = sel
        ? baselines[sel.key] != null
        : isGap ? calibrated && gap != null
        : isBaseline ? calibrated && list.length > 0 && baselineCount === list.length
        : calibrated && cfg["calibration.scale"] != null && cfg["calibration.offset"] != null;
      const partial = perSensor && !sel && baselineCount > 0 && !done;
      const detail = sel
        ? `baseline ${sel.key in baselines ? `${fmt(baselines[sel.key])} ${u}` : "—"} · reference ${fmt(cfg["calibration.reference_thickness"])} ${u}`
        : isGap
        ? `gap ${fmt(gap)} ${u}${cfg["calibration.object_thickness"] != null
          ? ` · object ${fmt(cfg["calibration.object_thickness"])} ${u}` : ""}${cfg["calibration.tolerance_mm"] != null
          ? ` · ±${fmt(cfg["calibration.tolerance_mm"])} ${u}` : ""}`
        : isBaseline
        ? `baseline on ${perSensor ? `${baselineCount}/${list.length}` : baselineCount} sensor(s) · reference ${fmt(cfg["calibration.reference_thickness"])} ${u}`
        : `${d.label.toLowerCase()} = ${fmt(cfg["calibration.scale"], 4)} × input + ${fmt(cfg["calibration.offset"], 4)}`;
      // The graph-options page is a preview, not a live dashboard: it has no calibration
      // handlers, so hide the action buttons there instead of rendering dead controls.
      const actions = state.preview ? "" : `
        <button class="btn btn-sm" data-act="recalibrate">${done ? "Recalibrate" : "Calibrate"}</button>
        ${done && !sel ? `<button class="btn secondary btn-sm" data-act="reset-calibration">Reset</button>` : ""}`;
      return `${perSensor ? sensorSelect(list, selected) : ""}<div class="cal-status-row">
        <span class="badge ${done ? "good" : "warn"}">${done ? "calibrated" : partial ? `${baselineCount}/${list.length} calibrated` : "not calibrated"}</span>${actions}</div>
        <div class="stat-sub">${esc(detail)}
        ${done && !sel ? `<br/>by ${esc(cfg["calibration.calibrated_by"] || "")} · ${esc(new Date(cfg["calibration.calibrated_at"]).toLocaleString())}` : ""}</div>`;
    }
    case "limits_editor": {
      const keys = cfg.keys || {};
      const list = d.sensors || [];
      const selected = list.some((s) => s.key === state.sensor) ? state.sensor : "";
      const sensor = list.find((s) => s.key === selected);
      // A sensor can carry its own bounds (stored as event.sensor_limits, the same map the
      // alert engine reads); with nothing selected the card edits the instance limits.
      if (sensor) {
        const lim = sensor.limits || {};
        const map = JSON.stringify(cfg.sensor_limits || {});
        return `${sensorSelect(list, selected)}
          <div class="limits-row" data-sensor-limits="${esc(map)}">
            <label><span class="limit-label">lower ${u}</span>
              <input class="limit-input" data-limit="lower" type="number" step="any" value="${lim.lower ?? ""}"/></label>
            <label><span class="limit-label">upper ${u}</span>
              <input class="limit-input" data-limit="upper" type="number" step="any" value="${lim.upper ?? ""}"/></label>
            <button class="btn btn-sm" data-act="save-limits">Save</button>
            ${lim.override ? `<button class="btn secondary btn-sm" data-act="clear-sensor-limits">Use global</button>` : ""}
          </div>
          <div class="stat-sub">${esc(sensor.label || sensor.key)} · ${lim.override
            ? "own limits" : `inherits the global limits (${fmt(cfg.lower)} – ${fmt(cfg.upper)}, ${u})`}
            ${keys.limit_active ? "" : " · value limit is off"}</div>`;
      }
      const fields = [
        ["target", keys.target, cfg.target],
        ["lower", keys.lower, cfg.lower],
        ["upper", keys.upper, cfg.upper],
        ["reference lower", keys.reference_lower, cfg.reference_lower],
        ["reference upper", keys.reference_upper, cfg.reference_upper],
      ].filter(([, key]) => key);
      return `${sensorSelect(list, selected, "Global limits", true)}<div class="limits-row">
        ${fields.map(([label, key, value]) => `<label><span class="limit-label">${esc(label)} ${u}</span>
          <input class="limit-input" data-limit="${esc(key)}" type="number" step="any" value="${value ?? ""}"/></label>`).join("")}
        ${(keys.lower || keys.upper) ? `<label class="limit-check"><input type="checkbox" data-limit-flag="${esc(keys.limit_active || "thickness.limit_active")}"
          ${cfg.limit_active ? "checked" : ""}/><span>value limit</span></label>` : ""}
        ${keys.reference_lower ? `<label class="limit-check"><input type="checkbox" data-limit-flag="reference.limit_active"
          ${cfg["reference.limit_active"] ? "checked" : ""}/><span>reference limit</span></label>` : ""}
        <button class="btn btn-sm" data-act="save-limits">Save</button></div>
        ${keys.reference_lower && !cfg["reference.limit_active"]
          ? `<div class="stat-sub">Reference error is shown but its limit is disabled.</div>` : ""}`;
    }
    case "alerts_list":
      return (d.alerts || []).length ? `<ul class="walerts">${d.alerts.map((a) => `<li class="${a.resolved_at ? "resolved" : ""}">
        <span class="badge ${a.severity === "critical" ? "bad" : "warn"}">${esc(a.rule_type.replace("_", " "))}</span>
        <span>${esc(a.message)}</span><span class="td-dim">${esc(shortTime(a.created_at))}${a.resolved_at ? " · resolved" : ""}</span>
        ${!a.ack_at && !a.resolved_at ? `<button class="btn-ghost" data-ack="${a.event_id}">ack</button>` : ""}</li>`).join("")}</ul>`
        : `<div class="empty">no alerts</div>`;
    case "history_table": {
      const rows = [...(d.trend || [])].reverse().slice(0, 60);
      return rows.length ? `<table class="wtable"><thead><tr><th>Time</th><th class="td-r">${esc(d.label)} ${u}</th>
        <th class="td-r">Deviation</th><th>Status</th>${rows.some((r) => r.group) ? "<th>Product</th>" : ""}</tr></thead><tbody>
        ${rows.map((r) => `<tr><td class="td-mono td-dim">${esc(shortTime(r.ts))}</td><td class="td-mono td-r ${cls(r.status)}">${fmt(r.value)}</td>
          <td class="td-mono td-r">${r.deviation == null ? "—" : fmt(r.deviation)}</td>
          <td><span class="badge ${cls(r.status)}">${esc(r.status || "—")}</span></td>${r.group ? `<td class="td-mono">${esc(r.group)}</td>` : ""}</tr>`).join("")}
        </tbody></table>` : `<div class="empty">waiting for readings…</div>`;
    }
    case "sensor_list": {
      const list = d.sensors || [];
      const computed = list.some((s) => s.thickness != null || s.error != null);
      const withDistance = list.some((s) => s.distance != null);
      return list.length ? `<table class="wtable"><thead><tr><th>Sensor</th><th class="td-r">${withDistance ? "Distance" : "Raw"} ${u}</th>
        ${computed ? `<th class="td-r">Thickness / error ${u}</th>` : ""}</tr></thead><tbody>
        ${list.map((s) => {
          const value = s.is_reference ? s.error : s.thickness;
          const shown = withDistance && s.distance != null ? s.distance : s.value;
          return `<tr><td>${esc(s.label || s.key)}</td><td class="td-r td-mono">${fmt(shown)}</td>
            ${computed ? `<td class="td-r td-mono ${s.status === "FAIL" ? "bad" : ""}">${fmt(value)}</td>` : ""}</tr>`;
        }).join("")}
        </tbody></table>` : `<div class="empty">one sensor — add more in Setup › Map</div>`;
    }
    case "sensor_thickness": {
      const list = d.sensors || [];
      if (!list.length) return `<div class="empty">one sensor — add more in Setup › Map</div>`;
      return `<div class="raw-current-grid">${list.map((s) => {
        const computed = s.is_reference ? s.error : s.thickness;
        const raw = s.distance != null ? s.distance : s.value;
        const shown = computed != null ? computed : raw;
        const caption = s.is_reference ? "reference error" : (computed != null ? "thickness" : "raw input");
        return `<div class="raw-current-item">
          <div class="stat-sub">${esc(s.label || s.key)}</div>
          <div class="big ${cls(s.status)}">${fmt(shown)}<span class="u">${u}</span></div>
          <div class="stat-sub">${caption}</div>
        </div>`;
      }).join("")}</div>`;
    }
    case "sensor_trends": {
      const list = d.sensors || [];
      if (!list.length) return `<div class="empty">one sensor — add more in Setup › Map</div>`;
      const refs = [{ v: cfg.lower, color: "var(--bad)" }, { v: cfg.target, color: "var(--muted)" },
        { v: cfg.upper, color: "var(--bad)" }];
      return `<div class="sensor-trend-grid">${list.map((s) => {
        const thickness = s.thickness_series && s.thickness_series.length ? s.thickness_series : null;
        const series = thickness || s.series;
        const latest = s.thickness != null ? s.thickness : (s.distance != null ? s.distance : s.value);
        return `<div class="sensor-trend-item">
          <div class="sensor-trend-head"><span>${esc(s.label || s.key)}</span>
            <span class="td-mono ${cls(s.status)}">${fmt(latest)} ${u}</span></div>
          <div class="stat-sub">${thickness ? "thickness" : "raw input"}</div>
          ${lineChart(series || [], "value", refs, 130)}
        </div>`;
      }).join("")}</div>`;
    }
    case "raw_readings": {
      const list = d.sensors || [];
      if (!list.length) return `<div class="empty">one sensor — add more in Setup › Map</div>`;
      const selectedKey = list.some((s) => s.key === state.sensor) ? state.sensor : list[0].key;
      const sel = list.find((s) => s.key === selectedKey) || list[0];
      const opts = list.map((s) => `<option value="${esc(s.key)}"${s.key === selectedKey ? " selected" : ""}>${esc(s.label || s.key)}</option>`).join("");
      const rows = [...(sel.series || [])].reverse().slice(0, 60);
      return `<div class="raw-head"><label class="raw-select">Sensor <select data-sensor-select>${opts}</select></label></div>
        ${rows.length ? `<table class="wtable"><thead><tr><th>Time</th><th class="td-r">Raw ${u}</th></tr></thead><tbody>
        ${rows.map((r) => `<tr><td class="td-mono td-dim">${esc(shortTime(r.ts))}</td><td class="td-mono td-r">${fmt(r.value)}</td></tr>`).join("")}
        </tbody></table>` : `<div class="empty">waiting for raw readings…</div>`}`;
    }
    case "raw_current": {
      const list = d.sensors || [];
      return list.length
        ? `<div class="raw-current-grid">${list.map((sensor) => {
            const extra = sensor.is_reference
              ? (sensor.error == null ? "" : `<div class="stat-sub">error ${fmt(sensor.error)} ${u}</div>`)
              : (sensor.thickness == null ? "" : `<div class="stat-sub">thickness ${fmt(sensor.thickness)} ${u}</div>`);
            const shown = sensor.distance != null ? sensor.distance : sensor.value;
            return `<div class="raw-current-item">
              <div class="stat-sub">${esc(sensor.label || sensor.key)}</div>
              <div class="big">${fmt(shown)}<span class="u">${u}</span></div>${extra}
            </div>`;
          }).join("")}</div>`
        : `<div class="empty">waiting for raw readings…</div>`;
    }
    default:
      return `<div class="empty">unknown widget ${esc(type)}</div>`;
  }
}
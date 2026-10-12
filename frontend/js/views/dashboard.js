import { api } from "../api.js";
import { esc, fmt, shortTime, toast } from "../ui.js";

// Step 7 — BLOCK 6 dashboard (Live Run Mode). Widget layout is metadata;
// values come from the API. Device switcher lets a company with several
// CD22-35 sensors flip between them in the same run-mode page.
export async function renderDashboard(view, ctx, setCleanup, instanceId) {
  let instance;
  try { instance = await api.instance(instanceId); }
  catch (err) { view.innerHTML = `<div class="page narrow"><div class="error-box">${esc(err.message)}</div></div>`; return; }

  const widgets = (instance.template.dashboard && instance.template.dashboard.widgets) || [];
  const byType = {};
  for (const w of widgets) byType[w.type] = w;

  const cfg = instance.config || {};
  const target = cfg["thickness.target_mm"] != null ? cfg["thickness.target_mm"] : "";
  const lower = cfg["thickness.lower_limit_mm"] != null ? cfg["thickness.lower_limit_mm"] : "";
  const upper = cfg["thickness.upper_limit_mm"] != null ? cfg["thickness.upper_limit_mm"] : "";

  const DEVICE_KEY = "ucap_device_" + instanceId;
  let activeDevice = sessionStorage.getItem(DEVICE_KEY) || instance.device_id || "";
  let devices = [];
  let uiState = {};

  const SETTINGS = [
    ["show_device_switcher", "Device switcher (device button)"],
    ["show_live_status", "Status & LIVE indicator"],
    ["show_calibration", "Calibration status"],
    ["show_limits", "Thickness limit"],
    ["show_current", "Current thickness card"],
    ["show_target", "Target card"],
    ["show_statistics", "Statistics card"],
    ["show_quality", "Quality card"],
    ["show_health", "Device health card"],
    ["show_trend", "Live graph"],
    ["show_history", "Reading history"],
  ];

  const GRAPHS = {
    line: { title: "Live Graph - Thickness over time", legend: `<span>— actual</span><span class="lg-bad">- - limits</span><span class="lg-muted">- - target</span>` },
    deviation: { title: "Live Graph - Deviation over time", legend: `<span>— deviation</span><span class="lg-bad">- - deviation limits</span><span class="lg-muted">- - zero</span>` },
    histogram: { title: "Live Graph - Thickness distribution", legend: `<span class="lg-success">■ in-spec</span><span class="lg-bad">■ out-of-spec</span><span class="lg-muted">- - target</span>` },
    bars: { title: "Live Graph - Recent readings", legend: `<span class="lg-success">■ pass</span><span class="lg-bad">■ fail</span><span class="lg-muted">- - target</span><span class="lg-bad">- - limits</span>` },
  };

  view.innerHTML = `
    <div class="page run">
      <div class="run-header">
        <div class="crumbs"><a href="#/apps">Applications</a><span class="sep">/</span><b id="run-name">${esc(instance.name)}</b></div>
        <div class="run-header-row">
          <h1>Live Run Mode</h1>
          <div class="run-meta">
            <div class="device-switch" id="device-switch">
              <button type="button" class="device-btn" id="device-btn">
                <span class="s-dot" id="device-dot"></span>
                <span id="device-name">—</span>
                <span class="caret">▾</span>
              </button>
              <div class="device-menu" id="device-menu"></div>
            </div>
            <span class="run-status" id="live-status">
              <span id="app-status" class="badge muted">${esc(instance.status)}</span>
              <span id="live-dot" class="live-dot off"><span class="dot"></span><span id="live-dot-text">OFFLINE</span></span>
            </span>
            <button type="button" class="btn-ghost settings-btn" id="settings-btn" title="Run mode settings">⚙</button>
          </div>
        </div>
      </div>

      <div class="chip off" id="conn-chip"><i></i><span id="conn-text">Waiting for server... </span></div>

      <div class="section" id="sec-cal">
        <div class="section-header">
          <span class="section-title">Calibration Status</span>
          <div class="run-actions">
            <button type="button" class="btn btn-outline btn-sm" id="cal-reset">Reset Calibration</button>
            <button type="button" class="btn btn-sm" id="cal-open">Calibrate</button>
          </div>
        </div>
        <div class="cal-box" id="cal-status"></div>
      </div>

      <div class="section" id="sec-limits">
        <div class="section-header">
          <span class="section-title">Thickness Limit</span>
        </div>
        <div class="cal-box limit-box">
          <div>
            <div class="limit-label">TARGET (mm)</div>
            <input id="limit-target" class="limit-input" type="number" step="any" value="${esc(target)}" />
          </div>
          <div class="limit-sep">-</div>
          <div>
            <div class="limit-label">MIN (mm)</div>
            <input id="limit-min" class="limit-input" type="number" step="any" value="${esc(lower)}" />
          </div>
          <div class="limit-sep">-</div>
          <div>
            <div class="limit-label">MAX (mm)</div>
            <input id="limit-max" class="limit-input" type="number" step="any" value="${esc(upper)}" />
          </div>
          <div class="limit-active" id="limit-active"></div>
          <button class="btn btn-sm" id="limit-save">Save</button>
        </div>
      </div>

      <div class="stats-grid" id="stat-cards"><div class="stat-card">loading…</div></div>

      <div class="section" id="sec-trend">
        <div class="section-header">
          <span class="section-title" id="trend-title">Live Graph - Last 200 Samples</span>
          <span class="graph-head-right">
            <select id="graph-type" class="graph-select" title="Graph type">
              <option value="line">Thickness over time</option>
              <option value="deviation">Deviation over time</option>
              <option value="histogram">Distribution (histogram)</option>
              <option value="bars">Recent readings</option>
            </select>
            <span class="graph-val" id="graph-val">—</span>
          </span>
        </div>
        <div class="panel graph-panel">
          <div id="trend-body"></div>
          <div class="legend" id="legend">
            <span>— actual</span><span class="lg-bad">- - limits</span><span class="lg-muted">- - target</span>
          </div>
        </div>
      </div>

      <div class="section" id="sec-history">
        <div class="section-header">
          <span class="section-title">Reading History</span>
          <span class="section-count" id="history-count">0 records</span>
        </div>
        <div class="table-wrap scroll-table" id="history-body"><div class="empty">No thickness data yet - waiting for readings...</div></div>
      </div>
    </div>

    <div class="modal-overlay hidden" id="settings-modal">
      <div class="modal">
        <div class="modal-title">Run mode settings</div>
        <p class="settings-hint">Enable or disable what appears in run mode. Changes save automatically and apply to every device.</p>
        <div id="settings-list"></div>
        <div class="modal-actions"><button class="btn btn-secondary btn-sm" id="settings-close" type="button">Close</button></div>
      </div>
    </div>

    <div class="modal-overlay hidden" id="cal-modal">
      <div class="modal">
        <div class="modal-title">Thickness Calibration</div>
        <p class="settings-hint">Put a known reference piece under the sensor, enter its thickness and read the live displacement. Two references derive scale and offset: thickness = scale x displacement + offset.</p>
        <div class="field"><label>Reference 1 - known thickness (mm)</label>
          <div class="cal-read-row">
            <input id="cal-r1t" class="form-input" type="number" step="any" placeholder="e.g. 10" />
            <input id="cal-r1d" class="form-input cal-disp" type="number" step="any" placeholder="displacement" />
            <button type="button" class="btn btn-secondary btn-sm" id="cal-r1-read">Read</button>
          </div>
        </div>
        <div class="field"><label>Reference 2 - known thickness (mm)</label>
          <div class="cal-read-row">
            <input id="cal-r2t" class="form-input" type="number" step="any" placeholder="e.g. 8" />
            <input id="cal-r2d" class="form-input cal-disp" type="number" step="any" placeholder="displacement" />
            <button type="button" class="btn btn-secondary btn-sm" id="cal-r2-read">Read</button>
          </div>
        </div>
        <div class="modal-actions">
          <button type="button" class="btn btn-secondary btn-sm" id="cal-cancel">Cancel</button>
          <button type="button" class="btn btn-sm" id="cal-save">Calculate &amp; Save</button>
        </div>
      </div>
    </div>

    <div class="modal-overlay hidden" id="edit-modal">
      <div class="modal">
        <div class="modal-title">Configure device</div>
        <div class="field"><label>Device name</label><input id="ed-name" type="text" autocomplete="off" /></div>
        <div class="field"><label>IP / host:port</label><input id="ed-ip" type="text" autocomplete="off" placeholder="e.g. 192.168.1.200 or 192.168.1.200:8234" /></div>
        <div class="modal-actions">
          <button class="btn btn-secondary btn-sm" id="ed-cancel" type="button">Cancel</button>
          <button class="btn btn-sm" id="ed-save" type="button">Save</button>
        </div>
      </div>
    </div>

    <div class="modal-overlay hidden" id="add-modal">
      <div class="modal">
        <div class="modal-title">Add a CD22-35 device</div>
        <div id="add-list"><div class="spinner">loading…</div></div>
        <div class="modal-actions"><button class="btn btn-secondary btn-sm" id="add-cancel" type="button">Close</button></div>
      </div>
    </div>`;

  document.getElementById("limit-save").addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    const t = parseFloat(document.getElementById("limit-target").value);
    const mn = parseFloat(document.getElementById("limit-min").value);
    const mx = parseFloat(document.getElementById("limit-max").value);
    if ([t, mn, mx].some((v) => Number.isNaN(v))) { toast("Enter target, min and max limits"); return; }
    if (mn >= mx) { toast("Lower limit must be less than upper limit"); return; }
    btn.disabled = true; btn.textContent = "Saving…";
    try {
      await api.updateInstance(instanceId, {
        config: {
          "thickness.target_mm": t,
          "thickness.lower_limit_mm": mn,
          "thickness.upper_limit_mm": mx,
        },
      });
      toast("Limits saved");
      refresh();
    } catch (err) {
      toast(err.message);
    } finally {
      btn.disabled = false; btn.textContent = "Save";
    }
  });

  const deviceBtn = document.getElementById("device-btn");
  const menu = document.getElementById("device-menu");
  const deviceSwitch = document.getElementById("device-switch");

  deviceBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    menu.classList.toggle("open");
  });
  document.addEventListener("click", (event) => {
    if (!deviceSwitch.contains(event.target)) menu.classList.remove("open");
  });

  document.getElementById("ed-cancel").addEventListener("click", () =>
    document.getElementById("edit-modal").classList.add("hidden"));
  document.getElementById("add-cancel").addEventListener("click", () =>
    document.getElementById("add-modal").classList.add("hidden"));
  document.getElementById("settings-close").addEventListener("click", () =>
    document.getElementById("settings-modal").classList.add("hidden"));
  document.getElementById("settings-btn").addEventListener("click", openSettings);
  document.getElementById("graph-type").addEventListener("change", async (event) => {
    try {
      await api.updateInstance(instanceId, { config: { "ui.graph_type": event.target.value } });
      toast("Graph updated");
      refresh();
    } catch (err) { toast(err.message); }
  });

  let calR1 = { t: "", d: "" };
  let calR2 = { t: "", d: "" };

  function openCalModal() {
    document.getElementById("cal-r1t").value = calR1.t;
    document.getElementById("cal-r2t").value = calR2.t;
    document.getElementById("cal-r1d").value = calR1.d;
    document.getElementById("cal-r2d").value = calR2.d;
    document.getElementById("cal-modal").classList.remove("hidden");
  }

  async function readCal(idx) {
    const btn = idx === 1 ? document.getElementById("cal-r1-read") : document.getElementById("cal-r2-read");
    const inp = idx === 1 ? document.getElementById("cal-r1d") : document.getElementById("cal-r2d");
    btn.disabled = true;
    try {
      const d = await api.deviceDisplacement(activeDevice);
      const val = d && d["displacement.value_mm"];
      if (val == null) { toast(`No live displacement for ${activeDevice}`); return; }
      inp.value = Number(val).toFixed(3);
    } catch (err) { toast(err.message); }
    finally { btn.disabled = false; }
  }

  document.getElementById("cal-open").addEventListener("click", openCalModal);
  document.getElementById("cal-cancel").addEventListener("click", () =>
    document.getElementById("cal-modal").classList.add("hidden"));
  document.getElementById("cal-r1-read").addEventListener("click", () => readCal(1));
  document.getElementById("cal-r2-read").addEventListener("click", () => readCal(2));
  document.getElementById("cal-reset").addEventListener("click", async () => {
    try {
      await api.updateInstance(instanceId, {
        config: {
          "calibration.scale": -1,
          "calibration.offset": 10,
          "calibration.calibrated_at": null,
          "calibration.calibrated_by": null,
        },
      });
      calR1 = { t: "", d: "" };
      calR2 = { t: "", d: "" };
      toast("Calibration reset to defaults");
      refresh();
    } catch (err) { toast(err.message); }
  });
  document.getElementById("cal-save").addEventListener("click", async () => {
    const t1 = parseFloat(document.getElementById("cal-r1t").value);
    const d1 = parseFloat(document.getElementById("cal-r1d").value);
    const t2 = parseFloat(document.getElementById("cal-r2t").value);
    const d2 = parseFloat(document.getElementById("cal-r2d").value);
    if (![t1, d1, t2, d2].every((v) => Number.isFinite(v))) { toast("Enter both known thicknesses and both displacements"); return; }
    try {
      await api.calibrate(instanceId, {
        reference_1_thickness: t1,
        reference_1_displacement: d1,
        reference_2_thickness: t2,
        reference_2_displacement: d2,
      });
      calR1 = { t: String(t1), d: String(d1) };
      calR2 = { t: String(t2), d: String(d2) };
      toast("Calibration saved");
      document.getElementById("cal-modal").classList.add("hidden");
      refresh();
    } catch (err) { toast(err.message); }
  });

  document.getElementById("ed-save").addEventListener("click", async () => {
    const name = document.getElementById("ed-name").value.trim();
    const ip = document.getElementById("ed-ip").value.trim();
    try {
      await api.updateDevice(activeDevice, { name, ip });
      toast("Device updated");
      document.getElementById("edit-modal").classList.add("hidden");
      refresh();
    } catch (err) { toast(err.message); }
  });

  function buildDeviceMenu() {
    menu.innerHTML = devices.map((d) => `
      <button type="button" class="device-item ${d.device_id === activeDevice ? "sel" : ""}" data-id="${esc(d.device_id)}">
        <span class="s-dot ${d.online ? "on" : "off"}"></span>
        <span class="di-main">
          <span class="di-name">${esc(d.name)}</span>
          <span class="di-sub">${esc(d.model || "")}${d.ip ? " · " + esc(d.ip) : ""}</span>
        </span>
      </button>`).join("")
      + `<button type="button" class="device-item action" id="menu-add">+ Add device</button>
         <button type="button" class="device-item action" id="menu-config">Configure</button>`;
    menu.querySelectorAll(".device-item[data-id]").forEach((el) => {
      el.addEventListener("click", () => {
        activeDevice = el.dataset.id;
        sessionStorage.setItem(DEVICE_KEY, activeDevice);
        menu.classList.remove("open");
        refresh();
      });
    });
    document.getElementById("menu-add").addEventListener("click", openAdd);
    document.getElementById("menu-config").addEventListener("click", openConfig);
  }

  async function openAdd() {
    const modal = document.getElementById("add-modal");
    const list = document.getElementById("add-list");
    list.innerHTML = `<div class="spinner">loading…</div>`;
    modal.classList.remove("hidden");
    try {
      const data = await api.instanceDevices(instanceId);
      const avail = data.available || [];
      if (!avail.length) { list.innerHTML = `<div class="empty">No other CD22-35 devices available. Register them in "New solution".</div>`; return; }
      list.innerHTML = avail.map((d) => `
        <button type="button" class="device-item-add" data-id="${esc(d.device_id)}">
          <span class="s-dot ${d.online ? "on" : "off"}"></span>
          <span class="di-main">
            <span class="di-name">${esc(d.name)}</span>
            <span class="di-sub">${esc(d.model || "")}${d.ip ? " · " + esc(d.ip) : ""}</span>
          </span>
        </button>`).join("");
      list.querySelectorAll(".device-item-add").forEach((el) => {
        el.addEventListener("click", async () => {
          try {
            await api.addInstanceDevice(instanceId, el.dataset.id);
            toast("Device added");
            modal.classList.add("hidden");
            refresh();
          } catch (err) { toast(err.message); }
        });
      });
    } catch (err) {
      list.innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
    }
  }

  function openConfig() {
    const sel = devices.find((d) => d.device_id === activeDevice);
    document.getElementById("ed-name").value = sel ? sel.name : activeDevice;
    document.getElementById("ed-ip").value = (sel && sel.ip) || "";
    document.getElementById("edit-modal").classList.remove("hidden");
  }

  function openSettings() {
    const list = document.getElementById("settings-list");
    list.innerHTML = SETTINGS.map(([key, label]) => {
      const on = uiState[key] !== false;
      return `<label class="setting-row">
        <span>${esc(label)}</span>
        <input type="checkbox" class="setting-toggle" data-key="ui.${key}" ${on ? "checked" : ""} />
      </label>`;
    }).join("");
    list.querySelectorAll(".setting-toggle").forEach((el) => {
      el.addEventListener("change", async () => {
        try {
          await api.updateInstance(instanceId, { config: { [el.dataset.key]: el.checked ? "1" : "0" } });
          toast("Settings saved");
          refresh();
        } catch (err) {
          toast(err.message);
          el.checked = !el.checked;
        }
      });
    });
    document.getElementById("settings-modal").classList.remove("hidden");
  }

  function colorOf(v, status) {
    if (status === "FAIL") return "bad";
    if (status === "PASS") return "good";
    return "muted";
  }

  function thicknessChart(trend, upper, target, lower) {
    const pts = (trend || []).filter((p) => p["thickness.actual_mm"] != null);
    if (pts.length < 2) return `<div class="empty">waiting for measurements…</div>`;
    const w = 720, h = 200, pad = 8;
    const values = pts.map((p) => p["thickness.actual_mm"]);
    const bounds = [...values, upper, target, lower].filter((v) => v != null);
    let min = Math.min(...bounds), max = Math.max(...bounds);
    if (max - min < 1e-6) { max = min + 1; }
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

  function deviationChart(trend, devLow, devHigh) {
    const pts = (trend || []).filter((p) => p["thickness.deviation_mm"] != null);
    if (pts.length < 2) return `<div class="empty">waiting for measurements…</div>`;
    const w = 720, h = 200, pad = 8;
    const values = pts.map((p) => p["thickness.deviation_mm"]);
    const bounds = [...values, 0, devLow, devHigh].filter((v) => v != null);
    let min = Math.min(...bounds), max = Math.max(...bounds);
    if (max - min < 1e-6) { max = min + 1; }
    const span = max - min;
    const X = (i) => (i / (pts.length - 1)) * w;
    const Y = (v) => h - ((v - min) / span) * (h - 12) - 6;
    const path = pts.map((p, i) => `${X(i).toFixed(1)},${Y(p["thickness.deviation_mm"]).toFixed(1)}`).join(" ");
    const line = (v, color) => v == null ? "" :
      `<line x1="0" y1="${Y(v).toFixed(1)}" x2="${w}" y2="${Y(v).toFixed(1)}" stroke="${color}" stroke-width="1" stroke-dasharray="4 4" />`;
    const inBand = (v) => (devLow == null || v >= devLow) && (devHigh == null || v <= devHigh);
    const points = pts.map((p, i) => inBand(p["thickness.deviation_mm"])
      ? `<circle cx="${X(i).toFixed(1)}" cy="${Y(p["thickness.deviation_mm"]).toFixed(1)}" r="2.5" fill="var(--accent)" />`
      : `<circle cx="${X(i).toFixed(1)}" cy="${Y(p["thickness.deviation_mm"]).toFixed(1)}" r="2.5" fill="var(--bad)" />`).join("");
    return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      ${line(0, "var(--muted)")}${line(devLow, "var(--bad)")}${line(devHigh, "var(--bad)")}
      <polyline fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke" points="${path}" />
      ${points}
    </svg>`;
  }

  function histogramChart(trend, lower, upper, target) {
    const pts = (trend || []).filter((p) => p["thickness.actual_mm"] != null);
    if (pts.length < 2) return `<div class="empty">waiting for measurements…</div>`;
    const w = 720, h = 200, pad = { top: 10, bottom: 30 };
    const vals = pts.map((p) => p["thickness.actual_mm"]);
    const bins = 20;
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi - lo < 1e-9) { lo -= 0.1; hi += 0.1; }
    const span = hi - lo;
    const counts = new Array(bins).fill(0);
    vals.forEach((v) => {
      const b = Math.min(bins - 1, Math.floor(((v - lo) / span) * bins));
      counts[b]++;
    });
    const maxC = Math.max(...counts);
    const bw = w / bins;
    const area = h - pad.top - pad.bottom;
    const bars = counts.map((c, i) => {
      const hh = Math.max(2, (c / maxC) * area);
      const x = i * bw;
      const centre = lo + ((i + 0.5) / bins) * span;
      const inSpec = (lower == null || centre >= lower) && (upper == null || centre <= upper);
      return `<rect x="${x.toFixed(1)}" y="${(h - pad.bottom - hh).toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${hh.toFixed(1)}" fill="${inSpec ? "rgba(22,199,154,.75)" : "rgba(255,93,93,.8)"}" rx="1" />`;
    }).join("");
    const targetX = target == null ? "" : `<line x1="${(((target - lo) / span) * w).toFixed(1)}" y1="${pad.top}" x2="${(((target - lo) / span) * w).toFixed(1)}" y2="${h - pad.bottom}" stroke="var(--muted)" stroke-width="1.5" stroke-dasharray="5 4" />`;
    const labels = `
      <text x="2" y="${h - 8}" fill="#8aa0b8" font-size="10" font-family="var(--mono)">${lo.toFixed(3)}</text>
      <text x="${w - 2}" y="${h - 8}" fill="#8aa0b8" font-size="10" text-anchor="end" font-family="var(--mono)">${hi.toFixed(3)}</text>`;
    return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${bars}${targetX}${labels}</svg>`;
  }

  function barsChart(trend, lower, upper, target) {
    const pts = (trend || []).filter((p) => p["thickness.actual_mm"] != null).slice(-60);
    if (pts.length < 2) return `<div class="empty">waiting for measurements…</div>`;
    const w = 720, h = 200, pad = { top: 10, bottom: 26 };
    const vals = pts.map((p) => p["thickness.actual_mm"]);
    const bounds = [...vals, lower, upper, target].filter((v) => v != null);
    let min = Math.min(...bounds), max = Math.max(...bounds);
    if (max - min < 1e-6) { max = min + 1; }
    const span = max - min;
    const area = h - pad.top - pad.bottom;
    const Y = (v) => (v - min) / span;
    const yy = (v) => pad.top + (1 - Y(v)) * area;
    const bw = w / pts.length;
    const bars = pts.map((p, i) => {
      const ok = p["quality.status"] !== "FAIL";
      const x = i * bw;
      const yTop = yy(p["thickness.actual_mm"]);
      return `<rect x="${(x + 0.5).toFixed(1)}" y="${yTop.toFixed(1)}" width="${(bw - 1.5).toFixed(1)}" height="${(h - pad.bottom - yTop).toFixed(1)}" fill="${ok ? "rgba(22,199,154,.8)" : "rgba(255,93,93,.85)"}" rx="1" />`;
    }).join("");
    const line = (v, color) => v == null ? "" :
      `<line x1="0" y1="${yy(v).toFixed(1)}" x2="${w}" y2="${yy(v).toFixed(1)}" stroke="${color}" stroke-width="1" stroke-dasharray="4 4" />`;
    return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      ${line(lower, "var(--bad)")}${line(upper, "var(--bad)")}${line(target, "var(--muted)")}${bars}
    </svg>`;
  }

  function renderChart(type, data, cfgQ) {
    const lower = cfgQ["thickness.lower_limit_mm"];
    const upper = cfgQ["thickness.upper_limit_mm"];
    const target = cfgQ["thickness.target_mm"];
    if (type === "deviation") {
      const devLow = lower != null && target != null ? lower - target : null;
      const devHigh = upper != null && target != null ? upper - target : null;
      return deviationChart(data.trend, devLow, devHigh);
    }
    if (type === "histogram") return histogramChart(data.trend, lower, upper, target);
    if (type === "bars") return barsChart(data.trend, lower, upper, target);
    return thicknessChart(data.trend, upper, target, lower);
  }

  function render(data) {
    const c = data.current || {};
    const s = data.statistics || {};
    const q = data.quality || {};
    const h = data.device_health || {};
    const cfgQ = data.config || {};
    const status = c["quality.status"];
    const online = h.communication === "ONLINE";
    const badge = status === "PASS" ? "good" : status === "FAIL" ? "bad" : "muted";

    uiState = { ...Object.fromEntries(SETTINGS.map(([k]) => [k, true])), ...(data.ui || {}) };
    const setVis = (id, show) => {
      const el = document.getElementById(id);
      if (el) el.classList.toggle("hidden", !show);
    };
    setVis("device-switch", uiState.show_device_switcher);
    setVis("live-status", uiState.show_live_status);
    setVis("conn-chip", uiState.show_live_status);
    setVis("sec-cal", uiState.show_calibration);
    setVis("sec-limits", uiState.show_limits);
    setVis("sec-trend", uiState.show_trend);
    setVis("sec-history", uiState.show_history);

    devices = data.devices || [];
    activeDevice = data.active_device || activeDevice || instance.device_id || "";
    sessionStorage.setItem(DEVICE_KEY, activeDevice);
    const sel = devices.find((d) => d.device_id === activeDevice);
    document.getElementById("device-name").textContent = sel ? sel.name : activeDevice;
    document.getElementById("device-dot").className = `s-dot ${sel && sel.online ? "on" : "off"}`;
    buildDeviceMenu();

    document.getElementById("run-name").textContent = data.name;
    const badgeEl = document.getElementById("app-status");
    badgeEl.className = `badge ${badge}`;
    badgeEl.textContent = status || data.status;

    const dot = document.getElementById("live-dot");
    dot.className = online ? "live-dot" : "live-dot off";
    document.getElementById("live-dot-text").textContent = online ? "LIVE" : "OFFLINE";

    const chip = document.getElementById("conn-chip");
    chip.className = online ? "chip" : "chip off";
    document.getElementById("conn-text").textContent = online
      ? `Connected · ${sel ? sel.name + " · " : ""}ONLINE · ${h.seconds_since == null ? "just now" : h.seconds_since + "s ago"}`
      : "Disconnected · communications offline";

    const scale = cfgQ["calibration.scale"];
    const offset = cfgQ["calibration.offset"];
    const calibrated = !!cfgQ["calibration.calibrated_at"];
    document.getElementById("cal-status").innerHTML = `
      <div class="cal-status-row">
        <div class="cal-title ${calibrated ? "" : "muted"}">${calibrated ? `Calibrated: ${fmt(cfgQ["thickness.target_mm"])} mm` : "Not Calibrated (0 mm)"}</div>
      </div>
      <div class="cal-chips">
        <span class="mini-chip">target: ${fmt(cfgQ["thickness.target_mm"])} mm</span>
        <span class="mini-chip">thickness = scale × displacement + offset</span>
        <span class="mini-chip">scale ${scale == null ? "—" : fmt(scale, 4)} · offset ${offset == null ? "—" : fmt(offset, 4)}</span>
      </div>`;

    document.getElementById("limit-active").textContent = `Active: ${fmt(cfgQ["thickness.lower_limit_mm"])} → ${fmt(cfgQ["thickness.upper_limit_mm"])} mm`;

    const cards = [];
    if (byType.current_value && uiState.show_current) {
      cards.push(`<div class="stat-card primary">
        <div class="stat-label"><span class="s-dot ${online ? "on" : "off"}"></span>${esc(byType.current_value.title || "Current thickness")}</div>
        <div class="stat-val ${colorOf(c["thickness.actual_mm"], status)}">${fmt(c["thickness.actual_mm"])}<span class="u">mm</span></div>
        <div class="stat-sub"><span class="badge ${badge}">${esc(status || "—")}</span> · ${esc(shortTime(c.ts))}</div>
      </div>`);
    }
    if (byType.target && uiState.show_target) {
      const dev = c["thickness.deviation_mm"];
      cards.push(`<div class="stat-card">
        <div class="stat-label">${esc(byType.target.title || "Target")}</div>
        <div class="stat-val">${fmt(cfgQ["thickness.target_mm"])}<span class="u">mm</span></div>
        <div class="stat-sub">deviation ${dev == null ? "—" : (dev > 0 ? "+" : "") + fmt(dev)} mm</div>
      </div>`);
    }
    if (byType.statistics && uiState.show_statistics) {
      cards.push(`<div class="stat-card">
        <div class="stat-label">${esc(byType.statistics.title || "Statistics")} (${s.samples || 0})</div>
        <div class="stat-sub" style="margin-top:8px;line-height:1.9;font-size:12px">
          avg <b>${fmt(s.average)}</b> · min <b>${fmt(s.minimum)}</b> · max <b>${fmt(s.maximum)}</b><br/>
          std dev <b>${fmt(s.std_dev, 4)}</b> mm
        </div>
      </div>`);
    }
    if (byType.quality && uiState.show_quality) {
      cards.push(`<div class="stat-card">
        <div class="stat-label">${esc(byType.quality.title || "Quality")}</div>
        <div class="stat-val">${q.pass_percentage == null ? "—" : q.pass_percentage + "%"}</div>
        <div class="stat-sub"><span class="badge good">${q.pass_count || 0} pass</span> <span class="badge bad">${q.fail_count || 0} fail</span></div>
      </div>`);
    }
    if (byType.device_health && uiState.show_health) {
      cards.push(`<div class="stat-card">
        <div class="stat-label"><span class="s-dot ${online ? "on" : "off"}"></span>${esc(byType.device_health.title || "Device health")}</div>
        <div class="stat-val" style="font-size:18px">${esc(h.model || "—")}</div>
        <div class="stat-sub"><span class="badge ${online ? "good" : "bad"}">${esc(h.communication || "—")}</span> · ${h.seconds_since == null ? "no data" : h.seconds_since + "s ago"}</div>
      </div>`);
    }
    document.getElementById("stat-cards").innerHTML = cards.join("") || "<div class='stat-card'>No widgets configured</div>";

    const gtype = GRAPHS[uiState.graph_type] ? uiState.graph_type : "line";
    document.getElementById("graph-type").value = gtype;
    document.getElementById("trend-title").textContent = GRAPHS[gtype].title;
    document.getElementById("legend").innerHTML = GRAPHS[gtype].legend;
    document.getElementById("trend-body").innerHTML = renderChart(gtype, data, cfgQ);
    const gv = document.getElementById("graph-val");
    gv.textContent = c["thickness.actual_mm"] == null ? "—" : `${fmt(c["thickness.actual_mm"])} mm`;
    gv.className = `graph-val ${colorOf(c["thickness.actual_mm"], status)}`;

    const trend = [...(data.trend || [])].reverse();
    document.getElementById("history-count").textContent = `${trend.length} records`;
    if (trend.length === 0) {
      document.getElementById("history-body").innerHTML = `<div class="empty">No thickness data yet - waiting for sensor readings...</div>`;
    } else {
      document.getElementById("history-body").innerHTML = `
        <table>
          <thead><tr>
            <th>#</th><th>Timestamp</th>
            <th class="td-r">Thickness (mm)</th>
            <th class="td-r">Deviation (mm)</th>
            <th>Status</th>
          </tr></thead>
          <tbody>
            ${trend.map((r, i) => `
              <tr>
                <td class="td-mono td-dim">${trend.length - i}</td>
                <td class="td-mono td-dim">${esc(shortTime(r.ts))}</td>
                <td class="td-mono td-r ${colorOf(r["thickness.actual_mm"], r["quality.status"])}">${fmt(r["thickness.actual_mm"])}</td>
                <td class="td-mono td-r">${r["thickness.deviation_mm"] == null ? "—" : fmt(r["thickness.deviation_mm"])}</td>
                <td><span class="badge ${r["quality.status"] === "FAIL" ? "bad" : "good"}">${esc(r["quality.status"] || "—")}</span></td>
              </tr>`).join("")}
          </tbody>
        </table>`;
    }
  }

  async function refresh() {
    try { render(await api.dashboard(instanceId, activeDevice || undefined)); } catch { /* transient */ }
  }

  await refresh();
  const timer = setInterval(refresh, 1000);
  setCleanup(() => clearInterval(timer));
}
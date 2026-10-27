import { api } from "../api.js";
import { esc, toast } from "../ui.js";
import { mountBoard } from "./board.js";
import { calibrationPanel } from "./commission.js";

// Last dashboard bundle per instance+device, kept in sessionStorage so a reload
// or a return to the page paints immediately while fresh data is fetched.
const CACHE_PREFIX = "ucap_dash:";
const MAX_CACHE_ENTRIES = 8;

const cacheKey = (instanceId, deviceId) => `${CACHE_PREFIX}${instanceId}:${deviceId || "default"}`;

function readDashboardCache(instanceId, deviceId) {
  if (!deviceId) return null;
  try {
    const raw = sessionStorage.getItem(cacheKey(instanceId, deviceId));
    const entry = raw ? JSON.parse(raw) : null;
    const data = entry && entry.data;
    return data && data.layout && Array.isArray(data.layout.widgets) ? data : null;
  } catch {
    return null;
  }
}

function pruneDashboardCache() {
  const keys = [];
  for (let i = 0; i < sessionStorage.length; i++) {
    const key = sessionStorage.key(i);
    if (key && key.startsWith(CACHE_PREFIX)) keys.push(key);
  }
  while (keys.length > MAX_CACHE_ENTRIES) sessionStorage.removeItem(keys.shift());
}

function writeDashboardCache(instanceId, deviceId, data) {
  if (!deviceId || !data) return;
  try {
    sessionStorage.setItem(cacheKey(instanceId, deviceId), JSON.stringify({ t: Date.now(), data }));
    pruneDashboardCache();
  } catch {
    /* quota exceeded — skip caching */
  }
}

// BLOCK 6 dashboard (Live Run Mode). The widget layout is per instance (saved from the
// graph-options editor, default from the template); every widget renders from the generic
// dashboard bundle. Device switcher lets a company with several sensors flip between them.
export async function renderDashboard(view, ctx, setCleanup, instanceId) {
  let instance;
  try { instance = await api.instance(instanceId); }
  catch (err) { view.innerHTML = `<div class="page narrow"><div class="error-box">${esc(err.message)}</div></div>`; return; }

  const DEVICE_KEY = "ucap_device_" + instanceId;
  let activeDevice = sessionStorage.getItem(DEVICE_KEY) || instance.device_id || "";
  const cached = readDashboardCache(instanceId, activeDevice);
  let devices = [];
  let board = null;
  let layoutKey = "";

  view.innerHTML = `
    <div class="page run wide">
      <div class="run-header">
        <div class="crumbs"><a href="#/apps">Applications</a><span class="sep">/</span><b id="run-name">${esc(instance.name)}</b>
          <span class="sep">·</span><span>${esc(instance.template.name)}</span></div>
        <div class="run-header-row">
          <h1>${esc(instance.name)}</h1>
          <div class="run-meta">
            <div class="device-switch" id="device-switch">
              <button type="button" class="device-btn" id="device-btn">
                <span class="s-dot" id="device-dot"></span><span id="device-name">—</span><span class="caret">▾</span>
              </button>
              <div class="device-menu" id="device-menu"></div>
            </div>
            <span class="run-status" id="live-status">
              <span id="app-status" class="badge muted">${esc(instance.status)}</span>
              <span id="live-dot" class="live-dot off"><span class="dot"></span><span id="live-dot-text">OFFLINE</span></span>
            </span>
            <span class="alert-bell" id="bell" title="Open alerts">🔔<b id="bell-n"></b></span>
          </div>
        </div>
        <div class="toolbar">
          <a class="btn-ghost" href="#/app/${esc(instanceId)}/customize">Customize graphs</a>
          <a class="btn-ghost" href="#/report/${esc(instanceId)}">Shift report</a>
          <button class="btn-ghost" id="export">Export CSV</button>
          <button class="btn-ghost" id="clone">Copy to another line</button>
          <a class="btn-ghost" href="#/setup/${esc(instanceId)}/map">Setup</a>
          <a class="btn-ghost" href="#/history/${esc(instanceId)}">Change history</a>
        </div>
      </div>
      ${instance.status !== "active" ? `<div class="banner">Setup is not finished — this application is <b>${esc(instance.status)}</b>.
        <a href="#/setup/${esc(instanceId)}/activate">Finish setup →</a></div>` : ""}
      <div id="board"></div>
    </div>

    <div class="modal-overlay hidden" id="cal-modal"><div class="modal">
      <div class="modal-title">Calibration</div><div id="cal-host"></div>
      <div class="modal-actions"><button class="btn btn-secondary btn-sm" id="cal-close" type="button">Close</button></div></div></div>

    <div class="modal-overlay hidden" id="export-modal"><div class="modal">
      <div class="modal-title">Export CSV</div>
      <div class="field"><label>From</label><input id="ex-from" type="datetime-local" /></div>
      <div class="field"><label>To</label><input id="ex-to" type="datetime-local" /></div>
      <div class="modal-actions"><button class="btn btn-secondary btn-sm" id="ex-cancel">Cancel</button>
        <button class="btn btn-sm" id="ex-go">Download</button></div></div></div>

    <div class="modal-overlay hidden" id="clone-modal"><div class="modal">
      <div class="modal-title">Copy to another line</div>
      <p class="settings-hint">Creates a new source of the same type with the same mapping, limits, alerts and graphs.
        Calibration is not copied: every sensor is calibrated on its own.</p>
      <div class="field"><label>Application name</label><input id="cl-name" /></div>
      <div class="field"><label>Line / machine</label><input id="cl-asset" placeholder="e.g. Extruder 3" /></div>
      <div class="field"><label>Port (optional, gateway sources)</label><input id="cl-port" placeholder="192.168.1.201:8234" /></div>
      <div class="modal-actions"><button class="btn btn-secondary btn-sm" id="cl-cancel">Cancel</button>
        <button class="btn btn-sm" id="cl-go">Create copy</button></div></div></div>

    <div class="modal-overlay hidden" id="edit-modal"><div class="modal">
      <div class="modal-title">Configure device</div>
      <div class="field"><label>Device name</label><input id="ed-name" type="text" autocomplete="off" /></div>
      <div class="field"><label>IP / host:port</label><input id="ed-ip" type="text" autocomplete="off" placeholder="e.g. 192.168.1.200:8234" /></div>
      <div class="modal-actions"><button class="btn btn-secondary btn-sm" id="ed-cancel" type="button">Cancel</button>
        <button class="btn btn-sm" id="ed-save" type="button">Save</button></div></div></div>

    <div class="modal-overlay hidden" id="add-modal"><div class="modal">
      <div class="modal-title">Add a device</div><div id="add-list"><div class="spinner">loading…</div></div>
      <div class="modal-actions"><button class="btn btn-secondary btn-sm" id="add-cancel" type="button">Close</button></div></div></div>`;

  const $ = (id) => document.getElementById(id);
  const show = (id) => $(id).classList.remove("hidden");
  const hide = (id) => $(id).classList.add("hidden");
  let stopCal = null;

  const handlers = {
    recalibrate: () => {
      show("cal-modal");
      stopCal = calibrationPanel($("cal-host"), instanceId, activeDevice, () => refresh());
    },
    "save-limits": async (btn) => {
      const config = {};
      btn.closest(".w-body").querySelectorAll("[data-limit]").forEach((el) => { config[el.dataset.limit] = el.value === "" ? null : Number(el.value); });
      try { await api.updateInstance(instanceId, { config }); toast("Limits saved"); board.redraw("limits_editor"); refresh(); }
      catch (err) { toast(err.message); }
    },
    ack: async (eventId) => { try { await api.ackAlert(instanceId, eventId); refresh(); } catch (err) { toast(err.message); } },
  };

  $("cal-close").addEventListener("click", () => { hide("cal-modal"); if (stopCal) stopCal(); stopCal = null; });
  $("bell").addEventListener("click", () => {
    const el = view.querySelector('.widget[data-type="alerts_list"]');
    if (el) el.scrollIntoView({ behavior: "smooth" });
    else toast("Add the Alerts widget via Customize graphs");
  });

  // export
  const pad = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  $("export").addEventListener("click", () => {
    $("ex-to").value = pad(new Date()); $("ex-from").value = pad(new Date(Date.now() - 24 * 3600e3)); show("export-modal");
  });
  $("ex-cancel").addEventListener("click", () => hide("export-modal"));
  $("ex-go").addEventListener("click", async () => {
    try {
      const res = await api.exportCsv(instanceId, { start: new Date($("ex-from").value).toISOString(),
        end: new Date($("ex-to").value).toISOString(), device_id: activeDevice });
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = `${instanceId}.csv`; a.click();
      URL.revokeObjectURL(a.href);
      hide("export-modal");
    } catch (err) { toast(err.message); }
  });

  // clone
  $("clone").addEventListener("click", () => { $("cl-name").value = instance.name.replace(/\d+/, (n) => String(Number(n) + 1)); show("clone-modal"); });
  $("cl-cancel").addEventListener("click", () => hide("clone-modal"));
  $("cl-go").addEventListener("click", async () => {
    try {
      const c = await api.clone(instanceId, { name: $("cl-name").value.trim() || instance.name + " (copy)",
        asset_id: $("cl-asset").value.trim() || null, port: $("cl-port").value.trim() || null });
      toast("Copied. Calibrate the new sensor to go live.");
      hide("clone-modal");
      ctx.navigate(`#/setup/${c.instance_id}/commission`);
    } catch (err) { toast(err.message); }
  });

  // device switcher (from the earlier run mode)
  const menu = $("device-menu");
  $("device-btn").addEventListener("click", (e) => { e.stopPropagation(); menu.classList.toggle("open"); });
  const closeMenu = (e) => { if (!$("device-switch")?.contains(e.target)) menu.classList.remove("open"); };
  document.addEventListener("click", closeMenu);
  $("ed-cancel").addEventListener("click", () => hide("edit-modal"));
  $("add-cancel").addEventListener("click", () => hide("add-modal"));
  $("ed-save").addEventListener("click", async () => {
    try { await api.updateDevice(activeDevice, { name: $("ed-name").value.trim(), ip: $("ed-ip").value.trim() });
      toast("Device updated"); hide("edit-modal"); refresh(); }
    catch (err) { toast(err.message); }
  });

  function buildDeviceMenu() {
    menu.innerHTML = devices.map((d) => `
      <button type="button" class="device-item ${d.device_id === activeDevice ? "sel" : ""}" data-id="${esc(d.device_id)}">
        <span class="s-dot ${d.online ? "on" : "off"}"></span>
        <span class="di-main"><span class="di-name">${esc(d.name)}</span>
          <span class="di-sub">${esc(d.model || "")}${d.ip ? " · " + esc(d.ip) : ""}</span></span></button>`).join("")
      + `<button type="button" class="device-item action" id="menu-add">+ Add device</button>
         <button type="button" class="device-item action" id="menu-config">Configure</button>`;
    menu.querySelectorAll(".device-item[data-id]").forEach((el) => el.addEventListener("click", () => {
      activeDevice = el.dataset.id; sessionStorage.setItem(DEVICE_KEY, activeDevice); menu.classList.remove("open");
      const snapshot = readDashboardCache(instanceId, activeDevice);
      if (snapshot) render(snapshot);
      refresh();
    }));
    $("menu-add").addEventListener("click", openAdd);
    $("menu-config").addEventListener("click", () => {
      const sel = devices.find((d) => d.device_id === activeDevice);
      $("ed-name").value = sel ? sel.name : activeDevice; $("ed-ip").value = (sel && sel.ip) || ""; show("edit-modal");
    });
  }

  async function openAdd() {
    const list = $("add-list");
    list.innerHTML = `<div class="spinner">loading…</div>`;
    show("add-modal");
    try {
      const avail = (await api.instanceDevices(instanceId)).available || [];
      if (!avail.length) { list.innerHTML = `<div class="empty">No other devices of this model. Register them via New solution.</div>`; return; }
      list.innerHTML = avail.map((d) => `<button type="button" class="device-item-add" data-id="${esc(d.device_id)}">
        <span class="s-dot ${d.online ? "on" : "off"}"></span><span class="di-main"><span class="di-name">${esc(d.name)}</span>
        <span class="di-sub">${esc((d.model && d.model.model_name) || "")}</span></span></button>`).join("");
      list.querySelectorAll(".device-item-add").forEach((el) => el.addEventListener("click", async () => {
        try { await api.addInstanceDevice(instanceId, el.dataset.id); toast("Device added"); hide("add-modal"); refresh(); }
        catch (err) { toast(err.message); }
      }));
    } catch (err) { list.innerHTML = `<div class="error-box">${esc(err.message)}</div>`; }
  }

  let lastData = null;
  let lastCacheWrite = 0;
  function render(data) {
    lastData = data;
    const key = JSON.stringify(data.layout.widgets.map((w) => [w.id, w.type, w.x, w.y, w.w, w.h]));
    if (!board || key !== layoutKey) {
      if (board) board.destroy();
      board = mountBoard($("board"), data.layout.widgets, { handlers });
      layoutKey = key;
    }
    board.update(data);

    devices = data.devices || [];
    activeDevice = data.active_device || activeDevice;
    sessionStorage.setItem(DEVICE_KEY, activeDevice);
    const now = Date.now();
    if (now - lastCacheWrite > 3000) {
      lastCacheWrite = now;
      writeDashboardCache(instanceId, activeDevice, data);
    }
    const sel = devices.find((d) => d.device_id === activeDevice);
    $("device-name").textContent = sel ? sel.name : activeDevice;
    $("device-dot").className = `s-dot ${sel && sel.online ? "on" : "off"}`;
    if (!menu.classList.contains("open")) buildDeviceMenu();

    const online = data.device_health && data.device_health.communication === "ONLINE";
    $("live-dot").className = online ? "live-dot" : "live-dot off";
    $("live-dot-text").textContent = online ? "LIVE" : "OFFLINE";
    const st = data.current.status;
    $("app-status").className = `badge ${st === "PASS" ? "good" : st === "FAIL" ? "bad" : "muted"}`;
    $("app-status").textContent = st || data.status;
    const open = (data.alerts || []).filter((a) => !a.resolved_at && !a.ack_at).length;
    $("bell-n").textContent = open ? open : "";
    $("bell").classList.toggle("on", open > 0);
  }

  let disposed = false;
  let refreshing = false;
  let pending = false;
  let lastRefreshAt = 0;
  let refreshTimer = null;
  let fallbackTimer = null;

  async function refresh() {
    if (disposed) return;
    if (refreshing) { pending = true; return; }
    refreshing = true;
    try {
      const data = await api.dashboard(instanceId, activeDevice || undefined);
      if (!disposed) render(data);
    } catch (err) {
      if (!disposed && !board) $("board").innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
    } finally {
      refreshing = false;
      lastRefreshAt = Date.now();
      if (pending) { pending = false; scheduleRefresh(); }
    }
  }

  // Coalesce bursts of live events: one refresh at most every 500 ms.
  function scheduleRefresh() {
    if (disposed || refreshTimer) return;
    const wait = Math.max(0, 500 - (Date.now() - lastRefreshAt));
    refreshTimer = setTimeout(() => { refreshTimer = null; refresh(); }, wait);
  }

  if (cached) render(cached);
  await refresh();
  const scheduleFallback = () => {
    if (disposed || fallbackTimer) return;
    fallbackTimer = setTimeout(async () => {
      fallbackTimer = null;
      await refresh();
      scheduleFallback();
    }, 5000);
  };
  const stopLive = api.liveUpdates(instanceId, () => {
    if (fallbackTimer) { clearTimeout(fallbackTimer); fallbackTimer = null; }
    scheduleRefresh();
  }, scheduleFallback);
  scheduleFallback();
  setCleanup(() => {
    disposed = true;
    if (refreshTimer) clearTimeout(refreshTimer);
    if (fallbackTimer) clearTimeout(fallbackTimer);
    if (lastData) writeDashboardCache(instanceId, activeDevice, lastData);
    stopLive();
    document.removeEventListener("click", closeMenu);
    if (stopCal) stopCal();
    if (board) board.destroy();
  });
}

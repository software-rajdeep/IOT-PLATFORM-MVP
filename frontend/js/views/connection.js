import { api } from "../api.js";
import { store } from "../store.js";
import { esc, toast } from "../ui.js";
import { stepper } from "./stepper.js";

// Step 2 — asset + how the data reaches us, then test the source.
// Four source types; the connector only knows transport. What the fields MEAN is decided in
// step 3 (mapping), because we never know a customer's schema up front.
const SOURCES = [
  { key: "gateway", title: "Rajdeep gateway", sub: "Our sensor wired over RS-485 (serial port or serial-to-Ethernet). Auto-mapped." },
  { key: "api", title: "Customer API (pull)", sub: "They give an API URL + key + secret. We poll it and map their JSON." },
  { key: "db", title: "Customer database (pull)", sub: "Read-only DB access. Pick the table; we read the newest row." },
  { key: "push", title: "HTTP push", sub: "Their system posts JSON to a URL we issue, with a device key." },
];

export async function renderConnection(view, ctx, setCleanup) {
  const sel = store.selection();
  if (!sel.model || !sel.template) { ctx.navigate("#/catalog"); return; }
  const { model, template } = sel;
  let source = "gateway";

  view.innerHTML = `
    <div class="page">
      ${stepper("connect")}
      <h1>Asset & data source</h1>
      <p class="lead"><b>${esc(model.manufacturer || "")} ${esc(model.model_name)}</b> · ${esc(template.name)}.
        Where is it installed, and how does its data reach us?</p>

      <div class="two-col">
        <div>
          <div class="panel">
            <h3>Asset</h3>
            <div class="field"><label>Application name</label><input id="iname" value="${esc(sel.instance_name || template.name)}" /></div>
            <div class="grid cols-2" style="gap:10px">
              <div class="field"><label>Plant</label><input id="plant" list="plant-list" placeholder="pick or type a new plant" /><datalist id="plant-list"></datalist></div>
              <div class="field"><label>Line / machine</label><input id="asset" placeholder="e.g. Extruder 2" /></div>
            </div>
          </div>

          <div class="panel">
            <h3>Data source</h3>
            <div class="source-grid">${SOURCES.map((s) => `
              <button type="button" class="source-card ${s.key === source ? "selected" : ""}" data-src="${s.key}">
                <b>${esc(s.title)}</b><span>${esc(s.sub)}</span></button>`).join("")}</div>

            <div id="src-gateway" class="src-fields">
              <div class="grid cols-2" style="gap:10px">
                <div class="field"><label>Gateway ID</label><input id="gateway" value="EDGE001" /></div>
                <div class="field"><label>RS-485 address</label><input id="address" type="number" value="1" /></div>
              </div>
              <div class="field"><label>Port</label><input id="port" placeholder="COM3, /dev/ttyUSB0 or 192.168.1.200:8234" />
                <div class="hint">Serial port on the gateway, or host:port of a serial-to-Ethernet converter.</div></div>
              <div class="field inline"><input type="checkbox" id="simulate" checked /><label for="simulate">Simulate the sensor (no hardware connected)</label></div>
            </div>

            <div id="src-api" class="src-fields hidden">
              <div class="field"><label>API URL</label><input id="api-url" placeholder="https://customer.example.com/api/latest" /></div>
              <div class="grid cols-2" style="gap:10px">
                <div class="field"><label>API key</label><input id="api-key" /></div>
                <div class="field"><label>Secret key</label><input id="api-secret" type="password" /></div>
              </div>
              <div class="field"><label>Poll every (ms)</label><input id="api-poll" type="number" value="1000" /></div>
              <button type="button" class="btn-ghost" id="demo-fill">Use the demo customer's line API</button>
              <button class="btn secondary btn-sm" id="api-discover" type="button">Discover tables</button>
              <div id="api-discover-result" class="hint" style="margin:8px 0"></div>
              <div id="api-table-wrap" class="hidden">
                <div class="field"><label>Table exposed by the API</label><select id="api-table"></select></div>
                <div id="api-cols" style="margin-top:8px"></div>
              </div>
            </div>

            <div id="src-db" class="src-fields hidden">
              <div class="grid cols-2" style="gap:10px">
                <div class="field"><label>Host</label><input id="db-host" placeholder="db.customer.local" /></div>
                <div class="field"><label>Port</label><input id="db-port" type="number" value="5432" /></div>
                <div class="field"><label>Database</label><input id="db-name" /></div>
                <div class="field"><label>Username</label><input id="db-user" /></div>
              </div>
              <div class="field"><label>Password</label><input id="db-pass" type="password" /></div>
              <button class="btn secondary btn-sm" id="db-discover" type="button">Discover tables</button>
              <div id="discover-result" class="hint" style="margin:8px 0"></div>
              <div id="db-table-wrap" class="hidden">
                <div class="field"><label>Table</label><select id="db-table"></select></div>
                <div class="field"><label>Timestamp column (to read the newest row)</label><select id="db-order"></select></div>
                <div id="db-cols" style="margin-top:8px"></div>
              </div>
              <div class="field"><label>Poll every (ms)</label><input id="db-poll" type="number" value="1000" /></div>
            </div>

            <div id="src-push" class="src-fields hidden">
              <p class="hint">We create the device and issue a URL + key. Their system posts any JSON; you map it in the next step.</p>
            </div>

            <button class="btn" id="create-device" style="margin-top:6px">Create source &amp; test</button>
          </div>
        </div>
        <div class="panel" id="test-panel"><h3>Source test</h3><div class="empty">not tested yet</div></div>
      </div>
    </div>`;

  const $ = (id) => document.getElementById(id);
  const testPanel = $("test-panel");
  api.plants().then((d) => {
    const plants = d.plants || [];
    $("plant-list").innerHTML = plants.map((p) => `<option value="${esc(p.plant_name)}"></option>`).join("");
    if (plants.length && !$("plant").value) $("plant").value = plants[0].plant_name;
  }).catch(() => {});
  let device = null;
  let tables = [];
  let apiTables = [];
  let discoverResult = null;
  function invalidateSource() {
    device = null;
    testPanel.innerHTML = `<h3>Source test</h3><div class="empty">Create and test the updated source before continuing.</div>`;
    $("create-device").textContent = "Create source & test";
  }
  view.querySelectorAll(".src-fields input, .src-fields select").forEach((el) => {
    el.addEventListener("input", invalidateSource);
    el.addEventListener("change", invalidateSource);
  });

  view.querySelectorAll(".source-card").forEach((el) => el.addEventListener("click", () => {
    if (source !== el.dataset.src) invalidateSource();
    source = el.dataset.src;
    view.querySelectorAll(".source-card").forEach((c) => c.classList.toggle("selected", c === el));
    SOURCES.forEach((s) => $(`src-${s.key}`).classList.toggle("hidden", s.key !== source));
  }));

  $("demo-fill").addEventListener("click", () => {
    invalidateSource();
    $("api-url").value = `${location.origin}/api/v1/demo-customer/api/v2/latest?line=EXT-07`;
    $("api-key").value = "demo-customer-key";
    $("api-secret").value = "demo-customer-secret";
  });

  function fillSelect(id, options) {
    $(id).innerHTML = `<option value="">— select —</option>` + options.map((o) => `<option value="${esc(o)}">${esc(o)}</option>`).join("");
  }

  function dbConnection() {
    return {
      mode: "internal", host: $("db-host").value.trim() || null, port: parseInt($("db-port").value, 10) || null,
      database: $("db-name").value.trim() || null, username: $("db-user").value.trim() || null,
      password: $("db-pass").value || null,
    };
  }

  $("db-discover").addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    btn.disabled = true; btn.textContent = "Discovering…";
    try {
      discoverResult = await api.discoverSource({
        connection: { interface: "database_api", database: dbConnection() },
        template_id: template.template_id,
      });
      tables = discoverResult.tables || [];
      fillSelect("db-table", tables.map((t) => t.table));
      $("db-table-wrap").classList.toggle("hidden", !tables.length);
      $("db-cols").innerHTML = "";
      const app = discoverResult.application ? ` for ${esc(discoverResult.application.name)}` : "";
      const hidden = discoverResult.hidden_tables ? ` · ${discoverResult.hidden_tables} unrelated hidden` : "";
      $("discover-result").innerHTML = tables.length
        ? `${tables.length} table(s) related to your application${app}${esc(hidden)}.`
        : `${esc(discoverResult.note || "No tables found.")}`;
      if (tables.length) { $("db-table").value = tables[0].table; $("db-table").dispatchEvent(new Event("change")); }
    } catch (err) {
      $("discover-result").textContent = err.message;
    } finally { btn.disabled = false; btn.textContent = "Discover tables"; }
  });
  $("db-table").addEventListener("change", (e) => {
    const t = tables.find((x) => x.table === e.target.value);
    const cols = t ? t.columns : [];
    fillSelect("db-order", cols.map((c) => c.column));
    const guess = cols.find((c) => c.category === "datetime") || cols.find((c) => /time|date|ts/i.test(c.column));
    if (guess) $("db-order").value = guess.column;
    $("db-cols").innerHTML = t ? `
      <div class="hint">Columns related to this application — these are the ones you will map next:</div>
      <div class="table-wrap"><table><thead><tr><th>Column</th><th>Type</th><th>Feeds</th></tr></thead><tbody>
        ${cols.map((c) => `<tr><td class="td-mono">${esc(c.column)}</td><td class="td-dim">${esc(c.type)}</td>
          <td>${(c.matches || []).map((m) => `<span class="badge muted">${esc(m)}</span>`).join(" ")
            || `<span class="td-dim">${esc(c.category)}</span>`}</td></tr>`).join("")}
      </tbody></table></div>` : "";
  });

  $("api-discover").addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    if (!$("api-url").value.trim()) { toast("Enter the API URL"); return; }
    btn.disabled = true; btn.textContent = "Discovering…";
    try {
      const res = await api.discoverSource({
        connection: { interface: "database_api", simulate: false,
          database: { mode: "customer", api_url: $("api-url").value.trim(),
            api_key: $("api-key").value.trim(), api_secret: $("api-secret").value } },
        template_id: template.template_id,
      });
      apiTables = res.tables || [];
      fillSelect("api-table", apiTables.map((t) => t.table));
      $("api-table-wrap").classList.toggle("hidden", !apiTables.length);
      $("api-cols").innerHTML = "";
      $("api-discover-result").innerHTML = apiTables.length
        ? `${apiTables.length} table(s) related to this application.`
        : `${esc(res.note || "This API exposes no table listing; its latest response is mapped directly.")}`;
      if (apiTables.length) { $("api-table").value = apiTables[0].table; $("api-table").dispatchEvent(new Event("change")); }
    } catch (err) {
      $("api-discover-result").textContent = err.message;
    } finally { btn.disabled = false; btn.textContent = "Discover tables"; }
  });
  $("api-table").addEventListener("change", (e) => {
    const t = apiTables.find((x) => x.table === e.target.value);
    const cols = t ? t.columns : [];
    $("api-cols").innerHTML = t ? `
      <div class="hint">Columns exposed by this table — these are the ones you will map next:</div>
      <div class="table-wrap"><table><thead><tr><th>Column</th><th>Type</th><th>Feeds</th></tr></thead><tbody>
        ${cols.map((c) => `<tr><td class="td-mono">${esc(c.column)}</td><td class="td-dim">${esc(c.type)}</td>
          <td>${(c.matches || []).map((m) => `<span class="badge muted">${esc(m)}</span>`).join(" ")
            || `<span class="td-dim">${esc(c.category || "")}</span>`}</td></tr>`).join("")}
      </tbody></table></div>` : "";
  });

  function payloadFor() {
    const conn = {};
    if (source === "gateway") {
      Object.assign(conn, { interface: "RS485", address: parseInt($("address").value, 10) || 1,
        simulate: $("simulate").checked, port: $("port").value.trim() || null, poll_interval_ms: 500 });
    } else if (source === "api") {
      Object.assign(conn, { interface: "database_api", simulate: false, poll_interval_ms: parseInt($("api-poll").value, 10) || 1000,
        database: { mode: "customer", api_url: $("api-url").value.trim(), api_key: $("api-key").value.trim(),
          api_secret: $("api-secret").value, table: $("api-table").value || null } });
    } else if (source === "db") {
      Object.assign(conn, { interface: "database_api", simulate: false, poll_interval_ms: parseInt($("db-poll").value, 10) || 1000,
        database: { ...dbConnection(), table: $("db-table").value || null, order_by: $("db-order").value || null } });
    } else {
      Object.assign(conn, { interface: "http_push", simulate: false });
    }
    return {
      model_key: model.model_key, plant_key: $("plant").value.trim() || null,
      asset_id: $("asset").value.trim() || null, gateway_id: source === "gateway" ? $("gateway").value.trim() || null : null,
      connection: conn,
    };
  }

  async function runTest() {
    testPanel.innerHTML = `<h3>Source test</h3><div class="spinner">connecting…</div>`;
    try {
      const r = await api.testDevice(device.device_id);
      let body;
      if (r.interface === "http_push") {
        const info = await api.ingestInfo(device.device_id);
        body = `
          <div class="field"><label>Push URL</label><input readonly value="${esc(info.url)}" /></div>
          <div class="field"><label>Header ${esc(info.header)}</label><input readonly value="${esc(info.ingest_key)}" /></div>
          <div class="field"><label>Example</label><textarea class="code" readonly rows="4">${esc(info.example)}</textarea></div>
          <div class="field"><label>Send a test message (any JSON)</label>
            <textarea class="code" id="push-body" rows="4">{"line": "EXT-08", "thk_disp_mm": 1.49, "recipe": "RUB-EXTR-2"}</textarea></div>
          <button class="btn secondary btn-sm" id="push-send">Send</button>
          <div class="hint" id="push-state">${r.connected ? `Last message ${esc(r.last_message_at)}` : "Waiting for the first message…"}</div>`;
      } else if (r.payload) {
        body = `<div class="stat primary"><div class="k">Connection</div><div class="v" style="font-size:18px">CONNECTED</div></div>
          <div class="field" style="margin-top:12px"><label>Latest record from the source</label>
          <pre class="code">${esc(JSON.stringify(r.payload, null, 2))}</pre></div>`;
      } else {
        body = `<div class="stat-grid" style="grid-template-columns:1fr 1fr">
            <div class="stat ${r.connected ? "primary" : ""}"><div class="k">Connection</div>
              <div class="v" style="font-size:18px">${r.connected ? "CONNECTED" : "FAILED"}</div></div>
            <div class="stat"><div class="k">Displacement</div>
              <div class="v">${r["displacement.value_mm"] ?? "—"}<span class="u">mm</span></div>
              <div class="meta">raw ${r.measurement_raw ?? "—"}</div></div></div>
          ${r.error ? `<div class="error-box">${esc(r.error)}</div>` : ""}`;
      }
      const ok = r.connected || r.interface === "http_push";
      testPanel.innerHTML = `<h3>Source test</h3>${body}
        <button class="btn" id="continue" style="margin-top:14px" ${ok ? "" : "disabled"}>Continue to data mapping →</button>`;
      document.getElementById("continue").addEventListener("click", createInstance);
      const send = document.getElementById("push-send");
      if (send) send.addEventListener("click", async () => {
        const info = await api.ingestInfo(device.device_id);
        try {
          const res = await fetch(`/api/v1/ingest/${encodeURIComponent(device.device_id)}`, {
            method: "POST", headers: { "Content-Type": "application/json", [info.header]: info.ingest_key },
            body: document.getElementById("push-body").value,
          });
          document.getElementById("push-state").textContent = res.ok ? "Received. It will appear as a sample in the mapping step." : `Rejected (${res.status})`;
        } catch (err) { toast(err.message); }
      });
    } catch (err) {
      testPanel.innerHTML = `<h3>Source test</h3><div class="error-box">${esc(err.message)}</div>`;
    }
  }

  async function createInstance() {
    const btn = document.getElementById("continue");
    btn.disabled = true; btn.textContent = "Creating…";
    try {
      const instance = await api.createInstance({
        name: $("iname").value.trim() || template.name, template_id: template.template_id,
        device_id: device.device_id, plant_key: $("plant").value.trim() || null, asset_id: $("asset").value.trim() || null,
      });
      store.setSelection({ device, instance });
      ctx.navigate(`#/setup/${instance.instance_id}/map`);
    } catch (err) {
      toast(err.message);
      btn.disabled = false; btn.textContent = "Continue to data mapping →";
    }
  }

  $("create-device").addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    if (source === "api" && !$("api-url").value.trim()) { toast("Enter the API URL"); return; }
    if (source === "db" && !$("db-table").value) { toast("Discover and pick a table first"); return; }
    btn.disabled = true; btn.textContent = "Creating…";
    try {
      device = await api.createDevice(payloadFor());
      store.setSelection({ device });
      btn.textContent = "Recreate source";
      await runTest();
    } catch (err) {
      toast(err.message);
      btn.textContent = "Create source & test";
    } finally { btn.disabled = false; }
  });
}

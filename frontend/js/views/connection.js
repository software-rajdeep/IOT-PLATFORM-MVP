import { api } from "../api.js";
import { store } from "../store.js";
import { esc, toast } from "../ui.js";

// Step 4 — register the physical device + its connection, then test it.
// Two transports: RS-485 sensor, or a database/API source.
// For a database source we DISCOVER what the customer exposes, then MAP the
// platform's target fields (measurement_raw, timestamp, device_id) onto it —
// because we never know the customer's schema up front.
export async function renderConnection(view, ctx, setCleanup) {
  const sel = store.selection();
  if (!sel.model || !sel.template) { ctx.navigate("#/devices"); return; }
  const { model, template } = sel;

  view.innerHTML = `
    <div class="page narrow">
      <div class="crumbs"><a href="#/templates">Application</a><span class="sep">/</span>
        <b>${esc(template.name)}</b><span class="sep">·</span><b>Step 4</b>
        <span class="sep">/</span><span>Device &amp; connection</span></div>
      <h1>Device connection</h1>
      <p class="lead">${esc(model.manufacturer)} ${esc(model.model_name)}. The connector is customer-agnostic.</p>

      <div class="two-col">
        <div class="panel">
          <h3>Connection</h3>
          <div class="field"><label>Plant</label><input id="plant" value="MUMBAI" /></div>
          <div class="field"><label>Line / asset</label><input id="asset" value="EXTRUSION_LINE_2" /></div>
          <div class="field"><label>Gateway</label><input id="gateway" value="EDGE001" /></div>
          <div class="field"><label>Interface</label>
            <select id="interface">
              <option value="RS485" selected>RS-485 sensor</option>
              <option value="database_api">Database / API source</option>
            </select></div>

          <div id="rs485-fields">
            <div class="field"><label>RS-485 address</label><input id="address" type="number" value="1" /></div>
          </div>

          <div id="db-fields" style="display:none">
            <div class="field"><label>Database source</label>
              <select id="db-mode">
                <option value="internal" selected>Our / direct database</option>
                <option value="customer">Customer server (API key + secret)</option>
              </select></div>

            <div id="db-internal-fields">
              <div class="field"><label>Database host</label><input id="db-host" placeholder="db.ourcompany.com" /></div>
              <div class="field"><label>Port</label><input id="db-port" type="number" value="5432" /></div>
              <div class="field"><label>Database name</label><input id="db-name" /></div>
              <div class="field"><label>Username</label><input id="db-user" /></div>
              <div class="field"><label>Password</label><input id="db-pass" type="password" /></div>
            </div>

            <div id="db-customer-fields" style="display:none">
              <div class="field"><label>API URL</label><input id="db-url" placeholder="https://customer.example.com/api/latest" /></div>
              <div class="field"><label>API key</label><input id="db-apikey" /></div>
              <div class="field"><label>Secret key</label><input id="db-secret" type="password" /></div>
            </div>

            <button class="btn secondary" id="db-discover" type="button">Discover customer data</button>
            <div id="discover-result" class="hint" style="margin:8px 0"></div>

            <div id="db-table-wrap" class="field" style="display:none">
              <label>Table</label><select id="db-table"></select></div>

            <div id="db-mapping" style="display:none">
              <div class="field"><label>measurement_raw &larr; source (required)</label><select id="map-measurement_raw"></select></div>
              <div class="field"><label>timestamp &larr; source (optional)</label><select id="map-timestamp"></select></div>
              <div class="field"><label>device_id &larr; source (optional)</label><select id="map-device_id"></select></div>
            </div>
          </div>

          <div class="field inline"><input type="checkbox" id="simulate" checked /><label for="simulate">Simulate sensor (no hardware)</label></div>
          <button class="btn" id="create-device">Create device &amp; test</button>
        </div>
        <div class="panel" id="test-panel"><h3>Device test</h3><div class="empty">not tested yet</div></div>
      </div>
    </div>`;

  const testPanel = document.getElementById("test-panel");
  let device = null;
  let discoveryTables = [];

  document.getElementById("interface").addEventListener("change", (e) => {
    const isDb = e.target.value === "database_api";
    document.getElementById("rs485-fields").style.display = isDb ? "none" : "";
    document.getElementById("db-fields").style.display = isDb ? "" : "none";
  });

  document.getElementById("db-mode").addEventListener("change", (e) => {
    const isCustomer = e.target.value === "customer";
    document.getElementById("db-internal-fields").style.display = isCustomer ? "none" : "";
    document.getElementById("db-customer-fields").style.display = isCustomer ? "" : "none";
    document.getElementById("db-table-wrap").style.display = "none";
    document.getElementById("db-mapping").style.display = "none";
    document.getElementById("discover-result").textContent = "";
  });

  function populateSelect(id, options) {
    const sel = document.getElementById(id);
    sel.innerHTML = `<option value="">— select —</option>` +
      options.map((o) => `<option value="${esc(o)}">${esc(o)}</option>`).join("");
  }

  function sourceConnection() {
    const mode = document.getElementById("db-mode").value;
    const db = { mode };
    if (mode === "customer") {
      db.api_url = document.getElementById("db-url").value.trim() || null;
      db.api_key = document.getElementById("db-apikey").value.trim() || null;
      db.api_secret = document.getElementById("db-secret").value || null;
    } else {
      db.host = document.getElementById("db-host").value.trim() || null;
      db.port = parseInt(document.getElementById("db-port").value, 10) || null;
      db.database = document.getElementById("db-name").value.trim() || null;
      db.username = document.getElementById("db-user").value.trim() || null;
      db.password = document.getElementById("db-pass").value || null;
    }
    return db;
  }

  function showMappingFor(columns) {
    ["measurement_raw", "timestamp", "device_id"].forEach((f) => populateSelect(`map-${f}`, columns));
    document.getElementById("db-mapping").style.display = "";
  }

  document.getElementById("db-table").addEventListener("change", (e) => {
    const found = discoveryTables.find((t) => t.table === e.target.value);
    showMappingFor(found ? found.columns.map((c) => c.column) : []);
  });

  document.getElementById("db-discover").addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    const out = document.getElementById("discover-result");
    btn.disabled = true; btn.textContent = "Discovering…"; out.textContent = "";
    try {
      const result = await api.discoverSource({
        connection: { interface: "database_api", database: sourceConnection() },
      });
      if (result.mode === "customer") {
        const fields = result.fields || [];
        showMappingFor(fields);
        document.getElementById("db-table-wrap").style.display = "none";
        out.textContent = `Found ${fields.length} field(s) in the API response.`;
      } else {
        discoveryTables = result.tables || [];
        populateSelect("db-table", discoveryTables.map((t) => t.table));
        document.getElementById("db-table-wrap").style.display = "";
        document.getElementById("db-mapping").style.display = "none";
        out.textContent = `Found ${discoveryTables.length} table(s). Pick a table, then map the target fields.`;
      }
    } catch (err) {
      out.textContent = err.message;
    } finally {
      btn.disabled = false; btn.textContent = "Discover customer data";
    }
  });

  function readMapping() {
    const m = {};
    for (const target of ["measurement_raw", "timestamp", "device_id"]) {
      const value = document.getElementById(`map-${target}`).value;
      if (value) m[target] = value;
    }
    return m;
  }

  function formData() {
    const conn = {
      interface: document.getElementById("interface").value,
      simulate: document.getElementById("simulate").checked,
    };
    if (conn.interface === "database_api") {
      const mode = document.getElementById("db-mode").value;
      const db = sourceConnection();
      if (mode === "internal") {
        db.table = document.getElementById("db-table").value || null;
      }
      db.mapping = readMapping();
      conn.database = db;
    } else {
      conn.address = parseInt(document.getElementById("address").value, 10) || 1;
    }
    return {
      model_key: model.model_key,
      plant_key: document.getElementById("plant").value.trim() || null,
      asset_id: document.getElementById("asset").value.trim() || null,
      gateway_id: document.getElementById("gateway").value.trim() || null,
      connection: conn,
    };
  }

  async function runTest() {
    testPanel.innerHTML = `<h3>Device test</h3><div class="spinner">connecting…</div>`;
    try {
      const result = await api.testDevice(device.device_id);
      testPanel.innerHTML = `<h3>Device test</h3>
        <div class="stat-grid" style="grid-template-columns:1fr 1fr">
          <div class="stat ${result.connected ? "primary" : ""}">
            <div class="k">Connection</div>
            <div class="v" style="font-size:18px">${result.connected ? "CONNECTED" : "FAILED"}</div>
          </div>
          <div class="stat">
            <div class="k">Displacement</div>
            <div class="v">${result["displacement.value_mm"] ?? "—"}<span class="u">mm</span></div>
            <div class="meta">raw ${result.measurement_raw ?? "—"}</div>
          </div>
        </div>
        <button class="btn" id="continue" ${result.connected ? "" : "disabled"}>Continue to calibration →</button>`;
      const cont = document.getElementById("continue");
      if (cont) cont.addEventListener("click", createInstance);
    } catch (err) {
      testPanel.innerHTML = `<h3>Device test</h3><div class="error-box">${esc(err.message)}</div>`;
    }
  }

  async function createInstance() {
    const btn = document.getElementById("continue");
    if (btn) { btn.disabled = true; btn.textContent = "Creating…"; }
    try {
      const instance = await api.createInstance({
        name: sel.instance_name || template.name,
        template_id: template.template_id,
        device_id: device.device_id,
      });
      store.setSelection({ device, instance });
      ctx.navigate(`#/calibrate/${instance.instance_id}`);
    } catch (err) {
      toast(err.message);
      if (btn) { btn.disabled = false; btn.textContent = "Continue to calibration →"; }
    }
  }

  document.getElementById("create-device").addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    btn.disabled = true; btn.textContent = "Creating…";
    try {
      device = await api.createDevice(formData());
      store.setSelection({ device });
      btn.textContent = "Recreate device";
      btn.disabled = false;
      await runTest();
    } catch (err) {
      toast(err.message);
      btn.disabled = false; btn.textContent = "Create device & test";
    }
  });
}
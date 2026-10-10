import { api } from "../api.js";
import { store } from "../store.js";
import { esc, toast } from "../ui.js";

// Step 4 — register the physical device + its RS-485 connection, then test it.
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
      <p class="lead">${esc(model.manufacturer)} ${esc(model.model_name)} over ${esc(model.connector_type)}. The connector is customer-agnostic.</p>

      <div class="two-col">
        <div class="panel">
          <h3>Connection</h3>
          <div class="field"><label>Plant</label><input id="plant" value="MUMBAI" /></div>
          <div class="field"><label>Line / asset</label><input id="asset" value="EXTRUSION_LINE_2" /></div>
          <div class="field"><label>Gateway</label><input id="gateway" value="EDGE001" /></div>
          <div class="field"><label>RS-485 address</label><input id="address" type="number" value="1" /></div>
          <div class="field"><label>Baud rate</label>
            <select id="baud">
              <option>9600</option><option>19200</option><option selected>38400</option><option>57600</option><option>115200</option>
            </select></div>
          <div class="field"><label>Poll interval (ms)</label><input id="poll" type="number" value="100" /></div>
          <div class="field inline"><input type="checkbox" id="simulate" checked /><label for="simulate">Simulate sensor (no hardware)</label></div>
          <button class="btn" id="create-device">Create device &amp; test</button>
        </div>
        <div class="panel" id="test-panel"><h3>Device test</h3><div class="empty">not tested yet</div></div>
      </div>
    </div>`;

  const testPanel = document.getElementById("test-panel");
  let device = null;

  function formData() {
    return {
      model_key: model.model_key,
      plant_key: document.getElementById("plant").value.trim() || null,
      asset_id: document.getElementById("asset").value.trim() || null,
      gateway_id: document.getElementById("gateway").value.trim() || null,
      connection: {
        interface: "RS485",
        baud_rate: parseInt(document.getElementById("baud").value, 10),
        address: parseInt(document.getElementById("address").value, 10) || 1,
        poll_interval_ms: parseInt(document.getElementById("poll").value, 10) || 100,
        simulate: document.getElementById("simulate").checked,
      },
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

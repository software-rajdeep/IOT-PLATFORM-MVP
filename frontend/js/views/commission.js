import { api } from "../api.js";
import { esc, fmt, toast } from "../ui.js";
import { stepper } from "./stepper.js";

// Baseline calibration for side-by-side sensors: capture one reading per sensor
// with a known reference thickness. thickness = reference + (baseline - current),
// plus the reference-sensor drift when a reference column is configured.
function baselinePanel(host, instanceId, onSaved) {
  host.innerHTML = `
    <div class="live-read"><span class="k">Live sensors</span><span class="v" id="bl-live">—</span></div>
    <div id="bl-source" class="hint" style="margin:6px 0"></div>
    <p class="hint" style="margin:8px 0">Put the reference piece (or the production material) under every sensor,
      enter its actual thickness, then capture the baseline.</p>
    <div class="table-wrap"><table class="wtable"><thead><tr><th>Sensor</th><th class="td-r">Live</th>
      <th class="td-r">Baseline</th></tr></thead><tbody id="bl-sensors"></tbody></table></div>
    <div class="cal-read-row" style="margin-top:12px">
      <input id="bl-t" class="limit-input" type="number" step="any" placeholder="reference thickness mm" />
      <button type="button" class="btn btn-sm" id="bl-save">Capture &amp; save calibration</button>
    </div>
    <div id="bl-result" class="hint" style="margin-top:10px"></div>
    <div class="run-actions" style="margin-top:12px">
      <button type="button" class="btn secondary btn-sm" id="bl-reset">Reset calibration</button>
    </div>`;
  const $ = (id) => host.querySelector("#" + id);
  let disposed = false;
  let live = null;
  let prefilled = false;
  let busy = false;

  function renderSensors(data) {
    const rows = (data.sensors || []).map((sensor) => {
      const baseline = data.baselines ? data.baselines[sensor.key] : null;
      const name = `${sensor.label}${sensor.is_reference ? " (reference)" : ""}`;
      return `<tr><td>${esc(name)}<div class="td-mono td-dim">${esc(sensor.key)}</div></td>
        <td class="td-r td-mono">${sensor.value == null ? "—" : Number(sensor.value).toFixed(3)}</td>
        <td class="td-r td-mono">${baseline == null ? "—" : Number(baseline).toFixed(3)}</td></tr>`;
    }).join("");
    $("bl-sensors").innerHTML = rows || `<tr><td colspan="3" class="td-dim">waiting for readings…</td></tr>`;
    $("bl-live").textContent = data.sensors && data.sensors.length ? `${data.sensors.length} sensor(s)` : "no data";
    $("bl-source").textContent = data.source_mode === "replay"
      ? "Old readings are being replayed from the customer database (no live sensor connected)."
      : data.source_mode === "simulated" ? "Using the built-in simulator." : "";
    const ref = data.reference_sensor_key ? ` · reference ${data.reference_sensor_key}` : "";
    $("bl-result").innerHTML = data.calibrated_at
      ? `<span class="badge good">calibrated</span> reference ${esc(data.reference_thickness ?? "—")} mm${esc(ref)}
         <br/>by ${esc(data.calibrated_by || "")} · ${esc(new Date(data.calibrated_at).toLocaleString())}`
      : `<span class="badge warn">not calibrated</span>${esc(ref)}`;
    if (!prefilled && data.reference_thickness != null && data.calibrated_at) {
      $("bl-t").value = data.reference_thickness;
      prefilled = true;
    }
  }

  async function poll() {
    if (disposed) return;
    try {
      const data = await api.calibrationLive(instanceId);
      if (disposed) return;
      live = data;
      renderSensors(data);
    } catch (err) {
      if (!disposed) $("bl-live").textContent = "unavailable";
    }
  }
  poll();
  const timer = setInterval(poll, 1000);

  $("bl-save").addEventListener("click", async () => {
    const thickness = parseFloat($("bl-t").value);
    if (!Number.isFinite(thickness) || thickness < 0) { toast("Enter the reference thickness in mm"); return; }
    if (!live || !(live.sensors || []).length) { toast("No sensor readings yet"); return; }
    busy = true;
    $("bl-save").disabled = true;
    try {
      const data = await api.calibrateBaseline(instanceId, { reference_thickness: thickness });
      const count = Object.keys(data["calibration.baselines"] || {}).length;
      $("bl-result").innerHTML = `<span class="badge good">calibrated</span> ${count} sensor(s) ·
        reference ${fmt(data["calibration.reference_thickness"])} mm`;
      prefilled = true;
      toast("Baseline calibration saved");
      if (onSaved) onSaved(data);
      await poll();
    } catch (err) { toast(err.message); }
    finally { busy = false; $("bl-save").disabled = false; }
  });

  $("bl-reset").addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    try {
      await api.resetCalibration(instanceId);
      $("bl-t").value = "";
      prefilled = false;
      toast("Calibration reset");
      if (onSaved) onSaved(null);
      await poll();
    } catch (err) { toast(err.message); }
    finally { busy = false; }
  });

  return () => { disposed = true; clearInterval(timer); };
}

// Opposite-side sensors: manual gap or auto-gap with a tolerance band.
// thickness = gap - (35 + A) - (35 + B); auto gap = 2*35 + A + B + object.
function oppositePanel(host, instanceId, onSaved) {
  host.innerHTML = `
    <div class="live-read"><span class="k">Live sensors</span><span class="v" id="op-live">—</span></div>
    <div class="table-wrap"><table class="wtable"><thead><tr><th>Sensor</th><th class="td-r">Raw</th>
      <th class="td-r">Distance</th></tr></thead><tbody id="op-sensors"></tbody></table></div>
    <div class="cal-mode-row" style="display:flex;gap:8px;margin-top:12px">
      <button type="button" class="btn btn-sm" id="op-manual">Manual gap</button>
      <button type="button" class="btn secondary btn-sm" id="op-auto">Auto-gap</button>
    </div>
    <div id="op-manual-box" style="margin-top:12px">
      <p class="hint">Enter the distance between the two sensor faces.</p>
      <div class="cal-read-row">
        <input id="op-gap" class="limit-input" type="number" step="any" min="0" placeholder="gap mm" />
        <button type="button" class="btn btn-sm" id="op-save-gap">Set gap</button>
      </div>
    </div>
    <div id="op-auto-box" class="hidden" style="margin-top:12px">
      <p class="hint">Put the known object between the sensors. The gap is derived from the live A and B readings.</p>
      <div class="cal-read-row">
        <input id="op-object" class="limit-input" type="number" step="any" min="0" placeholder="object thickness mm" />
        <input id="op-tol" class="limit-input" type="number" step="any" min="0" placeholder="tolerance ± mm (optional)" />
      </div>
      <div id="op-preview" class="hint"></div>
      <button type="button" class="btn btn-sm" id="op-save-auto" style="margin-top:8px">Capture &amp; save auto-gap</button>
    </div>
    <div id="op-result" class="hint" style="margin-top:10px"></div>
    <div class="run-actions" style="margin-top:12px">
      <button type="button" class="btn secondary btn-sm" id="op-reset">Reset calibration</button>
    </div>`;
  const $ = (id) => host.querySelector("#" + id);
  let disposed = false;
  let live = null;
  let busy = false;
  let autoMode = false;

  function setMode(auto) {
    autoMode = auto;
    $("op-auto-box").classList.toggle("hidden", !auto);
    $("op-manual-box").classList.toggle("hidden", auto);
    $("op-auto").className = auto ? "btn btn-sm" : "btn secondary btn-sm";
    $("op-manual").className = auto ? "btn secondary btn-sm" : "btn btn-sm";
  }

  function renderSensors(data) {
    const rows = (data.sensors || []).map((sensor) => `<tr>
      <td>${esc(sensor.label)}<div class="td-mono td-dim">${esc(sensor.key)}</div></td>
      <td class="td-r td-mono">${sensor.value == null ? "—" : Number(sensor.value).toFixed(3)}</td>
      <td class="td-r td-mono">${sensor.distance == null ? "—" : Number(sensor.distance).toFixed(3)}</td>
    </tr>`).join("");
    $("op-sensors").innerHTML = rows || `<tr><td colspan="3" class="td-dim">waiting for readings…</td></tr>`;
    $("op-live").textContent = data.sensor_count ? `${data.sensor_count} sensor(s)` : "no data";

    const gap = data.gap || {};
    const limits = data.limits || {};
    const range = limits.lower != null || limits.upper != null
      ? `${fmt(limits.lower)} – ${fmt(limits.upper)} mm` : "no limits";
    $("op-result").innerHTML = gap.distance
      ? `<span class="badge good">calibrated</span> gap ${fmt(gap.distance)} mm${gap.object_thickness != null
        ? ` · object ${fmt(gap.object_thickness)} mm` : ""}${gap.tolerance != null
        ? ` · ±${fmt(gap.tolerance)} mm` : ""}<br/>limits ${range}
         ${data.calibrated_by ? `<br/>by ${esc(data.calibrated_by)} · ${esc(new Date(data.calibrated_at).toLocaleString())}` : ""}`
      : `<span class="badge warn">not calibrated</span>${data.sensor_count < 2
        ? " · map both sensor columns to use opposite-side mode" : ""}`;
  }

  async function poll() {
    if (disposed) return;
    try {
      const data = await api.calibrationLive(instanceId);
      if (disposed) return;
      live = data;
      renderSensors(data);
    } catch (err) {
      if (!disposed) $("op-live").textContent = "unavailable";
    }
  }
  poll();
  const timer = setInterval(poll, 1000);

  function updatePreview() {
    const object = parseFloat($("op-object").value);
    const tol = parseFloat($("op-tol").value);
    if (!Number.isFinite(object) || object <= 0) { $("op-preview").textContent = ""; return; }
    if (!Number.isFinite(tol) || tol < 0) {
      $("op-preview").textContent = `target ${object.toFixed(3)} mm · no tolerance band`;
      return;
    }
    $("op-preview").textContent =
      `limits ${(object - tol).toFixed(3)} mm – ${(object + tol).toFixed(3)} mm`;
  }
  $("op-object").addEventListener("input", updatePreview);
  $("op-tol").addEventListener("input", updatePreview);

  $("op-manual").addEventListener("click", () => setMode(false));
  $("op-auto").addEventListener("click", () => setMode(true));
  setMode(false);

  $("op-save-gap").addEventListener("click", async () => {
    const gap = parseFloat($("op-gap").value);
    if (!Number.isFinite(gap) || gap <= 0) { toast("Enter a gap distance greater than zero"); return; }
    busy = true;
    $("op-save-gap").disabled = true;
    try {
      await api.calibrateOpposite(instanceId, { gap_distance: gap });
      toast("Gap calibration saved");
      if (onSaved) onSaved();
      await poll();
    } catch (err) { toast(err.message); }
    finally { busy = false; $("op-save-gap").disabled = false; }
  });

  $("op-save-auto").addEventListener("click", async () => {
    const object = parseFloat($("op-object").value);
    if (!Number.isFinite(object) || object <= 0) { toast("Enter the object thickness greater than zero"); return; }
    if (!live || (live.sensor_count || 0) < 2) { toast("Both sensor readings are required for auto-gap"); return; }
    const tol = parseFloat($("op-tol").value);
    if ($("op-tol").value !== "" && (!Number.isFinite(tol) || tol < 0)) { toast("Enter a valid tolerance"); return; }
    busy = true;
    $("op-save-auto").disabled = true;
    try {
      await api.calibrateOpposite(instanceId, {
        object_thickness: object,
        tolerance_mm: $("op-tol").value === "" ? null : tol,
      });
      toast("Auto-gap calibration saved");
      if (onSaved) onSaved();
      await poll();
    } catch (err) { toast(err.message); }
    finally { busy = false; $("op-save-auto").disabled = false; }
  });

  $("op-reset").addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    try {
      await api.resetCalibration(instanceId);
      $("op-gap").value = "";
      $("op-object").value = "";
      $("op-tol").value = "";
      $("op-preview").textContent = "";
      toast("Calibration reset");
      if (onSaved) onSaved(null);
      await poll();
    } catch (err) { toast(err.message); }
    finally { busy = false; }
  });

  return () => { disposed = true; clearInterval(timer); };
}

// Two-point calibration wizard with a verification piece. Used by onboarding step 4 and
// by the dashboard's calibration widget. thickness = scale x displacement + offset.
export function calibrationPanel(host, instanceId, deviceId, onSaved, options = {}) {
  if (options.mode === "baseline") return baselinePanel(host, instanceId, onSaved);
  if (options.mode === "gap" || options.mode === "auto_gap" || options.mode === "opposite") {
    return oppositePanel(host, instanceId, onSaved);
  }
  host.innerHTML = `
    <div class="live-read"><span class="k">Live displacement</span><span class="v" id="cw-live">—</span><span class="u">mm</span></div>
    <ol class="cal-steps">
      <li><b>Reference piece 1.</b> Put a piece of known thickness under the sensor, enter its thickness, press Capture.
        <div class="cal-read-row"><input id="cw-t1" class="limit-input" type="number" step="any" placeholder="mm" />
          <input id="cw-d1" class="limit-input" type="number" step="any" placeholder="displacement" />
          <button type="button" class="btn secondary btn-sm" data-cap="cw-d1">Capture</button></div></li>
      <li><b>Reference piece 2.</b> A different known thickness (bigger spread = better accuracy).
        <div class="cal-read-row"><input id="cw-t2" class="limit-input" type="number" step="any" placeholder="mm" />
          <input id="cw-d2" class="limit-input" type="number" step="any" placeholder="displacement" />
          <button type="button" class="btn secondary btn-sm" data-cap="cw-d2">Capture</button></div></li>
    </ol>
    <button class="btn btn-sm" id="cw-save">Calculate &amp; save calibration</button>
    <div id="cw-result" class="hint" style="margin-top:10px"></div>
    <div id="cw-verify" class="hidden" style="margin-top:14px">
      <b>Verify with a third piece</b>
      <div class="cal-read-row" style="margin-top:6px"><input id="cw-tv" class="limit-input" type="number" step="any" placeholder="known mm" />
        <button type="button" class="btn secondary btn-sm" id="cw-vbtn">Verify now</button></div>
      <div id="cw-vres" class="hint"></div>
    </div>`;
  const $ = (id) => host.querySelector("#" + id);
  let live = null;
  let disposed = false;

  async function poll() {
    try {
      const d = await api.deviceDisplacement(deviceId);
      if (disposed) return;
      live = d && d["displacement.value_mm"];
      $("cw-live").textContent = live == null ? "—" : Number(live).toFixed(3);
    } catch {
      live = null;
      if (!disposed) $("cw-live").textContent = "unavailable";
    }
  }
  poll();
  const timer = setInterval(poll, 1000);

  host.querySelectorAll("[data-cap]").forEach((b) => b.addEventListener("click", () => {
    if (live == null) { toast("No live displacement yet"); return; }
    $(b.dataset.cap).value = Number(live).toFixed(3);
  }));

  $("cw-save").addEventListener("click", async () => {
    const v = ["cw-t1", "cw-d1", "cw-t2", "cw-d2"].map((id) => parseFloat($(id).value));
    if (!v.every(Number.isFinite)) { toast("Enter both thicknesses and capture both displacements"); return; }
    try {
      const r = await api.calibrate(instanceId, { reference_1_thickness: v[0], reference_1_displacement: v[1],
        reference_2_thickness: v[2], reference_2_displacement: v[3] });
      $("cw-result").innerHTML = `Saved: thickness = <b>${fmt(r["calibration.scale"], 4)}</b> × displacement + <b>${fmt(r["calibration.offset"], 4)}</b>`;
      $("cw-verify").classList.remove("hidden");
      if (onSaved) onSaved(r);
    } catch (err) { toast(err.message); }
  });

  $("cw-vbtn").addEventListener("click", async () => {
    const t = parseFloat($("cw-tv").value);
    if (!Number.isFinite(t)) { toast("Enter the known thickness"); return; }
    if (live == null) { toast("No live displacement available for verification"); return; }
    try {
      const r = await api.verifyCalibration(instanceId, { known_thickness: t, displacement: live });
      const ok = Math.abs(r.error_mm) <= 0.02;
      $("cw-vres").innerHTML = `calculated ${fmt(r.calculated_thickness)} mm · error <span class="badge ${ok ? "good" : "warn"}">${r.error_mm > 0 ? "+" : ""}${fmt(r.error_mm)} mm</span>`;
    } catch (err) { toast(err.message); }
  });

  return () => { disposed = true; clearInterval(timer); };
}

// Step 4 — commissioning: calibration (if the application needs it) + input filter.
export async function renderCommission(view, ctx, setCleanup, instanceId) {
  const instance = await api.instance(instanceId);
  const calibrationMode = (instance.template.commissioning || {}).calibration;
  const needsCal = ["two_point", "baseline", "gap", "auto_gap"].includes(calibrationMode);
  const isGap = calibrationMode === "gap" || calibrationMode === "auto_gap";
  const cfg = instance.config || {};
  view.innerHTML = `
    <div class="page">
      ${stepper("commission", instanceId)}
      <h1>Commission</h1>
      <p class="lead">${isGap
        ? "Enter the gap between the two sensor faces, or use auto-gap with a known object thickness and tolerance."
        : calibrationMode === "baseline"
        ? "Capture a baseline with a known reference thickness so every side-by-side sensor reports real thickness."
        : needsCal ? "Calibrate the sensor against reference pieces so its displacement becomes real thickness."
        : "This application needs no calibration. Set the input filter and continue."}</p>
      <div class="two-col">
        <div class="panel">
          <h3>${needsCal ? "Calibration" : "No calibration needed"}</h3>
          ${needsCal ? `<div id="cal-host"></div>
            ${cfg["calibration.calibrated_at"] ? `<div class="hint" style="margin-top:10px">Current: ${
              isGap
                ? `gap ${esc(cfg["calibration.gap_distance"] ?? "—")} mm${cfg["calibration.object_thickness"] != null
                  ? `, object ${esc(cfg["calibration.object_thickness"])} mm` : ""}`
                : calibrationMode === "baseline"
                ? `reference ${esc(cfg["calibration.reference_thickness"] ?? "—")} mm`
                : `scale ${esc(cfg["calibration.scale"])}, offset ${esc(cfg["calibration.offset"])}`}
              · by ${esc(cfg["calibration.calibrated_by"])} at ${esc(cfg["calibration.calibrated_at"])}</div>` : ""}`
            : `<div class="empty">Values are used as they arrive (after mapping).</div>`}
        </div>
        <div class="panel">
          <h3>Input filter</h3>
          <p class="hint">Trimmed mean over the last N readings, so single noisy samples don't cause false FAILs.</p>
          <div class="field"><label>Window (readings, 1 = off)</label><input id="fw" type="number" min="1" value="${esc(cfg["filter.window"] || 1)}" /></div>
          <div class="field"><label>Trim % from each end</label><input id="ft" type="number" min="0" max="45" value="${esc(cfg["filter.trim_pct"] || 10)}" /></div>
          <button class="btn secondary btn-sm" id="fsave">Save filter</button>
        </div>
      </div>
      <button class="btn" id="next">Continue to limits &amp; alerts →</button>
    </div>`;
  let stop = null;
  if (needsCal) stop = calibrationPanel(document.getElementById("cal-host"), instanceId, instance.device_id, null,
    { mode: calibrationMode });
  setCleanup(() => stop && stop());
  async function saveFilter() {
    const windowSize = Number(document.getElementById("fw").value);
    const trim = Number(document.getElementById("ft").value);
    if (!Number.isInteger(windowSize) || windowSize < 1 || !Number.isFinite(trim) || trim < 0 || trim > 45) {
      throw new Error("Use a whole-number window of at least 1 and a trim percentage from 0 to 45.");
    }
    await api.updateInstance(instanceId, { config: { "filter.window": windowSize, "filter.trim_pct": trim } });
  }
  document.getElementById("fsave").addEventListener("click", async () => {
    try {
      await saveFilter();
      toast("Filter saved");
    } catch (err) { toast(err.message); }
  });
  document.getElementById("next").addEventListener("click", async () => {
    try { await saveFilter(); ctx.navigate(`#/setup/${instanceId}/rules`); }
    catch (err) { toast(err.message); }
  });
}

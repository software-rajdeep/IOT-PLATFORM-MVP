import { api } from "../api.js";
import { esc, fmt, toast } from "../ui.js";
import { stepper } from "./stepper.js";

// Two-point calibration wizard with a verification piece. Used by onboarding step 4 and
// by the dashboard's calibration widget. thickness = scale x displacement + offset.
export function calibrationPanel(host, instanceId, deviceId, onSaved) {
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
  const needsCal = (instance.template.commissioning || {}).calibration === "two_point";
  const cfg = instance.config || {};
  view.innerHTML = `
    <div class="page">
      ${stepper("commission", instanceId)}
      <h1>Commission</h1>
      <p class="lead">${needsCal ? "Calibrate the sensor against reference pieces so its displacement becomes real thickness."
        : "This application needs no calibration. Set the input filter and continue."}</p>
      <div class="two-col">
        <div class="panel">
          <h3>${needsCal ? "Calibration" : "No calibration needed"}</h3>
          ${needsCal ? `<div id="cal-host"></div>
            ${cfg["calibration.calibrated_at"] ? `<div class="hint" style="margin-top:10px">Current: scale ${esc(cfg["calibration.scale"])},
              offset ${esc(cfg["calibration.offset"])} · by ${esc(cfg["calibration.calibrated_by"])} at ${esc(cfg["calibration.calibrated_at"])}</div>` : ""}`
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
  if (needsCal) stop = calibrationPanel(document.getElementById("cal-host"), instanceId, instance.device_id);
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

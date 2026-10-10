import { api } from "../api.js";
import { store } from "../store.js";
import { esc, toast } from "../ui.js";

// Step 5 — two-point calibration: thickness = scale * displacement + offset.
export async function renderCalibration(view, ctx, setCleanup, instanceId) {
  let instance;
  try { instance = await api.instance(instanceId); }
  catch (err) { view.innerHTML = `<div class="page narrow"><div class="error-box">${esc(err.message)}</div></div>`; return; }

  const deviceId = instance.device_id;

  view.innerHTML = `
    <div class="page narrow">
      <div class="crumbs"><a href="#/apps">Applications</a><span class="sep">/</span>
        <b>${esc(instance.name)}</b><span class="sep">·</span><b>Step 5</b>
        <span class="sep">/</span><span>Calibration</span></div>
      <h1>Thickness calibration</h1>
      <p class="lead">Put two known reference pieces under the sensor. The platform derives scale and offset — no magic numbers.</p>

      <div class="panel">
        <h3>Reference points</h3>
        ${refRow(1, 10.0)}
        ${refRow(2, 8.0)}
        <div style="display:flex;gap:10px;align-items:center;margin-top:6px">
          <button class="btn" id="calc">Calculate calibration</button>
          <span id="cal-result"></span>
        </div>
      </div>

      <div class="panel">
        <h3>Verify (optional)</h3>
        <div class="field"><label>Known thickness (mm)</label><input id="ver-known" type="number" step="any" value="9.0" /></div>
        <div class="field"><label>Measured displacement (mm)</label>
          <div style="display:flex;gap:8px"><input id="ver-disp" type="number" step="any" style="flex:1" />
          <button class="btn secondary" id="ver-read">Read</button></div></div>
        <button class="btn secondary" id="ver-run">Verify</button>
        <div id="ver-result" style="margin-top:10px"></div>
      </div>

      <button class="btn" id="continue">Continue to limits →</button>
    </div>`;

  function refRow(n, def) {
    return `<div class="two-col" style="margin-bottom:8px">
      <div class="field"><label>Reference ${n} — known thickness (mm)</label>
        <input id="ref${n}-t" type="number" step="any" value="${def}" /></div>
      <div class="field"><label>Reference ${n} — measured displacement (mm)</label>
        <div style="display:flex;gap:8px"><input id="ref${n}-d" type="number" step="any" style="flex:1" />
        <button class="btn secondary" data-read="${n}">Read</button></div></div>
    </div>`;
  }

  async function readDisplacement(inputId) {
    try {
      const d = await api.deviceDisplacement(deviceId);
      if (d && d["displacement.value_mm"] != null) {
        document.getElementById(inputId).value = Number(d["displacement.value_mm"]).toFixed(3);
        toast(`Read ${Number(d["displacement.value_mm"]).toFixed(3)} mm`);
      } else {
        toast("No live reading yet — wait a moment or enter manually");
      }
    } catch (err) { toast(err.message); }
  }

  document.querySelectorAll("[data-read]").forEach((btn) => {
    btn.addEventListener("click", () => readDisplacement(`ref${btn.dataset.read}-d`));
  });
  document.getElementById("ver-read").addEventListener("click", () => readDisplacement("ver-disp"));

  document.getElementById("calc").addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    const t1 = parseFloat(document.getElementById("ref1-t").value);
    const d1 = parseFloat(document.getElementById("ref1-d").value);
    const t2 = parseFloat(document.getElementById("ref2-t").value);
    const d2 = parseFloat(document.getElementById("ref2-d").value);
    if ([t1, d1, t2, d2].some((v) => Number.isNaN(v))) { toast("Enter both references (read displacement or type it)"); return; }
    btn.disabled = true; btn.textContent = "Calculating…";
    try {
      const result = await api.calibrate(instanceId, {
        reference_1_thickness: t1, reference_1_displacement: d1,
        reference_2_thickness: t2, reference_2_displacement: d2,
      });
      document.getElementById("cal-result").innerHTML =
        `<span class="badge good">scale ${esc(result["calibration.scale"])}</span>
         <span class="badge muted">offset ${esc(result["calibration.offset"])}</span>`;
      btn.textContent = "Recalculate";
      btn.disabled = false;
    } catch (err) {
      toast(err.message);
      btn.disabled = false; btn.textContent = "Calculate calibration";
    }
  });

  document.getElementById("ver-run").addEventListener("click", async () => {
    const known = parseFloat(document.getElementById("ver-known").value);
    const disp = parseFloat(document.getElementById("ver-disp").value);
    const body = { known_thickness: known };
    if (!Number.isNaN(disp)) body.displacement = disp;
    try {
      const r = await api.verifyCalibration(instanceId, body);
      document.getElementById("ver-result").innerHTML =
        `<span class="badge ${Math.abs(r.error_mm) <= 0.05 ? "good" : "warn"}">
           calculated ${esc(r.calculated_thickness)} mm · error ${esc(r.error_mm)} mm</span>`;
    } catch (err) { toast(err.message); }
  });

  document.getElementById("continue").addEventListener("click", () => {
    ctx.navigate(`#/limits/${instanceId}`);
  });
}

import { api } from "../api.js";
import { store } from "../store.js";
import { esc, toast } from "../ui.js";

// Step 6 — quality limits (nominal / min / max), then activate.
export async function renderLimits(view, ctx, setCleanup, instanceId) {
  let instance;
  try { instance = await api.instance(instanceId); }
  catch (err) { view.innerHTML = `<div class="page narrow"><div class="error-box">${esc(err.message)}</div></div>`; return; }

  const cfg = instance.config || {};
  const val = (k, d) => (cfg[k] != null && cfg[k] !== "" ? cfg[k] : d);

  view.innerHTML = `
    <div class="page narrow">
      <div class="crumbs"><a href="#/apps">Applications</a><span class="sep">/</span>
        <b>${esc(instance.name)}</b><span class="sep">·</span><b>Step 6</b>
        <span class="sep">/</span><span>Quality limits</span></div>
      <h1>Quality configuration</h1>
      <p class="lead">Set the nominal thickness and the tolerance band. Samples outside the band are marked FAIL.</p>

      <div class="panel">
        <div class="two-col">
          <div class="field"><label>Nominal thickness (mm)</label><input id="target" type="number" step="any" value="${esc(val("thickness.target_mm", 8.5))}" /></div>
          <div class="field"><label>Lower limit (mm)</label><input id="lower" type="number" step="any" value="${esc(val("thickness.lower_limit_mm", 8.3))}" /></div>
          <div class="field"><label>Upper limit (mm)</label><input id="upper" type="number" step="any" value="${esc(val("thickness.upper_limit_mm", 8.7))}" /></div>
          <div class="field"><label>Warning lower (optional)</label><input id="wlow" type="number" step="any" value="${esc(val("thickness.warning_lower_mm", ""))}" /></div>
          <div class="field"><label>Warning upper (optional)</label><input id="whigh" type="number" step="any" value="${esc(val("thickness.warning_upper_mm", ""))}" /></div>
        </div>
        <button class="btn" id="activate">Save &amp; activate →</button>
      </div>
    </div>`;

  document.getElementById("activate").addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    const target = parseFloat(document.getElementById("target").value);
    const lower = parseFloat(document.getElementById("lower").value);
    const upper = parseFloat(document.getElementById("upper").value);
    if ([target, lower, upper].some((v) => Number.isNaN(v))) { toast("Enter nominal, lower and upper limits"); return; }
    if (lower >= upper) { toast("Lower limit must be less than upper limit"); return; }
    btn.disabled = true; btn.textContent = "Activating…";
    try {
      await api.updateInstance(instanceId, {
        config: {
          "thickness.target_mm": target,
          "thickness.lower_limit_mm": lower,
          "thickness.upper_limit_mm": upper,
          "thickness.warning_lower_mm": document.getElementById("wlow").value || null,
          "thickness.warning_upper_mm": document.getElementById("whigh").value || null,
        },
      });
      await api.activate(instanceId);
      ctx.navigate(`#/app/${instanceId}`);
    } catch (err) {
      toast(err.message);
      btn.disabled = false; btn.textContent = "Save & activate →";
    }
  });
}

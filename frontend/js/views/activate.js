import { api } from "../api.js";
import { esc, toast } from "../ui.js";
import { stepper } from "./stepper.js";

// Step 7 — validation gate. Required items must pass before the instance goes live.
export async function renderActivate(view, ctx, setCleanup, instanceId) {
  const [check, instance] = await Promise.all([api.checklist(instanceId), api.instance(instanceId)]);
  const link = { mapping: "map", calibration: "commission", limits: "rules", data: "map" };
  view.innerHTML = `
    <div class="page narrow">
      ${stepper("activate", instanceId)}
      <h1>Activate</h1>
      <p class="lead">${esc(instance.name)} · ${esc(instance.template.name)}</p>
      <div class="panel">
        <h3>Checklist</h3>
        <ul class="checklist">${check.items.map((i) => `<li class="${i.ok ? "ok" : i.required ? "bad" : "warn"}">
          <span class="ck">${i.ok ? "✓" : i.required ? "✕" : "!"}</span>
          <div><b>${esc(i.label)}</b>${i.required ? "" : ` <span class="badge muted">recommended</span>`}
            <div class="hint">${esc(i.detail || "")}</div></div>
          ${i.ok ? "" : `<a href="#/setup/${esc(instanceId)}/${link[i.key] || "map"}">fix →</a>`}</li>`).join("")}</ul>
      </div>
      ${check.status === "active"
        ? `<div class="panel"><b>Already live.</b> <a href="#/app/${esc(instanceId)}">Open the dashboard →</a></div>`
        : `<button class="btn" id="go" ${check.ready ? "" : "disabled"}>Activate &amp; open dashboard</button>`}
    </div>`;
  const go = document.getElementById("go");
  if (go) go.addEventListener("click", async () => {
    go.disabled = true;
    try { await api.activate(instanceId); toast("Live"); ctx.navigate(`#/app/${instanceId}`); }
    catch (err) { toast(err.message); go.disabled = false; }
  });
}

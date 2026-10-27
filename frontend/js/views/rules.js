import { api } from "../api.js";
import { esc, toast } from "../ui.js";
import { stepper } from "./stepper.js";

const RULES = {
  fail_streak: { label: "Consecutive FAIL readings", param: "count", unit: "readings" },
  offline: { label: "Source offline", param: "seconds", unit: "seconds" },
  mapping_failure: { label: "Data cannot be mapped", param: null },
};

// Step 5 — limits (rendered from the template's input definitions) + alert rules.
export async function renderRules(view, ctx, setCleanup, instanceId) {
  const [instance, al] = await Promise.all([api.instance(instanceId), api.alerts(instanceId)]);
  const cfg = instance.config || {};
  const inputs = (instance.template.inputs || []).filter((f) => !f.key.startsWith("calibration."));
  const inputByKey = Object.fromEntries(inputs.map((f) => [f.key, f]));
  const truthy = (value) => ["1", "true", "yes", "on"].includes(String(value ?? "").toLowerCase());
  const rules = al.rules || {};

  view.innerHTML = `
    <div class="page">
      ${stepper("rules", instanceId)}
      <h1>Limits &amp; alerts</h1>
      <p class="lead">What counts as good, and who needs to know when it isn't. Fields come from the application
        template; nothing here is specific to one customer.</p>
      <div class="two-col">
        <div class="panel">
          <h3>Limits</h3>
          ${inputs.map((f) => f.type === "boolean"
            ? `<label class="setting-row"><span>${esc(f.label)}</span>
                <input data-key="${esc(f.key)}" type="checkbox" ${truthy(cfg[f.key]) ? "checked" : ""} /></label>`
            : `<div class="field"><label>${esc(f.label)}${f.unit ? ` (${esc(f.unit)})` : ""}</label>
                <input data-key="${esc(f.key)}" type="${f.type === "text" ? "text" : "number"}" step="any"
                  value="${esc(cfg[f.key] ?? "")}" /></div>`).join("")}
          <button class="btn secondary btn-sm" id="lsave">Save limits</button>
        </div>
        <div class="panel">
          <h3>Alert rules</h3>
          ${Object.entries(RULES).map(([type, r]) => {
            const cur = rules[type] || { enabled: false, params: {} };
            return `<div class="rule-row" data-type="${type}">
              <label class="setting-row"><span>${esc(r.label)}</span><input type="checkbox" class="r-on" ${cur.enabled ? "checked" : ""} /></label>
              ${r.param ? `<div class="rule-param">after <input class="r-param limit-input" type="number" value="${esc(cur.params[r.param] ?? "")}" /> ${esc(r.unit)}</div>` : ""}
            </div>`;
          }).join("")}
          <p class="hint">Alerts appear on the dashboard. Email / WhatsApp delivery plugs in at the same point.</p>
          <button class="btn secondary btn-sm" id="asave">Save alert rules</button>
        </div>
      </div>
      <button class="btn" id="next">Continue to graphs →</button>
    </div>`;

  async function saveLimits(quiet) {
    const config = {};
    view.querySelectorAll("[data-key]").forEach((el) => {
      const spec = inputByKey[el.dataset.key] || {};
      if (spec.type === "boolean") config[el.dataset.key] = el.checked;
      else if (spec.type === "text") config[el.dataset.key] = el.value === "" ? null : el.value;
      else config[el.dataset.key] = el.value === "" ? null : Number(el.value);
    });
    const roles = instance.template.field_roles || {};
    const pairs = [[roles.lower, roles.upper], ["reference.lower_limit_mm", "reference.upper_limit_mm"]];
    for (const [loKey, hiKey] of pairs) {
      if (!loKey || !hiKey) continue;
      if (config[loKey] != null && config[hiKey] != null && config[loKey] >= config[hiKey]) {
        toast("Lower limit must be below the upper limit");
        return false;
      }
    }
    await api.updateInstance(instanceId, { config });
    if (!quiet) toast("Limits saved");
    return true;
  }
  async function saveRules(quiet) {
    for (const row of view.querySelectorAll(".rule-row")) {
      const type = row.dataset.type;
      const p = row.querySelector(".r-param");
      await api.updateAlertRule(instanceId, type, { enabled: row.querySelector(".r-on").checked,
        params: p && p.value ? { [RULES[type].param]: Number(p.value) } : undefined });
    }
    if (!quiet) toast("Alert rules saved");
  }
  document.getElementById("lsave").addEventListener("click", () => saveLimits().catch((e) => toast(e.message)));
  document.getElementById("asave").addEventListener("click", () => saveRules().catch((e) => toast(e.message)));
  document.getElementById("next").addEventListener("click", async () => {
    try {
      if (!(await saveLimits(true))) return;
      await saveRules(true);
      ctx.navigate(`#/setup/${instanceId}/graphs`);
    } catch (e) { toast(e.message); }
  });
}

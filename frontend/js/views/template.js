import { api } from "../api.js";
import { store } from "../store.js";
import { esc, toast } from "../ui.js";

// Layout glyphs keyed by the template's sensor_layout value (data, not code).
const LAYOUT_ICON = {
  side_by_side: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
      stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="20" rx="2.2"/>
      <line x1="2" y1="12" x2="22" y2="12"/><line x1="12" y1="2" x2="12" y2="22"/></svg>`,
  opposite: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
      stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="8" height="10" rx="1"/>
      <rect x="14" y="7" width="8" height="10" rx="1"/><line x1="2" y1="12" x2="10" y2="12"/>
      <line x1="14" y1="12" x2="22" y2="12"/></svg>`,
  sbs_reference: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
      stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="20" rx="2.2"/>
      <line x1="2" y1="12" x2="22" y2="12"/><line x1="12" y1="2" x2="12" y2="22"/>
      <circle cx="18" cy="6" r="3"/></svg>`,
};

// Step 3 — pick the application template, then continue to device setup.
export async function renderTemplates(view, ctx, setCleanup) {
  const selection = store.selection();
  if (!selection.model || !selection.purpose) { ctx.navigate("#/devices"); return; }
  const { model, purpose } = selection;

  view.innerHTML = `
    <div class="page">
      <div class="crumbs"><a href="#/devices">Devices</a><span class="sep">/</span>
        <a href="#/purposes">${esc(model.model_name)}</a><span class="sep">/</span>
        <b>${esc(purpose.name)}</b><span class="sep">·</span><b>Step 3</b>
        <span class="sep">/</span><span>Application</span></div>
      <h1>Choose an application</h1>
      <p class="lead">Ready-made application templates. Pick one, then configure the device, calibration and limits.</p>
      <div id="templates"><div class="spinner">loading applications…</div></div>
      <div id="create" style="margin-top:22px"></div>
    </div>`;

  const tHost = document.getElementById("templates");
  const createHost = document.getElementById("create");

  function showCreate(template) {
    createHost.innerHTML = `
      <div class="panel" style="max-width:560px">
        <h3>Application · ${esc(template.name)}</h3>
        <div class="field">
          <label for="appname">Application name</label>
          <input id="appname" type="text" value="${esc(model.model_name + " — " + template.name)}" />
        </div>
        <button class="btn" id="create-btn">Continue to device setup →</button>
      </div>`;
    document.getElementById("create-btn").addEventListener("click", () => {
      const name = document.getElementById("appname").value.trim();
      if (!name) { toast("Please enter a name"); return; }
      store.setSelection({ template, instance_name: name });
      ctx.navigate("#/connection");
    });
  }

  try {
    const data = await api.templates(purpose.purpose_key, model.model_key);
    const templates = data.templates || [];
    if (!templates.length) {
      tHost.innerHTML = `<div class="empty">No applications exist for this purpose yet.</div>`;
      return;
    }
    tHost.innerHTML = `<div class="grid cols-3">${templates.map((t) => `
      <div class="card" data-key="${esc(t.template_id)}">
        <div class="card-icon">${LAYOUT_ICON[t.sensor_layout] || ""}</div>
        <span class="num">v${esc(t.version)}</span>
        <h3>${esc(t.name)}</h3>
        <p>${esc(t.description || t.category || "")}</p>
        <div class="tagrow">
          <span class="tag accent">${esc(t.template_id)}</span>
          <span class="tag">${esc(t.processor_key)}</span>
        </div>
      </div>`).join("")}</div>`;

    tHost.querySelectorAll(".card").forEach((card) => {
      card.addEventListener("click", () => {
        tHost.querySelectorAll(".card").forEach((c) => c.classList.remove("selected"));
        card.classList.add("selected");
        showCreate(templates.find((t) => t.template_id === card.dataset.key));
      });
    });
  } catch (err) {
    tHost.innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
  }
}
import { api } from "../api.js";
import { store } from "../store.js";
import { esc } from "../ui.js";

// Step 2 — for what purpose is the device used? Options come from the catalogue.
export async function renderPurposes(view, ctx, setCleanup) {
  const selection = store.selection();
  if (!selection.model) { ctx.navigate("#/devices"); return; }
  const model = selection.model;

  view.innerHTML = `
    <div class="page">
      <div class="crumbs"><a href="#/devices">Devices</a><span class="sep">/</span>
        <b>${esc(model.model_name)}</b><span class="sep">·</span><b>Step 2</b>
        <span class="sep">/</span><span>Purpose</span><span class="sep">·</span><span>3 Template</span>
        <span class="sep">·</span><span>4 Application</span></div>
      <h1>What do you use it for?</h1>
      <p class="lead">Choose the measurement purpose. Each purpose leads to ready-made solution templates.</p>
      <div id="purposes"><div class="spinner">loading…</div></div>
    </div>`;

  const host = document.getElementById("purposes");
  try {
    const data = await api.purposes(model.model_key);
    const purposes = data.purposes || [];
    if (!purposes.length) {
      host.innerHTML = `<div class="empty">This device has no configured purposes yet.</div>`;
      return;
    }
    host.innerHTML = `<div class="grid cols-3">${purposes.map((p) => `
      <div class="card" data-key="${esc(p.purpose_key)}">
        <span class="num">${p.template_count || 0} template${p.template_count === 1 ? "" : "s"}</span>
        <h3>${esc(p.name)}</h3>
        <p>${esc(p.description || "")}</p>
      </div>`).join("")}</div>`;

    host.querySelectorAll(".card").forEach((card) => {
      card.addEventListener("click", () => {
        const purpose = purposes.find((p) => p.purpose_key === card.dataset.key);
        store.setSelection({ purpose });
        ctx.navigate("#/templates");
      });
    });
  } catch (err) {
    host.innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
  }
}

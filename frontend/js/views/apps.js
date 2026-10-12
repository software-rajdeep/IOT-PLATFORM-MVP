import { api } from "../api.js";
import { store } from "../store.js";
import { esc } from "../ui.js";

// "My applications" — every configured solution for this company.
export async function renderApplications(view, ctx, setCleanup) {
  view.innerHTML = `
    <div class="page">
      <div class="crumbs"><b>My applications</b></div>
      <h1>My applications</h1>
      <p class="lead">Every configured solution: product, application, data source and dashboard.
        <a href="#/catalog">+ New solution</a></p>
      <div id="list"><div class="spinner">loading…</div></div>
    </div>`;

  const host = document.getElementById("list");
  try {
    const data = await api.instances();
    const instances = data.instances || [];
    if (!instances.length) {
      host.innerHTML = `<div class="empty">No applications yet. <a href="#/catalog">Create your first solution →</a></div>`;
      return;
    }
    host.innerHTML = `<div class="grid cols-3">${instances.map((i) => `
      <div class="card" data-id="${esc(i.instance_id)}">
        <span class="num">${esc(i.status)}</span>
        <h3>${esc(i.name)}</h3>
        <p>${esc((i.template && i.template.name) || "")} · device ${esc(i.device_id || "—")}</p>
        <div class="tagrow">
          <span class="tag accent">${esc((i.template && i.template.template_id) || "")}</span>
          ${i.plant ? `<span class="tag">${esc(i.plant)}</span>` : ""}
          <span class="tag ${i.steps && i.steps.active ? "accent" : ""}">${i.steps && i.steps.active ? "live" : "setup"}</span>
          ${i.interface ? `<span class="tag">${esc(String(i.interface).replace("database_api", "customer data").replace("http_push", "http push").toLowerCase())}</span>` : ""}
        </div>
      </div>`).join("")}</div>`;

    host.querySelectorAll(".card").forEach((card) => {
      card.addEventListener("click", () => {
        const inst = instances.find((x) => x.instance_id === card.dataset.id);
        ctx.navigate(inst.status === "active" ? `#/app/${card.dataset.id}` : `#/setup/${card.dataset.id}/activate`);
      });
    });
  } catch (err) {
    host.innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
  }
}
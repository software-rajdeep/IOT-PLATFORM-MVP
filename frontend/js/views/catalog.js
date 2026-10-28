import { api } from "../api.js";
import { store } from "../store.js";
import { esc } from "../ui.js";
import { stepper } from "./stepper.js";

const ICONS = {
  layers: `<path d="M12 3 2 8l10 5 10-5-10-5Z"/><path d="m2 13 10 5 10-5"/>`,
  activity: `<path d="M22 12h-4l-3 8L9 4l-3 8H2"/>`,
  target: `<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>`,
  gauge: `<path d="M12 14l4-4"/><path d="M3.3 19a10 10 0 1 1 17.4 0"/>`,
  bolt: `<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z"/>`,
  eye: `<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>`,
};

function icon(name) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"
    stroke-linejoin="round">${ICONS[name] || ICONS.layers}</svg>`;
}

// Step 1 — the solution catalog: every compatible product x application pair (computed by
// the platform from device signals and application inputs), plus "coming soon" cards.
export async function renderCatalog(view, ctx) {
  store.clearSelection();
  view.innerHTML = `
    <div class="page">
      ${stepper("solution")}
      <h1>Choose a solution</h1>
      <div class="searchbar"><input id="q" type="text" placeholder="Filter by product or application…" autocomplete="off" /></div>
      <div id="sol"><div class="spinner">loading catalog…</div></div>
    </div>`;

  const host = document.getElementById("sol");
  let all = [];
  try {
    all = (await api.solutions()).solutions || [];
  } catch (err) {
    host.innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
    return;
  }

  function draw(filter) {
    const f = filter.toLowerCase();
    const list = all.filter((s) => !f || `${s.title} ${s.model.model_name} ${s.template.name}`.toLowerCase().includes(f));
    const available = list.filter((s) => s.status === "available");
    const soon = list.filter((s) => s.status !== "available");
    const card = (s) => `
      <div class="card sol-card ${s.status !== "available" ? "soon" : ""}" data-id="${esc(s.solution_id)}">
        <div class="card-icon">${icon(s.icon)}</div>
        ${s.status !== "available" ? `<span class="num">coming soon</span>` : s.featured ? `<span class="num">featured</span>` : ""}
        <h3>${esc(s.title)}</h3>
      </div>`;
    host.innerHTML = `
      <div class="section-title" style="margin:6px 0 10px">Available now · ${available.length}</div>
      <div class="grid cols-3">${available.map(card).join("") || `<div class="empty">No match.</div>`}</div>
      ${soon.length ? `<div class="section-title" style="margin:26px 0 10px">Coming soon</div>
      <div class="grid cols-3">${soon.map(card).join("")}</div>` : ""}`;
    host.querySelectorAll(".sol-card:not(.soon)").forEach((el) => el.addEventListener("click", () => {
      const s = all.find((x) => x.solution_id === el.dataset.id);
      store.setSelection({
        model: s.model,
        purpose: { purpose_key: s.template.purpose_key },
        template: s.template,
        instance_name: `${s.model.model_name} — ${s.template.name}`,
      });
      ctx.navigate("#/connection");
    }));
  }

  const q = document.getElementById("q");
  q.addEventListener("input", () => draw(q.value.trim()));
  draw("");
}

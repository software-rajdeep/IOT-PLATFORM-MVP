import { api } from "../api.js";
import { store } from "../store.js";
import { esc, toast } from "../ui.js";

// Step 1 — which device does the customer use? A searchable, data-driven list.
export async function renderDevices(view, ctx, setCleanup) {
  store.clearSelection();
  view.innerHTML = `
    <div class="page">
      <div class="crumbs"><b>Step 1</b><span class="sep">/</span><span>Select device</span>
        <span class="sep">·</span><span>2 Purpose</span><span class="sep">·</span><span>3 Template</span>
        <span class="sep">·</span><span>4 Application</span></div>
      <h1>Which device do you use?</h1>
      <p class="lead">Search our supported device catalogue. Pick the model installed in your plant.</p>
      <div class="searchbar">
        <input id="search" type="text" placeholder="Search by name, manufacturer or model…" autocomplete="off" />
        <button id="search-btn">Search</button>
      </div>
      <div id="results"><div class="spinner">loading catalogue…</div></div>
    </div>`;

  const input = document.getElementById("search");
  const results = document.getElementById("results");

  async function load(search) {
    results.innerHTML = `<div class="spinner">searching…</div>`;
    try {
      const data = await api.deviceModels(search);
      const models = data.device_models || [];
      if (!models.length) {
        results.innerHTML = `<div class="empty">No devices match “${esc(search)}”.</div>`;
        return;
      }
      results.innerHTML = `<div class="grid cols-3">${models.map((m) => `
        <div class="card" data-key="${esc(m.model_key)}">
          <span class="num">${esc(m.connector_type || "")}</span>
          <h3>${esc(m.manufacturer ? m.manufacturer + " " : "")}${esc(m.model_name)}</h3>
          <p>${esc(m.series || "")} · ${esc(m.device_type || "")}</p>
          <div class="tagrow">
            <span class="tag accent">${esc(m.connector_type || "")}</span>
            ${m.centre_distance != null ? `<span class="tag">centre ${esc(m.centre_distance)} ${esc(m.unit || "mm")}</span>` : ""}
            ${m.range_min != null ? `<span class="tag">${esc(m.range_min)}…${esc(m.range_max)}</span>` : ""}
            <span class="tag">res ${esc(m.resolution)}</span>
          </div>
        </div>`).join("")}</div>`;

      results.querySelectorAll(".card").forEach((card) => {
        card.addEventListener("click", () => {
          const model = models.find((m) => m.model_key === card.dataset.key);
          store.setSelection({ model });
          ctx.navigate("#/purposes");
        });
      });
    } catch (err) {
      results.innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
    }
  }

  document.getElementById("search-btn").addEventListener("click", () => load(input.value.trim()));
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") load(input.value.trim()); });
  let t;
  input.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => load(input.value.trim()), 350); });

  setCleanup(() => clearTimeout(t));
  await load("");
  input.focus();
}

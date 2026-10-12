import { api } from "../api.js";
import { esc, toast } from "../ui.js";
import { mountBoard } from "./board.js";
import { stepper } from "./stepper.js";

// Step 6 (and "Customize" on the dashboard) — graph options for THIS instance.
// Available = every field the widget needs exists; Locked = it needs an optional input
// that isn't mapped yet (link back to mapping); Not applicable = hidden.
export async function renderGraphs(view, ctx, setCleanup, instanceId, mode = "setup") {
  const [opts, instance] = await Promise.all([api.widgets(instanceId), api.instance(instanceId)]);
  const widgets = opts.widgets || [];
  const available = widgets.filter((w) => w.status === "available");
  const locked = widgets.filter((w) => w.status === "locked");
  const na = widgets.filter((w) => w.status === "not_applicable");
  const setup = mode === "setup";

  view.innerHTML = `
    <div class="page wide">
      ${setup ? stepper("graphs", instanceId) : `<div class="crumbs"><a href="#/apps">Applications</a><span class="sep">/</span>
        <a href="#/app/${esc(instanceId)}">${esc(instance.name)}</a><span class="sep">/</span><b>Customize</b></div>`}
      <h1>${setup ? "Choose your graphs" : "Customize dashboard"}</h1>
      <p class="lead">These are the graphs that fit <b>${esc(instance.template.name)}</b> with the data you mapped.
        Drag to move, pull the corner to resize, × to remove.</p>
      <div class="graphs-layout">
        <aside class="panel lib">
          <h3>Available · ${available.length}</h3>
          ${available.map((w) => `<button class="lib-item" data-add="${esc(w.widget_type)}" title="${esc(w.description || "")}">
            <b>${esc(w.name)}</b><span>${esc(w.description || "")}</span><i>+</i></button>`).join("")}
          ${locked.length ? `<h3 style="margin-top:16px">Locked · ${locked.length}</h3>
          ${locked.map((w) => `<a class="lib-item locked" href="#/setup/${esc(instanceId)}/map">
            <b>${esc(w.name)}</b><span>Map <u>${esc(w.unlock_with.join(", "))}</u> to unlock</span><i>🔒</i></a>`).join("")}` : ""}
          ${na.length ? `<div class="hint" style="margin-top:12px">${na.length} other graph types don't apply to this application.</div>` : ""}
        </aside>
        <div>
          <div class="run-actions" style="margin-bottom:10px">
            <button class="btn" id="save">${setup ? "Save & continue →" : "Save layout"}</button>
            <button class="btn-ghost" id="reset">Reset to template default</button>
            <span class="hint" id="dirty"></span>
          </div>
          <div id="board"></div>
        </div>
      </div>
    </div>`;

  const sizes = Object.fromEntries(widgets.map((w) => [w.widget_type, [w.default_w, w.default_h]]));
  const board = mountBoard(document.getElementById("board"), opts.layout.widgets || [], {
    editable: true, onChange: () => { document.getElementById("dirty").textContent = "unsaved changes"; },
  });
  view.querySelectorAll("[data-add]").forEach((b) => b.addEventListener("click", () => {
    const [w, h] = sizes[b.dataset.add] || [4, 2];
    board.add(b.dataset.add, w, h);
  }));

  async function preview() {
    try { board.update(await api.dashboard(instanceId)); } catch { /* transient */ }
  }
  preview();
  const timer = setInterval(preview, 3000);
  setCleanup(() => { clearInterval(timer); board.destroy(); });

  document.getElementById("save").addEventListener("click", async () => {
    try {
      await api.saveLayout(instanceId, board.getLayout());
      toast("Layout saved");
      ctx.navigate(setup ? `#/setup/${instanceId}/activate` : `#/app/${instanceId}`);
    } catch (err) { toast(err.message); }
  });
  document.getElementById("reset").addEventListener("click", async () => {
    try {
      await api.resetLayout(instanceId);
      toast("Reset to default");
      ctx.navigate(setup ? `#/setup/${instanceId}/graphs` : `#/app/${instanceId}/customize`);
    }
    catch (err) { toast(err.message); }
  });
}

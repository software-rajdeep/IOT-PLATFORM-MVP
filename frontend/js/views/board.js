import { esc } from "../ui.js";
import { DEFAULT_TITLES, STATIC_WIDGETS, renderWidget } from "../widgets.js";

// A widget board on gridstack (vendored, global GridStack). Static on the dashboard,
// draggable/resizable in the graph-options editor. Layout = [{id, type, x, y, w, h, title?}].
export function mountBoard(container, layout, { editable = false, handlers = {}, onChange } = {}) {
  container.classList.add("grid-stack");
  container.innerHTML = "";
  const items = new Map();
  let seq = 0;
  let last = null;

  function element(w) {
    const id = w.id || `w${Date.now().toString(36)}${seq++}`;
    const el = document.createElement("div");
    el.className = "grid-stack-item";
    el.setAttribute("gs-id", id);
    for (const k of ["x", "y", "w", "h"]) if (w[k] != null) el.setAttribute(`gs-${k}`, w[k]);
    el.innerHTML = `<div class="grid-stack-item-content widget" data-type="${esc(w.type)}">
      <div class="w-head"><span class="w-title"></span>${editable ? `<button class="w-x" title="Remove">×</button>` : ""}</div>
      <div class="w-body"><div class="empty">loading…</div></div></div>`;
    items.set(id, { id, type: w.type, title: w.title, el, drawn: false, state: {} });
    if (editable) el.querySelector(".w-x").addEventListener("click", () => remove(id));
    return el;
  }

  layout.forEach((w) => container.appendChild(element(w)));
  const grid = window.GridStack.init({
    column: 12, cellHeight: 76, margin: 7, float: false, staticGrid: !editable,
    disableOneColumnMode: false, columnOpts: { breakpoints: [{ w: 760, c: 1 }] },
    resizable: { handles: "se,e,s" },
  }, container);
  if (editable && onChange) grid.on("change added removed", () => onChange());

  function remove(id) {
    const it = items.get(id);
    if (!it) return;
    grid.removeWidget(it.el);
    items.delete(id);
    if (onChange) onChange();
  }

  function add(type, w = 4, h = 2) {
    const el = element({ type, w, h });
    container.appendChild(el);
    grid.makeWidget(el);
    if (last) update(last);
    if (onChange) onChange();
  }

  function update(data) {
    last = data;
    for (const it of items.values()) {
      const title = it.title || (DEFAULT_TITLES[it.type] ? DEFAULT_TITLES[it.type](data) : it.type);
      it.el.querySelector(".w-title").textContent = title;
      if (STATIC_WIDGETS.has(it.type) && it.drawn) continue;
      it.el.querySelector(".w-body").innerHTML = renderWidget(it.type, data, it.state);
      it.drawn = true;
    }
  }

  function redraw(type) {
    for (const it of items.values()) if (it.type === type) it.drawn = false;
    if (last) update(last);
  }

  function getLayout() {
    return grid.engine.nodes.map((n) => {
      const id = n.el.getAttribute("gs-id");
      const it = items.get(id);
      return { id, type: it.type, x: n.x, y: n.y, w: n.w, h: n.h, ...(it.title ? { title: it.title } : {}) };
    }).sort((a, b) => a.y - b.y || a.x - b.x);
  }

  container.addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]");
    if (act && handlers[act.dataset.act]) handlers[act.dataset.act](act, e);
    const ack = e.target.closest("[data-ack]");
    if (ack && handlers.ack) handlers.ack(Number(ack.dataset.ack));
  });

  container.addEventListener("change", (e) => {
    const sel = e.target.closest("[data-sensor-select]");
    if (!sel) return;
    const itemEl = sel.closest(".grid-stack-item");
    const it = itemEl && items.get(itemEl.getAttribute("gs-id"));
    if (!it) return;
    it.state = { ...it.state, sensor: sel.value };
    if (last) update(last);
  });

  return { grid, add, remove, update, redraw, getLayout, types: () => [...items.values()].map((i) => i.type),
           destroy: () => grid.destroy(false) };
}

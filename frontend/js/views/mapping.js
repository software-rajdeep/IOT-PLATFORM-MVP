import { api } from "../api.js";
import { esc, fmt, toast } from "../ui.js";
import { stepper } from "./stepper.js";

// Step 3 — map the source's raw payload onto the template's standard fields.
// Our own devices arrive pre-mapped (device preset); anything else is mapped here
// from a captured sample, with a live preview through the full pipeline.
export async function renderMapping(view, ctx, setCleanup, instanceId) {
  view.innerHTML = `<div class="page">${stepper("map", instanceId)}<div class="spinner">loading…</div></div>`;
  let info, instance;
  try {
    [info, instance] = await Promise.all([api.mapping(instanceId), api.instance(instanceId)]);
  } catch (err) {
    view.innerHTML = `<div class="page narrow"><div class="error-box">${esc(err.message)}</div></div>`;
    return;
  }

  const units = info.units || [];
  const unitDim = Object.fromEntries(units.map((u) => [u.unit_key, u.dimension]));
  const contract = info.contract || [];
  const roles = instance.template.field_roles || {};
  const baseField = contract.find((c) => c.required && c.kind !== "text")
    || contract.find((c) => c.required) || contract[0] || {};
  const baseKey = baseField.key;
  let samples = [];
  let paths = [];
  let sampleIdx = 0;
  let rows = rowsFrom(info.current ? info.current.definition : null);
  let tsRow = tsFrom(info.current ? info.current.definition : null);
  let extras = extrasFrom(info.current ? info.current.definition : null);
  const presetMode = info.current && info.current.origin === "preset";
  let editing = !presetMode;

  function rowsFrom(def) {
    const byTarget = {};
    for (const f of (def && def.fields) || []) byTarget[f.target] = f;
    return contract.map((c) => {
      const f = byTarget[c.key] || {};
      const t = f.transforms || [];
      const unit = t.find((x) => x.type === "unit");
      const scale = t.find((x) => x.type === "scale");
      const offset = t.find((x) => x.type === "offset");
      return { key: c.key, path: f.path || "", from: unit ? unit.from : (c.unit || ""),
               scale: scale ? scale.factor : 1, offset: offset ? offset.value : 0 };
    });
  }
  function tsFrom(def) {
    const t = (def && def.timestamp) || {};
    return { path: t.path || "", format: t.format || "auto", tz: t.tz || "Asia/Kolkata" };
  }
  function extraRow(def, target) {
    const f = ((def && def.fields) || []).find((x) => x.target === target) || {};
    const t = f.transforms || [];
    const unit = t.find((x) => x.type === "unit");
    const scale = t.find((x) => x.type === "scale");
    const offset = t.find((x) => x.type === "offset");
    return { path: f.path || "", from: unit ? unit.from : (baseField.unit || ""),
             scale: scale ? scale.factor : 1, offset: offset ? offset.value : 0 };
  }
  function extrasFrom(def) {
    if (!baseKey) return [];
    const out = [];
    for (let n = 2; ; n += 1) {
      const target = `${baseKey}.${n}`;
      const exists = ((def && def.fields) || []).some((f) => f.target === target);
      if (!exists) break;
      out.push(extraRow(def, target));
    }
    return out;
  }

  function transformsFor(r, field) {
    const transforms = [];
    if (field.kind === "text") { transforms.push({ type: "cast", to: "text" }); return transforms; }
    transforms.push({ type: "cast", to: "decimal" });
    if (r.from && field.unit && r.from !== field.unit) transforms.push({ type: "unit", from: r.from, to: field.unit });
    if (Number(r.scale) !== 1 && r.scale !== "" && r.scale != null) transforms.push({ type: "scale", factor: Number(r.scale) });
    if (Number(r.offset)) transforms.push({ type: "offset", value: Number(r.offset) });
    return transforms;
  }

  function definition() {
    const fields = [];
    rows.forEach((r, i) => {
      if (!r.path) return;
      fields.push({ target: contract[i].key, path: r.path, transforms: transformsFor(r, contract[i]) });
    });
    if (baseKey) extras.forEach((r, i) => {
      if (!r.path) return;
      fields.push({ target: `${baseKey}.${i + 2}`, path: r.path, transforms: transformsFor(r, baseField) });
    });
    const def = { fields };
    if (tsRow.path) def.timestamp = { ...tsRow };
    return def;
  }

  const head = `
    ${stepper("map", instanceId)}
    <h1>Map your data</h1>`;

  function drawPreset() {
    const def = info.current.definition;
    view.innerHTML = `<div class="page">${head}
      <div class="panel">
        <h3>Automatic mapping · device preset</h3>
        <div class="table-wrap"><table><thead><tr><th>Standard field</th><th>From</th><th>Conversion</th></tr></thead><tbody>
          ${def.fields.map((f) => `<tr><td class="td-mono">${esc(f.target)}</td><td class="td-mono">${esc(f.path)}</td>
            <td class="td-mono">${esc(describe(f.transforms))}</td></tr>`).join("")}
        </tbody></table></div>
        ${unlockNotes()}
        <div class="run-actions" style="margin-top:14px">
          <button class="btn" id="next">Continue →</button>
          <button class="btn-ghost" id="edit">Edit manually</button>
        </div>
      </div></div>`;
    document.getElementById("next").addEventListener("click", () => ctx.navigate(`#/setup/${instanceId}/commission`));
    document.getElementById("edit").addEventListener("click", () => { editing = true; drawEditor(); capture(); });
  }

  function unlockNotes() {
    const opt = contract.filter((c) => !c.required && (info.unlocks || {})[c.key]);
    if (!opt.length) return "";
    return `<div class="hint" style="margin-top:12px">${opt.map((c) =>
      `Map <b>${esc(c.label)}</b> to unlock: ${esc(info.unlocks[c.key].join(", "))}.`).join("<br/>")}</div>`;
  }

  function describe(transforms) {
    return (transforms || []).map((t) => t.type === "unit" ? `${t.from}→${t.to}` : t.type === "scale" ? `×${t.factor}`
      : t.type === "offset" ? `+${t.value}` : t.type === "cast" ? t.to : t.type).join(", ") || "as is";
  }

  function pathOptions(selected, wantText) {
    const opts = paths.filter((p) => wantText ? true : ["number", "numeric_text"].includes(p.type));
    const known = opts.some((p) => p.path === selected);
    return `<option value="">— not mapped —</option>` +
      (selected && !known ? `<option value="${esc(selected)}" selected>${esc(selected)}</option>` : "") +
      opts.map((p) => `<option value="${esc(p.path)}" ${p.path === selected ? "selected" : ""}>${esc(p.path)}  (${esc(String(p.example).slice(0, 24))})</option>`).join("");
  }

  function unitOptions(c, selected) {
    if (!c.unit) return "";
    const dim = unitDim[c.unit];
    return units.filter((u) => u.dimension === dim).map((u) =>
      `<option value="${esc(u.unit_key)}" ${u.unit_key === selected ? "selected" : ""}>${esc(u.unit_key)}</option>`).join("");
  }

  function drawEditor() {
    view.innerHTML = `<div class="page wide">${head}
      <div class="map-layout">
        <div class="panel">
          <h3>Source sample</h3>
          <div class="run-actions" style="margin-bottom:10px">
            <button class="btn secondary btn-sm" id="cap">Capture sample</button>
            <select id="sample-pick" class="graph-select"></select>
          </div>
          <div id="sample-body"><div class="empty">Capture a sample to see the fields the source sends.</div></div>
        </div>
        <div>
          <div class="panel">
            <h3>Standard fields · ${esc(instance.template.name)}</h3>
            <div class="table-wrap"><table class="map-table"><thead><tr>
              <th>Field</th><th>Source path</th><th>Unit sent</th><th>Scale</th><th>Offset</th><th class="td-r">Result</th>
            </tr></thead><tbody id="map-rows"></tbody></table></div>
            <div class="ts-row">
              <div class="field"><label>Timestamp from</label><select id="ts-path"></select></div>
              <div class="field"><label>Format</label><select id="ts-format">
                ${["auto", "epoch_s", "epoch_ms"].map((f) => `<option ${f === tsRow.format ? "selected" : ""}>${f}</option>`).join("")}
              </select></div>
              <div class="field"><label>Source time zone</label><select id="ts-tz">
                ${["Asia/Kolkata", "UTC", "Asia/Dubai", "Europe/Berlin"].map((z) => `<option ${z === tsRow.tz ? "selected" : ""}>${z}</option>`).join("")}
              </select></div>
            </div>
            <div class="hint">No timestamp mapped = time the platform received the reading.</div>
          </div>
          ${baseKey ? `
          <div class="panel">
            <h3>Additional sensors</h3>
            <p class="hint">Map extra sensor columns to the same reading (e.g. sensor_a, sensor_b, sensor_c).
              They are stored with every measurement and listed on the dashboard.</p>
            <div class="table-wrap"><table class="map-table"><thead><tr>
              <th>Sensor</th><th>Source path</th><th>Unit sent</th><th>Scale</th><th>Offset</th><th class="td-r">Result</th><th></th>
            </tr></thead><tbody id="sensor-rows"></tbody></table></div>
            <button class="btn secondary btn-sm" id="add-sensor" type="button">+ Add sensor</button>
          </div>` : ""}
          <div class="panel">
            <h3>Preview through the whole pipeline</h3>
            <div id="preview"><div class="empty">Map the required fields to see the result.</div></div>
            <div class="run-actions" style="margin-top:12px">
              <button class="btn" id="save">Save mapping &amp; continue →</button>
              ${info.current ? `<span class="hint">current: v${esc(info.current.version)} (${esc(info.current.origin)})</span>` : ""}
              ${info.failures_last_hour ? `<a href="#" id="show-fail" class="hint">${info.failures_last_hour} unmapped messages in the last hour</a>` : ""}
            </div>
            <div id="failures"></div>
          </div>
        </div>
      </div></div>`;
    drawRows();
    drawSensors();
    document.getElementById("cap").addEventListener("click", capture);
    document.getElementById("sample-pick").addEventListener("change", (e) => { sampleIdx = Number(e.target.value); drawSample(); preview(); });
    ["ts-path", "ts-format", "ts-tz"].forEach((id) => document.getElementById(id).addEventListener("change", () => {
      tsRow = { path: document.getElementById("ts-path").value, format: document.getElementById("ts-format").value,
                tz: document.getElementById("ts-tz").value };
      preview();
    }));
    document.getElementById("save").addEventListener("click", save);
    const sf = document.getElementById("show-fail");
    if (sf) sf.addEventListener("click", async (e) => { e.preventDefault(); showFailures(); });
    const addSensor = document.getElementById("add-sensor");
    if (addSensor) addSensor.addEventListener("click", () => {
      extras.push({ path: "", from: baseField.unit || "", scale: 1, offset: 0 });
      drawSensors();
      preview();
    });
  }

  function drawSensors() {
    const body = document.getElementById("sensor-rows");
    if (!body) return;
    body.innerHTML = extras.map((r, i) => `<tr data-i="${i}">
      <td>Sensor ${i + 2}</td>
      <td><select class="x-path">${pathOptions(r.path, false)}</select></td>
      <td><select class="x-unit">${unitOptions(baseField, r.from)}</select></td>
      <td><input class="x-scale mini-input" type="number" step="any" value="${esc(r.scale)}" /></td>
      <td><input class="x-offset mini-input" type="number" step="any" value="${esc(r.offset)}" /></td>
      <td class="td-mono td-r x-result">—</td>
      <td><button class="w-x x-del" type="button" title="Remove">×</button></td></tr>`).join("");
    body.querySelectorAll("tr").forEach((tr) => {
      const i = Number(tr.dataset.i);
      tr.querySelector(".x-path").addEventListener("change", (e) => {
        extras[i].path = e.target.value;
        const p = paths.find((x) => x.path === e.target.value);
        if (p && p.unit_hint && baseField.unit && unitDim[p.unit_hint] === unitDim[baseField.unit]) {
          extras[i].from = p.unit_hint;
          const u = tr.querySelector(".x-unit"); if (u) u.value = p.unit_hint;
        }
        preview();
      });
      tr.querySelector(".x-unit").addEventListener("change", (e) => { extras[i].from = e.target.value; preview(); });
      tr.querySelector(".x-scale").addEventListener("input", (e) => { extras[i].scale = e.target.value; preview(); });
      tr.querySelector(".x-offset").addEventListener("input", (e) => { extras[i].offset = e.target.value; preview(); });
      tr.querySelector(".x-del").addEventListener("click", () => { extras.splice(i, 1); drawSensors(); preview(); });
    });
  }

  function drawRows() {
    document.getElementById("map-rows").innerHTML = rows.map((r, i) => {
      const c = contract[i];
      const hint = !c.required && (info.unlocks || {})[c.key] ? `<div class="unlock">unlocks ${esc(info.unlocks[c.key].join(", "))}</div>` : "";
      const numeric = c.kind !== "text";
      return `<tr data-i="${i}">
        <td><b>${esc(c.label || c.key)}</b> ${c.required ? `<span class="badge warn">required</span>` : `<span class="badge muted">optional</span>`}
          <div class="td-mono td-dim">${esc(c.key)}${c.unit ? ` · ${esc(c.unit)}` : ""}</div>${hint}</td>
        <td><select class="m-path">${pathOptions(r.path, !numeric)}</select></td>
        <td>${numeric && c.unit ? `<select class="m-unit">${unitOptions(c, r.from)}</select>` : `<span class="td-dim">—</span>`}</td>
        <td>${numeric ? `<input class="m-scale mini-input" type="number" step="any" value="${esc(r.scale)}" />` : ""}</td>
        <td>${numeric ? `<input class="m-offset mini-input" type="number" step="any" value="${esc(r.offset)}" />` : ""}</td>
        <td class="td-mono td-r m-result">—</td></tr>`;
    }).join("");
    document.getElementById("map-rows").querySelectorAll("tr").forEach((tr) => {
      const i = Number(tr.dataset.i);
      tr.querySelectorAll("select, input").forEach((el) => el.addEventListener(el.tagName === "INPUT" ? "input" : "change", () => {
        const r = rows[i];
        if (el.classList.contains("m-path")) {
          r.path = el.value;
          const p = paths.find((x) => x.path === el.value);
          if (p && p.unit_hint && contract[i].unit && unitDim[p.unit_hint] === unitDim[contract[i].unit]) {
            r.from = p.unit_hint;
            const u = tr.querySelector(".m-unit"); if (u) u.value = p.unit_hint;
          }
        }
        if (el.classList.contains("m-unit")) r.from = el.value;
        if (el.classList.contains("m-scale")) r.scale = el.value;
        if (el.classList.contains("m-offset")) r.offset = el.value;
        preview();
      }));
    });
    document.getElementById("ts-path").innerHTML = `<option value="">— receive time —</option>` +
      paths.map((p) => `<option value="${esc(p.path)}" ${p.path === tsRow.path ? "selected" : ""}>${esc(p.path)} (${esc(String(p.example).slice(0, 26))})</option>`).join("") +
      (tsRow.path && !paths.some((p) => p.path === tsRow.path) ? `<option selected>${esc(tsRow.path)}</option>` : "");
  }

  function drawSample() {
    const s = samples[sampleIdx];
    document.getElementById("sample-body").innerHTML = s ? `
      <div class="table-wrap scroll-table" style="max-height:260px"><table><thead><tr><th>Path</th><th>Type</th><th>Example</th></tr></thead><tbody>
        ${paths.map((p) => `<tr><td class="td-mono">${esc(p.path)}</td><td class="td-dim">${esc(p.type)}${p.unit_hint ? ` · ${esc(p.unit_hint)}?` : ""}</td>
          <td class="td-mono">${esc(String(p.example))}</td></tr>`).join("")}
      </tbody></table></div>
      <pre class="code" style="margin-top:10px">${esc(JSON.stringify(s.payload, null, 2))}</pre>` :
      `<div class="empty">No data from the source yet. For HTTP push, send a message first.</div>`;
  }

  async function capture() {
    const btn = document.getElementById("cap");
    if (btn) { btn.disabled = true; btn.textContent = "Capturing…"; }
    try {
      const res = await api.captureSamples(instanceId);
      samples = res.samples || [];
      paths = res.paths || [];
      sampleIdx = 0;
      document.getElementById("sample-pick").innerHTML = samples.map((s, i) =>
        `<option value="${i}">${s.source === "live" ? "live read" : "stored " + new Date(s.ts).toLocaleTimeString()}</option>`).join("");
      if (res.live_error) toast(`Live read failed: ${res.live_error}`);
      const sug = res.suggestions || {};
      rows.forEach((r, i) => {
        if (!r.path && sug[r.key]) {
          r.path = sug[r.key].path;
          const p = paths.find((x) => x.path === r.path);
          const c = contract[i];
          if (p && p.unit_hint && c.unit && unitDim[p.unit_hint] === unitDim[c.unit]) r.from = p.unit_hint;
        }
      });
      if (!tsRow.path && sug.timestamp) tsRow.path = sug.timestamp.path;
      drawRows();
      drawSample();
      preview();
      if (Object.keys(sug).length) toast("Suggested matches filled in. Check them before saving.");
    } catch (err) { toast(err.message); }
    finally { if (btn) { btn.disabled = false; btn.textContent = "Capture sample"; } }
  }

  let timer;
  function preview() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const box = document.getElementById("preview");
      if (!box) return;
      const payload = samples[sampleIdx] ? samples[sampleIdx].payload : undefined;
      try {
        const res = await api.previewMapping(instanceId, definition(), payload);
        document.querySelectorAll("#map-rows tr").forEach((tr) => {
          const key = contract[Number(tr.dataset.i)].key;
          const v = res.values[key];
          const err = res.errors.find((e) => e.field === key);
          const cell = tr.querySelector(".m-result");
          cell.innerHTML = err ? `<span class="bad">${esc(err.code)}</span>` : v == null ? "—" : esc(typeof v === "number" ? fmt(v, 4) : v);
        });
        document.querySelectorAll("#sensor-rows tr").forEach((tr) => {
          const key = `${baseKey}.${Number(tr.dataset.i) + 2}`;
          const v = res.values[key];
          const err = res.errors.find((e) => e.field === key);
          const cell = tr.querySelector(".x-result");
          if (cell) cell.innerHTML = err ? `<span class="bad">${esc(err.code)}</span>` : v == null ? "—" : esc(fmt(v, 4));
        });
        const out = res.outputs || {};
        const primary = out[roles.primary];
        const status = out[roles.status || "quality.status"];
        box.innerHTML = res.errors.length ? `<div class="error-box">${res.errors.map((e) => `${esc(e.field || "")}: ${esc(e.message)}`).join("<br/>")}</div>` : `
          <div class="pipeline">
            <div><span class="k">raw</span><span class="td-mono">${esc(rawOf(res.payload))}</span></div><i>→</i>
            <div><span class="k">mapped</span><span class="td-mono">${esc(fmt(res.values[roles.input], 4))} ${esc(contract[0].unit || "")}</span></div><i>→</i>
            <div><span class="k">${esc(roles.label || "value")}</span><span class="td-mono">${esc(fmt(primary))} ${esc(roles.unit || "")}</span></div><i>→</i>
            <div><span class="k">status</span><span class="badge ${status === "PASS" ? "good" : status === "FAIL" ? "bad" : "muted"}">${esc(status || "no limits")}</span></div>
          </div>
          ${res.context.timestamp ? `<div class="hint">timestamp → ${esc(res.context.timestamp)}</div>` : ""}`;
      } catch (err) { box.innerHTML = `<div class="error-box">${esc(err.message)}</div>`; }
    }, 250);
  }

  function rawOf(payload) {
    const r = rows.find((x, i) => contract[i].required && x.path);
    if (!r || !payload) return "—";
    const v = r.path.split(/\.|\[|\]/).filter(Boolean).reduce((o, k) => (o == null ? o : o[k]), payload);
    return v == null ? "—" : String(v);
  }

  async function save() {
    const btn = document.getElementById("save");
    btn.disabled = true;
    try {
      const saved = await api.saveMapping(instanceId, definition());
      toast(`Mapping saved (v${saved.version})`);
      ctx.navigate(`#/setup/${instanceId}/commission`);
    } catch (err) { toast(err.message); btn.disabled = false; }
  }

  async function showFailures() {
    const box = document.getElementById("failures");
    const res = await api.mappingFailures(instanceId);
    box.innerHTML = `<div class="table-wrap scroll-table" style="max-height:220px;margin-top:10px"><table>
      <thead><tr><th>Time</th><th>Error</th><th>Payload</th></tr></thead><tbody>
      ${res.failures.slice(0, 30).map((f) => `<tr><td class="td-mono td-dim">${esc(new Date(f.created_at).toLocaleTimeString())}</td>
        <td>${esc(f.errors.map((e) => e.message).join("; "))}</td>
        <td class="td-mono td-dim">${esc(JSON.stringify(f.raw_payload).slice(0, 80))}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  if (editing) { drawEditor(); capture(); } else drawPreset();
  setCleanup(() => clearTimeout(timer));
}

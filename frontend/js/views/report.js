import { api } from "../api.js";
import { esc, fmt, toast } from "../ui.js";

// Shift report (printable to PDF from the browser) and the change history (audit trail).
export async function renderReport(view, ctx, setCleanup, instanceId) {
  const now = new Date();
  const today = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  view.innerHTML = `
    <div class="page report">
      <div class="crumbs no-print"><a href="#/apps">Applications</a><span class="sep">/</span>
        <a href="#/app/${esc(instanceId)}">Dashboard</a><span class="sep">/</span><b>Shift report</b></div>
      <div class="run-actions no-print" style="margin-bottom:14px">
        <input type="date" id="day" class="limit-input" style="width:170px" value="${today}" />
        <button class="btn btn-sm" id="print">Print / save as PDF</button>
      </div>
      <div id="rep"><div class="spinner">loading…</div></div>
    </div>`;

  async function load() {
    const host = document.getElementById("rep");
    try {
      const r = await api.report(instanceId, document.getElementById("day").value);
      const u = esc(r.unit);
      host.innerHTML = `
        <div class="rep-head">
          <div><h1>${esc(r.instance.name)}</h1>
            <div class="hint">${esc(r.instance.template)} · ${esc(r.instance.plant || "")} ${esc(r.instance.asset_id || "")}</div></div>
          <div class="rep-meta"><b>${esc(r.date)}</b><span>${esc(r.timezone)}</span>
            <span>target ${fmt(r.target)} · limits ${fmt(r.lower)} – ${fmt(r.upper)} ${u}</span></div>
        </div>
        <div class="table-wrap"><table><thead><tr><th>Shift</th><th>Window</th><th class="td-r">Samples</th>
          <th class="td-r">Pass %</th><th class="td-r">Fail</th><th class="td-r">Avg ${u}</th><th class="td-r">Min</th>
          <th class="td-r">Max</th><th class="td-r">σ</th><th class="td-r">Alerts</th></tr></thead><tbody>
          ${r.shifts.map((s) => `<tr><td><b>${esc(s.name)}</b></td>
            <td class="td-mono td-dim">${new Date(s.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: r.timezone })}–${new Date(s.end).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: r.timezone })}</td>
            <td class="td-r">${s.samples}</td><td class="td-r">${s.pass_percentage == null ? "—" : s.pass_percentage + "%"}</td>
            <td class="td-r ${s.fail_count ? "bad" : ""}">${s.fail_count}</td><td class="td-r td-mono">${fmt(s.average)}</td>
            <td class="td-r td-mono">${fmt(s.minimum)}</td><td class="td-r td-mono">${fmt(s.maximum)}</td>
            <td class="td-r td-mono">${fmt(s.std_dev, 4)}</td><td class="td-r">${s.alerts}</td></tr>`).join("")}
        </tbody></table></div>
        ${r.shifts.filter((s) => s.samples).map((s) => `
          <h2 style="margin-top:22px">Shift ${esc(s.name)}</h2>
          <div class="grid cols-2">
            <div class="table-wrap"><table><thead><tr><th>Hour</th><th class="td-r">Samples</th><th class="td-r">Avg ${u}</th><th class="td-r">Fail</th></tr></thead>
              <tbody>${s.hourly.map((h) => `<tr><td class="td-mono">${esc(h.hour)}</td><td class="td-r">${h.samples}</td>
                <td class="td-r td-mono">${fmt(h.average)}</td><td class="td-r">${h.fail_count}</td></tr>`).join("")}</tbody></table></div>
            ${s.groups.length ? `<div class="table-wrap"><table><thead><tr><th>Product</th><th class="td-r">Samples</th><th class="td-r">Avg ${u}</th><th class="td-r">Fail</th></tr></thead>
              <tbody>${s.groups.map((g) => `<tr><td class="td-mono">${esc(g.group)}</td><td class="td-r">${g.samples}</td>
                <td class="td-r td-mono">${fmt(g.average)}</td><td class="td-r">${g.fail_count}</td></tr>`).join("")}</tbody></table></div>` : ""}
          </div>`).join("")}
        <p class="hint" style="margin-top:20px">Generated ${esc(new Date().toLocaleString())} · Rajdeep IoT platform</p>`;
    } catch (err) { host.innerHTML = `<div class="error-box">${esc(err.message)}</div>`; }
  }
  document.getElementById("day").addEventListener("change", load);
  document.getElementById("print").addEventListener("click", () => window.print());
  load();
}

export async function renderHistory(view, ctx, setCleanup, instanceId) {
  view.innerHTML = `<div class="page narrow"><div class="crumbs"><a href="#/apps">Applications</a><span class="sep">/</span>
    <a href="#/app/${esc(instanceId)}">Dashboard</a><span class="sep">/</span><b>Change history</b></div>
    <h1>Change history</h1><p class="lead">Who changed calibration, limits, mapping and alerts, and when.</p>
    <div id="h"><div class="spinner">loading…</div></div></div>`;
  try {
    const rows = (await api.history(instanceId)).history || [];
    document.getElementById("h").innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th>When</th><th>Who</th>
      <th>What</th><th>Before</th><th>After</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td class="td-mono td-dim">${esc(new Date(r.created_at).toLocaleString())}</td><td>${esc(r.username || "")}</td>
        <td><span class="tag">${esc(r.action)}</span></td>
        <td class="td-mono td-dim wrap">${esc(r.old_value ? JSON.stringify(r.old_value) : "")}</td>
        <td class="td-mono wrap">${esc(r.new_value ? JSON.stringify(r.new_value) : "")}</td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty">No changes recorded yet.</div>`;
  } catch (err) { toast(err.message); }
}

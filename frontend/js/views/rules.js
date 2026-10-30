import { api } from "../api.js";
import { esc, toast } from "../ui.js";
import { stepper } from "./stepper.js";

const RULES = {
  fail_streak: { label: "Consecutive FAIL readings", param: "count", unit: "readings" },
  offline: { label: "Source offline", param: "seconds", unit: "seconds" },
  mapping_failure: { label: "Data cannot be mapped", param: null },
};

// WhatsApp alert settings are browser-local for now; the delivery backend is
// not wired up yet. The shape mirrors the email alert config so it can be sent
// to the API unchanged once the backend exists.
const WA_DEFAULTS = {
  enabled: false,
  recipient_phone: "",
  template: "ucap_alert",
  alerts: { fail_streak: true, offline: true, mapping_failure: true },
  cooldown: { enabled: true, minutes: 5 },
};
const WA_STORAGE_KEY = "ucap_whatsapp_alerts";

function loadWhatsAppConfig() {
  try {
    const saved = JSON.parse(localStorage.getItem(WA_STORAGE_KEY) || "null") || {};
    return {
      ...WA_DEFAULTS,
      ...saved,
      alerts: { ...WA_DEFAULTS.alerts, ...(saved.alerts || {}) },
      cooldown: { ...WA_DEFAULTS.cooldown, ...(saved.cooldown || {}) },
    };
  } catch {
    return { ...WA_DEFAULTS };
  }
}

// Step 5 — limits (rendered from the template's input definitions) + alert rules.
export async function renderRules(view, ctx, setCleanup, instanceId) {
  const [instance, al, email] = await Promise.all([
    api.instance(instanceId),
    api.alerts(instanceId),
    api.emailAlertConfig().catch(() => null),
  ]);
  const cfg = instance.config || {};
  const inputs = (instance.template.inputs || []).filter((f) => !f.key.startsWith("calibration."));
  const inputByKey = Object.fromEntries(inputs.map((f) => [f.key, f]));
  const truthy = (value) => ["1", "true", "yes", "on"].includes(String(value ?? "").toLowerCase());
  const rules = al.rules || {};
  const isAdmin = ["superadmin", "admin"].includes(ctx.session?.user?.role);
  let emailCfg = email;
  let waCfg = loadWhatsAppConfig();

  const emailAuthMarkup = (config) => {
    const googleIcon = `<svg width="16" height="16" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.41-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
      <path fill="#FBBC05" d="M10.53 28.59A14.5 14.5 0 0 1 9.5 24c0-1.59.28-3.14.76-4.59l-7.98-6.19A23.99 23.99 0 0 0 0 24c0 3.8.89 7.4 2.56 10.78l7.97-6.19z"/>
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
    </svg>`;
    if (!config.google_configured) {
      return `<p class="email-status bad">Google OAuth is not configured. Set GOOGLE_OAUTH_CLIENT_ID and
        GOOGLE_OAUTH_CLIENT_SECRET in backend/.env.</p>`;
    }
    if (config.gmail_authenticated) {
      return `<span class="email-badge good">Gmail authenticated</span>
        <button class="btn secondary btn-sm" id="econnect" type="button">${googleIcon} Reconnect Google</button>`;
    }
    return `<button class="btn secondary btn-sm" id="econnect" type="button">${googleIcon} Sign in with Google</button>`;
  };

  view.innerHTML = `
    <div class="page">
      ${stepper("rules", instanceId)}
      <h1>Limits &amp; alerts</h1>
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
          <button class="btn secondary btn-sm" id="asave">Save alert rules</button>
        </div>
      </div>
      ${isAdmin ? (
        emailCfg ? `<div class="panel email-panel">
          <h3>Email alerts (Gmail)</h3>
          <label class="setting-row"><span>Enable email alerts</span>
            <input type="checkbox" class="setting-toggle email-enabled" ${emailCfg.enabled ? "checked" : ""} /></label>
          <div class="email-auth-row" id="email-auth">${emailAuthMarkup(emailCfg)}</div>
          <p class="hint">Authorised redirect URI:
            <span class="td-mono">${esc(emailCfg.redirect_uri)}</span></p>
          <div class="field">
            <label for="email-recipient">Recipient email</label>
            <input id="email-recipient" type="email" value="${esc(emailCfg.recipient_email)}"
              placeholder="alerts@company.com" />
          </div>
          <div class="email-rule-list">
            ${Object.entries(RULES).map(([type, r]) => `<label class="setting-row">
              <span>Email on: ${esc(r.label)}</span>
              <input type="checkbox" class="setting-toggle email-rule" data-rule="${type}"
                ${emailCfg.alerts?.[type] !== false ? "checked" : ""} /></label>`).join("")}
          </div>
          <div class="field">
            <label for="email-cooldown">Cooldown (minutes)</label>
            <input id="email-cooldown" type="number" min="1" max="1440"
              value="${esc(emailCfg.cooldown?.minutes ?? 5)}" />
          </div>
          <div class="email-actions">
            <button class="btn secondary btn-sm" id="esave" type="button">Save email settings</button>
            <button class="btn secondary btn-sm" id="etest" type="button">Send test email</button>
            <span class="email-status" id="email-status"></span>
          </div>
        </div>` : `<div class="panel email-panel">
          <h3>Email alerts (Gmail)</h3>
          <p class="hint">Could not load email settings. Refresh the page to try again.</p>
        </div>`
      ) : `<div class="panel email-panel">
        <h3>Email alerts (Gmail)</h3>
        <p class="hint">Email alert settings require a company administrator.</p>
      </div>`}
      ${isAdmin ? `<div class="panel whatsapp-panel">
        <h3>WhatsApp alerts</h3>
        <label class="setting-row"><span>Enable WhatsApp alerts</span>
          <input type="checkbox" class="setting-toggle wa-enabled" ${waCfg.enabled ? "checked" : ""} /></label>
        <div class="whatsapp-status-row">
          <span class="email-badge">Not connected</span>
          <span class="hint">Alerts are delivered with the WhatsApp Business Cloud API.</span>
        </div>
        <div class="field">
          <label for="wa-recipient">Client WhatsApp number</label>
          <input id="wa-recipient" type="tel" inputmode="tel" value="${esc(waCfg.recipient_phone)}"
            placeholder="+91 98765 43210" />
          <p class="hint">Include the country code. The client must opt in to receive alerts.</p>
        </div>
        <div class="field">
          <label for="wa-template">Message template</label>
          <input id="wa-template" type="text" value="${esc(waCfg.template)}" placeholder="ucap_alert" />
          <p class="hint">Business-initiated messages require a pre-approved WhatsApp template.</p>
        </div>
        <div class="email-rule-list">
          ${Object.entries(RULES).map(([type, r]) => `<label class="setting-row">
            <span>WhatsApp on: ${esc(r.label)}</span>
            <input type="checkbox" class="setting-toggle wa-rule" data-rule="${type}"
              ${waCfg.alerts?.[type] !== false ? "checked" : ""} /></label>`).join("")}
        </div>
        <div class="field">
          <label for="wa-cooldown">Cooldown (minutes)</label>
          <input id="wa-cooldown" type="number" min="1" max="1440"
            value="${esc(waCfg.cooldown?.minutes ?? 5)}" />
        </div>
        <div class="email-actions">
          <button class="btn secondary btn-sm" id="wasave" type="button">Save WhatsApp settings</button>
          <button class="btn secondary btn-sm" id="watest" type="button">Send test message</button>
          <span class="email-status" id="wa-status"></span>
        </div>
      </div>` : `<div class="panel whatsapp-panel">
        <h3>WhatsApp alerts</h3>
        <p class="hint">WhatsApp alert settings require a company administrator.</p>
      </div>`}
      <button class="btn" id="next">Continue to graphs →</button>
    </div>`;

  function setEmailStatus(text, cls) {
    const el = document.getElementById("email-status");
    if (!el) return;
    el.textContent = text;
    el.className = "email-status" + (cls ? ` ${cls}` : "");
  }

  function collectEmailConfig() {
    const alerts = {};
    view.querySelectorAll(".email-rule").forEach((el) => {
      alerts[el.dataset.rule] = el.checked;
    });
    const minutes = Number(document.getElementById("email-cooldown")?.value);
    return {
      enabled: document.querySelector(".email-enabled")?.checked || false,
      recipient_email: document.getElementById("email-recipient")?.value.trim() || "",
      alerts,
      cooldown: { enabled: true, minutes: Number.isFinite(minutes) ? minutes : 5 },
    };
  }

  async function saveEmail(quiet) {
    emailCfg = await api.updateEmailAlertConfig(collectEmailConfig());
    if (!quiet) toast("Email settings saved");
    return true;
  }

  async function connectGoogle() {
    if (!emailCfg) return;
    setEmailStatus("Redirecting to Google...");
    try {
      const { auth_url } = await api.emailAlertAuthUrl();
      try {
        sessionStorage.setItem("ucap_oauth_return", location.hash || "#/apps");
      } catch { /* private mode: the callback falls back to #/apps */ }
      // Redirect this tab instead of opening a popup. Popup blockers and
      // extensions can silently swallow window.open(), which made the button
      // look dead on some browsers/domains.
      window.location.href = auth_url;
    } catch (e) {
      setEmailStatus(e.message, "bad");
      throw e;
    }
  }

  async function testEmail() {
    setEmailStatus("Sending test email...");
    try {
      await saveEmail(true);
      await api.testEmailAlert();
      setEmailStatus("Test email sent.", "good");
    } catch (e) {
      setEmailStatus(e.message, "bad");
    }
  }

  function bindEmailAuth() {
    const btn = document.getElementById("econnect");
    if (btn) btn.addEventListener("click", () => connectGoogle().catch((e) => toast(e.message)));
  }

  function setWaStatus(text, cls) {
    const el = document.getElementById("wa-status");
    if (!el) return;
    el.textContent = text;
    el.className = "email-status" + (cls ? ` ${cls}` : "");
  }

  function collectWaConfig() {
    const alerts = {};
    view.querySelectorAll(".wa-rule").forEach((el) => {
      alerts[el.dataset.rule] = el.checked;
    });
    const minutes = Number(document.getElementById("wa-cooldown")?.value);
    return {
      enabled: document.querySelector(".wa-enabled")?.checked || false,
      recipient_phone: document.getElementById("wa-recipient")?.value.trim() || "",
      template: document.getElementById("wa-template")?.value.trim() || "",
      alerts,
      cooldown: { enabled: true, minutes: Number.isFinite(minutes) ? minutes : 5 },
    };
  }

  function saveWhatsApp(quiet) {
    waCfg = collectWaConfig();
    localStorage.setItem(WA_STORAGE_KEY, JSON.stringify(waCfg));
    if (!quiet) setWaStatus("Saved on this device. Delivery is not connected yet.", "good");
  }

  function testWhatsApp() {
    const cfg = collectWaConfig();
    if (!cfg.recipient_phone) {
      setWaStatus("Enter a WhatsApp number first.", "bad");
      return;
    }
    saveWhatsApp(true);
    setWaStatus("WhatsApp delivery is not connected on the backend yet.", "bad");
  }

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
  if (isAdmin && emailCfg) {
    document.getElementById("esave").addEventListener("click", () => saveEmail().catch((e) => toast(e.message)));
    document.getElementById("etest").addEventListener("click", () => testEmail());
    bindEmailAuth();
  }
  if (isAdmin) {
    document.getElementById("wasave").addEventListener("click", () => saveWhatsApp());
    document.getElementById("watest").addEventListener("click", () => testWhatsApp());
  }
  document.getElementById("next").addEventListener("click", async () => {
    try {
      if (!(await saveLimits(true))) return;
      await saveRules(true);
      if (isAdmin && emailCfg) await saveEmail(true);
      if (isAdmin) saveWhatsApp(true);
      ctx.navigate(`#/setup/${instanceId}/graphs`);
    } catch (e) { toast(e.message); }
  });
}

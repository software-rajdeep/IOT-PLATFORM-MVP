import { api } from "./api.js";
import { store } from "./store.js";
import { esc } from "./ui.js";
import { renderLogin } from "./views/login.js";
import { renderDevices } from "./views/device.js";
import { renderPurposes } from "./views/purpose.js";
import { renderTemplates } from "./views/template.js";
import { renderApplications } from "./views/apps.js";
import { renderConnection } from "./views/connection.js";
import { renderDashboard } from "./views/dashboard.js";
import { renderCatalog } from "./views/catalog.js";
import { renderMapping } from "./views/mapping.js";
import { renderCommission } from "./views/commission.js";
import { renderRules } from "./views/rules.js";
import { renderGraphs } from "./views/graphs.js";
import { renderActivate } from "./views/activate.js";
import { renderHistory, renderReport } from "./views/report.js";

const SETUP_STEPS = {
  map: renderMapping, commission: renderCommission, rules: renderRules,
  graphs: (v, c, s, id) => renderGraphs(v, c, s, id, "setup"), activate: renderActivate,
};

const app = document.getElementById("app");
let cleanup = null;

function setCleanup(fn) {
  if (typeof cleanup === "function") { try { cleanup(); } catch { /* ignore */ } }
  cleanup = typeof fn === "function" ? fn : null;
}

export async function navigate(hash) {
  if (location.hash === hash) await route();
  else location.hash = hash;
}

const ctx = {
  navigate,
  get session() { return store.session(); },
  get selection() { return store.selection(); },
};

function navLink(hash, label, active) {
  return `<a href="${hash}" class="${active ? "active" : ""}">${label}</a>`;
}

function renderShell(authenticated, session, activePath) {
  if (!authenticated) {
    app.innerHTML = `<div id="view"></div>`;
    return;
  }
  const who = session && session.user ? session.user.username : "";
  const company = session && session.company ? session.company.company_name : "";
  app.innerHTML = `
    <header class="topbar">
      <div class="brand">
        <span class="dot">U</span>
        <span>Rajdeep IoT<small>INDUSTRIAL PLATFORM</small></span>
      </div>
      <nav>
        ${navLink("#/catalog", "New solution", ["catalog", "devices", "purposes", "templates", "connection", "setup"].includes(activePath))}
        ${navLink("#/apps", "My applications", ["apps", "app", "report", "history"].includes(activePath))}
      </nav>
      <div class="spacer"></div>
      <div class="company-chip">
        <span class="who">${esc(company)}</span><span>·</span><span>${esc(who)}</span>
      </div>
      <button class="btn-ghost" id="signout">Sign out</button>
    </header>
    <div id="view"></div>`;
  document.getElementById("signout").addEventListener("click", () => {
    store.clearSession();
    store.clearSelection();
    navigate("#/catalog");
  });
}

async function route() {
  const raw = location.hash.replace(/^#/, "") || "/apps";
  const parts = raw.split("/").filter(Boolean);
  const activePath = parts[0] || "apps";

  if (!store.token()) {
    setCleanup(null);
    renderShell(false);
    renderLogin(document.getElementById("view"), ctx);
    return;
  }

  let session = store.session();
  if (!session || !session.user) {
    try {
      const me = await api.me();
      session = { user: me.user, company: me.company };
      sessionStorage.setItem("ucap_session", JSON.stringify(session));
    } catch {
      store.clearSession();
      renderShell(false);
      renderLogin(document.getElementById("view"), ctx);
      return;
    }
  }

  setCleanup(null);
  renderShell(true, session, activePath);
  const view = document.getElementById("view");
  view.innerHTML = `<div class="spinner">loading…</div>`;

  try {
    if (parts[0] === "catalog") await renderCatalog(view, ctx, setCleanup);
    else if (parts[0] === "devices") await renderDevices(view, ctx, setCleanup);
    else if (parts[0] === "setup" && parts[1] && SETUP_STEPS[parts[2]]) await SETUP_STEPS[parts[2]](view, ctx, setCleanup, parts[1]);
    else if (parts[0] === "app" && parts[1] && parts[2] === "customize") await renderGraphs(view, ctx, setCleanup, parts[1], "customize");
    else if (parts[0] === "report" && parts[1]) await renderReport(view, ctx, setCleanup, parts[1]);
    else if (parts[0] === "history" && parts[1]) await renderHistory(view, ctx, setCleanup, parts[1]);
    else if (parts[0] === "purposes") await renderPurposes(view, ctx, setCleanup);
    else if (parts[0] === "templates") await renderTemplates(view, ctx, setCleanup);
    else if (parts[0] === "connection") await renderConnection(view, ctx, setCleanup);
    else if (parts[0] === "apps") await renderApplications(view, ctx, setCleanup);
    else if (parts[0] === "app" && parts[1]) await renderDashboard(view, ctx, setCleanup, parts[1]);
    else if ((parts[0] === "calibrate" || parts[0] === "limits") && parts[1]) {
      location.hash = `#/setup/${parts[1]}/${parts[0] === "calibrate" ? "commission" : "rules"}`;
    }
    else await renderApplications(view, ctx, setCleanup);
  } catch (err) {
    view.innerHTML = `<div class="page narrow"><div class="error-box">${esc(err.message)}</div></div>`;
  }
}

window.addEventListener("hashchange", route);
route();
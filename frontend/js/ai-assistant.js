// Rajdeep AI Assistant — floating chat for the dependency-free SPA.
// Talks to POST /api/v1/ai/chat through the authenticated API client. The
// provider key and all analytics stay on the server; this module only renders
// the response (message, structured data, time range, units and source).
import { api } from "./api.js";
import { esc } from "./ui.js";

const MAX_MESSAGE = 4000;
const SESSION_ERROR = /session expired|not authenticated|invalid or expired token|unauthorized|401/i;
const NETWORK_ERROR = /failed to fetch|networkerror|network request failed|load failed|network error/i;

const GENERAL_SUGGESTIONS = [
  "What machines do I have?",
  "What is the average thickness today?",
  "Show out-of-range readings for today.",
  "Are my sensors online?",
];

const MACHINE_SUGGESTIONS = [
  "Summarise today for this machine.",
  "What was the average thickness yesterday?",
  "Show out-of-range readings for this machine today.",
  "Are this machine's sensors online?",
];

const state = {
  mounted: false,
  visible: false,
  open: false,
  sending: false,
  messages: [],
  conversationId: null,
  machineId: null,
  lastUserText: "",
  sequence: 0,
};

let root = null;
let fab = null;
let panel = null;
let log = null;
let input = null;
let send = null;
let contextLabel = null;
let statusLive = null;
let newButton = null;
let closeButton = null;

export function initAssistant() {
  if (state.mounted) return;
  state.mounted = true;

  document.body.insertAdjacentHTML("beforeend", `
    <div class="ai-root" id="ai-root" hidden>
      <button class="ai-fab" id="ai-fab" type="button"
        aria-label="Open Rajdeep AI Assistant" aria-expanded="false" aria-controls="ai-panel"
        title="Rajdeep AI Assistant">
        <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
          stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 11.5a8.4 8.4 0 0 1-8.5 8.3 9.2 9.2 0 0 1-3.8-.8L3 20.5l1.6-4.4a8 8 0 0 1-1.1-4.1A8.4 8.4 0 0 1 12 3.2a8.4 8.4 0 0 1 9 8.3Z"/>
          <path d="M8.5 11.5h.01M12 11.5h.01M15.5 11.5h.01"/>
        </svg>
      </button>

      <section class="ai-panel" id="ai-panel" role="dialog" aria-modal="true"
        aria-labelledby="ai-title" hidden>
        <header class="ai-head">
          <div class="ai-brand">
            <span class="ai-logo" aria-hidden="true">AI</span>
            <div class="ai-brand-text">
              <h2 id="ai-title">Rajdeep AI Assistant</h2>
              <p id="ai-context">All applications</p>
            </div>
          </div>
          <div class="ai-head-actions">
            <button class="ai-icon-btn" id="ai-new" type="button" title="New chat" aria-label="Start a new chat">
              <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
                stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
            </button>
            <button class="ai-icon-btn" id="ai-close" type="button" title="Close" aria-label="Close assistant">
              <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
                stroke-width="2" stroke-linecap="round"><path d="m6 6 12 12M18 6 6 18"/></svg>
            </button>
          </div>
        </header>

        <div class="ai-log" id="ai-log" role="log" aria-label="Conversation"></div>
        <div class="sr-only" id="ai-status" role="status" aria-live="polite"></div>

        <form class="ai-composer" id="ai-form" autocomplete="off">
          <label class="sr-only" for="ai-input">Message Rajdeep AI Assistant</label>
          <textarea class="ai-input" id="ai-input" rows="1" maxlength="${MAX_MESSAGE}"
            placeholder="Ask about machines, thickness or alerts…"
            aria-label="Message Rajdeep AI Assistant"></textarea>
          <button class="ai-send" id="ai-send" type="submit" aria-label="Send message" disabled>
            <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
              stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>
            </svg>
          </button>
        </form>
        <div class="ai-foot">Answers use your live platform data. Verify critical decisions.</div>
      </section>
    </div>`);

  root = document.getElementById("ai-root");
  fab = document.getElementById("ai-fab");
  panel = document.getElementById("ai-panel");
  log = document.getElementById("ai-log");
  input = document.getElementById("ai-input");
  send = document.getElementById("ai-send");
  contextLabel = document.getElementById("ai-context");
  statusLive = document.getElementById("ai-status");
  newButton = document.getElementById("ai-new");
  closeButton = document.getElementById("ai-close");

  fab.addEventListener("click", () => (state.open ? closePanel() : openPanel()));
  closeButton.addEventListener("click", () => closePanel());
  newButton.addEventListener("click", () => newChat());
  document.getElementById("ai-form").addEventListener("submit", (event) => {
    event.preventDefault();
    ask(input.value);
  });

  input.addEventListener("input", () => {
    autoSize();
    updateComposer();
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!send.disabled) ask(input.value);
    }
  });

  panel.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closePanel();
      return;
    }
    if (event.key === "Tab") trapFocus(event);
  });

  log.addEventListener("click", (event) => {
    const retry = event.target.closest("[data-ai-retry]");
    if (retry) {
      const index = Number(retry.dataset.aiRetry);
      const message = state.messages[index];
      const text = message && message.retryText;
      if (text) {
        state.messages.splice(index, 1);
        ask(text, { replay: true });
      }
      return;
    }
    if (event.target.closest("[data-ai-new]")) {
      newChat();
      return;
    }
    if (event.target.closest("[data-ai-signin]")) {
      closePanel(false);
      window.dispatchEvent(new Event("hashchange"));
      return;
    }
    const suggestion = event.target.closest("[data-ai-q]");
    if (suggestion) {
      ask(suggestion.dataset.aiQ || "");
      if (state.open) input.focus();
    }
  });

  updateContext();
  renderLog();
  updateComposer();
}

export function setAssistantVisible(visible) {
  initAssistant();
  state.visible = Boolean(visible);
  root.hidden = !state.visible;
  if (!state.visible) closePanel(false);
}

export function setAssistantMachine(machineId) {
  initAssistant();
  const next = machineId ? String(machineId) : null;
  if (state.machineId === next) return;
  state.machineId = next;
  updateContext();
  if (state.open && state.messages.length === 0) renderLog();
}

export function resetAssistant() {
  state.sequence += 1;
  state.sending = false;
  state.open = false;
  state.messages = [];
  state.conversationId = null;
  state.lastUserText = "";
  if (panel) {
    panel.hidden = true;
    document.body.classList.remove("ai-open");
  }
  if (fab) fab.setAttribute("aria-expanded", "false");
  if (send) send.disabled = true;
  if (input) input.value = "";
  updateContext();
  renderLog();
}

function openPanel() {
  if (!state.visible) return;
  state.open = true;
  panel.hidden = false;
  document.body.classList.add("ai-open");
  fab.setAttribute("aria-expanded", "true");
  setStatus("Assistant opened.");
  renderLog();
  input.focus();
  requestAnimationFrame(() => {
    if (state.open) input.focus();
  });
}

function closePanel(returnFocus = true) {
  if (!state.open) {
    if (panel) panel.hidden = true;
    document.body.classList.remove("ai-open");
    return;
  }
  state.open = false;
  panel.hidden = true;
  document.body.classList.remove("ai-open");
  fab.setAttribute("aria-expanded", "false");
  if (returnFocus && state.visible) fab.focus();
}

function trapFocus(event) {
  const focusables = Array.from(panel.querySelectorAll(
    'button:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
  )).filter((node) => !node.hidden && node.offsetParent !== null);
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function newChat() {
  state.sequence += 1;
  state.sending = false;
  state.messages = [];
  state.conversationId = null;
  state.lastUserText = "";
  input.value = "";
  autoSize();
  updateComposer();
  renderLog();
  setStatus("New chat started.");
  if (state.open) input.focus();
}

function updateContext() {
  if (!contextLabel) return;
  contextLabel.textContent = state.machineId
    ? `Machine context · ${state.machineId}`
    : "All applications";
}

function updateComposer() {
  const empty = !input.value.trim();
  send.disabled = state.sending || empty;
  send.setAttribute("aria-label", state.sending ? "Assistant is responding" : "Send message");
}

function autoSize() {
  input.style.height = "auto";
  const max = 120;
  input.style.height = Math.min(input.scrollHeight, max) + "px";
  input.style.overflowY = input.scrollHeight > max ? "auto" : "hidden";
}

function setStatus(text) {
  if (statusLive) statusLive.textContent = text || "";
}

async function ask(rawText, { replay = false } = {}) {
  const text = String(rawText || "").trim();
  if (!text || state.sending || !state.visible) return;
  if (text.length > MAX_MESSAGE) {
    setStatus("Message is too long.");
    return;
  }

  if (state.open) input.focus();
  state.sending = true;
  state.lastUserText = text;
  const sequence = ++state.sequence;

  if (!replay) {
    state.messages.push({ role: "user", text, at: Date.now() });
  }
  input.value = "";
  autoSize();
  updateComposer();

  const loading = { role: "assistant", loading: true, at: Date.now() };
  state.messages.push(loading);
  renderLog();
  scrollToEnd();
  setStatus("Assistant is responding.");

  try {
    const response = await api.aiChat({
      message: text,
      machine_id: state.machineId || undefined,
      conversation_id: state.conversationId || undefined,
    });
    if (sequence !== state.sequence) return;

    removeMessage(loading);
    if (!response || typeof response !== "object") {
      throw new Error("The assistant returned an empty response.");
    }
    if (response.conversation_id) state.conversationId = String(response.conversation_id);
    if (response.error && response.error.code === "conversation_not_found") {
      state.conversationId = null;
    }

    const messageText = responseText(response);
    const hasData = hasStructuredData(response.data || response.facts);
    if (!messageText && !hasData) {
      state.messages.push({
        role: "assistant",
        at: Date.now(),
        response,
        error: {
          message: "The assistant returned an empty response. Please try again.",
          retryText: text,
        },
      });
      setStatus("The assistant returned an empty response.");
    } else {
      state.messages.push({
        role: "assistant",
        text: messageText,
        at: Date.now(),
        response,
      });
      setStatus(messageText ? "New answer received." : "Structured result received.");
    }
  } catch (error) {
    if (sequence !== state.sequence) return;
    removeMessage(loading);
    const message = friendlyError(error);
    const sessionExpired = SESSION_ERROR.test(String(error && error.message ? error.message : ""));
    state.messages.push({
      role: "assistant",
      at: Date.now(),
      error: {
        message,
        retryText: sessionExpired ? "" : text,
        signin: sessionExpired,
      },
    });
    setStatus(message);
  } finally {
    if (sequence === state.sequence) {
      state.sending = false;
      updateComposer();
      renderLog();
      scrollToEnd();
    }
  }
}

function removeMessage(target) {
  const index = state.messages.indexOf(target);
  if (index >= 0) state.messages.splice(index, 1);
}

function friendlyError(error) {
  const raw = String(error && error.message ? error.message : "").trim();
  if (SESSION_ERROR.test(raw)) return "Your session expired. Please sign in again.";
  if (NETWORK_ERROR.test(raw)) return "I couldn't reach the server. Check your connection and try again.";
  if (/timeout|timed out/i.test(raw)) return "The request took too long. Please try again.";
  return raw || "Something went wrong while contacting the assistant. Please try again.";
}

function renderLog() {
  if (!log) return;
  if (!state.messages.length) {
    log.innerHTML = emptyStateHtml();
  } else {
    log.innerHTML = state.messages.map((message, index) => messageHtml(message, index)).join("");
  }
  scrollToEnd();
}

function emptyStateHtml() {
  const questions = state.machineId ? MACHINE_SUGGESTIONS : GENERAL_SUGGESTIONS;
  return `
    <div class="ai-empty">
      <div class="ai-empty-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
          stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 3.5 13.6 8l4.5 1.6-4.5 1.6L12 15.7l-1.6-4.5L5.9 9.6 10.4 8Z"/>
          <path d="M19 15.5 19.7 17.5l2 .7-2 .7L19 21l-.7-2.1-2-.7 2-.7Z"/>
        </svg>
      </div>
      <h3>How can I help?</h3>
      <p>Ask about your machines, measurements, limits, incidents or connectivity.</p>
      <div class="ai-suggestions" aria-label="Suggested questions">
        ${questions.map((question) => `
          <button class="ai-suggestion" type="button" data-ai-q="${esc(question)}">
            <span>${esc(question)}</span>
            <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"
              stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>
          </button>`).join("")}
      </div>
    </div>`;
}

function messageHtml(message, index) {
  const at = formatTime(message.at);
  if (message.loading) {
    return `
      <div class="ai-msg ai-msg-assistant ai-msg-loading">
        <span class="ai-avatar" aria-hidden="true">AI</span>
        <div class="ai-stack">
          <div class="ai-bubble ai-typing" role="status" aria-label="Assistant is responding">
            <span></span><span></span><span></span>
          </div>
        </div>
      </div>`;
  }

  if (message.role === "user") {
    return `
      <div class="ai-msg ai-msg-user">
        <div class="ai-stack">
          <div class="ai-bubble">${textHtml(message.text)}</div>
          <time class="ai-time" datetime="${esc(isoTime(message.at))}">${esc(at)}</time>
        </div>
      </div>`;
  }

  const text = message.text ? `<div class="ai-bubble">${textHtml(message.text)}</div>` : "";
  const data = message.response ? structuredHtml(message.response) : "";
  const evidence = message.response ? evidenceHtml(message.response) : "";
  const warning = message.response && message.response.error
    ? warningHtml(message.response.error, message.text) : "";
  const error = message.error ? errorHtml(message.error, index) : "";
  return `
    <div class="ai-msg ai-msg-assistant">
      <span class="ai-avatar" aria-hidden="true">AI</span>
      <div class="ai-stack">
        ${text}${data}${evidence}${warning}${error}
        <time class="ai-time" datetime="${esc(isoTime(message.at))}">${esc(at)}</time>
      </div>
    </div>`;
}

function errorHtml(error, index) {
  const actions = [];
  if (error.retryText) {
    actions.push(`<button class="ai-link" type="button" data-ai-retry="${index}">Try again</button>`);
  }
  if (error.signin) {
    actions.push(`<button class="ai-link" type="button" data-ai-signin>Sign in</button>`);
  }
  if (!error.retryText && !error.signin) {
    actions.push(`<button class="ai-link" type="button" data-ai-new>Start a new chat</button>`);
  }
  return `
    <div class="ai-error" role="alert">
      <div>${esc(error.message)}</div>
      ${actions.length ? `<div class="ai-error-actions">${actions.join("")}</div>` : ""}
    </div>`;
}

function warningHtml(error, text) {
  const message = String(error && error.message ? error.message : (typeof error === "string" ? error : "")).trim();
  if (!message || message === text) return "";
  return `<div class="ai-warning" role="status">${esc(message)}</div>`;
}

function textHtml(value) {
  return esc(value).replace(/\n/g, "<br>");
}

function responseText(response) {
  for (const key of ["message", "answer", "reply"]) {
    if (typeof response[key] === "string" && response[key].trim()) return response[key].trim();
  }
  return "";
}

function hasStructuredData(data) {
  if (!data || typeof data !== "object") return false;
  if (Array.isArray(data)) return data.length > 0;
  return Object.keys(data).length > 0;
}

function structuredHtml(response) {
  const data = response.data || response.facts;
  if (!hasStructuredData(data)) return "";
  if (Array.isArray(data.tool_results)) {
    const cards = data.tool_results
      .map((item, index) => dataObjectHtml(item, response, `Result ${index + 1}`))
      .filter(Boolean);
    return cards.length ? `<div class="ai-data">${cards.join("")}</div>` : "";
  }
  const cards = dataObjectHtml(data, response, "");
  return cards ? `<div class="ai-data">${cards}</div>` : "";
}

function dataObjectHtml(data, response, fallbackTitle) {
  if (!data || typeof data !== "object" || Array.isArray(data)) return "";
  const cards = [];
  const machine = asObject(data.machine);
  const sensor = asObject(data.sensor);
  const unit = unitFor(data, response);

  const contextBits = [];
  if (machine && machine.name) contextBits.push(`<b>${esc(machine.name)}</b>`);
  if (machine && machine.asset_id) contextBits.push(esc(machine.asset_id));
  if (machine && machine.plant) contextBits.push(esc(machine.plant));
  if (sensor && sensor.label) contextBits.push(esc(sensor.label));
  if (data.metric) contextBits.push(esc(metricLabel(data.metric)));
  const context = contextBits.length
    ? `<div class="ai-context-line">${contextBits.join('<span aria-hidden="true">·</span>')}</div>`
    : "";

  if (asObject(data.statistics)) {
    cards.push(statisticsCard(data, unit, sensor, response));
  }
  if (asObject(data.period_a) && asObject(data.period_b)) {
    cards.push(compareCard(data, unit));
  }
  if (Array.isArray(data.daily_summaries)) {
    cards.push(dailyCard(data, unit));
  }
  if (Array.isArray(data.machines)) {
    cards.push(machinesCard(data));
  }
  if (Array.isArray(data.devices)) {
    cards.push(connectivityCard(data));
  }
  if (Array.isArray(data.incidents)) {
    cards.push(incidentsCard(data, unit));
  }
  if (Array.isArray(data.points) && data.points.length) {
    cards.push(trendCard(data, unit));
  }
  if (data.out_of_range_count != null || data.out_of_range_percentage != null) {
    cards.push(outOfRangeCard(data, unit));
  }
  if (data.raw_readings != null || data.processed_readings != null) {
    cards.push(rawCountsCard(data));
  }
  if (data.reading_count != null || data.readings != null || data.with_values != null) {
    cards.push(countCard(data, unit));
  }
  if (asObject(data.limits) || data.target != null) {
    const limits = limitsCard(data, unit);
    if (limits) cards.push(limits);
  }
  if (!cards.length) {
    const generic = genericCard(data, fallbackTitle);
    if (generic) cards.push(generic);
  }
  return context + cards.join("");
}

function statisticsCard(data, unit, sensor, response) {
  const stats = asObject(data.statistics) || {};
  const metric = metricLabel(data.metric || "thickness");
  const subtitle = sensor && sensor.label ? `${metric} · ${sensor.label}` : metric;
  const hasValues = stats.values_count == null || Number(stats.values_count) > 0;
  const tiles = [
    tile("Average", stats.average, unit),
    tile("Minimum", stats.minimum, unit),
    tile("Maximum", stats.maximum, unit),
    tile("Std dev", stats.std_dev, unit),
    tile("Readings", stats.samples, "", true),
    tile("Pass rate", stats.pass_percentage, "%"),
    tile("Failures", stats.fail_count, "", true),
  ];
  const note = hasValues ? "" : `<div class="ai-note">No readings were returned for this period.</div>`;
  return card("Measurement statistics", subtitle, `${note}<div class="ai-tiles">${tiles.join("")}</div>`, response);
}

function compareCard(data, unit) {
  const a = asObject(data.period_a) || {};
  const b = asObject(data.period_b) || {};
  const statsA = asObject(a.statistics) || {};
  const statsB = asObject(b.statistics) || {};
  const delta = asObject(data.delta_b_minus_a) || {};
  const rows = [
    ["Average", formatNumber(statsA.average), formatNumber(statsB.average), formatNumber(delta.average)],
    ["Minimum", formatNumber(statsA.minimum), formatNumber(statsB.minimum), formatNumber(delta.minimum)],
    ["Maximum", formatNumber(statsA.maximum), formatNumber(statsB.maximum), formatNumber(delta.maximum)],
    ["Readings", formatInteger(statsA.samples), formatInteger(statsB.samples), formatInteger(delta.samples)],
    ["Pass rate", formatPercent(statsA.pass_percentage), formatPercent(statsB.pass_percentage),
      formatPercent(delta.pass_percentage)],
  ];
  const unitCell = unit ? ` <span class="ai-unit">${esc(unit)}</span>` : "";
  const body = `
    <div class="ai-table-wrap"><table class="ai-table">
      <thead><tr><th>Metric</th><th class="num">${esc(a.label || "A")}</th>
        <th class="num">${esc(b.label || "B")}</th><th class="num">Change</th></tr></thead>
      <tbody>${rows.map((row) => `<tr><th>${esc(row[0])}</th>
        <td class="num">${row[1]}${unitCell}</td><td class="num">${row[2]}${unitCell}</td>
        <td class="num">${row[3]}</td></tr>`).join("")}</tbody>
    </table></div>`;
  return card("Period comparison", `${a.label || "A"} vs ${b.label || "B"}`, body);
}

function dailyCard(data, unit) {
  const rows = data.daily_summaries || [];
  const body = rows.length ? `
    <div class="ai-table-wrap"><table class="ai-table">
      <thead><tr><th>Date</th><th class="num">Readings</th><th class="num">Average</th>
        <th class="num">Min</th><th class="num">Max</th><th class="num">Pass</th><th class="num">Alerts</th></tr></thead>
      <tbody>${rows.map((row) => `<tr>
        <td>${esc(row.date || "")}</td>
        <td class="num">${formatInteger(row.samples)}</td>
        <td class="num">${formatNumber(row.average)}${unit ? ` ${esc(unit)}` : ""}</td>
        <td class="num">${formatNumber(row.minimum)}</td>
        <td class="num">${formatNumber(row.maximum)}</td>
        <td class="num">${formatPercent(row.pass_percentage)}</td>
        <td class="num">${formatInteger(row.alerts)}</td>
      </tr>`).join("")}</tbody>
    </table></div>` : `<div class="ai-note">No daily rows were returned.</div>`;
  return card("Daily summary", `${rows.length} day${rows.length === 1 ? "" : "s"}`, body);
}

function machinesCard(data) {
  const machines = data.machines || [];
  const body = machines.length ? `
    <div class="ai-table-wrap"><table class="ai-table">
      <thead><tr><th>Machine</th><th>Application</th><th>Status</th><th>Sensors</th><th>Last reading</th></tr></thead>
      <tbody>${machines.map((machine) => `<tr>
        <td>${esc(machine.name || machine.machine_id || "")}</td>
        <td>${esc(machine.application || "")}</td>
        <td>${esc(machine.status || "")}</td>
        <td>${esc((machine.sensors || []).map((item) => item.label || item.key).join(", "))}</td>
        <td>${esc(formatDateTime(machine.last_reading))}</td>
      </tr>`).join("")}</tbody>
    </table></div>` : `<div class="ai-note">No machines were returned.</div>`;
  return card("Machines", `${machines.length} available`, body);
}

function connectivityCard(data) {
  const devices = data.devices || [];
  const tiles = [
    tile("Devices", data.device_count, "", true),
    tile("Online", data.online_count, "", true),
    tile("Offline", data.offline_count, "", true),
  ];
  const body = `${tiles.length ? `<div class="ai-tiles">${tiles.join("")}</div>` : ""}
    ${devices.length ? `<div class="ai-table-wrap" style="margin-top:8px"><table class="ai-table">
      <thead><tr><th>Device</th><th>Status</th><th>Model</th><th>Last seen</th></tr></thead>
      <tbody>${devices.map((device) => `<tr>
        <td>${esc(device.name || device.device_id || "")}</td>
        <td>${esc(device.communication || (device.online ? "ONLINE" : "OFFLINE"))}</td>
        <td>${esc(device.model || device.interface || "")}</td>
        <td>${esc(formatDateTime(device.last_seen))}</td>
      </tr>`).join("")}</tbody>
    </table></div>` : ""}`;
  return card("Device connectivity", data.machine ? data.machine.name || "" : "", body);
}

function incidentsCard(data, unit) {
  const incidents = data.incidents || [];
  const body = incidents.length ? `
    <div class="ai-table-wrap"><table class="ai-table">
      <thead><tr><th>Started</th><th>Ended</th><th class="num">Duration</th><th class="num">Readings</th>
        <th class="num">Average</th><th class="num">Min</th><th class="num">Max</th></tr></thead>
      <tbody>${incidents.map((item) => `<tr>
        <td>${esc(formatDateTime(item.started_at))}</td>
        <td>${esc(formatDateTime(item.ended_at))}</td>
        <td class="num">${esc(formatDuration(item.duration_seconds))}</td>
        <td class="num">${formatInteger(item.readings)}</td>
        <td class="num">${formatNumber(item.average)}${unit ? ` ${esc(unit)}` : ""}</td>
        <td class="num">${formatNumber(item.minimum)}</td>
        <td class="num">${formatNumber(item.maximum)}</td>
      </tr>`).join("")}</tbody>
    </table></div>` : `<div class="ai-note">No threshold incidents were returned for this period.</div>`;
  const subtitle = data.incident_count != null ? `${formatInteger(data.incident_count)} incident(s)` : "";
  return card("Threshold incidents", subtitle, body);
}

function trendCard(data, unit) {
  const points = (data.points || [])
    .map((point) => ({ ts: point.ts, value: Number(point.value) }))
    .filter((point) => Number.isFinite(point.value));
  if (!points.length) return card("Trend", "", `<div class="ai-note">Trend points were returned without numeric values.</div>`);
  const values = points.map((point) => point.value);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const span = maximum - minimum || 1;
  const width = 320;
  const height = 74;
  const path = points.map((point, index) => {
    const x = points.length === 1 ? width / 2 : (index / (points.length - 1)) * width;
    const y = height - ((point.value - minimum) / span) * (height - 10) - 5;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const last = points[points.length - 1];
  const unitText = unit ? ` ${esc(unit)}` : "";
  const body = `
    <svg class="ai-spark" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img"
      aria-label="Trend from ${esc(formatDateTime(points[0].ts))} to ${esc(formatDateTime(last.ts))}">
      <polyline points="${path}" fill="none" stroke="var(--accent)" stroke-width="2"
        vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>
    </svg>
    <div class="ai-tiles" style="margin-top:8px">
      ${tile("Latest", last.value, unit)}
      ${tile("Minimum", minimum, unit)}
      ${tile("Maximum", maximum, unit)}
      ${tile("Points", points.length, "", true)}
    </div>
    <div class="ai-note" style="margin-top:8px">Latest reading ${esc(formatDateTime(last.ts))}${unitText}.</div>`;
  return card("Trend", data.minutes ? `Last ${esc(String(data.minutes))} minutes` : "", body);
}

function outOfRangeCard(data, unit) {
  const tiles = [
    tile("Out of range", data.out_of_range_count, "", true),
    tile("Share", data.out_of_range_percentage, "%"),
    tile("Minimum", data.out_of_range_minimum, unit),
    tile("Maximum", data.out_of_range_maximum, unit),
    tile("Threshold fails", data.threshold_fail_count, "", true),
  ];
  const samples = Array.isArray(data.samples) ? data.samples : [];
  const sampleTable = samples.length ? `
    <div class="ai-table-wrap" style="margin-top:8px"><table class="ai-table">
      <thead><tr><th>Time</th><th class="num">Value</th></tr></thead>
      <tbody>${samples.map((sample) => `<tr>
        <td>${esc(formatDateTime(sample.ts))}</td>
        <td class="num">${formatNumber(sample.value)}${unit ? ` ${esc(unit)}` : ""}</td>
      </tr>`).join("")}</tbody>
    </table></div>` : "";
  const note = data.note ? `<div class="ai-note">${esc(data.note)}</div>` : "";
  return card("Out-of-range readings", "", `${note}<div class="ai-tiles">${tiles.join("")}</div>${sampleTable}`);
}

function rawCountsCard(data) {
  const tiles = [
    tile("Raw / unfiltered", data.raw_readings != null ? data.raw_readings : data.unfiltered_readings, "", true),
    tile("Processed / filtered", data.processed_readings != null ? data.processed_readings : data.filtered_readings, "", true),
    tile("Difference", data.difference, "", true),
  ];
  const filter = asObject(data.filter);
  const note = filter && filter.active
    ? `<div class="ai-note">Filter window ${esc(String(filter.window || ""))} · trim ${esc(String(filter.trim_pct || 0))}%</div>`
    : (data.note ? `<div class="ai-note">${esc(data.note)}</div>` : "");
  return card("Reading pipeline", "", `${tiles.join("") ? `<div class="ai-tiles">${tiles.join("")}</div>` : ""}${note}`);
}

function countCard(data, unit) {
  const count = data.reading_count != null ? data.reading_count : data.readings;
  const tiles = [tile("Readings", count, "", true)];
  if (data.with_values != null) tiles.push(tile("With numeric values", data.with_values, "", true));
  return card("Reading count", unit ? `Unit: ${unit}` : "", `<div class="ai-tiles">${tiles.join("")}</div>`);
}

function limitsCard(data, unit) {
  const limits = asObject(data.limits) || {};
  const tiles = [];
  if (limits.lower != null) tiles.push(tile("Lower limit", limits.lower, unit));
  if (data.target != null) tiles.push(tile("Target", data.target, unit));
  if (limits.upper != null) tiles.push(tile("Upper limit", limits.upper, unit));
  if (!tiles.length) return "";
  const note = limits.active === false ? `<div class="ai-note">Configured limits are not active.</div>` : "";
  return card("Limits and target", "", `${note}<div class="ai-tiles">${tiles.join("")}</div>`);
}

function genericCard(data, title) {
  const skip = new Set([
    "source", "time_range", "time_ranges", "units", "machine", "sensor", "metric",
    "has_data", "note", "configured", "limits_active",
  ]);
  const rows = Object.entries(data).filter(([key, value]) =>
    !skip.has(key) && (value == null || ["string", "number", "boolean"].includes(typeof value))
  );
  if (!rows.length) return "";
  return card(title || "Result", "", `
    <div class="ai-table-wrap"><table class="ai-table">
      <tbody>${rows.map(([key, value]) => `<tr>
        <th>${esc(humanize(key))}</th><td>${esc(value == null ? "—" : String(value))}</td>
      </tr>`).join("")}</tbody>
    </table></div>`);
}

function card(title, subtitle, body, response) {
  const sub = subtitle ? `<span class="ai-card-sub">${esc(subtitle)}</span>` : "";
  return `<div class="ai-card"><div class="ai-card-head"><span>${esc(title)}</span>${sub}</div>
    <div class="ai-card-body">${body}</div></div>`;
}

function tile(label, value, unit, integer = false) {
  const text = integer ? formatInteger(value) : formatNumber(value);
  const suffix = text !== "—" && unit ? `<span class="ai-unit">${esc(unit)}</span>` : "";
  return `<div class="ai-tile"><span class="ai-tile-label">${esc(label)}</span>
    <span class="ai-tile-value">${text}${suffix}</span></div>`;
}

function evidenceHtml(response) {
  const data = asObject(response.data) || asObject(response.facts);
  const range = asObject(response.time_range) || (data ? asObject(data.time_range) : null);
  const units = Object.assign({}, asObject(data && data.units), asObject(response.units));
  const sourceList = Array.isArray(response.data_sources) ? response.data_sources.filter(Boolean) : [];
  const source = sourceList.length
    ? sourceList.map(sourceLabel).join(" + ")
    : (data && data.source ? sourceLabel(data.source) : "");

  const parts = [];
  if (range && (range.label || range.start_local || range.start)) {
    parts.push(`<span><b>Period</b> ${esc(rangeText(range))}</span>`);
  }
  if (Object.keys(units).length) {
    parts.push(`<span><b>Units</b> ${esc(unitsText(units))}</span>`);
  }
  if (source) {
    parts.push(`<span><b>Source</b> ${esc(source)}</span>`);
  }
  const citations = citationText(response.citations);
  if (citations) {
    parts.push(`<span><b>Based on</b> ${esc(citations)}</span>`);
  }
  return parts.length ? `<div class="ai-evidence">${parts.join("")}</div>` : "";
}

function citationText(citations) {
  if (!Array.isArray(citations)) return "";
  return citations.map((citation) => {
    if (!citation || typeof citation !== "object") return "";
    if (citation.type === "instance") {
      return `Application ${citation.name || citation.instance_id || ""}`.trim();
    }
    if (citation.type === "device") return `Device ${citation.device_id || ""}`.trim();
    if (citation.type === "timestamp") return `Reading ${formatDateTime(citation.ts)}`;
    if (citation.type === "time_range") return `Last ${citation.minutes} minutes`;
    return "";
  }).filter(Boolean).join(" · ");
}

function rangeText(range) {
  const label = range.label ? String(range.label) : "";
  const start = formatPlantTime(range.start_local || range.start);
  const end = formatPlantTime(range.end_local || range.end);
  const span = start || end ? `${start || "…"} – ${end || "now"}` : "";
  const zone = range.timezone ? ` (${range.timezone})` : "";
  return [label, span ? span + zone : ""].filter(Boolean).join(" · ");
}

function unitsText(units) {
  return Object.entries(units)
    .filter(([, value]) => value != null && value !== "")
    .map(([key, value]) => `${humanize(key)}: ${value}`)
    .join(" · ");
}

function unitFor(data, response) {
  const units = Object.assign({}, asObject(response && response.units), asObject(data && data.units));
  const metric = String((data && data.metric) || "thickness");
  if (units[metric] != null) return String(units[metric]);
  if (units.thickness != null) return String(units.thickness);
  const first = Object.values(units).find((value) => value != null && value !== "");
  return first != null ? String(first) : "";
}

function sourceLabel(source) {
  const value = String(source || "");
  if (value.includes("raw_device_messages") && value.includes("measurements")) {
    return "Raw device messages + processed measurements";
  }
  if (value.includes("raw_device_messages")) return "Raw device messages";
  if (value.includes("metrics.measurements")) return "Processed measurements";
  if (value.includes("core.template_instances")) return "Application catalogue";
  if (value.includes("core.devices")) return "Device registry + measurements";
  return value;
}

function metricLabel(metric) {
  const value = String(metric || "").toLowerCase();
  if (value === "thickness") return "Thickness";
  if (value === "displacement") return "Displacement";
  if (value === "deviation") return "Deviation";
  if (value === "readings" || value === "raw") return "Readings";
  return humanize(value);
}

function humanize(value) {
  return String(value || "")
    .replace(/[_.-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase())
    .trim();
}

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : null;
}

function formatNumber(value) {
  if (value == null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return esc(String(value));
  return number.toLocaleString(undefined, { maximumFractionDigits: 3 });
}

function formatInteger(value) {
  if (value == null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return esc(String(value));
  return Math.round(number).toLocaleString();
}

function formatPercent(value) {
  if (value == null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return esc(String(value));
  return number.toLocaleString(undefined, { maximumFractionDigits: 2 }) + "%";
}

function formatDuration(seconds) {
  if (seconds == null || seconds === "") return "—";
  const total = Number(seconds);
  if (!Number.isFinite(total)) return esc(String(seconds));
  if (total < 60) return `${Math.round(total)}s`;
  if (total < 3600) return `${Math.floor(total / 60)}m ${Math.round(total % 60)}s`;
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return `${hours}h ${minutes}m`;
}

function formatTime(value) {
  try {
    return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

function isoTime(value) {
  try {
    return new Date(value).toISOString();
  } catch {
    return "";
  }
}

function formatDateTime(value) {
  if (!value) return "—";
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString([], {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return String(value);
  }
}

// Time ranges arrive as plant-local ISO strings. Show the plant wall clock
// rather than silently converting it to the browser's timezone.
function formatPlantTime(value) {
  if (!value) return "";
  const match = String(value).match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
  return match ? `${match[1]} ${match[2]}` : String(value);
}

function scrollToEnd() {
  if (!log) return;
  log.scrollTop = log.scrollHeight;
}

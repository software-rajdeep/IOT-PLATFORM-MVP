// Thin API client. Token is attached automatically; 401 clears the session.
import { store } from "./store.js";

const BASE = "/api/v1";

async function request(path, { method = "GET", body, raw = false } = {}) {
  const headers = {};
  const token = store.token();
  if (token) headers["Authorization"] = "Bearer " + token;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(BASE + path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (raw && res.ok) return res;
  const text = await res.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch { data = null; }
  }

  if (res.status === 401) {
    store.clearSession();
    throw new Error("Session expired. Please sign in again.");
  }
  if (!res.ok) {
    const detail = data && (data.detail || data.message);
    throw new Error(typeof detail === "string" ? detail : `Request failed (${res.status})`);
  }
  return data;
}

export const api = {
  login: (email, password) => request("/auth/login", { method: "POST", body: { email, password } }),
  me: () => request("/auth/me"),

  // catalogue
  deviceProfiles: () => request("/catalog/device-profiles"),
  deviceModels: (search = "") =>
    request("/catalog/device-models" + (search ? `?search=${encodeURIComponent(search)}` : "")),
  purposes: (modelKey) => request(`/catalog/device-models/${encodeURIComponent(modelKey)}/purposes`),
  templates: (purposeKey, modelKey) =>
    request(`/catalog/purposes/${encodeURIComponent(purposeKey)}/templates?model_key=${encodeURIComponent(modelKey)}`),

  // devices (BLOCK 2)
  devices: () => request("/devices"),
  device: (id) => request(`/devices/${id}`),
  createDevice: (data) => request("/devices", { method: "POST", body: data }),
  updateDevice: (id, data) => request(`/devices/${id}`, { method: "PATCH", body: data }),
  testDevice: (id) => request(`/devices/${id}/test`, { method: "POST" }),
  discoverSource: (data) => request("/devices/discover-source", { method: "POST", body: data }),
  deviceDisplacement: (id) => request(`/devices/${id}/displacement`),

  // template instances (BLOCK 5)
  instances: () => request("/instances"),
  instance: (id) => request(`/instances/${id}`),
  createInstance: (data) => request("/instances", { method: "POST", body: data }),
  updateInstance: (id, data) => request(`/instances/${id}`, { method: "PATCH", body: data }),
  calibrate: (id, data) => request(`/instances/${id}/calibrate`, { method: "POST", body: data }),
  verifyCalibration: (id, data) => request(`/instances/${id}/calibration/verify`, { method: "POST", body: data }),
  activate: (id) => request(`/instances/${id}/activate`, { method: "POST" }),
  instanceDevices: (id) => request(`/instances/${id}/devices`),
  addInstanceDevice: (id, deviceId) =>
    request(`/instances/${id}/devices`, { method: "POST", body: { device_id: deviceId } }),

  // dashboard (BLOCK 6)
  dashboard: (id, deviceId) =>
    request(`/instances/${id}/dashboard` + (deviceId ? `?device_id=${encodeURIComponent(deviceId)}` : "")),
  measurements: (id, limit = 100) => request(`/instances/${id}/measurements?limit=${limit}`),

  // catalog
  solutions: () => request("/catalog/solutions"),
  units: () => request("/catalog/units"),
  template: (id) => request(`/catalog/templates/${encodeURIComponent(id)}`),

  // sources
  plants: () => request("/devices/plants"),
  updateConnection: (id, data) => request(`/devices/${id}/connection`, { method: "PATCH", body: data }),
  ingestInfo: (id) => request(`/devices/${id}/ingest-info`),

  // onboarding steps
  mapping: (id) => request(`/instances/${id}/mapping`),
  captureSamples: (id) => request(`/instances/${id}/mapping/samples`, { method: "POST" }),
  previewMapping: (id, definition, payload) =>
    request(`/instances/${id}/mapping/preview`, { method: "POST", body: { definition, payload } }),
  saveMapping: (id, definition) => request(`/instances/${id}/mapping`, { method: "POST", body: { definition } }),
  mappingFailures: (id) => request(`/instances/${id}/mapping/failures`),
  widgets: (id) => request(`/instances/${id}/widgets`),
  saveLayout: (id, widgets) => request(`/instances/${id}/layout`, { method: "PUT", body: { widgets } }),
  resetLayout: (id) => request(`/instances/${id}/layout`, { method: "DELETE" }),
  checklist: (id) => request(`/instances/${id}/checklist`),

  // operations
  alerts: (id) => request(`/instances/${id}/alerts`),
  ackAlert: (id, eventId) => request(`/instances/${id}/alerts/${eventId}/ack`, { method: "POST" }),
  updateAlertRule: (id, type, data) => request(`/instances/${id}/alert-rules/${type}`, { method: "PUT", body: data }),
  report: (id, date) => request(`/instances/${id}/report` + (date ? `?date=${date}` : "")),
  history: (id) => request(`/instances/${id}/history`),
  clone: (id, data) => request(`/instances/${id}/clone`, { method: "POST", body: data }),
  exportCsv: (id, params) => request(`/instances/${id}/export.csv?` + new URLSearchParams(params), { raw: true }),
};

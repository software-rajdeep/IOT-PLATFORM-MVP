// Thin API client. Token is attached automatically; 401 clears the session.
import { store } from "./store.js";

const BASE = "/api/v1";

async function request(path, { method = "GET", body } = {}) {
  const headers = {};
  const token = store.token();
  if (token) headers["Authorization"] = "Bearer " + token;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(BASE + path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

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
};

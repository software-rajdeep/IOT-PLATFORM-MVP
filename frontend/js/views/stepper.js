import { esc } from "../ui.js";

// Shared onboarding stepper. Steps 3-7 are per instance and can be revisited.
export const STEPS = [
  { key: "solution", label: "Solution" },
  { key: "connect", label: "Asset & data" },
  { key: "map", label: "Map data" },
  { key: "commission", label: "Commission" },
  { key: "rules", label: "Limits & alerts" },
  { key: "graphs", label: "Graphs" },
  { key: "activate", label: "Activate" },
];

export function stepper(current, instanceId) {
  const idx = STEPS.findIndex((s) => s.key === current);
  return `<ol class="stepper">${STEPS.map((s, i) => {
    const state = i < idx ? "done" : i === idx ? "current" : "";
    const href = instanceId && i >= 2 ? `#/setup/${encodeURIComponent(instanceId)}/${s.key}` : null;
    const inner = `<span class="st-num">${i < idx ? "✓" : i + 1}</span><span class="st-label">${esc(s.label)}</span>`;
    return `<li class="${state}">${href ? `<a href="${href}">${inner}</a>` : inner}</li>`;
  }).join("")}</ol>`;
}

export function nextStep(current) {
  const idx = STEPS.findIndex((s) => s.key === current);
  return STEPS[idx + 1] ? STEPS[idx + 1].key : null;
}

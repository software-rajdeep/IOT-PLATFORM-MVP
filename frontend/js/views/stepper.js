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
  return "";
}

export function nextStep(current) {
  const idx = STEPS.findIndex((s) => s.key === current);
  return STEPS[idx + 1] ? STEPS[idx + 1].key : null;
}

/** Cash-engine presets (GET /api/v1/risk/presets) as What-if dropdown options, grouped joint / module / sensitivity. */
export const PRESET_PREFIX = "preset:";
export const PRESET_GROUPS: [string, string][] = [["joint", "Joint"], ["module", "Module"], ["sensitivity", "Sensitivity"]];

/** Only presets with a known group and a scenario; the prototype engine's presets carry no group and are not listed. */
export function groupEnginePresets(presets: any[]) {
  return PRESET_GROUPS.map(([id, label]) => ({ id, label, presets: (presets ?? []).filter(p => p?.group === id && p?.scenario) }))
    .filter(group => group.presets.length > 0);
}

export const presetOptionValue = (id: string) => PRESET_PREFIX + id;

export function presetFromOption(value: string, presets: any[]) {
  if (!value.startsWith(PRESET_PREFIX)) return null;
  const id = value.slice(PRESET_PREFIX.length);
  return (presets ?? []).find(p => p?.id === id) ?? null;
}

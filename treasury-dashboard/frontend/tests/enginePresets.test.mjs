import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/enginePresets.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
const { groupEnginePresets, presetFromOption, presetOptionValue } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

const presets = [
  { id: "baseline", name: "Baseline", group: "baseline", scenario: {} },
  { id: "rates_up_100bp", name: "Rates +100 bp", group: "sensitivity", scenario: { rates: {} } },
  { id: "red_sea_reescalation", name: "Red Sea re-escalation", group: "joint", scenario: { freight: {} } },
  { id: "credit_squeeze", name: "Credit squeeze", group: "module", scenario: { rates: {} } },
  { id: "oil_spike", name: "Oil spike", group: "joint", scenario: { bunker: {} } },
  { id: "eur_rally", name: "EUR rally", scenario: { fx: {} } },              // prototype engine: no group
];

test("engine presets are grouped joint, module, sensitivity, keeping server order", () => {
  const groups = groupEnginePresets(presets);
  assert.deepEqual(groups.map(g => g.label), ["Joint", "Module", "Sensitivity"]);
  assert.deepEqual(groups[0].presets.map(p => p.id), ["red_sea_reescalation", "oil_spike"]);
  assert.ok(!groups.flatMap(g => g.presets).some(p => p.id === "baseline" || p.id === "eur_rally"));
});

test("empty or ungrouped preset lists add no dropdown groups", () => {
  assert.deepEqual(groupEnginePresets([]), []);
  assert.deepEqual(groupEnginePresets(undefined), []);
  assert.deepEqual(groupEnginePresets([{ id: "x", name: "X", scenario: {} }]), []);
});

test("option values round-trip and never match news Event ids", () => {
  assert.equal(presetFromOption(presetOptionValue("credit_squeeze"), presets).name, "Credit squeeze");
  assert.equal(presetFromOption("evt_123", presets), null);
  assert.equal(presetFromOption("__observed__", presets), null);
  assert.equal(presetFromOption(presetOptionValue("unknown"), presets), null);
});

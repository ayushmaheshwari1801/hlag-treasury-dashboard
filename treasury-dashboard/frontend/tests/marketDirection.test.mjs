import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const compile = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText;
const uri = source => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const format = uri(compile(readFileSync(new URL("../src/format.ts", import.meta.url), "utf8")));
const { cashImpactTone, marketArrow, metricChange, shockText } = await import(format);
const eventScenario = uri(compile(readFileSync(new URL("../src/eventScenario.ts", import.meta.url), "utf8")));
const enginePresets = uri(compile(readFileSync(new URL("../src/enginePresets.ts", import.meta.url), "utf8")));
const engineUi = uri(compile(readFileSync(new URL("../src/engineUi.ts", import.meta.url), "utf8")));
const treasuryCopy = uri(compile(readFileSync(new URL("../src/treasuryCopy.ts", import.meta.url), "utf8")).replace('"./engineUi"', JSON.stringify(engineUi)));
const whatIfSource = compile(readFileSync(new URL("../src/WhatIf.tsx", import.meta.url), "utf8"))
  .replace('"./treasuryCopy"', JSON.stringify(treasuryCopy))
  .replace('"./format"', JSON.stringify(format))
  .replace('"./eventScenario"', JSON.stringify(eventScenario))
  .replace('"./enginePresets"', JSON.stringify(enginePresets))
  .replace('"./engineUi"', JSON.stringify(engineUi))
  .replace('"react/jsx-runtime"', JSON.stringify(import.meta.resolve("react/jsx-runtime")));
const { default: WhatIf } = await import(uri(whatIfSource));

test("cash benefit determines color using the exact impact sign", () => {
  for (const value of [3, .000001]) assert.equal(cashImpactTone(value), "favourable");
  for (const value of [-3, -.000001]) assert.equal(cashImpactTone(value), "adverse");
  for (const value of [0, -0]) assert.equal(cashImpactTone(value), "neutral");
});

test("unknown cash impact cannot appear favourable, adverse, or zero", () => {
  for (const value of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
    assert.equal(cashImpactTone(value), null);
  }
});

test("market arrows describe quoted moves independently of cash-benefit color", () => {
  for (const [metric, arrow, text] of [
    [{ id: "fx_eur", change: -.0016, change_pct: -.14301, tone: "favourable" }, "▼", "−0.1%"],
    [{ id: "fx_cny", change: .0003, change_pct: .23, tone: "favourable" }, "▲", "+0.23%"],
    [{ id: "interest", change: .3 }, "▲", "+0.3 bps"],
    [{ id: "bunker", change: 17, change_pct: 2.2, tone: "adverse" }, "▲", "+2.2%"],
    [{ id: "freight", change: -156, change_pct: -3.4, tone: "adverse" }, "▼", "−3.4%"],
  ]) {
    const before = structuredClone(metric);
    assert.equal(marketArrow(metric), arrow);
    assert.equal(metricChange(metric), text);
    assert.deepEqual(metric, before);
  }
});

test("rounded zero market moves have no signed-zero text or arrow", () => {
  for (const change of [-.049, -.01, -0, 0, .01, .049]) {
    const metric = { id: "interest", change };
    assert.equal(metricChange(metric), "0 bps");
    assert.equal(marketArrow(metric), "");
  }
  for (const id of ["fx_eur", "fx_cny"]) {
    const metric = { id, change: -.00001, change_pct: -.004 };
    assert.equal(metricChange(metric), id === "fx_cny" ? "0.00%" : "0.0%");
    assert.equal(marketArrow(metric), "");
  }
});

test("missing or non-finite market changes do not invent direction", () => {
  for (const metric of [
    { id: "fx_eur", change: null, change_pct: null },
    { id: "freight", change: 1, change_pct: Number.NaN },
    { id: "interest", change: Number.NaN },
    { id: "interest", change: Number.POSITIVE_INFINITY },
  ]) {
    assert.equal(metricChange(metric), "—");
    assert.equal(marketArrow(metric), "");
  }
});

test("what-if text retains quote signs and removes rounded signed zeros", () => {
  for (const [value, unit, text] of [
    [30, "bps", "+30 bps"], [-30, "bps", "−30 bps"],
    [17, "USD/t", "+$17/t"], [-17, "USD/t", "−$17/t"],
    [.23, "%", "+0.23%"], [-3.4, "%", "−3.40%"],
  ]) assert.equal(shockText(value, unit), text);
  for (const unit of ["bps", "USD/t", "%"]) {
    const roundedZeros = unit === "%" ? [-.004, -0, 0, .004] : [-.49, -.004, -0, 0, .004, .49];
    for (const value of roundedZeros) {
      const text = shockText(value, unit);
      assert.doesNotMatch(text, /[+−-]/);
      assert.equal(Number(text.replace(/[^0-9.]/g, "")), 0);
    }
  }
});

test("what-if colors follow current calculated cash impacts instead of shock direction", () => {
  const sliders = [
    { id: "bunker", label: "Bunker", value: 17, observed: 17, unit: "USD/t", min: -300, max: 300, step: 1 },
    { id: "fx_eur", label: "EUR", value: 1, observed: 1, unit: "%", min: -20, max: 20, step: .1 },
    { id: "freight", label: "Freight", value: -3.4, observed: -3.4, unit: "%", min: -50, max: 50, step: .1 },
  ];
  const html = renderToStaticMarkup(React.createElement(WhatIf, {
    scenario: { sliders, presets: [], note: "" }, shocks: {}, preset: "observed", busy: false,
    onChange: () => {}, onPreset: () => {}, onReset: () => {}, open: true, setOpen: () => {}, deltas: [],
    cashImpacts: { bunker: -2, fx_eur: 1.5, freight: 0 },
  }));
  assert.match(html, /class="sl-val adverse">\+\$17\/t<\/span>/);
  assert.match(html, /class="sl-val favourable">\+1%<\/span>/);
  assert.match(html, /class="sl-val neutral">−3\.40%<\/span>/);
});
test("What-if keeps standard presets but excludes news events", () => {
  const presets = (group, ids) => ids.map(id => ({ id, name: id.toUpperCase(), description: `${id} description`, group, scenario: {} }));
  const groups = [{ id: "joint", label: "Joint", presets: presets("joint", ["red_sea_reescalation"]) },
                  { id: "module", label: "Module", presets: presets("module", ["credit_squeeze"]) },
                  { id: "sensitivity", label: "Sensitivity", presets: presets("sensitivity", ["rates_up_100bp"]) }];
  const props = { scenario: { sliders: [], presets: [], note: "" }, shocks: {}, preset: "preset:credit_squeeze", busy: false,
    onChange: () => {}, onPreset: () => {}, onReset: () => {}, open: false, setOpen: () => {}, deltas: [], cashImpacts: {},
    eventScenarios: [{ event_id: "evt_1", title: "UK ETS", category: "regulatory" }], enginePresetGroups: groups,
    activePresetId: "credit_squeeze" };
  const html = renderToStaticMarkup(React.createElement(WhatIf, props));
  assert.doesNotMatch(html, /UK ETS|evt_1|\/6/);
  const labels = [...html.matchAll(/<optgroup label="([^"]+)"/g)].map(m => m[1]);
  assert.deepEqual(labels.slice(-3), ["Standard scenarios · Joint", "Standard scenarios · Module", "Standard scenarios · Sensitivity"]);
  assert.ok(html.indexOf("UK ETS") < html.indexOf("Standard scenarios · Joint"));
  assert.match(html, /<option value="preset:credit_squeeze" title="credit_squeeze description" selected="">CREDIT_SQUEEZE<\/option>/);
  const none = renderToStaticMarkup(React.createElement(WhatIf, { ...props, enginePresetGroups: [], activePresetId: undefined }));
  assert.ok(!none.includes("Engine presets"));
});

test("a memo-only currency slider says so and shows the translation memo, not a cash value", () => {
  const memo = { currencies: ["GBP"], memo_only: true, revaluation_eur_m: -2.14 };
  const sliders = [{ id: "fx_gbp", label: "GBP FX", value: 10, observed: 0.2, unit: "%", min: -20, max: 20, step: .5, memo_only: true, memo },
                   { id: "fx_cad", label: "CAD FX", value: 0, observed: 0, unit: "%", min: -20, max: 20, step: .5, memo_only: false, memo: null }];
  const html = renderToStaticMarkup(React.createElement(WhatIf, {
    scenario: { sliders, presets: [], note: "" }, shocks: { fx_gbp: 10 }, preset: "custom", busy: false,
    onChange: () => {}, onPreset: () => {}, onReset: () => {}, open: true, setOpen: () => {}, deltas: [], cashImpacts: { fx_gbp: 0 },
  }));
  assert.equal(html.match(/GBP balance-sheet exposure only · no scheduled cash payments/g)?.length, 1);
  assert.equal(html.match(/Memo: balance-sheet revaluation −€2.1m \(not cash\)/g)?.length, 1);
  assert.ok(!html.includes("No CAD cash flows"));
});

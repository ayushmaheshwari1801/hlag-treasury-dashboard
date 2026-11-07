import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
const compile = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText;
const uri = source => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const format = uri(compile(readFileSync(new URL("../src/format.ts", import.meta.url), "utf8")));
const metric = uri(compile(readFileSync(new URL("../src/dashboard/metricAssumptions.ts", import.meta.url), "utf8")));
const engine = uri(compile(readFileSync(new URL("../src/engineUi.ts", import.meta.url), "utf8")));
const copy = uri(compile(readFileSync(new URL("../src/treasuryCopy.ts", import.meta.url), "utf8"))
  .replace('"./engineUi"', JSON.stringify(engine)));
const summary = uri(compile(readFileSync(new URL("../src/ForecastSummary.tsx", import.meta.url), "utf8"))
  .replace('"./treasuryCopy"', JSON.stringify(copy))
  .replace('"./dashboard/metricAssumptions"', JSON.stringify(metric))
  .replace('"react/jsx-runtime"', JSON.stringify(import.meta.resolve("react/jsx-runtime"))));
const edits = uri(compile(readFileSync(new URL("../src/forecastEdits.ts", import.meta.url), "utf8")));
const source = compile(readFileSync(new URL("../src/NewsForecast.tsx", import.meta.url), "utf8"))
  .replace('"./forecastEdits"', JSON.stringify(edits))
  .replace('"./ForecastSummary"', JSON.stringify(summary))
  .replace('"./format"', JSON.stringify(format))
  .replace('"react/jsx-runtime"', JSON.stringify(import.meta.resolve("react/jsx-runtime")));
const { default: NewsForecast } = await import(uri(source));
const { unquantifiedPoints } = await import(summary);

test('unquantified impact explains the specific evidence gaps with short bullets', () => {
  const points = unquantifiedPoints({outlook:'• Purchase price undisclosed.\n• Further capital calls remain uncertain.\n• Monitor payment terms and commitments.'});
  assert.equal(points.length, 5);
  assert.ok(points.includes('Purchase price undisclosed.'));
  assert.ok(points.includes('Further capital calls remain uncertain.'));
  assert.match(points[0], /does not mean/);
  assert.equal(unquantifiedPoints({}).length, 5);
});
test("forecast preserves human values and hides internal identifiers", () => {
  const forecast = { forecast_id: "forecast_PRIVATE_INTERNAL_HASH", mode: "model", scenario: { name: "Shipping disruption" }, outlook: "Conditional outlook.",
    predictions: [{ param_key: "bunker.vlsfo_shock_pct", change: 8, unit: "pct", confidence: 0.5, rationale: "Fuel cost may rise.", effective_date: "2026-11-02", duration_days: 210, event_ids: ["E_PRIVATE_INTERNAL_HASH"] }] };
  const html = renderToStaticMarkup(React.createElement(NewsForecast, { forecast, edits: { "0": "12" }, active: true, busy: false, onEdit: () => {}, onApply: () => {} }));
  assert.doesNotMatch(html, /PRIVATE_INTERNAL_HASH/);
  assert.match(html, /<li>/);
  assert.doesNotMatch(html, /Conditional outlook/);
  assert.match(html, /Shipping disruption/);
  assert.match(html, /Initial forecast/);
  assert.match(html, /value="12"/);
  assert.match(html, /<b>\+12%<\/b>/);
  assert.equal(renderToStaticMarkup(React.createElement(NewsForecast, { forecast, edits: {}, active: false })), "");
  assert.match(html, /2026-11-02/);
  assert.match(html, /210/);
  assert.deepEqual(forecast.predictions[0].event_ids, ["E_PRIVATE_INTERNAL_HASH"]);
});

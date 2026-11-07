import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const asModule = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText;
const uri = source => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const metric = await import(uri(asModule(readFileSync(new URL("../src/dashboard/metricAssumptions.ts", import.meta.url), "utf8"))));
const format = uri(asModule(readFileSync(new URL("../src/format.ts", import.meta.url), "utf8")));
const insights = asModule(readFileSync(new URL("../src/Insights.tsx", import.meta.url), "utf8"))
  .replace('"./format"', JSON.stringify(format))
  .replace('"react/jsx-runtime"', JSON.stringify(import.meta.resolve("react/jsx-runtime")));
const { riskStatus } = await import(uri(insights));

test("News forecast inputs map to the market metric they move", () => {
  assert.equal(metric.metricOfInput("freight.rate_shock_pct.ASIA_EUROPE"), "freight");
  assert.equal(metric.metricOfInput("bunker.vlsfo_shock_pct"), "bunker");
  assert.equal(metric.metricOfInput("rates.rate_path.EUR"), "interest");
  assert.equal(metric.metricOfInput("fx.move_pct.CNY"), "fx_cny");
  assert.equal(metric.metricOfInput("fx.move_pct.INR"), null);
});

test("forecast inputs read as label and signed change in their own unit", () => {
  assert.equal(metric.inputLabel("freight.transit_delay_days.ASIA_AMERICA"), "Collection delay · Asia–America");
  assert.equal(metric.inputChange({ change: 15, unit: "pct" }), "+15%");
  assert.equal(metric.inputChange({ change: -25, unit: "bp" }), "−25 bp");
  assert.equal(metric.inputChange({ change: 12, unit: "days" }), "+12 days");
});

test("each Event's inputs are listed under the metric they move, in the Event's colour", () => {
  const forecasts = { E1: { predictions: [{ param_key: "bunker.vlsfo_shock_pct", change: 20, unit: "pct" }, { param_key: "fx.move_pct.EUR", change: 5, unit: "pct" }] }, E2: { predictions: [] } };
  const out = metric.newsDriversByMetric([{ event_id: "E1", title: "Fuel shock" }, { event_id: "E2", title: "Quiet news" }], id => forecasts[id], { E1: "#d1495b" });
  assert.deepEqual(Object.keys(out).sort(), ["bunker", "fx_eur"]);
  assert.deepEqual(out.bunker[0], { newsId: "E1", color: "#d1495b", label: "Fuel shock", value: "VLSFO price +20%" });
});

test("model assumptions are grouped by metric without mixing MFR and freight keys", () => {
  const params = [{ key: "bunker.mfr_share" }, { key: "freight.beta" }, { key: "freight.non_usd_share.ASIA_AMERICA" }, { key: "liquidity.minimum_operational_reserve_usd" }];
  assert.deepEqual(metric.modelAssumptions("bunker", params).params.map(p => p.key), ["bunker.mfr_share"]);
  assert.deepEqual(metric.modelAssumptions("freight", params).params.map(p => p.key), ["freight.beta"]);
  assert.deepEqual(metric.modelAssumptions("fx_cny", params).params.map(p => p.key), ["freight.non_usd_share.ASIA_AMERICA"]);
  assert.deepEqual(metric.modelAssumptions("unknown", params), { title: "", params: [] });
});

test("context bar risk status matches the monitoring rule", () => {
  const lq = { scenario_eur_m: 900, scenario_headroom_eur_m: 200, minimum_eur_m: 700, scenario_min_date: "2027-01-02" };
  assert.equal(riskStatus(lq, { status: "WATCH", flags: {}, missing_inputs: [], first_breach_date: null }).label, "Watch");
  assert.equal(riskStatus(lq, { status: "LOW", missing_inputs: ["SOFR"] }).label, "Incomplete");
  assert.equal(riskStatus(lq, { status: "CRITICAL", missing_inputs: ["SOFR"] }).label, "Critical");
  assert.equal(riskStatus(lq, { status: "LOW", flags: {}, missing_inputs: [], first_breach_date: null }).firstBreach, "None in selected horizon");
});

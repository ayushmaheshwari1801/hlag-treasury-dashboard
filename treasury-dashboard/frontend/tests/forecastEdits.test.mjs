import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/forecastEdits.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
const { editedForecastScenario } = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
const groupingSource = readFileSync(new URL("../src/newsEvents.ts", import.meta.url), "utf8");
const groupingCode = ts.transpileModule(groupingSource, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
const { groupNewsEvents } = await import(`data:text/javascript;base64,${Buffer.from(groupingCode).toString("base64")}`);
const cashSource = readFileSync(new URL("../src/cashDetailRows.ts", import.meta.url), "utf8");
const cashCode = ts.transpileModule(cashSource, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
const { cashDetailRows } = await import(`data:text/javascript;base64,${Buffer.from(cashCode).toString("base64")}`);
const eventSource = readFileSync(new URL("../src/eventScenario.ts", import.meta.url), "utf8");
const eventCode = ts.transpileModule(eventSource, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
const { eventScenarioRequest, eventScenarioCacheKey, topEventScenarios, savedEventForecast } = await import(`data:text/javascript;base64,${Buffer.from(eventCode).toString("base64")}`);

test("forecast overrides preserve original estimates and update the correct driver/date", () => {
  const forecast = {
    scenario: { freight: { rate_shock_pct: { ASIA_EUROPE: .15 } }, rates: { rate_path: { USD: [{ date: "2026-11-02", step_bp: 25 }, { date: "2026-12-02", step_bp: 50 }] } } },
    predictions: [{ param_key: "freight.rate_shock_pct.ASIA_EUROPE", unit: "pct", change: 15 },
      { param_key: "rates.rate_path.USD", unit: "bp", change: 50, effective_date: "2026-12-02" }],
  };
  const edited = editedForecastScenario(forecast, { 0: "30", 1: "-25" });
  assert.equal(edited.freight.rate_shock_pct.ASIA_EUROPE, .3);
  assert.deepEqual(edited.rates.rate_path.USD.map(s => s.step_bp), [25, -25]);
  assert.equal(forecast.scenario.freight.rate_shock_pct.ASIA_EUROPE, .15);
  assert.equal(forecast.scenario.rates.rate_path.USD[1].step_bp, 50);
});

test("empty or invalid overrides do not alter the saved scenario", () => {
  const forecast = { scenario: { bunker: { vlsfo_shock_pct: .15 } }, predictions: [{ param_key: "bunker.vlsfo_shock_pct", unit: "pct", change: 15 }] };
  for (const value of ["", "not a number", "Infinity"]) assert.deepEqual(editedForecastScenario(forecast, { 0: value }), forecast.scenario);
  assert.deepEqual(editedForecastScenario(forecast, { 0: "0" }), { bunker: { vlsfo_shock_pct: 0 } });
});

test("v8 tracker counts one scored Event while retaining every original Article", () => {
  const events = groupNewsEvents([
    { id: "A1", event_id: "E1", event_title: "Port disruption", score: 80, modules: ["freight"] },
    { id: "A2", event_id: "E1", event_title: "Port disruption", score: 80, modules: ["bunker"] },
    { id: "A3", event_id: "E2", event_title: "Rate decision", score: 40, modules: ["interest"] },
  ]);
  assert.equal(events.length, 2);
  assert.equal(events[0].score, 80);
  assert.equal(events[0].title, "Port disruption");
  assert.deepEqual(events[0].article_ids, ["A1", "A2"]);
  assert.deepEqual(events[0].modules, ["freight", "bunker"]);
});

test("already grouped v8 tracker entries preserve full evidence identifiers", () => {
  const events = groupNewsEvents([{ id: "A1", event_id: "E1", article_ids: ["A1", "A2", "A3"], score: null, modules: [] }]);
  assert.deepEqual(events[0].article_ids, ["A1", "A2", "A3"]);
  assert.equal(events[0].score, null);
});

test("cash detail attributes native and FX changes once and respects the displayed horizon", () => {
  const line = { date: "2026-12-31", owner: "freight", currency: "EUR", amount_native_base_m: 100, amount_native_scenario_m: 110, fx_rate_base: 1, delta_usd_m: 21 };
  const later = { ...line, date: "2027-10-31", delta_usd_m: 200 };
  assert.equal(cashDetailRows([line, later], "freight", "2027-02-28")[0].attributed_usd_m, 10);
  const fx = cashDetailRows([line, later], "fx_eur", "2027-02-28");
  assert.equal(fx.length, 1);
  assert.equal(fx[0].attributed_usd_m, 11);
  assert.equal(fx[0].attributed_usd_m + cashDetailRows([line], "freight")[0].attributed_usd_m, line.delta_usd_m);
});

test("USD hedge settlements are assigned by underlying currency and residual FX belongs to the EUR row", () => {
  const base = { date: "2026-12-31", owner: "fx", amount_native_base_m: 0, amount_native_scenario_m: 0, fx_rate_base: 1 };
  const lines = [{ ...base, currency: "USD", sub_id: "fx:hedge:CAD", delta_usd_m: -2 },
    { ...base, currency: "INR", sub_id: "fx:transaction:INR", delta_usd_m: 3 },
    { ...base, currency: "USD", sub_id: "fx:dividend:hedge", delta_usd_m: 5 },
    { ...base, currency: "GBP", sub_id: "fx:operating:GBP", delta_usd_m: 7 }];
  const sum = id => cashDetailRows(lines, id).reduce((s, l) => s + l.attributed_usd_m, 0);
  assert.equal(sum("fx_cad"), -2);
  assert.equal(sum("fx_gbp"), 7);
  assert.equal(sum("fx_eur"), 8);
  assert.equal(sum("fx_cad") + sum("fx_gbp") + sum("fx_cny") + sum("fx_eur"), 13);
});

test("each selected Event produces an independent single-Event forecast request and cache entry", () => {
  const a = eventScenarioRequest("E1", "2026-11-02", 180);
  const b = eventScenarioRequest("E2", "2026-11-02", 180);
  assert.deepEqual(a.event_ids, ["E1"]);
  assert.deepEqual(b.event_ids, ["E2"]);
  const cache = new Map([[eventScenarioCacheKey("E1", "R1", a.as_of, 180), { scenario: { fx: { move_pct: { EUR: .1 } } } }]]);
  assert.equal(cache.has(eventScenarioCacheKey("E2", "R1", a.as_of, 180)), false);
  assert.equal(cache.has(eventScenarioCacheKey("E1", "R2", a.as_of, 180)), false);
  assert.equal(cache.has(eventScenarioCacheKey("E1", "R1", a.as_of, 90)), false);
  assert.equal(cache.has(eventScenarioCacheKey("E1", "R1", "2026-11-03", 180)), false);
  assert.throws(() => eventScenarioRequest("", a.as_of, 180));
});

test("scenario pool takes only the top two in each category without filling a short category from another", () => {
  const events = [
    { event_id: "ER1", category: "regulatory", score: 20 },
    { event_id: "ER2", category: "regulatory", score: 80 },
    { event_id: "ER3", category: "regulatory", score: 60 },
    { event_id: "EM1", category: "macroeconomics", score: { total: 50 } },
    { event_id: "EI1", category: "industry", score: 90 },
    { event_id: "EI2", category: "industry", score: 70 },
    { event_id: "EI3", category: "industry", score: 40 },
  ];
  assert.deepEqual(topEventScenarios(events).map(e => e.event_id), ["ER2", "ER3", "EM1", "EI1", "EI2"]);
  assert.equal(events.length, 7);
  assert.equal(topEventScenarios([]).length, 0);
});

test("a direct cash flow edit changes only that effect's dated USD amount", () => {
  const flow = (date, amount) => ({ effect_id: "fine-eu", overlap_key: "eu-fine", date, amount_usd_m: amount, label: "EU fine", rationale: "x" });
  const forecast = {
    scenario: { direct: { cash_flows: [flow("2026-12-15", -60), flow("2027-01-15", -60)] } },
    predictions: [{ param_key: "direct.cash_flow", unit: "usd_m", change: -60, effective_date: "2027-01-15", effect_id: "fine-eu" }],
  };
  const edited = editedForecastScenario(forecast, { 0: "-90" });
  assert.deepEqual(edited.direct.cash_flows.map(f => f.amount_usd_m), [-60, -90]);
  assert.equal(edited.direct.cash_field, undefined);
  assert.equal(forecast.scenario.direct.cash_flows[1].amount_usd_m, -60);
});

test("with a model configured, a saved fixed-rule forecast is not reused for an Event", () => {
  const rule = { selected_event_ids: ["E1"], as_of: "2026-11-07", horizon_days: 205, source_run_id: "run", mode: "rule_based", predictions: [] };
  const model = { ...rule, mode: "model", predictions: [{ param_key: "fx.move_pct.EUR" }] };
  assert.equal(savedEventForecast([rule], "E1", "2026-11-07", 205, "run", true), undefined);
  assert.equal(savedEventForecast([rule, model], "E1", "2026-11-07", 205, "run", true), model);
  assert.equal(savedEventForecast([rule], "E1", "2026-11-07", 205, "run", false), rule);
  assert.equal(savedEventForecast([model], "E1", "2026-11-07", 205, "other-run", true), undefined);
});

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/dashboard/hedgeAgentView.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
const m = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

test("recommendation amounts convert USD to EUR without treating a missing rate as zero", () => {
  assert.equal(m.eurAmount(111.65, 1.1165), "EUR 100.0m");
  assert.equal(m.eurAmount(-111.65, 1.1165, 1, true), "−EUR 100.0m");
  assert.equal(m.eurAmount(111.65, undefined), "—");
  assert.equal(m.eurAmount(111.65, 0), "—");
  const row = m.effectRows({effect:{min_headroom_change_usd_m:111.65}}, (v,d,s)=>m.eurAmount(v,1.1165,d,s)).find(r=>r[0]==="Change in minimum headroom");
  assert.equal(row[1], "+EUR 100.0m");
});

test("old and historical assessments never claim to match a changed scenario", () => {
  assert.equal(m.assessmentIsStale("before", "after"), true);
  assert.equal(m.assessmentIsStale(null, "current"), true);
  assert.equal(m.assessmentIsStale("same", "same"), false);
});

test("engine USD amounts are shown as given, signed only when asked", () => {
  assert.equal(m.usd(76.795), "USD 76.8m");
  assert.equal(m.usd(76.795, 1, true), "+USD 76.8m");
  assert.equal(m.usd(-271.592, 0), "−USD 272m");
  assert.equal(m.usd(null), "—");
});

test("the news window ends on the analysis date and the request never looks ahead", () => {
  assert.deepEqual(m.defaultWindow("2026-11-07"), { from: "2026-11-01", to: "2026-11-07" });
  const body = m.agentRequest({ from: "2026-11-01", to: "2026-11-07" }, false, { bunker: 200 }, {});
  assert.equal(body.as_of, "2026-11-07");
  assert.equal(body.shocks, undefined);                                   // What-if moves only when included
  assert.deepEqual(m.agentRequest({ from: "a", to: "b" }, true, { bunker: 200 }, {}).shocks, { bunker: 200 });
  assert.equal(m.windowProblem({ from: "2026-11-08", to: "2026-11-07" }, "2026-11-07"), "The start date is after the end date.");
  assert.equal(m.windowProblem({ from: "2026-11-01", to: "2026-11-09" }, "2026-11-07"), "The end date cannot be in the future.");
  assert.equal(m.windowProblem({ from: "2026-11-01", to: "2026-11-07" }, "2026-11-07"), null);
});

test("hedges and grades read in plain words", () => {
  assert.equal(m.hedgeLabel({ lever: "hypothetical_hedge", spec: { instrument: "swap", target: "fuel_all", coverage_pct: 50, tenor_months: 3 } }),
    "Swap · 50% of blended fuel · 3 months");
  assert.equal(m.hedgeLabel({ lever: "hedge_ratio_change", spec: { key: "fx.hedge_ratio.eur_operating_costs", from: 0, to: 1 } }),
    "FX hedge ratio · eur operating costs: 0% → 100%");
  assert.equal(m.gradeOf("material").label, "Material");
  assert.equal(m.originOf("rule_based").label, "Standard assessment");
  assert.match(m.originOf("hybrid").help, /effect on liquidity/);
});

test("effect rows flag over-hedges and the reversal cost", () => {
  const rows = Object.fromEntries(m.effectRows({ effect: { min_headroom_change_usd_m: 153.6, offset_of_driver_loss_pct: 127.2, over_hedge: true,
    cost_if_reverses_over_horizon_usd_m: 260.6, mirror_margin_on_t0_usd_m: -543.2 }, margin_test: { applicable: true, passed: true, lowest_margin_eligible_usd_m: 699.1 } })
    .map(([k, v, tone]) => [k, [v, tone]]));
  assert.deepEqual(rows["Change in minimum headroom"], ["+USD 153.6m", "pos"]);
  assert.deepEqual(rows["Offset of the module's net adverse cash effect"], ["127% · over-hedge", "neg"]);
  assert.deepEqual(rows["Cash given up if the scenario reverses"], ["USD 260.6m", "neg"]);
  assert.equal(rows["Margin-eligible liquidity test"][0], "Passed · lowest USD 699.1m");
});

test("reliability notes come from engine flags, never a confidence percentage", () => {
  const notes = m.reliabilityNotes({ diagnosis: { flags: { synthetic_register: true, proxy_based: true } } });
  assert.ok(notes.some(n => n.includes("exposures are illustrative")));
  assert.ok(notes.every(n => !/\d+%/.test(n)));
});


test("complete selected scenario and edited forecast accompany manual assumptions", () => {
  const forecast = {forecast_id:"saved", as_of:"2026-11-07", scenario:{name:"Edited event", bunker:{vlsfo_shock_pct:0.2}}};
  const window = {from:"2026-11-01",to:"2026-11-07"};
  const body = m.agentRequest(window, true, {collection_delay:60}, {"fx.hedge_ratio.cad":0.7}, forecast, 205);
  assert.deepEqual(body.scenario, forecast.scenario);
  assert.equal(body.forecast_id, "saved");
  assert.equal(body.horizon_days, 205);
  assert.equal(body.shocks.collection_delay, 60);
  assert.equal(m.agentRequest(window, false, {}, {}, forecast).scenario, undefined);
});


test('news dates reject incomplete years and impossible calendar dates',()=>{
 for (const from of ['0001-11-01','2026-02-31','2026-1-01']) assert.ok(m.windowProblem({from,to:'2026-11-07'},'2026-11-07'));
 assert.equal(m.windowProblem({from:'2026-11-01',to:'2026-11-07'},'2026-11-07'),null);
});

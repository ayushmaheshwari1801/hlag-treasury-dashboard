import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const load = (file, replace = {}) => {
  let source = readFileSync(new URL(`../src/${file}`, import.meta.url), "utf8");
  for (const [from, to] of Object.entries(replace)) source = source.replace(from, to);
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
};
const engineUi = load("engineUi.ts");
const m = await import(load("methodologyUi.ts", { '"./engineUi"': `"${engineUi}"` }));
const { cashDetailRows } = await import(load("cashDetailRows.ts"));

const line = (date, owner = "freight", extra = {}) => ({ date, owner, currency: "USD", sub_id: "freight:spot:ASIA_EUROPE:arr",
  amount_native_base_m: 1, amount_native_scenario_m: 2, fx_rate_base: 1, delta_usd_m: 1, ...extra });

test("cash lines exclude lines dated on or before T0, like the row figure", () => {
  const rows = cashDetailRows([line("2026-11-03"), line("2026-11-04"), line("2026-12-31"), line("2027-06-30")], "freight", "2027-05-31", "2026-11-03");
  assert.deepEqual(rows.map(r => r.date), ["2026-11-04", "2026-12-31"]);
  assert.equal(cashDetailRows([line("2026-11-03")], "freight", "2027-05-31").length, 1); // no T0 given: unchanged
});

test("FX rows keep their currency allocation", () => {
  const hedge = line("2026-12-31", "fx", { sub_id: "fx:hedge:cad_operating_costs:fx:opex:CAD:2026-11-06:CAD" });
  assert.equal(cashDetailRows([hedge], "fx_cad", "2027-05-31", "2026-11-03").length, 1);
  assert.equal(cashDetailRows([hedge], "fx_eur", "2027-05-31", "2026-11-03").length, 0);
});

test("history ranges slice back from the last point", () => {
  const pts = Array.from({ length: 52 }, (_, i) => ({ date: new Date(Date.UTC(2025, 10, 7 + 7 * i)).toISOString().slice(0, 10), value: i }));
  assert.equal(m.historySlice(pts, "1M").length, 5);
  assert.equal(m.historySlice(pts, "3M").length, 14);
  assert.equal(m.historySlice(pts, "1Y").length, 52);
});

test("symbol values: profile values, per-trade maps, market values and missing ones", () => {
  assert.equal(m.symbolValueText({ value: 0.5, unit: null, key: "freight.beta", ref: "param" }), "0.5");
  assert.equal(m.symbolValueText({ value: 28, unit: "days", key: "freight.pricing_lag_days", ref: "param" }), "28 days");
  assert.equal(m.symbolValueText({ value: 0.15, unit: "fraction", key: "bunker.scrubber_share", ref: "param" }), "15%");
  assert.equal(m.symbolValueText({ value: { ASIA_EUROPE: 3686, AFRICA_INTRA: 4429.3 }, unit: "USD/FEU", key: "{index}", ref: "series" }),
    "Asia Europe 3,686 USD/FEU · Africa Intra 4,429.3 USD/FEU");
  assert.equal(m.symbolValueText({ value: null, ref: "none" }), "varies (see definition)");
  assert.equal(m.symbolValueText({ value: {}, ref: "scenario" }), "none in this scenario");
});

test("header value and 1-week change", () => {
  assert.equal(m.seriesValueText({ series_id: "WCI_SHA_RTM", value: 3686, unit: "USD/FEU" }), "3,686 USD/FEU");
  assert.equal(m.seriesValueText({ series_id: "ESTR", value: 2.44, unit: "%" }), "2.44%");
  assert.equal(m.seriesChangeText({ series_id: "ESTR", change: -0.2, change_unit: "bp" }), "−0.2 bp");
  assert.equal(m.seriesChangeText({ series_id: "VLSFO_RTM", change: 14.75, change_pct: 1.93189, change_unit: "USD/t" }), "+1.93% (+14.8)");
  assert.equal(m.seriesChangeText({ series_id: "FX_USD_PER_EUR", change: 0.0028, change_pct: 0.25 }), "+0.25%");
});

test("sensitivity labels, current step and override text", () => {
  assert.equal(m.stepLabel({ step_pct: -10, slider_value: -10 }, "%"), "−10%");
  assert.equal(m.stepLabel({ step_pct: 10, slider_value: 77.825 }, "USD/t"), "+10% (+$78/t)");
  assert.equal(m.currentStep([{ step_pct: 0, slider_value: 0 }, { step_pct: 10, slider_value: 10 }], 10), 10);
  assert.equal(m.currentStep([{ step_pct: 10, slider_value: 10 }], -3.4), null);
  assert.equal(m.overrideText({ changed: true, layer: "override", profile_value: 0.5, value: 0.6, key: "freight.beta" }), "0.5 → 0.6 (this run)");
  assert.equal(m.overrideText({ changed: false, layer: "profile" }), "profile value");
  assert.notEqual(m.sensitivityKey("freight", { shocks: { freight: 1 } }), m.sensitivityKey("freight", { shocks: { freight: 2 } }));
});

test("sensitivity rows: the current setting highlights its step, or adds a Current row in order from the card", () => {
  const pts = [-20, -10, 0, 10, 20].map(s => ({ step_pct: s, slider_value: s, impact_eur_m: s, headroom_change_eur_m: s / 2 }));
  const fig = { impact_eur_m: -1.3, headroom_change_eur_m: -0.4 };
  const on = m.sensitivityRows(pts, 10, fig);
  assert.equal(on.length, 5);
  assert.deepEqual(on.filter(r => r.here).map(r => r.step_pct), [10]);
  const between = m.sensitivityRows(pts, -2.5, fig);
  assert.deepEqual(between.map(r => r.kind === "current" ? "C" : r.step_pct), [-20, -10, "C", 0, 10, 20]);
  assert.deepEqual(between[2], { kind: "current", here: true, slider_value: -2.5, impact_eur_m: -1.3, headroom_change_eur_m: -0.4 });
  assert.equal(m.sensitivityRows(pts, 35, fig).at(-1).kind, "current");
  assert.equal(m.sensitivityRows(pts, -2.5, null).length, 5);      // figure not available (page recalculating): no row
  assert.equal(m.sensitivityRows(pts, null, fig).length, 5);
});

test("current headroom change is scenario minimum minus baseline minimum, at the T0 rate, or missing", () => {
  assert.equal(m.headroomChange({ scenario_min_usd_m: 900, baseline_min_usd_m: 1000 }, 1.25), -80);
  assert.equal(m.headroomChange({ scenario_min_usd_m: null, baseline_min_usd_m: 1000 }, 1.25), null);
  assert.equal(m.headroomChange({ scenario_min_usd_m: 900, baseline_min_usd_m: 1000 }, null), null);
});

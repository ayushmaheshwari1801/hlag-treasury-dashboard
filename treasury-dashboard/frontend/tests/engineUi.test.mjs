import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/engineUi.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
const m = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);

test("% of headroom is one decimal", () => {
  assert.equal(m.pctOfHeadroom(10.51126771729331), "10.5% of headroom");
  assert.equal(m.pctOfHeadroom(null), "");
});

test("debt labels are whole numbers", () => {
  assert.equal(m.wholeLabel(1234.5678), "1,235");
  assert.equal(m.wholeLabel(-0.3), "0");
});

test("recommended actions are de-duplicated by Event ID, then capped", () => {
  const actions = [
    { rule_id: "cash_data_missing" },
    { rule_id: "event:a1", event_ids: ["E1"] },
    { rule_id: "event:a2", event_ids: ["E1"] },
    { rule_id: "event:b1", event_ids: ["E2"] },
    { rule_id: "event:c1", event_ids: ["E3"] },
  ];
  assert.deepEqual(m.topActions(actions).map(a => a.rule_id), ["cash_data_missing", "event:a1", "event:b1"]);
  assert.deepEqual(m.topActions(actions, 10).map(a => a.rule_id), ["cash_data_missing", "event:a1", "event:b1", "event:c1"]);
});

test("slider steps follow the unit and range", () => {
  assert.deepEqual(m.sliderSpec({ value: 750e6, unit: "USD", allowed_range: [0, 2e9] }), { min: 0, max: 2e9, step: 2e7, scale: 1e6 });
  assert.equal(m.sliderSpec({ value: 28, unit: "days", allowed_range: [0, 90] }).step, 1);
  assert.equal(m.sliderSpec({ value: 200, unit: "bp", allowed_range: [0, 600] }).step, 5);
  assert.equal(m.sliderSpec({ value: 0.7, unit: "fraction", allowed_range: [0.3, 0.7] }).step, 0.005);
  for (const value of [0, 1]) assert.equal(m.sliderSpec({ key: "fx.hedge_ratio.eur_operating_costs", value, unit: "fraction", allowed_range: [0, 1] }).step, 0.01);
  assert.equal(m.sliderSpec({ value: true, allowed_values: [true, false] }), null);
});

test("assumption values carry their unit", () => {
  assert.equal(m.assumptionText(750e6, "USD"), "USD 750m");
  assert.equal(m.assumptionText(350e6, "EUR per quarter"), "EUR 350m per quarter");
  assert.equal(m.assumptionText(28, "days"), "28 days");
  assert.equal(m.assumptionText(false, null), "Off");
  assert.equal(m.assumptionText(0.7, "fraction", "freight.contract_share"), "70%");
  assert.equal(m.assumptionText(0.02, "fraction per year", "freight.growth_g"), "2% per year");
  assert.equal(m.assumptionText(0.25, null, "freight.gamma"), "25%");
  assert.equal(m.assumptionText(0.05, null, "freight.non_usd_share.ASIA_EUROPE"), "5%");
  assert.equal(m.assumptionText(0.5, null, "freight.beta"), "0.5");
});

test("assumptions have readable names, with a fallback", () => {
  assert.equal(m.assumptionName("liquidity.minimum_operational_reserve_usd"), "Minimum operating reserve");
  assert.equal(m.assumptionName("freight.kappa"), "Index-linked rate pass-through");
  assert.equal(m.assumptionName("freight.new_thing_days"), "New thing days");
});

test("only overrides that differ from the profile are listed", () => {
  const params = [{ key: "bunker.mfr_share", value: 0.7, unit: "fraction" }, { key: "rates.zim_tranche_on", value: false }];
  assert.deepEqual(m.changedOverrides(params, { "bunker.mfr_share": 0.5, "rates.zim_tranche_on": false, unknown: 1 }),
    [{ key: "bunker.mfr_share", from: 0.7, to: 0.5, unit: "fraction" }]);
});

test("cash flows pivot to EUR at the T0 rate within the horizon", () => {
  const rows = [
    { driver: "fx", month: "2026-11", delta_usd_m: 11 },
    { driver: "freight", month: "2026-11", delta_usd_m: -22 },
    { driver: "freight", month: "2026-12", delta_usd_m: 44 },
    { driver: "freight", month: "2027-06", delta_usd_m: 99 },
  ];
  const p = m.pivotCashflows(rows, "delta_usd_m", 1.1, "2026-12-31");
  assert.deepEqual(p.months, ["2026-11", "2026-12"]);
  assert.deepEqual(p.drivers, ["freight", "fx"]);
  assert.equal(p.cells.freight["2026-12"], 40);
  assert.ok(Math.abs(p.total - 30) < 1e-9);
  assert.equal(m.toEur(125, 1.25), 100);
  assert.equal(m.toEur(110, null), null);
});

test("WCI and fuel are marked as not fetched live", () => {
  assert.equal(m.notFetchedLive("WCI_COMPOSITE"), true);
  assert.equal(m.notFetchedLive("VLSFO_RTM"), true);
  assert.equal(m.notFetchedLive("BRENT"), false);
  assert.deepEqual(m.groupSeries([{ series_id: "BRENT" }, { series_id: "FX_USD_PER_EUR" }]).map(g => g.id), ["FX", "Fuel and oil"]);
});

test("memo-only currencies and the translation memo line", () => {
  const gbp = { currencies: ["GBP"], memo_only: true, revaluation_eur_m: -2.1398 };
  assert.equal(m.memoOnlyText(gbp), "GBP balance-sheet exposure only \u00b7 no scheduled cash payments");
  assert.equal(m.memoLine(gbp), "Memo: balance-sheet revaluation \u2212\u20ac2.1m (not cash)");
  const cny = { currencies: ["CNY"], memo_only: false, revaluation_eur_m: 7.58 };
  assert.equal(m.memoOnlyText(cny), "");
  assert.equal(m.memoLine(cny), "Memo: balance-sheet revaluation +\u20ac7.6m (not cash)");
  assert.equal(m.memoLine({ revaluation_eur_m: 0.01 }), "Memo: balance-sheet revaluation \u20ac0.0m (not cash)");
  assert.equal(m.memoLine(null), "");
});

test("hedge cash merges settlement and margin by month in EUR", () => {
  const rows = m.hedgeMonths([{ month: "2026-12", usd_m: -125 }, { month: "2026-11", usd_m: -50 }], [{ month: "2026-11", usd_m: -250 }], 1.25);
  assert.deepEqual(rows, [{ month: "2026-11", settlement: -40, margin: -200 }, { month: "2026-12", settlement: -100, margin: 0 }]);
  assert.equal(m.clampTo(140, [0, 100]), 100);
  assert.equal(m.clampTo(7.6, [1, 36], true), 8);
  assert.equal(m.clampTo(NaN, [1, 36], true), 1);
});

test("desk exceptions read in plain language, EUR with USD in the tooltip", () => {
  const e = { kind: "driver", driver: "freight", gross_adverse_usd_m: 2387.8, pct_of_headroom: 81.5, date: "2027-11-30" };
  const d = m.deskExceptionText(e, 1.1216, "2027-11-30", { months: 6, cardImpact: "−€71.5m" });
  assert.equal(d.label, "Desk exception · Freight");
  assert.equal(d.text, "freight alone could cost up to €2,129m, 81.5% of the cash cushion, by 30 Nov 2027.");
  assert.equal(d.measure, "This looks further ahead than the cards: it covers the full forecast period (to 30 Nov 2027), while the cards above show only the selected 6 months (freight: −€71.5m). It counts freight's losses on their own, before other drivers offset them, at the date its total losses are largest.");
  assert.match(d.usdTip, /^USD 2,387.8m/);
  const fx = m.deskExceptionText({ ...e, driver: "fx" }, 1.1216, "2027-11-30");
  assert.ok(fx.text.startsWith("FX alone could cost") && !fx.measure.includes("(FX:"));
  const margin = m.deskExceptionText({ kind: "margin", gross_adverse_usd_m: 1575.7, date: "2026-11-23" }, 1.1216, "2027-11-30");
  assert.equal(margin.label, "Desk exception · Margin");
  assert.equal(margin.text, "Margin calls on the tested hedge would exceed the cash set aside for them from 23 Nov 2026, by up to €1,405m.");
  assert.match(margin.measure, /^This covers the full forecast period \(to 30 Nov 2027\)\. The cash set aside/);
  const data = m.deskExceptionText({ kind: "data", series: ["WCI_COMPOSITE", "VLSFO_RTM"] }, 1.1216);
  assert.equal(data.text, "WCI_COMPOSITE, VLSFO_RTM data is stale or missing, so this result may be unreliable.");
  assert.equal(data.measure, ""); assert.equal(data.usdTip, "");
  assert.deepEqual(m.eurWithUsd(-1575.676, 1.1216), { text: "\u2212€1,405m", tip: "USD \u22121,575.7m (EUR at the T0 rate 1.1216)" });
  assert.match(m.headroomAtRiskTip(6, "2027-05-31"), /within the selected horizon \(next 6 months, to 31 May 2027\) ÷ the cash cushion/);
  assert.deepEqual(m.eurWithUsd(null, 1.1216), { text: "—", tip: "" });
});

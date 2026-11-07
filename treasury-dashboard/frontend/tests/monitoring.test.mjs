import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const asModule = source => ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText;
const uri = source => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const format = uri(asModule(readFileSync(new URL("../src/format.ts", import.meta.url), "utf8")));
const insights = asModule(readFileSync(new URL("../src/Insights.tsx", import.meta.url), "utf8"))
  .replace('"./format"', JSON.stringify(format))
  .replace('"react/jsx-runtime"', JSON.stringify(import.meta.resolve("react/jsx-runtime")));
const { Monitoring } = await import(uri(insights));
const liquidity = { scenario_eur_m: 900, scenario_headroom_eur_m: 200, minimum_eur_m: 700, scenario_min_date: "2027-01-02" };
const calculation = { status: "LOW", flags: {}, missing_inputs: [], consumed_pct: -3, first_breach_date: null, days_until_minimum: 61, horizon_end: "2027-05-31" };
const render = (lq = liquidity, calc = calculation) => renderToStaticMarkup(React.createElement(Monitoring, { liquidity: lq, calculation: calc }));

test("monitor shows projected values, favourable headroom and no breach", () => {
  const markup = render();
  assert.match(markup, /EUR 900\.0m/);
  assert.match(markup, /EUR 200\.0m/);
  assert.match(markup, /Headroom gained vs baseline/);
  assert.match(markup, /3\.0%/);
  assert.match(markup, /2 Jan 2027 \(in 61 days\)/);
  assert.match(markup, /None in selected horizon/);
});

test("missing inputs cannot appear safe or turn unavailable values into zero", () => {
  const markup = render({ ...liquidity, scenario_eur_m: null, scenario_headroom_eur_m: null }, { ...calculation, missing_inputs: ["SOFR"], consumed_pct: null });
  assert.match(markup, /Incomplete/);
  assert.match(markup, /Unavailable/);
  assert.doesNotMatch(markup, /None in selected horizon|>Low</);
  assert.doesNotMatch(markup, /EUR 0\.0m/);
  const critical = render(liquidity, { ...calculation, status: "CRITICAL", missing_inputs: ["SOFR"], first_breach_date: "2026-12-31" });
  assert.match(critical, />Critical</);
  assert.match(critical, /31 Dec 2026/);
});


test("backend incomplete coverage prevents a no-breach or safe claim", () => {
  const markup = render(liquidity, { ...calculation, monitoring_complete: false });
  assert.match(markup, /Incomplete/);
  assert.match(markup, /Unavailable/);
  assert.doesNotMatch(markup, /None in selected horizon|>Low</);
});

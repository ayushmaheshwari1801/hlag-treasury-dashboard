// Treasury recommendations card (recommendation-agent/3): view helpers and the layout regression (the decision card
// is its own block, not the 30px-icon .action grid that wrapped the old card letter by letter).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

globalThis.window ??= globalThis;                       // api.ts reads window at load time
globalThis.localStorage ??= { getItem: () => null };

const cache = new Map();
/** Transpile src/<file> and its relative imports (recursively) into data: URLs; React resolves from node_modules. */
function load(file) {
  if (cache.has(file)) return cache.get(file);
  const url = new URL(`../src/${file}`, import.meta.url);
  let code = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  code = code.replace(/from\s+"([^"]+)"/g, (whole, spec) => {
    if (!spec.startsWith(".")) return `from ${JSON.stringify(import.meta.resolve(spec))}`;
    const base = new URL(spec, url).pathname.split("/src/")[1];
    const target = ["", ".ts", ".tsx"].map(ext => base + ext).find(p => { try { readFileSync(new URL(`../src/${p}`, import.meta.url)); return true; } catch { return false; } });
    if (!target) throw new Error(`cannot resolve ${spec} from ${file}`);
    return `from ${JSON.stringify(load(target))}`;
  });
  const out = `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
  cache.set(file, out);
  return out;
}

const v = await import(load("dashboard/recommendationView.ts"));
const { DecisionCard } = await import(load("dashboard/Recommendations.tsx"));
const when = (iso) => iso;

const session = {
  session_id: "s1", origin: "model", decisions: [], prepared: true, prepared_at: "2026-11-08T05:00:00Z", created_at: "2026-11-08T05:00:00Z",
  facts: { "F.sit.min_headroom": { label: "Situation lowest headroom", source: "hlag_engine run (read-only)", date: "2027-01-06", provenance: "ENGINE" } },
  assumptions: [{ id: "A1", statement: "Fuel keeps rising.", likelihood: "likely" }],
  to_verify: [{ id: "V1", check: "Actual fuel commitments." }],
  how_reached: { ai_cost: { calls: 6, cost_usd: 0.031841, prompt_tokens: 144522, completion_tokens: 45531 } },
};
const decision = { decision_id: "dec_1", rank: 1, title: "Review collection terms for overdue accounts, within 2 weeks (Credit Control)",
  trigger: { type: "Forecast", date: "2026-11-06", source: "Example Wire" },
  cash_at_stake: { text: "EUR -92.5m within the horizon", label: "Engine", cites: ["F.sit.min_headroom"] },
  why: [{ text: "Collections slow down.", label: "News forecast", cites: [] }], expected_effect: { text: "not quantified" },
  owner: { role: "Credit Control" }, timing: { text: "within 2 weeks" }, effort: "low", reversibility: "easy",
  if_wrong: { text: "Little is lost.", cites: [] }, escalate_if: [], event_ids: [] };

test("labels, positions and source hover text without internal ids", () => {
  assert.equal(v.labelClass("News forecast"), "lbl-news");
  assert.equal(v.labelClass("To verify"), "lbl-verify");
  assert.deepEqual(v.positionOf("heightened_monitoring"), { text: "Heightened monitoring", cls: "warn" });
  const tip = v.sourceTip({ cites: ["F.sit.min_headroom", "A1", "V1", "cand_abc"] }, session);
  assert.match(tip, /Situation lowest headroom — hlag_engine run \(read-only\), 2027-01-06 \(ENGINE\)/);
  assert.match(tip, /Assumption \(likely\): Fuel keeps rising\./);
  assert.match(tip, /To verify: Actual fuel commitments\./);
  assert.doesNotMatch(tip, /F\.sit|cand_abc|A1|V1/);
  assert.equal(v.sourceTip({ cites: [] }, session), "No source cited");
});

test("Event ticks, more text, request body and stamps", () => {
  const active = { included: [{ event_id: "E1" }, { event_id: "E2" }], not_included: [{ event_id: "E3" }], more_count: 1 };
  assert.deepEqual(v.defaultTicks(active), ["E1", "E2"]);
  assert.equal(v.moreText(1), "1 more Event not included");
  assert.equal(v.moreText(59), "59 more Events not included");
  assert.deepEqual(v.requestBody(["E1"]), { event_ids: ["E1"], mode: "auto" });
  assert.equal(v.stampText(session, when), "Prepared at 2026-11-08T05:00:00Z");
  assert.equal(v.stampText({ ...session, prepared: false }, when), "Assessed at 2026-11-08T05:00:00Z");
  assert.equal(v.isLegacy({ recommendations: [] }), true);
  assert.equal(v.isLegacy(session), false);
});

test("AI cost and steps in plain words", () => {
  assert.equal(v.costText(session), "USD 0.032 for 6 AI calls (144,522 input and 45,531 output tokens).");
  assert.equal(v.costText({ origin: "rule_based" }), "No AI call (rule-based).");
  assert.deepEqual(v.toolSummary([{ tool: "get_fact_detail", ok: true }, { tool: "submit_recommendation_set", ok: false },
    { tool: "submit_recommendation_set", ok: true }]), ["1 fact detail lookup", "2 submissions to the checks (1 refused or sent back)"]);
});

test("decision card: own block layout, two per row, details and the decision form collapsed", async () => {
  const React = await import(import.meta.resolve("react"));
  const { renderToStaticMarkup } = await import(import.meta.resolve("react-dom/server"));
  const html = renderToStaticMarkup(React.createElement(DecisionCard, { d: decision, session }));
  assert.match(html, /^<article class="rec-card"/);
  const visible = html.split("<details")[0];
  for (const text of ["Cash at stake", "Owner", "By when", "Effort / reversibility"]) assert.ok(visible.includes(text), text);
  const hidden = html.split("<details")[1];
  for (const text of ["Why it matters", "Expected effect", "If wrong", "Escalate if"]) assert.ok(hidden.includes(text), text);
  assert.match(html, /Record decision/);
  assert.doesNotMatch(html, /Reviewer name/);                 // the decision form opens only after "Record decision"
  const css = readFileSync(new URL("../src/TreasuryDashboard.css", import.meta.url), "utf8");
  assert.match(css, /\.rec-card\{display:block;min-width:0/);
  assert.match(css, /\.rec-cards\{display:grid;grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
});

test("model assumption label, prepared line, context line and event statuses", () => {
  assert.equal(v.labelClass("Model assumption"), "lbl-model");
  assert.equal(v.preparedLine({ ...session, fact_pack: { as_of: "2026-11-08" } }, when), "Prepared 2026-11-08T05:00:00Z · facts as of 2026-11-08");
  assert.match(v.CONTEXT_LINE, /^Based on today's prices plus the ticked News forecasts; the header above shows the selected What-if scenario\.$/);
  assert.equal(v.eventStatusText({ status: "used_in_decision", decision: 2 }), "Used in decision 2");
  assert.equal(v.eventStatusText({ status: "not_quantifiable" }), "Not quantifiable");
  assert.equal(v.eventStatusText({ status: "no_cash_effect" }), "No cash effect in the horizon");
});

test("prepared time is shown in Singapore time like the rest of the page", async () => {
  const fmt = await import(load("format.ts"));
  const line = v.preparedLine({ ...session, prepared_at: "2026-11-08T06:53:32Z", fact_pack: { as_of: "2026-11-08" } }, fmt.when);
  assert.equal(line, "Prepared 8 Nov 2026, 14:53 SGT · facts as of 8 Nov 2026");
});

test("facts used: plain value, date and source, the provenance label, grouped", async () => {
  const fmt = await import(load("format.ts"));
  const credit = { id: "F.bank.undrawn_rcf", label: "Unused credit lines", value: 700.1, eur_value: 649.4, unit: "USD m",
    date: "2025-12-31", source: "Annual Report 2025", provenance: "DISCLOSED" };
  assert.equal(v.factLine(credit, fmt.when), "Unused credit lines: EUR 649.4m · 31 Dec 2025 · Annual Report 2025");
  assert.equal(v.factValueText({ label: "Hedge ratio: CAD operating costs", value: 70, unit: "%" }, fmt.when), "70%");
  assert.equal(v.factValueText({ label: "VLSFO move over 4 weeks", value: 12.2, unit: "%" }, fmt.when), "+12.2%");
  assert.equal(v.factValueText({ label: "ZIM in the forecast", value: false }, fmt.when), "No");
  assert.equal(v.factLabelOf("DISCLOSED"), "Fact");
  assert.equal(v.factLabelOf("MARKET"), "Fact");
  assert.equal(v.factLabelOf("DERIVED"), "Fact");
  assert.equal(v.factLabelOf("ASSUMPTION"), "Model assumption");
  assert.equal(v.factLabelOf("PROXY"), "Model assumption");
  assert.equal(v.factLabelOf("ENGINE"), "Engine");
  assert.equal(v.factLabelOf("NEWS"), "News forecast");
  const groups = v.groupFacts([credit, { id: "F.alert.x" }, { id: "F.news.e1.freight" }, { id: "F.sit.low" }, { id: "F.policy.cad" }]);
  assert.deepEqual(groups.map(g => [g.group, g.facts.length]),
    [["Market & alerts", 1], ["News forecasts", 1], ["Liquidity forecast", 1], ["Company & policy", 2]]);
});

test("how this was reached: steps in order with each item sent back, singular counts", async () => {
  const React = await import(import.meta.resolve("react"));
  const { renderToStaticMarkup } = await import(import.meta.resolve("react-dom/server"));
  const m = await import(load("dashboard/Recommendations.tsx"));
  const s = { ...session, how_reached: { checks: { passed: 1, repaired: 1, removed: 1 }, engine_runs: 1, facts_used: [
      { id: "F.sit.low", label: "Today's prices: lowest headroom", value: 10, eur_value: 9.3, unit: "USD m", source: "Liquidity forecast (calculation engine)", provenance: "ENGINE" }],
    steps: [{ text: "Submitted 3 items to the checks." }, { text: "Round 1: 1 item sent back.", sent_back: [
      { item: "Decision 1", reason: "cash at stake missing", resolution: "corrected" }] }] } };
  const html = renderToStaticMarkup(React.createElement(m.HowReachedForTest ?? (() => null), { session: s }));
  if (!m.HowReachedForTest) return;
  assert.match(html, /1 item passed the checks after 1 repair round; 1 item removed\. 1 liquidity forecast run\./);
  assert.ok(html.indexOf("Submitted 3 items") < html.indexOf("Round 1"));
  assert.match(html, /<b>Decision 1<\/b>: cash at stake missing/);
  assert.match(html, />corrected</);
  assert.match(html, /<h5>Liquidity forecast<\/h5>/);
  assert.match(html, /Engine<\/span> Today&#x27;s prices: lowest headroom: EUR 9\.3m · Liquidity forecast/);
  assert.doesNotMatch(html, /F\.sit/);
});

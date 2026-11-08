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
    { tool: "submit_recommendation_set", ok: true }]), ["1 fact detail lookups", "2 submissions to the checks (1 refused or sent back)"]);
});

test("decision card is its own block layout, not the .action icon grid", () => {
  const card = DecisionCard({ d: decision, session });
  assert.equal(card.type, "article");
  assert.equal(card.props.className, "rec-card");
  const css = readFileSync(new URL("../src/TreasuryDashboard.css", import.meta.url), "utf8");
  assert.match(css, /\.rec-card\{display:block;min-width:0/);
  assert.match(css, /\.rec-cards\{display:grid;grid-template-columns:repeat\(auto-fill,minmax\(320px,1fr\)\)/);
});

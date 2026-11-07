// Row click path: a cash-impact row on the Market card calls onCashDetails with its row id (TreasuryDashboard then
// sets `modal`), and engine runs open the Methodology view (methodologyOn). Transpiles the real sources with
// typescript, as dashboardLayout.test.mjs does; no new dependencies.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

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

const { MarketImpactCard } = await import(load("dashboard/MarketCards.tsx"));
const { methodologyOn } = await import(load("dashboard/rowDetail.ts"));

/** Depth-first search of a React element tree, expanding only the named function components (no hooks run). */
function find(node, pred, expand) {
  if (node == null || typeof node !== "object") return null;
  if (Array.isArray(node)) { for (const n of node) { const hit = find(n, pred, expand); if (hit) return hit; } return null; }
  if (typeof node.type === "function") return expand.includes(node.type.name) ? find(node.type(node.props), pred, expand) : null;
  if (pred(node)) return node;
  return find(node.props?.children, pred, expand);
}

const row = (id, impact) => ({ id, module: id.startsWith("fx_") ? "fx" : id, label: id, impact_eur_m: impact, cash: true,
  scenario: "Observed weekly moves", impact_type: "dated_cash_flow_delta", status: "LOW", provenance: "ASSUMPTION", headroom_at_risk_pct: 1 });
const view = calculation => ({
  f: { market_metrics: [], calculation, scenario: { sliders: [] } },
  ci: { rows: [row("freight", -69.5), row("bunker", -20.8)], direct: null, note: "", adverse_total_eur_m: -90.3, net_eur_m: -90.3 },
  baseRows: {}, cls: () => "", sliderOf: () => undefined,
});
const r = { months: 6, custom: false };

test("clicking a cash-impact row calls onCashDetails with the row id", () => {
  const opened = [];
  const card = MarketImpactCard({ r, v: view({ run_id: "run_1", engine_status: "OK" }), onDetails: () => assert.fail("metric detail"),
    onCashDetails: id => opened.push(id), rowTitle: "How this figure was built" });
  const bunker = find(card, n => n.props?.role === "button" && String(n.props.className).startsWith("cfrow")
    && find(n.props.children, c => c.props?.className === "cfname" && [].concat(c.props.children).includes("bunker"), []), ["ImpactCell"]);
  assert.ok(bunker, "cash-impact row (cfrow) found");
  assert.equal(bunker.props.title, "How this figure was built");
  bunker.props.onClick();
  bunker.props.onKeyDown({ key: "Enter", preventDefault() {} });
  assert.deepEqual(opened, ["bunker", "bunker"]);
});

test("without the view the row has no Methodology tooltip", () => {
  const card = MarketImpactCard({ r, v: view({ run_id: "run_1" }), onDetails: () => {}, onCashDetails: () => {} });
  const cell = find(card, n => n.props?.role === "button" && String(n.props.className).startsWith("cfrow"), ["ImpactCell"]);
  assert.equal(cell.props.title, undefined);
});

test("engine runs open the Methodology view; other runs keep the cash-flow detail", () => {
  assert.equal(methodologyOn({ run_id: "run_1", engine_status: "OK" }), true);
  assert.equal(methodologyOn({ run_id: "run_1" }), false);
  assert.equal(methodologyOn({ engine_status: "OK" }), false);
  assert.equal(methodologyOn(undefined), false);
});

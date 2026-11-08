import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const load = async (rel) => {
  const source = readFileSync(new URL(rel, import.meta.url), "utf8").replace(/^import .*react";$/m, "const useState = () => [], useEffect = () => {};");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
};
const m = await load("../src/dashboard/pinnedNews.ts");
const row = (id, category = "industry", score = 50) => ({ id, event_id: id, title: id, category, score, article_ids: [`a-${id}`], modules: [], extra: "x" });

test("pinned News comes first, is marked, and is not shown twice", () => {
  const auto = [row("E1", "industry", 90), row("E2", "industry", 80)];
  const out = m.mergePinned(auto, [m.pinRecord(row("E5", "industry", 20)), m.pinRecord(row("E2"))]);
  assert.deepEqual(out.map(r => r.event_id), ["E5", "E2", "E1"]);
  assert.ok(out[0].pinned && out[1].pinned && !out[2].pinned);
  assert.equal(out[1].score, 80);                         // the live row wins over the stored copy
  assert.equal(m.pinRecord(row("E9")).extra, undefined);  // only tracker fields are stored
});

test("pins of News that disappeared are dropped; the rest refresh", () => {
  const kept = m.reconcilePinned([m.pinRecord(row("E1", "industry", 10)), m.pinRecord(row("GONE"))], [row("E1", "industry", 70)]);
  assert.deepEqual(kept.map(r => [r.event_id, r.score]), [["E1", 70]]);
});

test("pinned rows stay first inside their category even with a lower priority", async () => {
  const { groupNewsCategories } = await load("../src/eventScenario.ts");
  const groups = groupNewsCategories(m.mergePinned([row("HIGH", "industry", 90)], [m.pinRecord(row("LOW", "industry", 10))]));
  assert.deepEqual(groups.find(g => g.category === "industry").rows.map(r => r.event_id), ["LOW", "HIGH"]);
});

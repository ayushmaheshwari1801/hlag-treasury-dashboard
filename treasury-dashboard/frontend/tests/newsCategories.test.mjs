import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
const code = ts.transpileModule(readFileSync(new URL('../src/eventScenario.ts',import.meta.url),'utf8'), {compilerOptions:{module:ts.ModuleKind.ESNext}}).outputText;
const {groupNewsCategories} = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
test('news groups keep category order, descending priority and empty categories',()=>{
 const rows=[{title:'Industry',category:'industry',score:99},{title:'Lower',category:'regulatory',score:60},{title:'Higher',category:'regulatory',score:67}];
 const groups=groupNewsCategories(rows);
 assert.deepEqual(groups.map(g=>g.label),['Regulatory','Macro','Industry']);
 assert.deepEqual(groups[0].rows.map(r=>r.title),['Higher','Lower']);
 assert.equal(groups[1].rows.length,0);
 assert.equal(rows[0].title,'Industry');
});

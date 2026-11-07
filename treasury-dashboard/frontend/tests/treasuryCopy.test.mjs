import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
const url = code => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(code, {compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2020}}).outputText).toString('base64')}`;
const dependency = url(readFileSync(new URL('../src/engineUi.ts', import.meta.url),'utf8'));
const source = readFileSync(new URL('../src/treasuryCopy.ts',import.meta.url),'utf8').replace('"./engineUi"',JSON.stringify(dependency));
const {treasuryText, assumptionExplanation} = await import(url(source));

test('fuel assumptions and old recommendations use business wording',()=>{
 assert.doesNotMatch(assumptionExplanation('MFR share sigma; Asia-Europe share of consumption alpha; MFR reference-price lag L. Brent fill beta when fewer than 13 weeks of data.'), /sigma|alpha|beta|lag L/);
 assert.doesNotMatch(treasuryText('profile-based, tested by the engine, hand-collected CSV'), /profile-based|engine|CSV/);
});
test('saved prose hides record hashes while preserving treasury figures',()=>{
  assert.equal(treasuryText('spec_1234567890abcdef: EUR 3276.3m on 2026-11-07'), 'saved assessment: EUR 3276.3m on 2026-11-07');
  assert.ok(!treasuryText('fx.hedge_ratio.eur_operating_costs = 100%').includes('fx.'));
  assert.equal(treasuryText('EUR 2603.7405890808636m; 0.000000000000000123 EUR m'), 'EUR 2603.7405890808636m; 0 EUR m');
  assert.equal(treasuryText('2603.7405890808636 EUR m'), '2,603.74 EUR m');
});


test('assumption copy removes spec references while retaining economic limits and caveats',()=>{
 const copy=assumptionExplanation('Contract share; swept 30-70%, never an estimate (6.5, D7). Range equals the catalogue hard limits (6.10).');
 assert.match(copy,/30-70%, never an estimate/);
 assert.doesNotMatch(copy,/6\.5|D7|catalogue|swept/);
});

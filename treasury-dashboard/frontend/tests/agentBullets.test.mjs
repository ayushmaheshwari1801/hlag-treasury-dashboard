import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const compile=source=>ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2020,jsx:ts.JsxEmit.ReactJSX}}).outputText;
const uri=source=>`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const read=name=>readFileSync(new URL(`../src/${name}`,import.meta.url),'utf8');
const engine=uri(compile(read('engineUi.ts')));
const copy=uri(compile(read('treasuryCopy.ts')).replace('"./engineUi"',JSON.stringify(engine)));
const source=compile(read('AgentBullets.tsx')).replace('"./treasuryCopy"',JSON.stringify(copy)).replace('"react/jsx-runtime"',JSON.stringify(import.meta.resolve('react/jsx-runtime')));
const {agentPoints,AgentBullets}=await import(uri(source));
test('prose and numbered output become bullets without losing money or conditions',()=>{
 assert.deepEqual(agentPoints('1. Impact: -2.5 EUR m.\n2. Review funding. Only act if terms are confirmed.'),['Impact: -2.5 EUR m.','Review funding.','Only act if terms are confirmed.']);
 assert.deepEqual(agentPoints('**Impact:** EUR 12m.\n• Review timing.'),['Impact: EUR 12m.','Review timing.']);
});
test('agent rendering is a safe list and empty answers explain gaps',()=>{
 const html=renderToStaticMarkup(React.createElement(AgentBullets,{text:'<script>alert(1)</script>'}));
 assert.match(html,/<ul/);assert.match(html,/<li>/);assert.doesNotMatch(html,/<script>/);
 const empty=renderToStaticMarkup(React.createElement(AgentBullets,{text:''}));
 assert.match(empty,/not been established/);assert.match(empty,/reassess/);
});


test('initials and abbreviations remain intact while clauses become short bullets',()=>{
 assert.deepEqual(agentPoints('C.H. Robinson reports softer rates; Review collections. U.S. demand is uncertain.'), ['C.H. Robinson reports softer rates','Review collections.','U.S. demand is uncertain.']);
});

test('clauses split at a semicolon start as their own capitalised bullet', () => {
 assert.deepEqual(agentPoints('Fuel cost rises with consumption; more tonnes on longer routings raise cash cost.'),
  ['Fuel cost rises with consumption', 'More tonnes on longer routings raise cash cost.']);
});

'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {Chat,ChatMessage}=require('@lmstudio/sdk');
const {SystemPrefixTelemetry}=require('../dist/evidence-telemetry');
const {ReviewRegister}=require('../dist/review-register');
test('v3 system fingerprint measures system changes with execution-local lifetime',()=>{
 const tracker=new SystemPrefixTelemetry();
 const input=(system,user)=>Chat.from([{role:'system',content:system},{role:'user',content:user}]).getMessagesArray();
 const a=tracker.observe(input('stable','one')),b=tracker.observe(input('stable','two')),c=tracker.observe(input('changed','two'));
 assert.equal(a.changed,false);assert.equal(a.previousSystemHash,null);assert.equal(b.changed,false);assert.equal(a.systemHash,b.systemHash);
 assert.equal(c.changed,true);assert.equal(c.previousSystemHash,b.systemHash);assert.match(c.systemHash,/^[a-f0-9]{64}$/);
 assert.equal(new SystemPrefixTelemetry().observe(input('changed','two')).changed,false);
 assert.equal(JSON.stringify(c).includes('stable'),false);
});
test('v3 note schema fields cannot schedule missing report findings',()=>{
 const register=new ReviewRegister();
 register.observe([ChatMessage.create('assistant','`openQuestions` is missing. `reviewClaims` has a P1 bug.\n`ApplyDamage` has a P1 bug.')],0);
 const index=new (require('../dist/review-register').ReviewEvidenceIndex)();
 index.observe(require('./helpers/v4.cjs').sourceHistory('Audit source.',['openQuestions','reviewClaims','ApplyDamage']).getMessagesArray());
 register.attach(index);
 assert.deepEqual(register.missing('').map(row=>row.identifier),['ApplyDamage']);
});
test('v4 prefix metrics reuse exact template strings without serializing or retokenizing them',async()=>{
 const {measureContext}=require('../dist/context-manager'),{configFor}=require('./helpers/v4.cjs');
 let variant=0, counts=0;const events=[];
 const source={async getContextLength(){return 84224;},async applyPromptTemplate(chat){return `<template-${variant}>`+chat.toString();},
  async countTokens(text){counts++;return Math.ceil(text.length/4);}};
 const history=Chat.from([{role:'system',content:'SENSITIVE_SYSTEM_FIXTURE'},{role:'user',content:'request'}]);
 const tracker=new SystemPrefixTelemetry(),ctl={debug:event=>events.push(event)};
 const first=await measureContext(source,history,configFor());
 tracker.record(ctl,{executionId:'e',modelInputId:'e:0',roundIndex:0},history.getMessagesArray(),first,[]);
 variant=1;const second=await measureContext(source,history,configFor());
 tracker.record(ctl,{executionId:'e',modelInputId:'e:1',roundIndex:1},history.getMessagesArray(),second,['projection']);
 assert.equal(counts,2,'telemetry does not tokenize');
 assert.deepEqual(events.map(e=>e.event),['system_prefix_fingerprint','prefix_reuse','system_prefix_fingerprint','prefix_reuse']);
 assert.equal(events[3].commonPrefixChars,'<template-'.length);
 assert.equal(events[3].previousInputChars,first.templatedPrompt.length);
 assert.equal(events[3].systemChanged,false);assert.deepEqual(events[3].rewriteCauses,['projection']);
 assert.equal(JSON.stringify([events,first,second]).includes('SENSITIVE_SYSTEM_FIXTURE'),false);
 assert.equal(Object.keys(first).includes('templatedPrompt'),false);
 const fresh=new SystemPrefixTelemetry();fresh.record(ctl,{executionId:'next',modelInputId:'next:0',roundIndex:0},history.getMessagesArray(),second,[]);
 assert.equal(events.at(-1).previousInputChars,null);
});

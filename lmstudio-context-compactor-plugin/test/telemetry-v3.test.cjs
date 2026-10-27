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

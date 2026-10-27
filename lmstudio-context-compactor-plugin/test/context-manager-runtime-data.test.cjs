"use strict";
const test=require("node:test"),assert=require("node:assert/strict");
const {Chat,pair,configFor,prepare}=require("./helpers/v4.cjs");
const {composeModelHistory}=require("../dist/context-manager");
const {RUNTIME_FACTS}=require("../dist/execution-contracts");
function compose(oldSize,allowance=11929){
 const h=Chat.from([{role:"user",content:"Review"}]);
 for(const m of pair("read_file",{path:"A.cpp"},{ok:true,path:"A.cpp",content:"int A;"}))h.append(m);
 const old="Runtime evidence observation: "+ "x".repeat(oldSize-30);
 const current="Runtime evidence observation: "+ "y".repeat(2970);
 return composeModelHistory(h,null,configFor(),["scope"],false,undefined,
  [{key:"review_register",text:current}],new Map([["user",[old]]]),false,allowance);
}
test("consolidation does not happen in a non-rewrite round",()=>{
 const r=compose(9000);assert.equal(r.dataBlock?.replacesShown,undefined);assert.match(r.history.toString(),/xxxx/);
});
test("superseded data above allowance forces one consolidation and reports forcedRewrite",()=>{
 const r=compose(20000);assert.equal(r.dataBlock.replacesShown,true);assert.equal(r.forcedRewrite,"data_consolidation");
 assert.doesNotMatch(r.history.toString(),/xxxx/);
});
test("runtime facts respect priority and allowance",()=>{
 assert.ok(RUNTIME_FACTS);
 // Infinity means no report truncation, so use a finite long report fixture.
 const facts=Object.entries(RUNTIME_FACTS).map(([key,c])=>({key,text:"Runtime evidence observation: "+key+"\n"+"x".repeat(Number.isFinite(c.maxChars)?c.maxChars*2:12000)}));
 const r=composeModelHistory(Chat.from([{role:"user",content:"Review"}]),null,configFor(),["scope"],false,undefined,facts,undefined,false,11929);
 const text=r.history.toString();
 for(const [key,c] of Object.entries(RUNTIME_FACTS))if(c.priority===0)assert.ok(text.includes(key),key);
 assert.match(text,/…\[truncated/);assert.match(text,/Runtime data omitted for budget:/);
 assert.ok(r.privateDataParts.some(p=>p.startsWith("Runtime data omitted for budget:")));
 const p0=facts.filter(f=>RUNTIME_FACTS[f.key].priority===0).reduce((n,f)=>n+Math.min(f.text.length,RUNTIME_FACTS[f.key].maxChars),0);
 assert.ok(r.runtimeDataCurrentChars<=11929+p0);
});
test("availabilityMetadataTokens rename and runtimeDataTokens",async()=>{
 const {events}=await prepare(Chat.from([{role:"user",content:"Review"}]),{getRuntimeFacts:()=>[{key:"budget",text:"Runtime read-result budget facts: fixture"}]});
 const e=events.find(e=>e.event==="context_low_water");
 assert.ok("availabilityMetadataTokens" in e);assert.ok(!("metadataTokens" in e));
 assert.equal(e.runtimeDataTokens,e.postMetadataTokens-e.postCompactionTokens);
 assert.equal(typeof e.runtimeDataCurrentChars,"number");assert.equal(typeof e.runtimeDataSupersededChars,"number");
});

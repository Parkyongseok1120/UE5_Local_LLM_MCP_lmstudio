"use strict";
const test=require("node:test"),assert=require("node:assert/strict");
const {Chat,prepare,configFor,readers,tool}=require("./helpers/v4.cjs");
const {EvidenceManager}=require("../dist/evidence-manager");
const {emptyReferenceSnapshot}=require("../dist/reference-context");
const {minimumReadResultTokens,reserveReadResult}=require("../dist/budget-broker");
const history=()=>Chat.from([{role:"user",content:"Review the design."}]);
const system=r=>r.modelInput.getMessagesArray().filter(m=>m.isSystemPrompt()).map(m=>m.getText()).join("\n");
async function guidanceFixture(){
 const retention={},snapshot=emptyReferenceSnapshot(),em=new EvidenceManager(null,readers);
 em.referenceSnapshot=()=>snapshot;
 const extra={config:configFor({designGuidanceMode:"auto",designGuidanceMaxTokens:8192}),evidenceManager:em,
  guidanceRetention:retention,guidanceScope:{engine:"unreal",source:"explicit",projectIdentity:"Fixture.uproject"},
  guidanceIntent:{primary:"design",reason:"explicit_task_topic"}};
 const first=await prepare(history(),extra);assert.ok(retention.kept);
 return {retention,snapshot,extra,first,diagnostic(){
  snapshot.autoSignals=[{signal:"diagnostic_present",order:"last_observation",observationIds:["build-1"]}];
 }};
}
test("guidance rescope is deferred in a non-rewrite round",async()=>{
 const f=await guidanceFixture();f.diagnostic();
 const next=await prepare(history(),{...f.extra,roundIndex:1});
 assert.equal(system(next.result),system(f.first.result));
 assert.equal(next.events.find(e=>e.event==="design_guidance_input").stickyCause,"rescope_deferred");
});
test("deferred rescope applies on the next rewrite round",async()=>{
 const f=await guidanceFixture();f.diagnostic();
 const deferred=await prepare(history(),{...f.extra,roundIndex:1});
 assert.ok(f.retention.pendingScope);
 const rewritten=await prepare(history(),{...f.extra,turnStart:true});
 assert.notEqual(system(rewritten.result),system(deferred.result));assert.equal(f.retention.pendingScope,undefined);
});
test("no A-B-A system flip without rewrites",async()=>{
 const f=await guidanceFixture();f.diagnostic();
 const middle=await prepare(history(),{...f.extra,roundIndex:1});f.snapshot.autoSignals=[];
 const last=await prepare(history(),{...f.extra,roundIndex:2});
 assert.deepEqual([system(middle.result),system(last.result)],[system(f.first.result),system(f.first.result)]);
});
test("finalization round keeps the admitted instruction",async()=>{
 const f=await guidanceFixture();const final=await prepare(history(),{...f.extra,finalizing:true});
 assert.equal(system(final.result),system(f.first.result));
});
function restoredHistory(){
 const h=Chat.empty();
 for(let i=0;i<13;i++){h.append("user","Prior request "+i);h.append("assistant","Prior answer "+i+" "+ "p".repeat(16000));}
 const previous="Last answer: "+"z".repeat(3800);
 h.append("user","Audit the fixture.");h.append("assistant",previous);h.append("user","수정 진행해");
 return {h,previous};
}
test("turn start compacts a restored window to low water and keeps the previous answer",async()=>{
 const {h,previous}=restoredHistory(),{result}=await prepare(h,{turnStart:true});
 assert.ok(result.finalMeasurement.inputTokens<=47719);
 assert.ok(result.workingHistory.getMessagesArray().some(m=>m.isAssistantMessage()&&m.getText().includes(previous)));
 assert.ok(result.compactionAppliedModes.includes("final_exact_low_water"));
});
test("turn start without restored window does not deepen",async()=>{
 const {h}=restoredHistory(),{result}=await prepare(h,{turnStart:false});
 assert.ok(!result.compactionAppliedModes.includes("final_exact_low_water"));
});
test("msg5 r0 replay derives capacity from turn-start compaction",async()=>{
 const {h}=restoredHistory(),{result,broker}=await prepare(h,{turnStart:true});
 assert.ok(result.compactionAppliedModes.includes("final_exact_low_water"),"must exercise real compaction");
 const batch=broker.beginBatch(result.finalMeasurement,true,minimumReadResultTokens(readers));
 const requests=[["read_file_range",7150],["read_file_range",5340],["read_file_range",5340],["read_file_range",5340],["search_files",7337]];
 const accepted=requests.map(([name,byteBudget],i)=>reserveReadResult(batch,String(i),tool(name),{byteBudget}));
 assert.ok(accepted.every(r=>r.allowed));assert.ok(result.finalMeasurement.inputTokens<=47719);
});

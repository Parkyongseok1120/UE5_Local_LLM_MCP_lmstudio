"use strict";
const test=require("node:test"),assert=require("node:assert/strict");
const {architectureEnabled,selectArchitectureContext,ARCHITECTURE_BASELINE}=require("../dist/architecture-policy");
const {projectArchitectureEvidence}=require("../dist/architecture-observation");
const {catalog,evidence}=require("./helpers/architecture.cjs");
const opts=(extra={})=>({enabled:true,objectiveKind:"mutate",observations:[],patterns:catalog.patterns,...extra});
const facet=(body)=>projectArchitectureEvidence([{ref:evidence(),body}]);
test("A01 ordinary Unreal mutation gets a baseline without architecture keywords",()=>assert.equal(selectArchitectureContext(opts()).depth,"baseline"));
test("A02 ordinary review includes owner, lifetime, data and wiring criteria",()=>{
  assert.equal(selectArchitectureContext(opts({objectiveKind:"review"})).depth,"baseline");
  for(const criterion of ["owner","lifetime","sharing","definition","cleanup"])assert.ok(ARCHITECTURE_BASELINE.includes(criterion));
});
test("A03 off, observation-only and zero allowance are disabled",()=>{
  const config={observeOnly:false,designGuidanceMode:"auto",designGuidanceMaxTokens:6144};
  for(const x of [{observeOnly:true},{designGuidanceMode:"off"},{designGuidanceMaxTokens:0}])assert.equal(architectureEnabled({...config,...x},{engine:"unreal",source:"explicit"}),false);
});
test("A04 document/explain, Unity and unproven engine do not enable Unreal architecture",()=>{
  assert.equal(selectArchitectureContext(opts({objectiveKind:"explain"})).depth,"none");
  for(const scope of [{engine:"unity",source:"explicit"},{engine:"unreal",source:"available_tools"}])assert.equal(architectureEnabled({observeOnly:false,designGuidanceMode:"auto",designGuidanceMaxTokens:6144},scope),false);
});
test("A05 existing Component is a comparison hint, not a mandatory extraction",()=>{
  const result=selectArchitectureContext(opts({observations:facet("class UHealth : public UActorComponent {}; ")}));
  assert.ok(result.selectedPatternIds.includes("ue.component-composition"));assert.ok(ARCHITECTURE_BASELINE.includes("Keep a suitable existing owner"));
});
test("A06 requested state outliving its Actor presents lifetime alternatives",()=>{
  const result=selectArchitectureContext(opts({objectiveText:"Actor가 파괴된 뒤에도 상태를 유지해줘",observations:facet("class AOwner : public AActor {};")}));
  assert.equal(result.depth,"focused");assert.ok(result.selectedPatternIds.includes("ue.scoped-subsystem"));assert.equal(result.chosen,undefined);
});
test("A07 shared definition selects instance separation",()=>assert.ok(selectArchitectureContext(opts({observations:facet("class UDefinition : public UDataAsset {};")})).selectedPatternIds.includes("ue.definition-instance")));
test("A08 World service does not unconditionally choose a GameInstance owner",()=>{
  const result=selectArchitectureContext(opts({observations:facet("class UService : public UWorldSubsystem {}; ")}));
  assert.ok(result.selectedPatternIds.includes("ue.scoped-subsystem"));assert.equal(result.chosen,undefined);
});
test("A09 length and class naming alone do not demand extraction",()=>assert.equal(selectArchitectureContext(opts({observations:facet("// Manager\n".repeat(1000))})).depth,"baseline"));
test("A10 lexical evidence is never a claim of absent teardown",()=>{
  const result=facet("SetTimer(Handle);");assert.equal(result[0].confidence,"hypothesis");assert.ok(!result[0].facets.some(f=>f.includes("absent")));
});
test("A11 existing teardown remains visible as counter evidence",()=>assert.ok(facet("void EndPlay(); void Logout(); ClearTimer();")[0].facets.includes("teardown_present")));
test("A12 pure selection is stable and does not mutate inputs",()=>{const x=opts({observations:facet("UDataAsset Asset;")}),saved=JSON.stringify(x);assert.deepEqual(selectArchitectureContext(x),selectArchitectureContext(x));assert.equal(JSON.stringify(x),saved);});
test("A13 insufficient optional budget defers rather than changing read reserve",()=>assert.equal(selectArchitectureContext(opts({budgetAvailable:false})).disposition,"deferred_budget"));
test("A14 elective reference changes retain the admitted instruction",async()=>{
  const {prepare,Chat,configFor}=require("./helpers/v4.cjs"),retention={};
  const extra={config:configFor({designGuidanceMode:"auto",designGuidanceMaxTokens:8192}),guidanceRetention:retention,guidanceScope:{engine:"unreal",source:"explicit",projectIdentity:"Fixture.uproject"}};
  const h=Chat.from([{role:"user",content:"Fix health."}]);await prepare(h,extra);const first=retention.kept.instruction;
  await prepare(h,{...extra,roundIndex:1,guidanceIntent:{primary:"multiplayer",reason:"explicit_task_topic"}});assert.equal(retention.kept.instruction,first);
});
test("A15 invalidation is separate from elective policy state",()=>assert.equal(selectArchitectureContext(opts({complete:false,observations:facet("UDataAsset Definition;")})).selectedPatternIds.length,0));
test("A16 finalization/recovery start no new architecture evaluation",()=>{
  for(const x of [{finalizing:true},{recovery:true}])assert.equal(selectArchitectureContext(opts(x)).depth,"none");
});
test("A17 repeated dependencies produce the same bounded hint",()=>{
  const x=opts({observations:facet("AddDynamic(); RequestAsyncLoad(); UDataAsset D;")});
  assert.deepEqual(selectArchitectureContext(x),selectArchitectureContext(x));assert.ok(selectArchitectureContext(x).selectedPatternIds.length<=2);
});

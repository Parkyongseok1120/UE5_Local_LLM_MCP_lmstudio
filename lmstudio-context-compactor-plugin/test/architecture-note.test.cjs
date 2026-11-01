"use strict";
const test=require("node:test"),assert=require("node:assert/strict");
const {ChatMessage}=require("@lmstudio/sdk");
const notes=require("../src/continuity-model-notes");
const contract=require("../src/architecture-note-contract");
const {ArchitectureState,binding,evidence,draft,issue,populated,catalog,renderArchitectureContext}=require("./helpers/architecture.cjs");
const scope={objectiveFingerprint:"a".repeat(64)};
const general=()=>({scope,decisions:[],rejectedHypotheses:[],openQuestions:[]});
const stored=state=>notes.mergeArchitecture(general(),state.snapshot(),scope);
const footer=value=>"Visible.\n<!-- direct-continuity-note-v2 -->\n<continuity-note>\n"+JSON.stringify(value)+"\n</continuity-note>";
test("N01 legacy v1 note remains valid and has no structural authority",()=>assert.deepEqual(notes.validateStoredNote(general()),general()));
test("N02 stored v2 roundtrip restores judgments as historical",()=>{
  const note=stored(populated());assert.equal(notes.validateStoredNote(note).schemaVersion,2);
  const next=new ArchitectureState(binding({executionId:"new"}),notes.architecturePart(note));
  assert.equal(next.snapshot().architecture[0].applicability,"historical");assert.equal(next.currentBinding().executionId,"new");
  assert.ok(!JSON.stringify(note).includes("executionId"));
});
test("N03 history note keeps precedence over window without union",()=>{
  const {NotePersistence}=require("../dist/note-persistence");
  const messages=[ChatMessage.create("user","Fix health.")],fingerprint=notes.objectiveFingerprint(messages);
  const a=stored(populated()),b=stored(populated({subject:"Other subject"}));a.scope.objectiveFingerprint=b.scope.objectiveFingerprint=fingerprint;
  const window={note:b,verifiedNoteRefs:()=>new Set()};
  // The prior history key exists when there is a prior message before the latest user.
  const history=[ChatMessage.create("user","Fix health."),ChatMessage.create("assistant","Continuing."),ChatMessage.create("user","Continue.")];
  const fp=notes.objectiveFingerprint(history);a.scope.objectiveFingerprint=b.scope.objectiveFingerprint=fp;
  const events=[];
  const persistence=new NotePersistence({read:()=>a},window,"C:/Fixture",{objectiveFingerprint:fp,projectDescriptor:""},e=>events.push(e));
  assert.equal(persistence.restore(history,false).architecture[0].subject,a.architecture[0].subject);
  assert.ok(events.some(e=>e.event==="architecture_restore" && e.source==="history" && e.historicalCount===1));
});
test("N04 absent/empty patches preserve omitted decisions and arrays are rejected",()=>{
  const state=populated(),before=state.snapshot();assert.equal(state.commit({},issue(state)).disposition,"commit");assert.deepEqual(state.snapshot(),before);
  assert.equal(contract.validateArchitecturePatch([]),null);
  const parsed=notes.splitVisibleAnswer(footer({schemaVersion:2}));assert.equal(parsed.architecturePatch,undefined);
});
test("N05 a v1 clear preserves the independent structure snapshot",()=>{
  const state=populated(),v1=notes.resolveNoteUpdate({},scope.objectiveFingerprint,[]);
  const merged=notes.mergeArchitecture(v1.note,state.snapshot(),scope);assert.equal(merged.architecture.length,1);assert.equal(merged.decisions.length,0);
});
test("N06 model cannot supply host revision, hash, scope or approval",()=>{
  for(const extra of [{revision:7},{observedVersion:"x"},{scope:{}},{approved:true}])assert.equal(contract.validateArchitecturePatch({upsert:[draft(extra)]}),null);
});
test("N07 unknown handles, duplicate IDs and cyclic supersedes do not erase prior work",()=>{
  const state=populated(),before=state.snapshot();assert.equal(state.commit({upsert:[draft({refs:["fake"]})]},issue(state)).disposition,"reject");
  assert.equal(contract.validateArchitecturePatch({upsert:[draft(),draft()]}),null);
  assert.equal(contract.validateArchitecturePatch({upsert:[draft({supersedes:["decision_a"]})]}),null);assert.deepEqual(state.snapshot(),before);
});
test("N08 overlong fields and cardinality are rejected without truncation",()=>{
  assert.equal(contract.validateArchitecturePatch({upsert:[draft({subject:"x".repeat(120)})]}).upsert[0].subject.length,120);
  for(const extra of [{subject:"x".repeat(121)},{refs:Array(7).fill("e")},{options:Array(4).fill(draft().options[0])}])assert.equal(contract.validateArchitecturePatch({upsert:[draft(extra)]}),null);
});
test("N09 malformed and absent v2 footers never create a model judgment",()=>{
  assert.equal(notes.splitVisibleAnswer("Visible.").hasFooter,false);
  const x=notes.splitVisibleAnswer(footer({schemaVersion:2}).replace("{\"schemaVersion\":2}","{"));assert.equal(x.note,null);assert.equal(x.visibleText,"Visible.");
});
test("N10 a footer in a code fence is data",()=>assert.equal(notes.splitVisibleAnswer("```text\n"+footer({schemaVersion:2})+"\n```").hasFooter,false));
test("N11 structural fields and pattern IDs are not findings",()=>{
  const {ReviewRegister}=require("../dist/review-register"),register=new ReviewRegister();
  register.observe([ChatMessage.create("assistant","`currentOwner` unused\n`connectionPlan` bug\n`ue.definition-instance` unused")],1);
  assert.equal(register.rows.length,0);
});
test("N12 malformed optional architecture does not destroy a valid legacy note",()=>{
  const note={...general(),schemaVersion:2,architecture:[{approved:true}]};assert.deepEqual(notes.validateStoredNote(note),general());
});
test("N13 partial input cannot delete or retire an unseen judgment",()=>{
  const state=populated(),refs=issue(state);state.commit({upsert:[draft({id:"decision_b"})]},refs);
  const partial=issue(state);partial.decisions.delete("decision_b");partial.complete=false;
  assert.equal(state.commit({upsert:[draft({subject:"Updated subject"})]},partial).disposition,"commit");
  assert.equal(state.snapshot().architecture.length,2);
  assert.equal(state.commit({retireIds:["decision_b"]},partial).disposition,"reject");
});
test("N14 general summary projection cannot see or reconstruct architecture",()=>{
  const note=stored(populated()),projection=notes.generalNote(note);
  assert.equal(projection.architecture,undefined);assert.doesNotMatch(notes.renderAssistantNote(note),/Health feature|connectionPlan/);
  assert.equal(notes.mergeArchitecture({...general(),decisions:[]},notes.architecturePart(note),scope).architecture.length,1);
});
test("N15 same ID versions have different input-specific handles",()=>{
  const state=populated(),card=catalog.patterns[0];
  const a=renderArchitectureContext(binding(),"one",state.snapshot(),[],[card],"focused"),b=renderArchitectureContext(binding(),"two",state.snapshot(),[],[card],"focused");
  assert.notDeepEqual([...a.issued.patterns.keys()],[...b.issued.patterns.keys()]);
  assert.equal(state.commit({upsert:[draft({patternRefs:[...a.issued.patterns.keys()]})]},b.issued).reason,"unissued_reference");
});
test("N16 inactive v2 pass-through retains stored judgment without input injection",()=>{
  const note=stored(populated());assert.equal(notes.validateStoredNote(note).architecture.length,1);
  assert.equal(notes.generalNote(note).architecture,undefined);
});
test("N17 malformed structural patch preserves valid general patch and host invalidation",()=>{
  const state=populated();state.invalidateUnknown();
  const parsed=notes.splitVisibleAnswer(footer({schemaVersion:2,decisions:[{statement:"Keep existing cleanup",rationale:"Cleanup was observed"}],architecture:{upsert:[{approved:true}]}}));
  assert.ok(parsed.note);assert.equal(parsed.architectureError,"invalid_architecture_patch");
  const update=notes.resolveNoteUpdate(parsed.note,scope.objectiveFingerprint,[]);
  assert.equal(notes.mergeArchitecture(update.note,state.snapshot(),scope).architecture[0].applicability,"unknown");
});
test("N18 stored structure is absent from base composeModelHistory",()=>{
  const {composeModelHistory}=require("../dist/context-manager"),{Chat,configFor}=require("./helpers/v4.cjs");
  const composed=composeModelHistory(Chat.from([{role:"user",content:"Fix health"}]),stored(populated()),configFor(),[],true);
  assert.doesNotMatch(composed.history.toString(),/Health feature|connectionPlan/);
});
test("N19 capacity rejects insertion, explicit retirement permits replacement",()=>{
  const state=populated();state.commit({upsert:[draft({id:"decision_b"})]},issue(state));
  assert.equal(state.commit({upsert:[draft({id:"decision_c"})]},issue(state)).reason,"capacity");
  assert.equal(state.commit({retireIds:["decision_a"],upsert:[draft({id:"decision_c"})]},issue(state)).disposition,"commit");
  assert.equal(state.snapshot().architectureTombstones.length,1);
});
test("N20 shared note visitor includes historical evidence refs but no pattern authority",()=>{
  const note=stored(populated());assert.deepEqual([...notes.noteRefs(note)],["tool-call:r1"]);
});

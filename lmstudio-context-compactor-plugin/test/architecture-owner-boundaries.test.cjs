"use strict";
const test=require("node:test"),assert=require("node:assert/strict");
const {Chat,pair,tools,prepare,configFor}=require("./helpers/v4.cjs");
const {EvidenceManager}=require("../dist/evidence-manager");
const {ArchitectureState,binding,draft,issue,populated,evidence,renderArchitectureContext}=require("./helpers/architecture.cjs");
const notes=require("../src/continuity-model-notes");
const project="C:/Fixture/Game.uproject",file="Source/A.cpp";
function body(id,sha){return pair("read_file",{path:file,project},{ok:true,path:file,canonicalProject:project,sha256:sha,startLine:1,endLine:1,totalLines:1,content:"class UHealth : public UActorComponent {};"},id);}
function sourceFixture(){const history=Chat.from([{role:"user",content:"Fix health."}]);for(const m of body("read_a","a".repeat(64)))history.append(m);const em=new EvidenceManager(null,[...tools]);em.recordReturnedSources(history.getMessagesArray(),"exec");return {history,em};}
test("provider-normalized write invalidates the exact source that was read",()=>{
  const {history,em}=sourceFixture(),sources=em.architectureSources(history);assert.equal(sources.length,1);
  const state=new ArchitectureState(binding()),refs=issue(state,sources.map(s=>s.ref));
  const mutation=pair("replace_in_file",{path:file,project},{ok:true,path:file,canonicalProject:project,changed:true,sha256:"b".repeat(64),status:"completed"},"write_b");
  const changes=em.architectureChanges(mutation);assert.equal(changes[0].sourceKey,sources[0].ref.sourceKey);
  state.revalidateIssued(refs,changes);assert.equal(state.commit({upsert:[draft({refs:[...refs.evidence.keys()]})]},refs).reason,"superseded_reference");
});
test("newest observed read is not poisoned by the superseded prior read",()=>{
  const {history,em}=sourceFixture(),second=body("read_b","b".repeat(64));for(const m of second)history.append(m);em.recordReturnedSources(second,"exec");
  const sources=em.architectureSources(history);assert.equal(sources.length,1);assert.equal(sources[0].ref.observedVersion,"b".repeat(64));
  const state=new ArchitectureState(binding()),refs=issue(state,sources.map(s=>s.ref));state.revalidateIssued(refs,em.architectureVersions());
  assert.equal(state.commit({upsert:[draft({refs:[...refs.evidence.keys()]})]},refs).disposition,"commit");
});
test("general update overflow retains the host-invalidated architecture snapshot",()=>{
  const scope={objectiveFingerprint:"a".repeat(64)},state=populated(),part=state.snapshot();
  part.architecture[0].evidenceRefs=Array.from({length:4},(_,i)=>evidence({ref:`tool-call:r${i}`,sourceKey:"x".repeat(850)}));
  const prior=notes.mergeArchitecture({scope,decisions:[],rejectedHypotheses:[],openQuestions:[]},part,scope);
  assert.ok(prior);const update={scope,decisions:[{id:"general_one",status:"open",statement:"s".repeat(280),rationale:"r".repeat(380)}],rejectedHypotheses:[{id:"general_two",status:"open",hypothesis:"h".repeat(250),reason:"q".repeat(180)}],openQuestions:[]};
  assert.ok(notes.validateStoredNote(update));assert.ok(JSON.stringify({...update,...part}).length>6000);
  const merged=notes.mergeArchitecture(update,part,scope,prior);assert.equal(merged.architecture.length,1);assert.deepEqual(merged.decisions,prior.decisions);
});
test("returned target mismatch omits Unreal architecture from the actual input",async()=>{
  const {history,em}=sourceFixture();em.referenceSnapshot=()=>({items:[],omittedItems:0,signals:[],applicability:{packages:{},networkPackagesAmbiguous:false},projectObservation:{usable:false,reason:"returned_target_mismatch",engine:"unreal"}});
  const {result}=await prepare(history,{config:configFor({designGuidanceMode:"auto",designGuidanceMaxTokens:8192}),evidenceManager:em,
    guidanceScope:{engine:"unreal",source:"explicit",projectIdentity:project}});
  assert.doesNotMatch(result.modelInput.toString(),/For an Unreal code task|Architecture reference data/);
});
test("a prior judgment never claims an evicted body remains in the current input",()=>{
  const state=populated(),view=renderArchitectureContext(binding(),"input-new",state.snapshot(),[],[],"baseline");
  const data=JSON.parse(view.text.split("\n").at(-1));assert.equal(data.decisions[0].evidenceRefs[0].availability,"unavailable");
});
test("write invalidation survives future rounds and ledger persistence until a new direct read",()=>{
  const {history,em}=sourceFixture(),mutation=pair("replace_in_file",{path:file,project},
    {ok:true,path:file,canonicalProject:project,changed:true,sha256:"b".repeat(64),status:"completed"},"write_b");
  em.recordReturnedSources(mutation,"exec");
  assert.equal(em.architectureSources(history)[0].ref.applicability,"unknown");
  const state=new ArchitectureState(binding()),refs=issue(state,em.architectureSources(history).map(s=>s.ref));
  assert.equal(state.commit({upsert:[draft({refs:[...refs.evidence.keys()]})]},refs).reason,"superseded_reference");
  const {ReadLedger}=require("../src/read-ledger"),ledger=new ReadLedger();ledger.load(em.ledger.toJSON());
  assert.equal(ledger.returnedSources("exec").files[0].sourceInvalidated,true);
  const latest=body("read_b","b".repeat(64));for(const m of latest)history.append(m);em.recordReturnedSources(latest,"exec");
  assert.equal(em.architectureSources(history)[0].ref.applicability,"same_observed_version");
});
test("a sealed v2 window restores through its parent lineage without current execution authority",t=>{
  const fs=require("node:fs"),os=require("node:os"),path=require("node:path"),{WorkingContext,hash,serialize}=require("../src/working-context");
  const root=fs.mkdtempSync(path.join(os.tmpdir(),"architecture-window-"));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const {history}=sourceFixture(),scope={conversation:"arch",lineage:"arch",workspace:"fixture",repository:"repo"};
  const context=new WorkingContext(scope,{root,durable:true,lineage:"first"});
  const noteScope=notes.attachScope({},notes.objectiveFingerprint(history.getMessagesArray()),history.getMessagesArray()).scope;
  context.note=notes.mergeArchitecture({scope:noteScope,decisions:[],rejectedHypotheses:[],openQuestions:[]},populated().snapshot(),noteScope);
  context.captureReturned(history,()=>true);assert.equal(context.commit(history,history,{exact:true,remainingTokens:5000},hash(serialize(history)),"model"),true);
  assert.equal(context.seal(history,history),true);context.close();
  const next=new WorkingContext(scope,{root,durable:true,lineage:"next",parentLineage:"first"});
  assert.equal(next.restore(history).reason,"restored_remeasure_required");assert.equal(next.note.schemaVersion,2);
  const hydrated=new ArchitectureState(binding({executionId:"next",lineage:"next"}),notes.architecturePart(next.note));
  assert.equal(hydrated.snapshot().architecture[0].applicability,"historical");next.close();
});
test("scope rejection excludes rather than recreates prior structural judgments",()=>{
  const {reconcileArchitectureRound}=require("../dist/architecture-round"),{history}=sourceFixture();
  const fingerprint=notes.objectiveFingerprint(history.getMessagesArray()),scope=notes.attachScope({},fingerprint,history.getMessagesArray()).scope;
  const state=populated(),note=notes.mergeArchitecture({scope,decisions:[],rejectedHypotheses:[],openQuestions:[]},state.snapshot(),scope);
  for(const m of pair("read_file",{path:file,project:"C:/Other/Other.uproject"},{ok:true,path:file,canonicalProject:"C:/Other/Other.uproject",sha256:"c".repeat(64),content:"int Other;"},"other"))history.append(m);
  const result=reconcileArchitectureRound(state,note,history.getMessagesArray(),new Set(),{objectiveFingerprint:fingerprint,projectDescriptor:project});
  assert.equal(result.note,null);assert.equal(result.state,null);assert.equal(state.commit({},issue(state)).disposition,"reject");
});
for(const changed of [false,true])test(`archive re-delivery cannot replace the latest direct observation (changed=${changed})`,()=>{
  const {history,em}=sourceFixture(),latest=body("read_b","b".repeat(64));for(const m of latest)history.append(m);em.recordReturnedSources(latest,"exec");
  const key=em.architectureSources(history)[0].ref.sourceKey;
  if(changed)em.recordReturnedSources(pair("replace_in_file",{path:file,project},
    {ok:true,path:file,canonicalProject:project,changed:true,sha256:"c".repeat(64),status:"completed"},"write_c"),"exec");
  const local={name:"evidence_first_read_context",parametersJsonSchema:{properties:{}}};
  require("../dist/tool-capability-registry").localObservationTools.add(local);em.tools.push(local);
  em.recordReturnedSources(pair("evidence_first_read_context",{ref:"fixture"},{ok:true,kind:"historical_evidence_range",sourceKey:key,
    sourceVersion:"a".repeat(64),sourceIdentity:{path:file},sourceFacts:{startLine:1,endLine:1,totalLines:1},
    representation:"sanitized_body",bodyComplete:true,content:"class UHealth : public UActorComponent {};"},"archive_a"),"exec");
  const sources=em.architectureSources(history);assert.equal(sources[0].ref.observedVersion,"b".repeat(64));
  assert.equal(sources[0].ref.applicability,changed?"unknown":"same_observed_version");
  assert.equal(em.architectureVersions()[0].observedVersion,"b".repeat(64));
});

test("unknown writes invalidate version observations even after delivery metadata eviction",()=>{
  const {history,em}=sourceFixture(),key=em.architectureSources(history)[0].ref.sourceKey;
  for(let i=0;i<257;i++)em.recordReturnedSources(pair("search_files",{query:"Other",project},
    {ok:true,canonicalProject:project,results:[{path:`Source/Other${i}.cpp`,line:1,text:"int Other;"}]},`search_${i}`),"exec");
  assert.equal(em.returnedSources().files.some(f=>f.sourceKey===key),false);
  assert.ok(em.ledger.sourceObservation(key));
  em.recordReturnedSources(pair("replace_in_file",{project},{ok:false,status:"outcome_unknown"},"uncertain_write"),"exec");
  assert.equal(em.ledger.sourceObservation(key).invalidated,true);
  const local={name:"evidence_first_read_context",parametersJsonSchema:{properties:{}}};
  require("../dist/tool-capability-registry").localObservationTools.add(local);em.tools.push(local);
  em.recordReturnedSources(pair(local.name,{ref:"fixture"},{ok:true,kind:"historical_evidence_range",sourceKey:key,
    sourceVersion:"a".repeat(64),sourceIdentity:{path:file},sourceFacts:{startLine:1,endLine:1,totalLines:1},
    representation:"sanitized_body",bodyComplete:true,content:"class UHealth : public UActorComponent {};"},"archive_a"),"exec");
  const sources=em.architectureSources(history);assert.equal(sources.length,1);assert.equal(sources[0].ref.applicability,"unknown");
  const refs=issue(new ArchitectureState(binding()),sources.map(s=>s.ref));assert.equal(refs.evidence.size,1);
  assert.equal(new ArchitectureState(binding()).commit({upsert:[draft({refs:[...refs.evidence.keys()]})]},refs).reason,"superseded_reference");
});

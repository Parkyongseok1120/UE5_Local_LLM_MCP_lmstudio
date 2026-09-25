"use strict";
require('./tool-round-lifecycle.test.cjs');
require('./runtime-policy.test.cjs');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {Chat,ChatMessage}=require('@lmstudio/sdk');
const {BudgetBroker,BatchReservation,reserveReadResult,minimumReadResultTokens}=require('../dist/budget-broker');
const {ContextManager,createInputAssembler}=require('../dist/context-manager');
const {readConfig}=require('../dist/execution-config');
const {EvidenceManager,observeRecoveryProgress,seedRecoveryProgress,coverageQueryKey,coverageSourceVersion,completedRequestFingerprints}=require('../dist/evidence-manager');
const {WorkingContext,hash,serialize}=require('../dist/working-context');
const {ToolCapabilityRegistry,isObservationOnlyToolCall}=require('../dist/tool-capability-registry');
const {RoundTransaction}=require('../dist/execution-state');
const {DeliveryController}=require('../dist/delivery-controller');
const {visibleBlocks,evaluateCompletion}=require('../scripts/measurement-contract.cjs');
const tool={name:'read_file',pluginIdentifier:'mcp/unity-tools'};
const scope=conversation=>({conversation,lineage:conversation,workspace:'w',repository:'r'});
function history(value,id='r',args={path:'Assets/a.txt'}) {
  const h=Chat.from([{role:'user',content:'Read the evidence.'}]);
  h.append(ChatMessage.from({role:'assistant',content:[{type:'toolCallRequest',toolCallRequest:{type:'function',id,name:'read_file',arguments:args}}]}));
  h.append(ChatMessage.from({role:'tool',content:[{type:'toolCallResult',toolCallId:id,content:JSON.stringify(value)}]}));
  return h;
}
const payload=(extra={})=>({ok:true,kind:'workspace_file_observation',workspaceIdentity:'w',hash:'v1',path:'Assets/a.txt',startLine:1,endLine:2,text:'evidence '+ 'x'.repeat(3000),...extra});

test('H02 final mapping excludes thinking; swapped facts, duplicate reads and length are failures',()=>{
  const oracle={A:731,B:942},base={text:JSON.stringify(oracle),finishReason:'eosFound',calls:['A','B'],oracle};
  assert.equal(evaluateCompletion(base).success,true);
  for(const change of [{text:'{"A":942,"B":731}'},{calls:['A','B','A']},{finishReason:'maxPredictedTokensReached'},
    {text:visibleBlocks([{roleOverride:'assistant',style:{type:'thinking'},text:base.text}])}])
    assert.equal(evaluateCompletion({...base,...change}).success,false);
});
test('T03 context/output truncation and unknown completion preserve results but discard planning',()=>{
  const h=history(payload()),messages=h.getMessagesArray().slice(1);
  for(const finishReason of ['contextLengthReached','maxPredictedTokensReached','userStopped','unknown']){
    const tx=new RoundTransaction({messages,predictionCompleted:true,finishReason},false);
    assert.equal(tx.planningCommitted,false,finishReason);
    const out=Chat.empty();tx.commitEvidence(out);assert.equal(out.getMessagesArray().at(-1).getToolCallResults().length,1);
  }
  assert.equal(new RoundTransaction({messages,predictionCompleted:true,finishReason:'eosFound'},false).planningCommitted,true);
});
test('T04 delivered EOS report cannot certify objective completion',()=>{
  const evaluated=new DeliveryController().evaluate(Chat.empty(),{messages:[ChatMessage.create('assistant','More files remain unread.')],finishReason:'eosFound'},false,'research_recovery_complete',0);
  assert.equal(evaluated.generationCompleted,true);assert.equal(evaluated.reportDelivered,true);
  assert.equal(evaluated.objectiveSatisfied,null);assert.equal(evaluated.taskCompleted,false);
});
test('B01 mandatory floor LOW fit does not certify next read liveness',()=>{
  const b=new BudgetBroker({workingInputTargetTokens:18000,workingInputTriggerTokens:22000,safetyMarginTokens:2048});
  const m={contextLength:38912,outputReserve:8192,inputTokens:22000};
  const w=b.watermarks(m,22000,true);
  assert.equal(w.effectiveHighWaterTokens,16896);assert.equal(w.effectiveLowWaterTokens,22000);
  assert.equal(w.nextActionFit,false);assert.equal(b.beginBatch(m).capacity,0);
});
test('B02 native Unreal maxBytes is clamped and oversize return blocks later dispatch',()=>{
  const batch=new BatchReservation(9000),t={name:'read_file',pluginIdentifier:'mcp/unreal-agent',parametersJsonSchema:{properties:{maxBytes:{type:'number'}}}};
  const reservation=reserveReadResult(batch,'a',t,{});
  assert.equal(reservation.bounded,true);assert.ok(reservation.arguments.maxBytes>=1024);
  assert.ok(reservation.arguments.maxBytes<65536);
  assert.equal(batch.receive('a',50000),true);assert.equal(batch.exceeded,true);
  assert.equal(batch.reserve('b',1),0);
  const entry=batch.snapshot().entries[0];assert.equal(entry.actualEnvelopeBytes,50000);assert.ok(entry.reservedTokens<=9000);
});
test('B03 schema maximum/default applied before reservation; two small reads share capacity',()=>{
  const b=new BatchReservation(12000),t={parametersJsonSchema:{properties:{byteBudget:{minimum:1024,maximum:1024}}}};
  const a=reserveReadResult(b,'a',t,{}),c=reserveReadResult(b,'b',t,{});
  assert.equal(a.arguments.byteBudget,1024);assert.equal(a.reservedTokens,2048);assert.equal(c.allowed,true);
  const d=reserveReadResult(new BatchReservation(12000),'c',{name:'git_changed_files',parametersJsonSchema:{properties:{byteBudget:{minimum:1024,maximum:65536}}}},{});
  assert.equal(d.arguments.byteBudget,4096);
});

test('B01 a cheap auxiliary archive reader cannot hide native read starvation',()=>{
  const tools=[{name:'read_file',pluginIdentifier:'mcp/unreal-agent',parametersJsonSchema:{properties:{maxBytes:{type:'integer'}}}},
    {name:'evidence_first_read_context',parametersJsonSchema:{properties:{maxChars:{minimum:1,maximum:8192}}}}];
  const min=minimumReadResultTokens(tools);assert.equal(min,7168);
  const broker=new BudgetBroker({workingInputTargetTokens:18000,workingInputTriggerTokens:22000,safetyMarginTokens:2048});
  assert.equal(broker.watermarks({contextLength:38912,outputReserve:8192,inputTokens:19000},19000,true,min).nextActionFit,false);
});
test('T01 operation read profiles retain reads and cannot grant writes',()=>{
  for(const [name,action] of [['unity_prefab','read'],['unity_scene','list'],['unity_tests','status']]) {
    const original={name,pluginIdentifier:'mcp/unity-tools',parametersJsonSchema:{type:'object',properties:{action:{type:'string'}},required:[]}};
    const [profile]=new ToolCapabilityRegistry([original]).readProfile();assert.ok(profile);
    assert.ok(profile.parametersJsonSchema.properties.action.enum.includes(action));
    assert.equal(isObservationOnlyToolCall(profile,{name,arguments:{action}}),true);
    assert.equal(isObservationOnlyToolCall(profile,{name,arguments:{action:'run'}}),false);
    assert.equal(original.parametersJsonSchema.properties.action.enum,undefined);
  }
});
test('T02 known raw changed-files intent retains dependent diff/source readers',()=>{
  const {planReadRecovery}=require('../dist/recovery-coordinator');
  const tools=['git_changed_files','git_diff_file','git_read_file'].map(name=>({name,pluginIdentifier:'mcp/unreal-agent'}));
  const h=Chat.from([{role:'user',content:'Inspect the changes'}]);
  const decision=planReadRecovery(h,h,{names:['git_changed_files'],unknownNames:[],registeredButWithheldNames:[],registeredUnsafeNames:[],knownReadOnlyNames:['git_changed_files']},tools);
  assert.deepEqual(decision.retryTools.map(t=>t.name),tools.map(t=>t.name));
});
test('E01 native Unity runtime read flows into successful recovery evidence',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'astra-native-'));
  try {
    for(const d of ['Assets','Packages','ProjectSettings'])fs.mkdirSync(path.join(root,d));
    fs.writeFileSync(path.join(root,'Packages/manifest.json'),'{}');fs.writeFileSync(path.join(root,'ProjectSettings/ProjectVersion.txt'),'m_EditorVersion: 6000');
    fs.writeFileSync(path.join(root,'Assets/a.txt'),'native oracle 731\nsecond line');
    const runtime=require('../../lmstudio-unity-mcp/src/server').createRuntime({UNITY_PROJECT_ROOT:root});
    const value=await runtime.call('read_file',{path:'Assets/a.txt'});
    assert.equal(value.kind,'workspace_file_observation');
    const manager=new EvidenceManager(null,[tool]),progress=manager.observe(history(value).getMessagesArray());
    assert.equal(progress.newSourceUnits,1);assert.equal(progress.progressed,true);
    const {RecoveryCoordinator}=require('../dist/recovery-coordinator');
    assert.equal(new RecoveryCoordinator().advance(progress,12,true,false,1,1,'').trigger,null);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});
test('E02 historical rehydration missing in current input is useful without new source coverage',()=>{
  const value={ok:true,kind:'historical_evidence_range',evidenceId:'e',version:'v',returnedRange:[0,100],nextOffset:100,content:'evidence'};
  const h=history(value),manager=new EvidenceManager(null,[tool]);manager.seed(h);
  manager.captureExposure(Chat.empty(),'without');const regained=manager.observe(h.getMessagesArray());
  assert.equal(regained.newSourceUnits,0);assert.equal(regained.newArchiveUnits,0);
  assert.equal(regained.restoredWorkingEvidenceUnits,1);assert.equal(regained.progressed,true);
  assert.equal(manager.observe(h.getMessagesArray()).progressed,false);
  manager.captureExposure(h,'present');assert.equal(manager.observe(h.getMessagesArray()).progressed,false);
  assert.equal(completedRequestFingerprints(Chat.empty()).size,0);
});
test('E03 query, nested workspace and source hash cannot collapse',()=>{
  const base={kind:'workspace_file_observation',workspaceIdentity:'w',repositoryIdentity:'r',hash:'h',path:'a'};
  assert.notEqual(coverageQueryKey({...base,query:'Alpha'}),coverageQueryKey({...base,query:'Beta'}));
  assert.notEqual(coverageQueryKey(base),coverageQueryKey({...base,workspaceIdentity:'nested'}));
  assert.notEqual(coverageQueryKey(base),coverageQueryKey({...base,hash:'changed'}));
  assert.notEqual(coverageSourceVersion({kind:'git_observation',comparison:'worktree',head:'same',text:'a'}),coverageSourceVersion({kind:'git_observation',comparison:'worktree',head:'same',text:'b'}));
});
test('E04 capture and repeated shifted projection share one archive ID; different call IDs retain provenance',()=>{
  const store=new WorkingContext(scope('dedup')),h=history(payload());
  store.captureReturned(h,()=>true,{executionId:'e'});assert.equal(store.archive.entries().length,1);
  store.project(h,()=>true,{executionId:'e'},256);assert.equal(store.archive.entries().length,1);
  const shifted=Chat.from([{role:'user',content:'earlier'}]);for(const m of h.getMessagesArray())shifted.append(m);
  store.project(shifted,()=>true,{executionId:'other'},256);assert.equal(store.archive.entries().length,1);
  store.captureReturned(history(payload(),'other'),()=>true);assert.equal(store.archive.entries().length,2);
  assert.deepEqual(store.archive.entries().map(e=>e.record.metadata.providerRequestId).sort(),['other','r']);
});
test('E05 sanitized persistent view permits 140 observations under quota without altering live capabilities',()=>{
  const store=new WorkingContext(scope('soak'),{maxRecords:8}),reasoning=require('../dist/continuity-text').REASONING_SEPARATOR;
  for(let n=0;n<140;n++) {
    const h=history(payload({text:'fact-'+n+' '+ 'z'.repeat(3500),receipt:'live-receipt',nextCursor:'live-cursor'}),'r'+n);
    h.append('assistant','hidden reasoning'+reasoning+'visible conclusion');
    store.captureReturned(h,()=>true);const projection=store.project(h,()=>true,{},256);
    assert.equal(projection.archiveFailed,false,'round '+n);
    store.captureExposure(projection.history,'input-'+n,true);
    const safe=store.persistable(projection.history);
    assert.equal(safe.toString().includes('live-cursor'),false);assert.equal(h.toString().includes('live-cursor'),true);
    assert.equal(store.commit(h,safe,{exact:true,remainingTokens:1000},hash(serialize(h)),'m'),true,store.lastCommitReason);
    assert.ok(store.archive.entries().length<=8);assert.equal(store.refsValid(),true);
    assert.equal(store.restore(h).reason,'restored_remeasure_required');
  }
});
test('E06 source failure stays failure through capture, archive read, seed and observe',()=>{
  for(const failure of [{ok:false},{ok:false,status:'complete'},{errorCode:'ENOENT'},{status:'failed'},{error:'SDK guard denied'}]){
    const store=new WorkingContext(scope(JSON.stringify(failure))),h=history(payload(failure));store.captureReturned(h,()=>true);
    const r=store.archive.entries()[0].record,v=store.archive.read(r.evidenceId,r.archivedBodyHash,0,8192);
    assert.equal(v.ok,true);const archived=history(v);
    assert.equal(seedRecoveryProgress(archived).coverage.size,0);
    const progress=observeRecoveryProgress(archived.getMessagesArray(),new Set());
    assert.equal(progress.progressed,false);assert.equal(progress.errorCount,1);
    const projected=store.project(h,()=>true,{},256).history;
    assert.equal(seedRecoveryProgress(projected).coverage.size,0);
  }
});

test('E03 source and projected page use the same identity/query across provider and pagination',()=>{
  const store=new WorkingContext(scope('canonical')),manager=new EvidenceManager(store,[tool]);
  const h=history(payload({kind:'git_observation',pageStart:1,pageEnd:2,query:'Alpha'}),'r',{path:'a',byteBudget:65536,startLine:1});
  manager.captureReturned(h.getMessagesArray(),'e');
  const raw=require('../dist/evidence-manager').observationRecords(h.getMessagesArray(),[tool])[0].value;
  const projection=JSON.parse(manager.project(h,{},256).history.getMessagesArray().at(-1).getToolCallResults()[0].content);
  assert.equal(coverageQueryKey(raw),coverageQueryKey(projection));
  const again=require('../dist/evidence-manager').observationRecords(history(payload({kind:'git_observation',pageStart:3,pageEnd:4,query:'Alpha'}),'r2',{path:'a',byteBudget:1024,startLine:3}).getMessagesArray(),[tool])[0].value;
  assert.equal(coverageQueryKey(raw),coverageQueryKey(again));
  assert.equal(manager.view({kind:'git_observation',comparison:'worktree',head:'x',text:'changed'},'p').version.verified,false);
});

test('E05 candidate omitting unconsumed evidence blocks explicitly before quota exhaustion',()=>{
  const store=new WorkingContext(scope('pending'),{maxRecords:4});
  store.captureReturned(history(payload()),()=>true);
  const candidate=Chat.from([{role:'user',content:'omitted candidate'}]);
  assert.equal(store.commit(history(payload()),candidate,{exact:true,remainingTokens:1000},hash(serialize(history(payload()))),'m'),false);
  assert.equal(store.lastCommitReason,'pending_evidence_omitted');assert.equal(store.manifest,null);
  assert.throws(()=>store.captureExposure(Chat.empty(),'omitted',false),/CONTEXT_PENDING_EVIDENCE_OMITTED/);
  assert.equal(store.returnedRefs.size,1);
  const projected=store.project(history(payload()),()=>true,{},256).history;
  store.captureExposure(projected,'retry',false);store.captureExposure(projected,'retry',true);
  assert.equal(store.returnedRefs.size,0);
});

test('E01 duplicate/orphan callbacks cannot become native success observations',()=>{
  const h=history(payload());h.append(h.getMessagesArray().at(-1));
  const manager=new EvidenceManager(null,[tool]);
  assert.equal(manager.observe(h.getMessagesArray()).progressed,false);
  assert.equal(manager.observe(h.getMessagesArray().slice(-1)).progressed,false);
});

test('SDK guard denial is failed evidence and cannot continue research recovery',()=>{
  const manager=new EvidenceManager(null,[tool]);
  const denied=manager.observe(history({error:'Shared read-result budget exhausted.'}).getMessagesArray());
  assert.equal(denied.progressed,false);assert.equal(denied.errorCount,1);
  assert.equal(completedRequestFingerprints(history({error:'Denied'})).size,0);
  const {RecoveryCoordinator}=require('../dist/recovery-coordinator');
  assert.equal(new RecoveryCoordinator().advance(denied,12,true,false,1,1,'').trigger,'research_recovery_exhausted');
});

test('O02 distinct assembly stages and schema/reserve cache keys are measured',async()=>{
  let tokenCalls=0;
  const source={async getContextLength(){return 38912},async applyPromptTemplate(chat,{toolDefinitions}){return chat.toString()+JSON.stringify(toolDefinitions)},
    async countTokens(text){tokenCalls++;return text.length}};
  const config=readConfig({getPluginConfig(){return {get:()=>undefined}}});
  const before=Chat.from([{role:'user',content:'before long projection'}]),after=Chat.from([{role:'user',content:'short'}]);
  const assemble=createInputAssembler({tokenSource:source,config,roundTools:[],roundScopeInstructions:[],getRoundOutputReserve:()=>8192,
    modelInputId:'cost',noteEnabled:false,getNote:()=>null,historicalAvailabilityLedger:[],preProjectionHistory:before,postProjectionHistory:after});
  const a=await assemble(after),n=tokenCalls,b=await assemble(after);
  assert.ok(a.stages.postHistoryTokens>a.stages.postProjectionTokens);
  assert.notEqual(a.stages.stageFingerprints.history,a.stages.stageFingerprints.projection);
  assert.equal(tokenCalls,n);assert.ok(b.stages.measurementCost.cacheHits>0);
  await assemble(after,{tools:[{...tool,description:'native',parametersJsonSchema:{type:'object'}}]});
  assert.ok(tokenCalls>n);
  const c=await assemble(after,{outputReserve:4096});assert.equal(c.measurement.outputReserve,4096);
});

test('O03 multilingual, escaped JSON and emoji estimates cannot authorize exact-only execution',async()=>{
  const config=readConfig({getPluginConfig(){return {get:()=>undefined}}});
  const source={async getContextLength(){return 38912},async applyPromptTemplate(){throw new Error('offline tokenizer')}};
  for(const content of ['한글'.repeat(200),'😀'.repeat(200),JSON.stringify({text:'\\\"'.repeat(200)})]){
    const h=Chat.from([{role:'user',content}]);
    const assemble=createInputAssembler({tokenSource:source,config,roundTools:[],roundScopeInstructions:[],getRoundOutputReserve:()=>8192,
      modelInputId:'inexact',noteEnabled:false,getNote:()=>null,historicalAvailabilityLedger:[]});
    const result=await new ContextManager(config,new BudgetBroker(config)).enforceLowWater(h,assemble,{hasReadTools:false});
    assert.equal(result.canRun,false);assert.equal(result.disposition,'exact_measurement_unavailable');
  }
});

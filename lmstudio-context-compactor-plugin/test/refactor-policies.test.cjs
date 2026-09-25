"use strict";
const test = require('node:test'), assert = require('node:assert/strict');
const {Chat, ChatMessage} = require('@lmstudio/sdk');
const evidence = require('../dist/evidence-manager');
const capability = require('../dist/tool-capability-registry');
const boundary = require('../dist/tool-boundary');
const intent = require('../dist/raw-tool-intent');
const delivery = require('../dist/delivery-controller');
const { ExecutionState, ALLOWED_TRANSITIONS, RoundTransaction } = require('../dist/execution-state');
const {BudgetBroker, BatchReservation, reserveReadResult} = require('../dist/budget-broker');
const {ContextManager, createInputAssembler} = require('../dist/context-manager');
const {readConfig} = require('../dist/execution-config');
test('exact measurement never labels an invalid token counter as exact', async () => {
  const {measureContext}=require('../dist/context-manager');
  const fixture=contextFixture();
  const measured=await measureContext({...fixture.source,async countTokens(){return NaN}},Chat.from([{role:'user',content:'x'}]),fixture.config);
  assert.equal(measured.exact,false);
  assert.equal(measured.promptMeasurementSource,'character_estimate');
});
test('orchestrator delegates policy and stays below structural size target', () => {
  const source=require('node:fs').readFileSync(require('node:path').join(__dirname,'../src/prediction-loop.ts'),'utf8').replace(/\r\n/g,'\n');
  assert.ok(Buffer.byteLength(source)<=112000);
  for(const policy of ['function coverageRange','function subtractCoverageRanges','const UNITY_OBSERVATION_TOOLS',
    'const UNREAL_OBSERVATION_TOOLS','const gitInvestigation','const mutationIntent','function targetRemainingForInput']) assert.ok(!source.includes(policy),policy);
  for(const owner of ['ContextManager','BudgetBroker','EvidenceManager','ExecutionState','ToolCapabilityRegistry','createToolGuard','RecoveryCoordinator','DeliveryController']) assert.ok(source.includes(owner),owner);
});
test('recovery uses typed provider authority, supports non-Git and ends on no progress', () => {
  const {readOnlyRecoveryProfile,RecoveryCoordinator}=require('../dist/recovery-coordinator');
  const tools=[{name:'read_symbol',pluginIdentifier:'mcp/unreal-agent'}];
  const history=Chat.from([{role:'user',content:'심볼을 살펴봐'}]);
  assert.equal(readOnlyRecoveryProfile(history,tools).eligible,true);
  const state=new RecoveryCoordinator();
  const decision=state.advance({progressed:false,errorCount:0},3,true,false,1,1,'');
  assert.equal(decision.trigger,'research_recovery_exhausted');
  assert.equal(state.noProgressRounds,1);
  const controller=new delivery.DeliveryController();
  const result=controller.evaluate(history,{finishReason:'eosFound',messages:[ChatMessage.create('assistant','partial findings')]},false,decision.trigger,1);
  assert.equal(result.delivery.deliveryState,'partial');
  assert.ok(result.partial);
});

test('a denied member of a useful batch does not exhaust read recovery', () => {
  const {RecoveryCoordinator}=require('../dist/recovery-coordinator');
  for (const bounded of [false,true]) {
    const state=new RecoveryCoordinator();
    const decision=state.advance({progressed:true,newSuccessfulObservationCount:2,errorCount:1},3,true,true,3,3,'',bounded);
    assert.equal(decision.trigger,null);
    assert.equal(decision.continue,true);
    assert.equal(state.noProgressRounds,0);
    const stalled=state.advance({progressed:false,newSuccessfulObservationCount:0,errorCount:1},3,true,true,1,1,'',bounded);
    assert.equal(stalled.trigger,'research_recovery_exhausted');
  }
  const bounded=new RecoveryCoordinator();
  assert.equal(bounded.advance({progressed:true,errorCount:1},1,true,true,3,3,'',true).trigger,'research_recovery_complete');
});

test('timeout fallback preserves successful file ranges and body excerpts beside a batch error', () => {
  const history=Chat.from([{role:'user',content:'Report from the evidence.'}]);
  const append=(id,value)=>history.append(ChatMessage.from({role:'tool',content:[{
    type:'toolCallResult',toolCallId:id,content:JSON.stringify([{type:'text',text:JSON.stringify(value)}])
  }]}));
  append('A',{ok:true,kind:'workspace_file_observation',path:'Assets/A.cs',startLine:1,endLine:200,totalLines:482,
    text:'public void StartDay() { BeginPartOne(); }'});
  append('B',{ok:true,kind:'workspace_file_observation',path:'Assets/B.cs',startLine:1,endLine:200,totalLines:370,
    text:'public void CompleteBar() { LeaveBar(); }'});
  append('C',{error:'Shared read-result budget exhausted.'});
  const result=new delivery.DeliveryController().evaluate(history,{messages:[],finishReason:'userStopped'},true,'research_recovery_exhausted',0);
  assert.equal(result.delivery.rejectionReason,'final_timeout');
  assert.equal(result.partial.evidenceCount,2);
  assert.equal(result.partial.errorCount,1);
  assert.match(result.partial.text,/Assets\/A\.cs/);
  assert.match(result.partial.text,/1.*200/);
  assert.match(result.partial.text,/BeginPartOne/);
  assert.match(result.partial.text,/LeaveBar/);
  assert.match(result.partial.text,/final_timeout/);
  assert.doesNotMatch(result.partial.text,/성공 결과를 확인하지 못했습니다/);
  assert.equal(result.taskCompleted,false);
});

test('delivery limits are owned by the selected mode, not the automatic recovery trigger', () => {
  const controller=new delivery.DeliveryController();
  const config={maxOutputReserve:8192,auditFinalMaxTokens:4096,auditFinalSeconds:70,
    outputRecoveryMaxTokens:2048,outputRecoverySeconds:30};
  for(const trigger of ['context_budget','research_recovery_complete','research_recovery_exhausted']) {
    assert.deepEqual(controller.limits(config,false,trigger),{maxTokens:8192,timeoutMs:null});
    assert.deepEqual(controller.limits(config,true,trigger),{maxTokens:4096,timeoutMs:70000});
  }
  for(const bounded of [false,true]) assert.deepEqual(controller.limits(config,bounded,'output_recovery'),
    {maxTokens:2048,timeoutMs:30000});
});

test('fallback keeps byte ranges, excludes failed file bodies, deduplicates and bounds excerpts', () => {
  const history=Chat.empty();
  const values=[{ok:false,kind:'workspace_file_observation',path:'failed.cs',errorCode:'read_failed',text:'NOT_OBSERVED'},
    ...Array.from({length:9},(_,i)=>({ok:true,kind:'workspace_file_observation',path:`file${i}.cpp`,
      range:{unit:'byte',start:64,endExclusive:128},content:`BODY_${i} `+'x'.repeat(5000)}))];
  for(const [i,value] of values.entries())history.append(ChatMessage.from({role:'tool',content:[{
    type:'toolCallResult',toolCallId:String(i),content:JSON.stringify(value)
  }]}));
  const report=delivery.evidenceBackedPartialReport(history,[history.getMessagesArray().at(-1)],'final_timeout');
  assert.equal(report.evidenceCount,8);
  assert.match(report.text,/bytes \[64, 128\)/);
  assert.match(report.text,/BODY_8/);
  assert.doesNotMatch(report.text,/NOT_OBSERVED|BODY_0/);
  assert.ok(report.text.length<5000);
  assert.equal((report.text.match(/file8\.cpp —/g)||[]).length,1);
  const pending=Chat.empty();
  for(const value of [{kind:'workspace_file_observation',text:'UNKNOWN_BODY'},
    {ok:true,pending:true,kind:'workspace_file_observation',text:'PENDING_BODY'}])
    pending.append(ChatMessage.from({role:'tool',content:[{type:'toolCallResult',content:JSON.stringify(value),toolCallId:'unknown'}]}));
  const unknown=delivery.evidenceBackedPartialReport(pending,[],'final_timeout');
  assert.equal(unknown.evidenceCount,0);
  assert.doesNotMatch(unknown.text,/UNKNOWN_BODY|PENDING_BODY/);
});
test('capability SSOT rejects provider suffix spoof and name collision; safe profile is stable', () => {
  const {ToolCapabilityRegistry,resolveCapability}=capability;
  const trusted={name:'read_file',pluginIdentifier:'mcp/unreal-agent'};
  const spoof={name:'read_file',pluginIdentifier:'attacker/unreal-agent'};
  assert.equal(resolveCapability(spoof,{name:'read_file',arguments:{}}).approval,'host');
  assert.equal(new ToolCapabilityRegistry([trusted,spoof]).resolve('read_file'),undefined);
  const registry=new ToolCapabilityRegistry([trusted,{name:'write_file',pluginIdentifier:'mcp/unreal-agent'}]);
  assert.deepEqual(registry.readProfile(),[trusted]);
  assert.equal(resolveCapability(trusted,{name:'write_file',arguments:{}}).recoveryEligible,false);
});
test('Git/File/Symbol/Log/Unity/Unreal share first-consumer, archive and coverage lifecycle', () => {
  const {WorkingContext}=require('../dist/working-context');
  const {EvidenceManager}=require('../dist/evidence-manager');
  for(const source of ['git','file','symbol','log','unity','unreal']) {
    const store=new WorkingContext({conversation:source,lineage:source,workspace:'w',repository:'r'});
    const manager=new EvidenceManager(store,[{name:'read_file',pluginIdentifier:'mcp/unreal-agent'}]);
    const history=Chat.from([{role:'user',content:'inspect'}]);
    history.append(ChatMessage.from({role:'assistant',content:[{type:'toolCallRequest',toolCallRequest:{type:'function',id:'r',name:'read_file',arguments:{}}}]}));
    const payload={ok:true,kind:source+'_observation',path:'a',sha256:'v1',startLine:1,endLine:3,content:'BODY '+ 'z'.repeat(3000)};
    history.append(ChatMessage.from({role:'tool',content:[{type:'toolCallResult',toolCallId:'r',content:JSON.stringify(payload)}]}));
    assert.equal(manager.project(history,{preserveUnconsumedRawMaxChars:8192},256).changed,false);
    manager.captureExposure(history,'incomplete',false);
    assert.equal(store.consumedRawResults.size,0);
    manager.captureExposure(history,'completed',true);
    const projected=manager.project(history,{preserveUnconsumedRawMaxChars:8192},256);
    assert.equal(projected.changed,true);
    const value=JSON.parse(projected.history.getMessagesArray().at(-1).getToolCallResults()[0].content);
    assert.match(value.excerpt,/BODY/); assert.equal(value.fullRawProvided,false);
    assert.equal(manager.observe(history.getMessagesArray()).newSourceUnits,1);
    assert.equal(manager.observe(history.getMessagesArray()).newSourceUnits,0);
    assert.equal(manager.view(value,'verified-provider').representation,'sanitized_body');
  }
});
test('small returned evidence is archived before a canceled round can exit', () => {
  const {WorkingContext}=require('../dist/working-context');
  const {EvidenceManager}=require('../dist/evidence-manager');
  const store=new WorkingContext({conversation:'cancel',lineage:'cancel',workspace:'w',repository:'r'});
  const manager=new EvidenceManager(store,[{name:'read_file',pluginIdentifier:'mcp/unreal-agent'}]);
  const messages=[ChatMessage.from({role:'assistant',content:[{type:'text',text:'unfinished'},
    {type:'toolCallRequest',toolCallRequest:{type:'function',id:'r',name:'read_file',arguments:{}}}]}),
    ChatMessage.from({role:'tool',content:[{type:'toolCallResult',toolCallId:'r',content:'{"ok":true,"content":"FACT"}'}]})];
  assert.equal(manager.captureReturned(messages,'execution'),1);
  assert.equal(store.refs.size,1);
  const [id,version]=[...store.refs][0];
  assert.match(store.archive.read(id,version,0,4096).content,/FACT/);
  const transaction=new RoundTransaction({messages,predictionCompleted:false},false);
  assert.equal(transaction.planningCommitted,false);
  assert.equal(transaction.durable(messages[0]).getText(),'');
});
function contextFixture(overrides={}) {
  const config=readConfig({getPluginConfig(){return {get:key=>({contextManagementMode:'deterministic',
    workingInputTargetTokens:4000,workingInputTriggerTokens:6500,maxOutputReserve:2048,safetyMarginTokens:512,
    ...overrides})[key]}}});
  const source={async getContextLength(){return 38912}, async applyPromptTemplate(chat,{toolDefinitions}){
    return chat.toString()+JSON.stringify(toolDefinitions);
  },async countTokens(text){return Math.ceil(text.length/4)}};
  const assemble=createInputAssembler({tokenSource:source,config,roundTools:[],roundScopeInstructions:['required context instruction'],
    getRoundOutputReserve:()=>config.maxOutputReserve,modelInputId:'ratchet',noteEnabled:false,getNote:()=>null,historicalAvailabilityLedger:[]});
  return {config,source,assemble,manager:new ContextManager(config,new BudgetBroker(config))};
}
test('broker derives watermarks from loaded context and counts post-tool reserve once', () => {
  const broker=new BudgetBroker({workingInputTargetTokens:26000,workingInputTriggerTokens:40000,safetyMarginTokens:2048});
  const water=broker.watermarks({contextLength:38912,outputReserve:8192,inputTokens:18000},1000,true);
  assert.equal(water.hardInputCeiling,28672);
  assert.ok(water.effectiveHighWaterTokens<38912);
  assert.ok(water.configuredLowWaterTokens<water.effectiveHighWaterTokens);
  assert.equal(water.projectedNextInput,18000+water.reservedToolResultTokens+8192);
  const floor=broker.watermarks({contextLength:8192,outputReserve:2048,inputTokens:5000},5000,false);
  assert.equal(floor.effectiveLowWaterTokens,5000); assert.equal(floor.targetUnreachable,true);
});
test('shared batch cannot overbook or recycle settled result budget; release exactly once', () => {
  const batch=new BatchReservation(6000);
  assert.equal(batch.reserve('A',4000),4000);
  assert.equal(batch.reserve('B',4000),0);
  assert.equal(batch.reserve('B',4000,1000),2000);
  assert.equal(batch.settle('A','returned'),true);
  assert.equal(batch.reserve('C',1),0);
  assert.equal(batch.settle('B','canceled'),true);
  assert.equal(batch.settle('B','canceled'),false);
  assert.equal(batch.reserve('D',2000),2000);
  batch.close(); batch.close(); assert.equal(batch.reserve('E',1),0);
});
test('read envelope reservation reduces a published bound before dispatch', () => {
  const batch=new BatchReservation(5000);
  const tool={parametersJsonSchema:{properties:{byteBudget:{minimum:1024,maximum:65536}}}};
  const a=reserveReadResult(batch,'A',tool,{byteBudget:24000});
  assert.equal(a.allowed,true); assert.equal(a.arguments.byteBudget,3976);
  assert.equal(reserveReadResult(batch,'B',tool,{byteBudget:1024}).allowed,false);
});
test('three equivalent growth/compaction cycles verify final reassembled LOW without drift', async () => {
  const f=contextFixture(); let history=Chat.from([{role:'system',content:'invariant'},{role:'user',content:'mandatory current objective'}]);
  const baselines=[];
  for(let cycle=0;cycle<3;cycle++) {
    for(let i=0;i<12;i++) { history.append('user','Continue the same objective'); history.append('assistant','old evidence '.repeat(2500)); }
    const result=await f.manager.enforceLowWater(history,f.assemble,{hasReadTools:false});
    assert.equal(result.success,true);
    assert.ok(result.telemetry.finalExactInputTokens<=result.telemetry.effectiveLowWaterTokens);
    assert.ok(result.telemetry.beforeExactInputTokens>result.telemetry.effectiveHighWaterTokens);
    assert.match(result.history.toString(),/invariant/);
    baselines.push(result.assembled.measurement.inputTokens); history=result.history;
  }
  assert.ok(Math.max(...baselines)-Math.min(...baselines)<128,JSON.stringify(baselines));
});
test('unreachable LOW preserves mandatory request and reports its measured floor', async () => {
  const f=contextFixture({workingInputTargetTokens:2048,workingInputTriggerTokens:2048});
  const history=Chat.from([{role:'user',content:'mandatory '+ 'x'.repeat(16000)}]);
  const result=await f.manager.enforceLowWater(history,f.assemble,{hasReadTools:false});
  assert.equal(result.telemetry.targetUnreachable,true);
  assert.equal(result.success,true); assert.match(result.history.toString(),/x{16000}/);
});
test('canonical phase rejects every illegal transition and records transaction effects', () => {
  for (const from of Object.keys(ALLOWED_TRANSITIONS)) for (const to of Object.keys(ALLOWED_TRANSITIONS)) {
    const state = new ExecutionState();
    if (from !== 'RESEARCH') state.transition(from, 'fixture');
    if (ALLOWED_TRANSITIONS[from].includes(to)) {
      state.transition(to,'test');
      assert.equal(state.phase,to);
      assert.equal(state.transitions.at(-1).evidenceEffect,'retain_returned');
    } else assert.throws(()=>state.transition(to,'test'),/ILLEGAL_EXECUTION_TRANSITION/);
  }
});
test('canceled/truncated planning rolls back while returned provider evidence survives', () => {
  const request=ChatMessage.from({role:'assistant',content:[{type:'text',text:'unfinished planning'},
    {type:'toolCallRequest',toolCallRequest:{type:'function',id:'a',name:'read_file',arguments:{}}}]});
  const result=ChatMessage.from({role:'tool',content:[{type:'toolCallResult',toolCallId:'a',content:'returned evidence'}]});
  for(const mode of ['cancel','limit','failure']) {
    const tx=new RoundTransaction({messages:[request,result,ChatMessage.create('assistant','incomplete')],
      finishReason:mode==='limit'?'maxPredictedTokensReached':'eosFound', failure:mode==='failure'?new Error('failed'):undefined},mode==='cancel');
    const history=Chat.empty(); tx.commitEvidence(history);
    assert.equal(history.length,2);
    assert.equal(history.getMessagesArray()[0].getText(),'');
    assert.equal(history.getMessagesArray()[1].getToolCallResults()[0].content,'returned evidence');
  }
});
test('extracted evidence algebra preserves units, empty EOF and overlap', () => {
  assert.deepEqual(evidence.subtractCoverageRanges([0, 9], [[2, 4], [7, 8]]), [[0,1],[5,6],[9,9]]);
  assert.equal(evidence.coverageRange({returnedRange:[2,2],archiveReachedEnd:true},'archive'),null);
  assert.equal(evidence.coverageRange({returnedRange:['0',9]},'source'),null);
});
test('extracted boundary does not invent a provider start from guard allow', () => {
  const value = boundary.classifyToolCallBoundary({rawToolIntent:false, structuredRequestCount:1,
    sdkStartedCount:1,sdkFinalizedCount:1,sdkFailureCount:0,guardAllowedCount:1,guardDeniedCount:0,dispatchCount:null,resultCount:0});
  assert.equal(value.state,'guard_allowed_provider_execution_unknown');
  assert.equal(value.dispatchCount,null);
});
test('extracted capabilities and raw intent remain independently testable', () => {
  assert.equal(capability.isObservationOnlyToolCall({name:'read_file',pluginIdentifier:'mcp/untrusted'}, {name:'read_file',arguments:{}}),false);
  assert.equal(intent.containsUnresolvedToolIntent('<tool_call>{"name":"read_file"}</tool_call>',[]),true);
  assert.equal(intent.containsUnresolvedToolIntent('`<tool_call>example</tool_call>`',[]),false);
  assert.equal(delivery.classifyFinalDelivery('',{},false,[]).deliveryState,'no_answer');
});

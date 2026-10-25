'use strict';
const test = require('node:test'), assert = require('node:assert/strict'), path = require('node:path');
const build = process.env.HARNESS_CONTRACT_TEST_BUILD || path.join(__dirname, '../dist');
const { Chat, ChatMessage } = require('@lmstudio/sdk');
const { VisibleHistoryRecorder } = require(path.join(build, 'prediction-ui'));
const { WorkingContext, hash, serialize } = require(path.join(build, 'working-context'));
const { sealAndRelease } = require(path.join(build, 'terminal-persistence'));
const { EvidenceManager, seedRecoveryProgress, observeRecoveryProgress } = require(path.join(build, 'evidence-manager'));
const { ReviewRegister, ReviewEvidenceIndex } = require(path.join(build, 'review-register'));
const { unresolvedReportEngineClaimGaps } = require(path.join(build, 'engine-claim-anchoring'));
const { ReadLedger, readFacts } = require(path.join(build, 'read-ledger'));
const { localObservationTools } = require(path.join(build, 'tool-capability-registry'));
const scope = id => ({ conversation: id, lineage: id, workspace: 'synthetic', repository: 'synthetic' });
const block = () => ({ appendText() {}, appendToolRequest() {}, appendToolResult() {}, replaceToolRequest() {} });
const measurement = { exact: true, remainingTokens: 5000 };

test('supplements append immutable host blocks and survive the next user turn', () => {
  const initial = Chat.from([{role:'user',content:'Review source'}]);
  const visible = new VisibleHistoryRecorder({createContentBlock:block},initial);
  visible.controller.createContentBlock({roleOverride:'assistant'}).appendText('Report');
  const store = new WorkingContext(scope('supplement'));
  const committed = visible.snapshot();
  assert.equal(store.commit(committed,initial,measurement,hash(serialize(committed)),'model'),true);
  const empty = visible.continuationController().createContentBlock({roleOverride:'assistant'});
  assert.equal(visible.snapshot().getMessagesArray().length,2,'empty supplement adds no message');
  empty.appendText('\nSupplement');
  assert.equal(visible.lastAnswerText,'Report\nSupplement');
  assert.equal(visible.snapshot().getMessagesArray()[1].getText(),'Report','committed prefix stays immutable');
  const final = visible.freeze();
  empty.appendText('late');
  assert.equal(visible.snapshot().toString(),final.toString());
  const terminal = sealAndRelease(store,final,initial,[]);
  assert.equal(terminal.sealed,true);
  final.append('user','Fix the findings');
  assert.equal(store.restore(final).reason,'restored_remeasure_required');
});

test('terminal persistence releases once and reports a boolean release failure separately', () => {
  const initial=Chat.from([{role:'user',content:'Review'}]), store=new WorkingContext(scope('release'));
  assert.equal(store.commit(initial,initial,measurement,hash(serialize(initial)),'model'),true);
  let closes=0; const close=store.archive.close.bind(store.archive);
  store.archive.close=()=>{closes++;close();};
  assert.equal(sealAndRelease(store,initial,initial,[]).released,true);
  store.close(); assert.equal(closes,1);
  const failed=sealAndRelease({seal:()=>true,close:()=>false,lastRetentionReason:'lease_io_failed'},initial,initial,[]);
  assert.equal(failed.sealed,true);assert.equal(failed.released,false);
  assert.equal(failed.release.reason,'lease_io_failed');
  const primary=new Error('seal failed'), failures=[];
  assert.throws(()=>sealAndRelease({seal(){throw primary;},close(){throw new Error('close failed');}},initial,initial,failures),e=>e===primary);
  assert.equal(failures.length,1);
  const booleanFailures=[];
  assert.throws(()=>sealAndRelease({seal(){throw primary;},close:()=>false,lastRetentionReason:'lease_io_failed'},initial,initial,booleanFailures),e=>e===primary);
  assert.deepEqual(booleanFailures,['lease_io_failed']);
});

const sha='a'.repeat(64), file='project://Source/Sample.cpp';
const body='void Sample::Foo() {\n  return;\n}\nvoid Sample::Bar() {\n  return;\n}';
const source={ok:true,kind:'workspace_file_observation',canonicalProject:'C:/Game/Game.uproject',path:file,sha256:sha,startLine:1,endLine:6,totalLines:6,content:body};
const sourceTool={name:'read_file',pluginIdentifier:'mcp/unreal-agent'}, rangeTool={...sourceTool,name:'read_file_range'};
function pair(name,args,value,id){return [ChatMessage.from({role:'assistant',content:[{type:'toolCallRequest',toolCallRequest:{type:'function',id,name,arguments:args}}]}),
  ChatMessage.from({role:'tool',content:[{type:'toolCallResult',toolCallId:id,content:JSON.stringify(value)}]})];}
function chat(messages){const c=Chat.empty();for(const m of messages)c.append(m);return c;}

test('same source span has no novelty when the read transport changes',()=>{
  const seed=seedRecoveryProgress(chat(pair('read_file',{path:file},source,'first')),[sourceTool,rangeTool]);
  const progress=observeRecoveryProgress(pair('read_file_range',{path:file,startLine:1,endLine:6},source,'next'),seed.fingerprints,seed.coverage,new Map(seed.coverage),[sourceTool,rangeTool]);
  assert.equal(progress.newSourceUnits,0);
});
test('archive pages already visible in the source input are not restoration progress',()=>{
  const store=new WorkingContext(scope('archive-duplicate')), archiveTool=store.tool();localObservationTools.add(archiveTool);
  const manager=new EvidenceManager(store,[sourceTool,archiveTool]);
  const first=pair('read_file',{path:file},source,'first');manager.screenReturnedReads(first,0);
  const record=store.archive.put(JSON.stringify(source),{callKey:'record',providerRequestId:'record',toolName:'read_file',provider:sourceTool.pluginIdentifier,sourceIdentity:{path:file},sourceVersion:sha}).record;
  const readHandle=store.archive.readHandleFor(record), page=store.archive.readByHandle(readHandle,0,8192,'body');
  manager.captureExposure(chat(first),'input');manager.seed(chat(first));
  const screened=manager.screenReturnedReads(pair('evidence_first_read_context',{action:'read',readHandle,view:'body'},page,'page'),1);
  const progress=manager.observe(screened.messages,'next');
  assert.equal(progress.restoredWorkingEvidenceUnits,0);assert.equal(screened.replaced,1);
  store.close();
});
test('judgments do not close an entire file or become supported by a different version',()=>{
  const register=new ReviewRegister(), index=new ReviewEvidenceIndex();
  index.observe(pair('read_file',{path:file},source,'A'));
  register.observe([ChatMessage.create('assistant','`Sample::Foo` P1 bug: incorrect state.')],1);register.attach(index);
  assert.equal(register.closedFileFacts(file.replace('project://','').toLowerCase(),sha),null);
  index.observe(pair('read_file',{path:file},{...source,sha256:'b'.repeat(64),content:body.replace('return;','/* fixed */ return;')},'B'));
  register.attach(index);
  assert.equal(register.rows[0].bundle.sha,sha);assert.equal(register.rows[0].status,'needs_recheck');
});
test('partial visible ranges cannot certify a whole duplicate receipt body',()=>{
  const manager=new EvidenceManager(new WorkingContext(scope('receipt')),[sourceTool,rangeTool]);
  manager.screenReturnedReads(pair('read_file',{path:file},source,'first'),0);
  manager.captureExposure(chat(pair('read_file_range',{path:file,startLine:1,endLine:2},{...source,endLine:2,content:body.split('\n').slice(0,2).join('\n')},'part')),'input');
  const screened=manager.screenReturnedReads(pair('read_file',{path:file,repeatReceipt:'unknown'},{ok:true,duplicate:true,status:'no_new_information'},'duplicate'),1);
  const value=JSON.parse(screened.messages[1].getToolCallResults()[0].content);
  assert.notEqual(value.bodyInCurrentInput,true);
});
test('nested priority headings retain engine evidence obligations',()=>{
  const answer='## P2 — production\n\n### 3. DrawDebugLine Shipping behavior\nDrawDebugLine is compiled out in Shipping builds.';
  assert.ok(unresolvedReportEngineClaimGaps(answer,{files:[],contractIds:new Set()},'unreal').length);
});
test('same relative path and hash in another project is a different ledger source',()=>{
  const ledger=new ReadLedger();ledger.record(readFacts({...source,canonicalProject:'C:/ProjectA/Game.uproject'}));
  assert.equal(ledger.classify(readFacts({...source,canonicalProject:'C:/ProjectB/Game.uproject'})).status,'new_file');
});

test('a bodyless duplicate receipt cannot authorize another body omission',()=>{
  const { repeatReceiptCandidates }=require(path.join(build,'evidence-manager'));
  assert.equal(repeatReceiptCandidates(chat(pair('read_file',{path:file},{...source,content:undefined,duplicate:true,bodyOmitted:true,repeatReceipt:'rr_test'},'d'))).size,0);
});

test('reported coverage larger than the returned body never suppresses a later complete body',()=>{
  const manager=new EvidenceManager(new WorkingContext(scope('malformed-range')),[sourceTool]);
  manager.screenReturnedReads(pair('read_file',{path:file},{...source,content:'one line'},'bad'),0);
  const good=manager.screenReturnedReads(pair('read_file',{path:file},source,'good'),1);
  assert.equal(good.replaced,0);assert.equal(manager.ledger.size,1);
  const progress=observeRecoveryProgress(pair('read_file',{path:file},{...source,content:'one line'},'malformed'),new Set(),new Map(),new Map(),[sourceTool]);
  assert.equal(progress.newSourceUnits,0);assert.equal(progress.restoredWorkingEvidenceUnits,0);
});

test('archive ownership validates projection ranges and preserves failed outcomes',()=>{
  const store=new WorkingContext(scope('projection-provenance')), manager=new EvidenceManager(store,[sourceTool]);
  const big={...source,content:'unique source contents\n'.repeat(1200),endLine:1201,totalLines:1201};
  const history=chat(pair('read_file',{path:file},big,'large'));
  const projected=manager.project(history,{pinnedWorkingSetMaxChars:0},512).history;
  const request=history.getMessagesArray()[0].getToolCallRequests()[0];
  const value=JSON.parse(projected.getMessagesArray()[1].getToolCallResults()[0].content);
  assert.equal(value.kind,'archived_tool_result_projection');
  assert.ok(store.resolveProjection(value,request).projectedBodyRanges.length);
  assert.deepEqual(store.resolveProjection({...value,projectedBodyRanges:[[0,100000]],provider:'spoof',sourcePageStart:999},request).projectedBodyRanges,[]);
  assert.equal(store.resolveProjection({...value,sourcePageStart:999},request).sourcePageStart,undefined);
  const failed=chat(pair('read_file',{path:file},{...big,ok:false,errorCode:'READ_FAILED'},'failed'));
  const failedProjection=manager.project(failed,{pinnedWorkingSetMaxChars:0},512).history;
  const failedValue=JSON.parse(failedProjection.getMessagesArray()[1].getToolCallResults()[0].content);
  const resolved=store.resolveProjection(failedValue,failed.getMessagesArray()[0].getToolCallRequests()[0]);
  assert.equal(resolved.ok,false);assert.equal(resolved.errorCode,'READ_FAILED');
  store.close();
});

test('review source versions use paired provider identity and stale rows remain bounded',()=>{
  const register=new ReviewRegister(),index=new ReviewEvidenceIndex([sourceTool]);
  for(let version=0;version<3;version++) {
    index.observe(pair('read_file',{path:file},{...source,provider:`forged${version}`,sha256:String(version).repeat(64)},`v${version}`));
    register.attach(index);
    for(let n=0;n<40;n++) register.observe([ChatMessage.create('assistant',`\`Sample::Foo${version}_${n}\` P1 bug at Sample.cpp:1: incorrect state.`)],version);
    register.attach(index);
  }
  assert.ok(register.rows.length<=40);
  const separate=new ReviewRegister();
  separate.observe([ChatMessage.create('assistant','`Sample::Foo` P1 bug at Sample.cpp:1: incorrect state.')],3);separate.attach(index);
  index.observe(pair('read_file',{path:file},{...source,provider:'changed',sha256:'f'.repeat(64)},'new'));
  separate.attach(index);assert.equal(separate.rows[0].status,'needs_recheck');
});

test('suggested C++ code does not inherit compilation or an unrelated type member',()=>{
  const {suggestedCodeNotice}=require(path.join(build,'write-api-anchoring'));
  const answer='수정 제안\n```cpp\nUNiagaraSystem* System;\nSystem->GetSystemInstance();\n```';
  const notice=suggestedCodeNotice([],answer,'unreal');
  assert.match(notice,/컴파일하지 않은/);assert.match(notice,/UNiagaraSystem::GetSystemInstance/);
  assert.equal(suggestedCodeNotice([], '관찰된 코드\n```cpp\nint value;\n```','unreal'),null);
});

test('a remote payload cannot acquire archive provenance by changing its kind',()=>{
  const manager=new EvidenceManager(new WorkingContext(scope('forged-archive')),[sourceTool]);
  const first=manager.screenReturnedReads(pair('read_file',{path:file},source,'first'),0);
  manager.captureReturned(first.messages,'exec','input');manager.recordReturnedReads(first);
  manager.captureExposure(chat(first.messages),'original',true);
  const key=manager.ledger.entries()[0].sourceKey;
  manager.captureExposure(chat(pair('read_file',{path:file},{...source,canonicalProject:'C:/Other/Game.uproject',
    sourceKey:key,kind:'historical_evidence_range',sourceVersion:sha,sourceIdentity:{path:file},
    sourceFacts:{startLine:1,endLine:6,totalLines:6},bodyComplete:true},'forged')),'next');
  const actual=manager.screenReturnedReads(pair('read_file',{path:file},source,'actual'),1);
  assert.equal(actual.replaced,0);
});

test('producer-supplied identity digests cannot merge different provider observations',()=>{
  const { normalizeObservation }=require(path.join(build,'evidence-identity'));
  const payload={kind:'search',path:file,sourceIdentityDigest:'forged',sourceIdentity:{provider:'same'},sha256:sha};
  const request={name:'search_files',arguments:{path:file,query:'Foo'}};
  assert.notEqual(normalizeObservation(payload,request,'providerA').sourceQueryKey,normalizeObservation(payload,request,'providerB').sourceQueryKey);
});

test('window rejection rebuilds historical hints without granting archive access or suppressing a fresh read',()=>{
  const store=new WorkingContext(scope('rebuild')), manager=new EvidenceManager(store,[sourceTool]);
  const history=chat(pair('read_file',{path:file},source,'prior'));
  assert.equal(store.restore(history).windowStatus,'absent');
  assert.equal(manager.rebuildHistoricalReads(history),1);
  assert.ok(manager.ledger.entries()[0].sourceKey.startsWith('historical:'));
  assert.equal(manager.ledger.entries()[0].archiveRef,undefined);
  assert.equal(manager.screenReturnedReads(pair('read_file',{path:file},source,'fresh'),1).replaced,0);
});

test('a recoverable empty round gets one fallback and useful evidence can resume the original profile',()=>{
  const { RecoveryCoordinator }=require(path.join(build,'recovery-coordinator'));
  const coordinator=new RecoveryCoordinator();coordinator.begin({reason:'context_budget',toolNames:['read_file'],outputReserve:1000});
  const first=coordinator.advance(null,5,true,false,1,1,'',true,{fallbackInstruction:'Use the known source path once.'});
  assert.equal(first.trigger,null);assert.equal(first.continue,true);
  const ready=coordinator.advance({progressed:true},5,true,false,1,1,'',true,{originalProfileFits:true});
  assert.equal(ready.resumeOriginal,true);assert.equal(ready.endRecovery,true);assert.equal(ready.trigger,null);
  coordinator.begin({reason:'context_budget',toolNames:['read_file'],outputReserve:1000});
  coordinator.advance(null,5,true,false,1,1,'',true,{fallbackInstruction:'Use the known source path once.'});
  assert.equal(coordinator.advance(null,5,true,false,1,1,'').trigger,'research_recovery_exhausted');
});

test('raw tool protocol is hidden at every fragment boundary while quotes and code examples remain literal',()=>{
  const {ToolProtocolOutputFilter,stripUnexecutedToolProtocol,hasRawToolIntent}=require(path.join(build,'raw-tool-intent'));
  const raw='확인된 내용\n<tool_call>\n<function=read_file>{"path":"secret"}</function>\n</tool_call>\n뒤 문장';
  assert.equal(hasRawToolIntent(raw,[]),true);
  for(let split=0;split<=raw.length;split++) {
    const filter=new ToolProtocolOutputFilter();
    const visible=filter.push(raw.slice(0,split))+filter.push(raw.slice(split))+filter.finish();
    assert.doesNotMatch(visible,/tool_call|function=|secret/);assert.match(visible,/확인된 내용/);assert.match(visible,/뒤 문장/);
  }
  for(const literal of ['```xml\n<tool_call>x</tool_call>\n```','`<tool_call>x</tool_call>`','Example: <tool_call>x</tool_call>'])
    assert.equal(stripUnexecutedToolProtocol(literal),literal);
});

test('real provider byte/range observations share whole-file coverage and duplicate capabilities stay separate',async t=>{
  const fs=require('node:fs'),os=require('node:os');
  const {createDirectRuntime}=require('../../lmstudio-unreal-agent-mcp/src/direct-server');
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'provider-harness-contract-'));
  const project=path.join(root,'Game.uproject');fs.writeFileSync(project,JSON.stringify({FileVersion:3,Modules:[{Name:'Game',Type:'Runtime'}]}));
  fs.writeFileSync(path.join(root,'A.cpp'),'// 한글 😀 "quote"\nint Value = 1;\n');
  const runtime=createDirectRuntime({workspaceRoot:root,configPath:path.join(root,'config.json'),stateRoot:path.join(root,'state'),getActiveProject:()=>project,
    env:{ALLOW_WRITE:'0',ALLOW_COMMANDS:'0',ALLOW_UNREAL_BUILD:'0'}});
  t.after(()=>{runtime.close();assert.equal(path.dirname(root),os.tmpdir());fs.rmSync(root,{recursive:true,force:true});});
  const args={path:'project://A.cpp',maxBytes:4096},owner={sessionId:'consumer'};
  const whole=(await runtime.callTool('read_file',args,owner)).structuredContent;
  const store=new WorkingContext(scope('provider-bridge')),manager=new EvidenceManager(store,[sourceTool,rangeTool]);
  const first=manager.screenReturnedReads(pair('read_file',args,whole,'first'),0);
  manager.captureReturned(first.messages,'exec','first');manager.recordReturnedReads(first);
  manager.captureExposure(chat(first.messages),'input',true);manager.seed(chat(first.messages));
  const rangeArgs={path:args.path,startLine:1,endLine:2};
  const range=(await runtime.callTool('read_file_range',rangeArgs,owner)).structuredContent;
  const screened=manager.screenReturnedReads(pair('read_file_range',rangeArgs,range,'range'),1);
  assert.equal(manager.observe(screened.messages,'range').newSourceUnits,0,'full UTF-8 body already covered the same source lines');
  const duplicate=(await runtime.callTool('read_file',{...args,repeatReceipt:whole.repeatReceipt},owner)).structuredContent;
  assert.equal(duplicate.duplicate,true);assert.equal(duplicate.fileVersionReceipt,undefined);
  assert.equal(duplicate.sha256,whole.sha256);assert.equal(duplicate.canonicalProject,whole.canonicalProject);
  assert.equal(manager.screenReturnedReads(pair('read_file',{...args,repeatReceipt:whole.repeatReceipt},duplicate,'repeat'),2).redundant,1);
  store.close();
});

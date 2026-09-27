"use strict";
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {Chat} = require('@lmstudio/sdk');
const {createToolGuard, hostToolAutoApproval, readHostToolApprovalPolicy} = require('../dist/tool-boundary');
const {createMessageEmitter, createToolGenerationTracker, VisibleHistoryRecorder} = require('../dist/prediction-ui');

function fixture(name = 'unreal_set_active_project', provider = 'mcp/unreal-rag', policy = {}) {
  const events = [], cards = [], requests = [], abort = new AbortController();
  const tool = {name, pluginIdentifier: provider, parametersJsonSchema: {type:'object', properties:{project:{type:'string'}}}};
  const request = {id:'provider-request', type:'function', name, arguments:{project:'wrong', path:'Source/A.cpp'}};
  const ctl = {abortSignal:abort.signal, guardAbort(){abort.signal.throwIfAborted()},
    createStatus(){return {remove(){},setText(){},setState(){}}},
    createContentBlock(){return {appendToolRequest(value){requests.push(value);events.push('request')},
      replaceToolRequest(value){requests[requests.findIndex(x=>x.callId===value.callId)]=value},appendToolResult(){}}},
    createToolStatus(callId,state){const card={callId,state};cards.push(card);events.push(state.type);
      return {setStatus(value){card.state=value;events.push(value.type)}}},
    async requestConfirmToolCall(value){events.push('confirmation');
      // LM Studio renders the standalone request only through a matching native card.
      assert.equal(cards.find(x=>x.callId===value.callId)?.state.type,'confirmingToolCall');
      assert.deepEqual(requests.find(x=>x.callId===value.callId)?.parameters,value.parameters);
      return {type:'allow'};},
  };
  const recorder = new VisibleHistoryRecorder(ctl, Chat.empty());
  const emitter=createMessageEmitter(recorder.controller,[tool]), tracker=createToolGenerationTracker(ctl);
  const trace=[];
  const guard=createToolGuard({ctl,emitter,roundTools:[tool],config:{observeOnly:false},scope:{projectIdentity:'C:/Game'},
    toolPlanningRetryRound:false,toolPlanningRetryBlockedFingerprints:new Set(),batchReservation:null,
    reservationIds:new Map(),toolGeneration:tracker,traceCall:(id,value)=>trace.push(value),
    readApprovalPolicy:typeof policy==='function'?policy:()=>policy});
  let outcome;
  const invoke=()=>guard(0,42,{toolCallRequest:request,
    allow(){outcome={type:'allow',args:request.arguments}},
    allowAndOverrideParameters(args){outcome={type:'allow',args}},deny(reason){outcome={type:'deny',reason}}});
  return {ctl,request,emitter,tracker,trace,events,cards,requests,invoke,abort,recorder,get outcome(){return outcome}};
}

test('Allow all and exact tool preferences use provider identity, not tool name alone',()=>{
  const policy={skipToolConfirmationPatterns:['mcp/unreal-rag:*','mcp/unity-tools:patch_file']};
  assert.equal(hostToolAutoApproval(policy,'mcp/unreal-rag','unreal_set_active_project'),true);
  assert.equal(hostToolAutoApproval(policy,'mcp/unity-tools','patch_file'),true);
  assert.equal(hostToolAutoApproval(policy,'mcp/unity-tools','create_file'),false);
  assert.equal(hostToolAutoApproval(policy,'attacker/unreal-rag','unreal_set_active_project'),false);
  assert.equal(hostToolAutoApproval({neverAskForToolConfirmation:true},'mcp/unity-tools','patch_file'),true);
  assert.equal(hostToolAutoApproval({neverAskForToolConfirmation:true},undefined,'patch_file'),false);
});

test('saved policy is read afresh and unreadable settings require confirmation',t=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'tool-approval-'));
  const prior=process.env.LMSTUDIO_HOME;process.env.LMSTUDIO_HOME=dir;
  t.after(()=>{if(prior===undefined)delete process.env.LMSTUDIO_HOME;else process.env.LMSTUDIO_HOME=prior;fs.rmSync(dir,{recursive:true,force:true})});
  assert.deepEqual(readHostToolApprovalPolicy(),{});
  const file=path.join(dir,'settings.json');
  fs.writeFileSync(file,JSON.stringify({chat:{skipToolConfirmationPatterns:['mcp/unreal-rag:*']}}));
  assert.equal(hostToolAutoApproval(readHostToolApprovalPolicy(),'mcp/unreal-rag','unreal_set_active_project'),true);
  fs.writeFileSync(file,'{}');
  assert.equal(hostToolAutoApproval(readHostToolApprovalPolicy(),'mcp/unreal-rag','unreal_set_active_project'),false);
  fs.writeFileSync(file,'{');assert.deepEqual(readHostToolApprovalPolicy(),{});
});

test('Unreal project selection and Unity patch follow saved Allow all without a pending RPC',async()=>{
  for(const [name,provider] of [['unreal_set_active_project','mcp/unreal-rag'],['patch_file','mcp/unity-tools']]){
    const f=fixture(name,provider,{skipToolConfirmationPatterns:[provider+':*']});
    f.ctl.requestConfirmToolCall=()=>assert.fail('Allow all must not enter the explicit confirmation RPC');
    await f.invoke();
    assert.equal(f.outcome.type,'allow');assert.equal(f.outcome.args.project,'C:/Game');
    assert.equal(f.trace.at(-1).approvalState,'allowed_host_policy');
    assert.equal(f.cards[0].state.type,'callingTool');
    f.emitter.emitRequest(42,f.request);assert.equal(f.requests.length,1);
    f.tracker.completed('provider-request','{"ok":true}');assert.equal(f.cards[0].state.type,'toolCallSucceeded');
    f.tracker.finishPending();assert.equal(f.cards[0].state.type,'toolCallSucceeded');
  }
});

test('manual approval has a visible request and native card before waiting; edited args remain bound',async()=>{
  const f=fixture();const original=f.ctl.requestConfirmToolCall;
  f.ctl.requestConfirmToolCall=async args=>{await original(args);return {type:'allow',toolArgsOverride:{project:'other',path:'Source/B.cpp'}}};
  await f.invoke();
  assert.deepEqual(f.events.slice(0,3),['request','confirmingToolCall','confirmation']);
  assert.deepEqual(f.outcome.args,{project:'C:/Game',path:'Source/B.cpp'});
  assert.deepEqual(f.requests[0].parameters,f.outcome.args);
  assert.deepEqual(f.recorder.snapshot().getMessagesArray()[0].getToolCallRequests()[0].arguments,f.outcome.args);
});

test('denial and interrupted confirmation never permit execution or leave a loading card',async()=>{
  const denied=fixture();denied.ctl.requestConfirmToolCall=async()=>({type:'deny',denyReason:'no'});
  await denied.invoke();assert.equal(denied.outcome.type,'deny');
  denied.tracker.finishPending();assert.equal(denied.cards[0].state.type,'toolCallDenied');
  const canceled=fixture();
  canceled.ctl.requestConfirmToolCall=async()=>{canceled.abort.abort(new Error('stopped'));return {type:'allow'}};
  await assert.rejects(canceled.invoke(),/stopped/);canceled.tracker.finishPending();
  assert.equal(canceled.outcome,undefined);assert.equal(canceled.cards[0].state.type,'toolCallDenied');
});

test('failed result and missing result are not displayed as successful tool execution',async()=>{
  for(const result of ['{"errorCode":"edit_disabled"}',null]){
    const f=fixture();await f.invoke();
    if(result)f.tracker.completed('provider-request',result);
    f.tracker.finishPending();assert.equal(f.cards[0].state.type,'toolCallFailed');
  }
});

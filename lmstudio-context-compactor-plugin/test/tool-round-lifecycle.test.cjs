"use strict";
const test = require('node:test'), assert = require('node:assert/strict');
const { Chat, ChatMessage } = require('@lmstudio/sdk');
const { runOneToolRound, isCompletedPredictionReason } = require('../dist/round-loop');
const { RoundTransaction } = require('../dist/execution-state');
const { WorkingContext } = require('../dist/working-context');
const { DeliveryController } = require('../dist/delivery-controller');

function exchange(id) {
  return [ChatMessage.from({role:'assistant',content:[{type:'text',text:'Read next source.'},
    {type:'toolCallRequest',toolCallRequest:{type:'function',id,name:'read_file',arguments:{path:id}}}]}),
  ChatMessage.from({role:'tool',content:[{type:'toolCallResult',toolCallId:id,
    content:JSON.stringify({ok:true,path:id,text:'fact '+id})}]})];
}
test('SDK toolCalls completes the input consumer, while its newly returned result stays pending', async () => {
  const store = new WorkingContext({conversation:'sdk-stop',lineage:'sdk-stop',workspace:'w',repository:'r'});
  const input = Chat.from([{role:'user',content:'Read two sources.'}]);
  for (const m of exchange('first')) input.append(m);
  store.captureReturned(input,()=>true);
  store.captureExposure(input,'consumer',false);
  const source = {async act(_history,_tools,opts) {
    opts.onPredictionCompleted({stats:{stopReason:'toolCalls'}});
    for (const m of exchange('second')) opts.onMessage(m);
    opts.onRoundEnd();
    throw opts.signal.reason;
  }};
  const captured = await runOneToolRound(source,input,[],new AbortController().signal,
    {guardToolCall:async()=>{},onToolCallRequestFinalized:()=>{}});
  assert.equal(captured.predictionCompleted,true);
  assert.equal(captured.continueAfterTools,true);
  const output=Chat.empty();for(const m of captured.messages) output.append(m);
  store.captureReturned(output,()=>true);
  store.captureExposure(input,'consumer',captured.predictionCompleted);
  assert.equal(store.returnedRefs.size,1,'only newly returned result awaits its first consumer');
  assert.doesNotThrow(()=>store.captureExposure(output,'next',false));
  assert.equal(new RoundTransaction(captured,false).planningCommitted,true);
  assert.equal(new RoundTransaction(captured,true).planningCommitted,false);
  const report=new DeliveryController().evaluate(input,captured,false,null,0);
  assert.notEqual(report.delivery.deliveryState,'complete','toolCalls is not final answer completion');
  assert.equal(report.generationCompleted,false);
  assert.equal(report.taskCompleted,false);
});
test('truncation, cancellation and unknown reasons never consume pending evidence',()=>{
  for(const reason of ['maxPredictedTokensReached','contextLengthReached','userStopped','failed','modelUnloaded','unknown',undefined])
    assert.equal(isCompletedPredictionReason(reason),false,String(reason));
  for(const reason of ['eosFound','stopStringFound','toolCalls']) assert.equal(isCompletedPredictionReason(reason),true);
});
test('duplicate capture after consumption cannot re-pin a previously consumed result',()=>{
  const store=new WorkingContext({conversation:'duplicate-capture',lineage:'duplicate-capture',workspace:'w',repository:'r'});
  const h=Chat.empty();for(const m of exchange('same'))h.append(m);
  store.captureReturned(h,()=>true);store.captureExposure(h,'first',true);
  store.captureReturned(h,()=>true);
  assert.equal(store.returnedRefs.size,0);
  assert.doesNotThrow(()=>store.captureExposure(Chat.empty(),'compacted',false));
});

test('every settled round fences late SDK callbacks and cancels round-owned work', async () => {
  for (const exit of ['eos', 'reject', 'handoff', 'cancel']) {
    let saved, observed = 0;
    const parent = new AbortController(), failure = new Error('transport');
    const source = { async act(_h, _t, opts) {
      saved = opts;
      if (exit === 'reject') throw failure;
      if (exit === 'cancel') { parent.abort(failure); throw failure; }
      opts.onPredictionCompleted({stats:{stopReason:exit === 'handoff' ? 'toolCalls' : 'eosFound'}});
      if (exit === 'handoff') { for (const m of exchange('initial')) opts.onMessage(m); opts.onRoundEnd(); throw opts.signal.reason; }
    }};
    const captured = await runOneToolRound(source,Chat.empty(),[],parent.signal,{
      guardToolCall:async()=>{ observed++; }, onToolCallRequestFinalized:()=>{ observed++; },
      onMessageCaptured:()=>{ observed++; }, onFirstToken:()=>{ observed++; },
      onPromptProcessingProgress:()=>{ observed++; }, onPredictionFragment:()=>{ observed++; },
    });
    const before = JSON.stringify(captured), count = observed;
    for (const m of exchange('late')) saved.onMessage(m);
    saved.onPredictionCompleted({stats:{stopReason:'maxPredictedTokensReached'}});
    saved.onFirstToken(); saved.onPromptProcessingProgress(0.5);
    saved.onPredictionFragment({content:'late',tokensCount:1});
    saved.onToolCallRequestFinalized(0,1,{}); saved.onRoundEnd();
    let denied = false;
    await saved.guardToolCall(0,2,{deny(){denied=true;}});
    assert.equal(denied,true,exit);
    assert.equal(observed,count,exit);
    assert.equal(JSON.stringify(captured),before,exit);
    assert.equal(saved.signal.aborted,true,exit);
    if (exit === 'reject' || exit === 'cancel') assert.equal(captured.failure,failure);
  }
});

test('shared reservations distinguish pending peers from returned-result overrun', () => {
  const { BatchReservation, reserveReadResult } = require('../dist/budget-broker');
  const batch = new BatchReservation(29997);
  for (const id of ['a','b','c']) assert.equal(batch.reserve(id,9216),9216);
  const tool = {name:'read_symbol',pluginIdentifier:'mcp/unreal-agent'};
  assert.equal(reserveReadResult(batch,'d',tool,{}).denialReason,'held_reservations_insufficient');
  for (const id of ['a','b','c']) batch.receive(id,5900);
  assert.equal(batch.exceeded,false);
  assert.equal(reserveReadResult(batch,'d',tool,{}).allowed,true);
  assert.equal(reserveReadResult(batch,'d',tool,{}).denialReason,'duplicate_reservation');
  batch.receive('d',12000);
  assert.equal(reserveReadResult(batch,'e',tool,{}).denialReason,'returned_result_overrun');
  batch.close(); batch.close();
  assert.equal(reserveReadResult(batch,'e',tool,{}).denialReason,'batch_closed');
});

test('mixed guard outcomes preserve the allowed and denied counts', () => {
  const { classifyToolCallBoundary } = require('../dist/tool-boundary');
  const result=classifyToolCallBoundary({rawToolIntent:false,structuredRequestCount:2,sdkStartedCount:2,
    sdkFinalizedCount:1,sdkFailureCount:0,guardAllowedCount:1,guardDeniedCount:1,dispatchCount:1,resultCount:2});
  assert.equal(result.state,'mixed_guard_outcomes');
  assert.equal(result.guardAllowedCount,1); assert.equal(result.guardDeniedCount,1);
  assert.equal(result.dispatchCount,1);
});

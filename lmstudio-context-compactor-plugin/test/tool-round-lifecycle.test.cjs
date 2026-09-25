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

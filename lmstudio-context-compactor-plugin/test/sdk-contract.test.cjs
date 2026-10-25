'use strict';
const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path'), Module=require('node:module');
// Exercise the installed, version-pinned SDK implementation, including our
// compatibility patch. Stubs previously passed effective args where the SDK
// passed proposed args and concealed the broken callback contract.
const sdkFile=require.resolve('@lmstudio/sdk');
const compiled=new Module(sdkFile,module);compiled.filename=sdkFile;compiled.paths=Module._nodeModulePaths(path.dirname(sdkFile));
compiled._compile(fs.readFileSync(sdkFile,'utf8')+'\nexports.auditInternalAct=internalAct;',sdkFile);
const sdk=compiled.exports;
const {runOneToolRound}=require('../dist/round-loop');
const {VisibleHistoryRecorder,createMessageEmitter}=require('../dist/prediction-ui');
const logger={warn(){},error(){},info(){},debug(){},warnText(){},errorText(){},infoText(){},debugText(){}};
const request=(id,name)=>({type:'function',id,name,arguments:{path:'Source/A.cpp',byteBudget:65536}});
const tool=(name,implementation)=>({name,implementation,checkParameters(){},pluginIdentifier:'mcp/unreal-agent'});
function source(requests,finish=true){return {act(history,tools,opts){return sdk.auditInternalAct(history,tools,
  {...opts,allowParallelToolExecution:true,maxPredictionRounds:1},'',logger,performance.now(),hooks=>{
    for(const req of requests){hooks.handleToolCallGenerationStart(req.id);hooks.handleToolCallGenerationEnd(req,JSON.stringify(req));}
    if(finish)hooks.handlePredictionEnd({});
  },()=>({stats:{stopReason:'toolCalls'}}));}};}
const block=()=>({appendText(){},appendToolRequest(){},appendToolResult(){},replaceToolRequest(){}});
test('actual SDK finalized, dispatch, model history and host history share effective arguments',async()=>{
  let actual,finalized;
  const tools=[tool('read_file',async args=>{actual=args;return {ok:true,text:'fact'};})];
  const visible=new VisibleHistoryRecorder({createContentBlock:block},sdk.Chat.empty());
  const emitter=createMessageEmitter(visible.controller,tools);emitter.beginRound();
  const captured=await runOneToolRound(source([request('read','read_file')]),sdk.Chat.empty(),tools,new AbortController().signal,{
    guardToolCall(_r,id,c){emitter.registerRequest(id,c.toolCallRequest);c.allowAndOverrideParameters({...c.toolCallRequest.arguments,project:'BoundProject',byteBudget:8192});},
    onToolCallRequestFinalized(_r,id,info){finalized=info.toolCallRequest.arguments;emitter.emitRequest(id,info.toolCallRequest);},
    onMessageCaptured(message){emitter.emit(message,false,true);}
  });
  assert.deepEqual(finalized,actual);
  for(const history of [captured.messages,visible.snapshot().getMessagesArray()])
    assert.deepEqual(history.flatMap(m=>m.getToolCallRequests())[0].arguments,actual);
});
for(const finish of [true,false])test(`actual SDK preserves completed peer on exception (prediction ended=${finish})`,async()=>{
  let done;const completed=new Promise(r=>done=r), failure=new Error('transport');
  const tools=[tool('ok',async()=>{done();return {ok:true,text:'fact'};}),tool('bad',async()=>{await completed;throw failure;})];
  const captured=await runOneToolRound(source([request('a','ok'),request('b','bad')],finish),sdk.Chat.empty(),tools,new AbortController().signal,
    {guardToolCall(_r,_i,c){c.allow();},onToolCallRequestFinalized(){}});
  assert.equal(captured.failure,failure);assert.equal(captured.predictionCompleted,false);
  const results=captured.messages.flatMap(m=>m.getToolCallResults());
  assert.equal(results.length,1);assert.equal(results[0].toolCallId,'a');
  assert.ok(captured.messages.flatMap(m=>m.getToolCallRequests()).some(r=>r.id==='a'));
});
test('actual SDK guard denial preserves allowed peer and both result outcomes',async()=>{
  const tools=[tool('ok',async()=>({ok:true,text:'fact'})),tool('bad',()=>assert.fail('denied tool must not run'))];
  const captured=await runOneToolRound(source([request('a','ok'),request('b','bad')]),sdk.Chat.empty(),tools,new AbortController().signal,
    {guardToolCall(_r,_i,c){if(c.toolCallRequest.name==='bad')c.deny('denied');else c.allow();},onToolCallRequestFinalized(){}});
  assert.equal(captured.failure,undefined);assert.equal(captured.messages.flatMap(m=>m.getToolCallResults()).length,2);
});
test('an async guard completing after a peer failure cannot dispatch a new tool',async()=>{
  let allow, invoked=0;const gate=new Promise(r=>allow=r), failure=new Error('peer failed');
  const tools=[tool('waiting',async()=>{invoked++;return {ok:true};}),tool('bad',async()=>{throw failure;})];
  const captured=await runOneToolRound(source([request('a','waiting'),request('b','bad')]),sdk.Chat.empty(),tools,new AbortController().signal,
    {async guardToolCall(_r,_i,c){if(c.toolCallRequest.name==='waiting')await gate;c.allow();},onToolCallRequestFinalized(){}});
  assert.equal(captured.failure,failure);allow();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(invoked,0);
});

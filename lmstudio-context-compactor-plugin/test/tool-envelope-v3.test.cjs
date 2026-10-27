"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { decodeEnvelope } = require("../dist/tool-result-semantics");
const { BudgetBroker, BatchReservation, reserveReadResult } = require("../dist/budget-broker");
const retry = {allowed:true, mode:"different_arguments", minimum:{field:"maxChars",value:6200}};
for (const [dialect, value, code] of [
  ["agent", {ok:false,errorCode:"INVALID_ARGUMENT",message:"bad",retry:{allowed:false,mode:"none"}}, "INVALID_ARGUMENT"],
  ["rag", {ok:false,errorCode:"OUTPUT_LIMIT_EXCEEDED",message:"small",retry}, "OUTPUT_LIMIT_EXCEEDED"],
  ["evidence", {ok:false,error:"bad packet",errorCode:"VALIDATION_FAILED",retry}, "VALIDATION_FAILED"],
  ["unity", {status:"not_applied",errorCode:"editor_reloading",message:"reload"}, "EDITOR_RELOADING"],
  ["sdk", {error:"denied"}, "HARNESS_BUDGET_DENIED"],
]) test(`v3 envelope normalizes ${dialect} through the MCP text wire`, () => {
  const result = decodeEnvelope(JSON.stringify([{type:"text",text:JSON.stringify(value)}]));
  assert.equal(result.ok,false); assert.equal(result.errorCode,code);
  if(value.retry?.minimum) assert.deepEqual(result.retry.minimum,retry.minimum);
  if(dialect === "sdk") assert.equal(result.sdkDenial,true);
});
test("v3 malformed or conflicting envelopes cannot grant a retry floor",()=>{
  for(const input of ["{", {structuredContent:{ok:true},content:[{type:"text",text:'{"ok":false}'}]}])
    assert.equal(decodeEnvelope(input).errorCode,"INVALID_RESULT_ENVELOPE");
  assert.equal(decodeEnvelope({ok:false,retry:{...retry,minimum:{field:"path",value:6200}}}).retry.minimum,undefined);
});
const tool={name:"unreal_symbol_lookup",pluginIdentifier:"mcp/unreal-rag",parametersJsonSchema:{properties:{maxChars:{minimum:128,maximum:10000}}}};
test("v3 retry minimum survives known-size shortcut and refuses insufficient dispatch",()=>{
  const args={project:"A",query:"Controller",maxChars:2000};
  const broker=new BudgetBroker({workingInputTargetTokens:40000,workingInputTriggerTokens:56000,safetyMarginTokens:1024});
  broker.observeResult(tool,args,decodeEnvelope({ok:false,errorCode:"OUTPUT_LIMIT_EXCEEDED",retry}));
  const floor=broker.retryMinimum(tool,{...args,maxChars:3362});
  assert.deepEqual(floor,retry.minimum);
  const denied=reserveReadResult(new BatchReservation(5000),"denied",tool,args,500,floor);
  assert.equal(denied.allowed,false); assert.deepEqual(denied.requiredMinimum,retry.minimum);
  const allowed=reserveReadResult(new BatchReservation(20000),"allowed",tool,args,500,floor);
  assert.equal(allowed.allowed,true); assert.ok(allowed.arguments.maxChars>=6200);
  assert.equal(broker.retryMinimum(tool,{...args,project:"B"}),undefined);
  assert.equal(broker.retryMinimum({...tool,pluginIdentifier:"untrusted"},args),undefined);
  broker.observeResult(tool,args,decodeEnvelope({ok:true}));
  assert.equal(broker.retryMinimum(tool,args),undefined);
  assert.equal(reserveReadResult(new BatchReservation(50000),"ceiling",tool,args,500,{field:"maxChars",value:12000}).allowed,false);
});

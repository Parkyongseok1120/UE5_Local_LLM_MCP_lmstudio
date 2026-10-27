"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { ChatMessage } = require("@lmstudio/sdk");
const { classifyObjective, readOnlyReviewRequest, ToolCapabilityRegistry } = require("../dist/tool-capability-registry");
for (const [text, kind] of [
  ["수정 진행해봐", "mutate"], ["P2부터 고쳐줘", "mutate"], ["Fix the reported bugs", "mutate"],
  ["빌드해줘", "build"], ["Compile and run", "build"],
  ["코드 수정하지 말고 계약 검토해줘", "review"], ["Do not edit code. Review RPC.", "review"],
  ["Review without changing files.", "review"], ["Explain how respawn works", "review"], ["안녕하세요", "explain"],
]) test("objective: " + text, () => {
  const objective = classifyObjective(text);
  assert.equal(objective.kind, kind);
  assert.equal(objective.mutationState, "none");
  assert.equal(objective.requiredEffects.has("mutation"), ["mutate", "build"].includes(kind));
  assert.equal(objective.requiredEffects.has("long_running"), kind === "build");
  assert.equal(readOnlyReviewRequest(text), kind === "review");
});
test("only provider-qualified paired mutation results count; prose does not", () => {
  const tool = { name: "apply_edit_bundle", pluginIdentifier: "mcp/unreal-agent" };
  const registry = new ToolCapabilityRegistry([tool]);
  const pair = value => [
    ChatMessage.from({role:"assistant",content:[{type:"toolCallRequest",toolCallRequest:{type:"function",id:"w",name:tool.name,arguments:{}}}]}),
    ChatMessage.from({role:"tool",content:[{type:"toolCallResult",toolCallId:"w",content:JSON.stringify(value)}]})
  ];
  assert.deepEqual(registry.mutationOutcomes([ChatMessage.create("assistant", "I applied the fix.")]), []);
  assert.deepEqual(registry.mutationOutcomes(pair({ok:true,operation:"bundle_applied"})), ["applied"]);
  assert.deepEqual(registry.mutationOutcomes(pair({ok:false,errorCode:"STALE_READ"})), ["failed"]);
  assert.deepEqual(new ToolCapabilityRegistry([{...tool,pluginIdentifier:"mcp/unknown"}]).mutationOutcomes(pair({ok:true})), []);
});

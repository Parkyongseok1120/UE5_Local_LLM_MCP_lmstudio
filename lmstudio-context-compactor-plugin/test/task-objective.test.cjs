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
  // Revised plan 2026-11-07 §G: the two audit requests that started the investigation.
  ["해당 프로젝트에서 책임, 수명, 미연결, 코어 이상, 등등의 문제점과 SSOT, SOLID 법칙을 위반하는 것들, 전반적인 아키텍쳐에 구조적인 문제가 없는지 찾을것", "review"],
  ["캐릭터, 무기, 체력, 네트워크 RPC 계약 쪽과 SSOT, SOLID, 계약, 책임, 수명관리, 또는 UE API 매크로가 잘못된 것들을 찾아서 보고해줘.", "review"],
  ["문제점 찾기", "review"], ["결함이 있는지 찾는 중이야", "review"],
  // A change request still wins, and an explanation request keeps its read-only review profile.
  ["찾아서 수정해", "mutate"], ["결함을 고쳐줘", "mutate"], ["이 함수 설명해줘", "review"],
  ["위반 사항은 수정하지 말고 찾아만 줘", "review"],
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

// Conversation 1794393906420: a plan request was mutate (it named '수정'),
// the real approval was turn 12, and "쭉 해줘봐 …" ran as explain.
for (const [text, kind] of [
  ["그럼 지금 수정 계획 세워봐. 단계적으로", "review"], ["수정하지 말고 계획만 세워봐", "review"],
  ["리팩터링 계획 짜줘", "review"], ["Make a plan to fix the bug", "review"],
  ["계획 세우고 바로 고쳐줘", "mutate"], ["수정 계획대로 진행해", "mutate"],
  ["데이터 에셋이나 기타 에디터 건드리는거 제외하고 코드 쪽만 한번 수정해보자", "mutate"],
  ["니가 세운 계획 검토해줘", "review"],
]) test("plan objective: " + text, () => {
  const objective = classifyObjective(text);
  assert.equal(objective.kind, kind);
  assert.equal(objective.requiredEffects.has("mutation"), kind === "mutate");
});

test("a go-ahead carries the approved edit, and runs a plan only when it approves it", () => {
  const { executionObjective, planOnlyRequest } = require("../dist/tool-capability-registry");
  const { Chat } = require("@lmstudio/sdk");
  const kindAfter = (...users) => executionObjective(Chat.from(users.flatMap((content, index) =>
    index < users.length - 1 ? [{ role: "user", content }, { role: "assistant", content: "답변" }] : [{ role: "user", content }]))
    .getMessagesArray()).kind;
  const plan = "그럼 지금 수정 계획 세워봐. 단계적으로";
  const approve = "데이터 에셋이나 기타 에디터 건드리는거 제외하고 코드 쪽만 한번 수정해보자";
  const goOn = "쭉 해줘봐. 계획 니가 세운거 끝까지.";
  assert.equal(planOnlyRequest(plan), true);
  assert.equal(kindAfter(plan, approve, goOn), "mutate", "the approved turn, not the plan, is carried");
  assert.equal(kindAfter(plan, goOn), "mutate", "carrying the plan out to the end approves it");
  assert.equal(kindAfter(plan, "진행해"), "mutate");
  assert.equal(kindAfter(plan, "해줘"), "mutate");
  assert.equal(kindAfter(plan, "계속"), "review", "continuing a plan keeps writing the plan");
  assert.equal(kindAfter("코드를 수정하지 말고 RPC 계약만 검토해줘", "진행해"), "review", "an explicit no-edit review is never widened");
  assert.equal(kindAfter("이 함수 설명해줘", "쭉 해줘봐"), "review");
});

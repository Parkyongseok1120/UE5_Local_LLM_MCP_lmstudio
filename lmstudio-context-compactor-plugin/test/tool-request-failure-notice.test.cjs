"use strict";
// Conversation 1794386851846, last turn: a 2,799-char plan streamed, git_status
// returned, and evidence_first_validate was cut off at the 8,192-token output
// cap. The partial report then showed reasoning notes and the continuity JSON.
const test = require("node:test"), assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { DeliveryController, evidenceBackedPartialReport, ANSWERED_ROUND_MIN_CHARS } = require("../dist/delivery-controller");
const { renderTaskProgress } = require("../dist/continuity-memory.js");
const { TASK_CONTINUITY_HEADER } = require("../dist/internal-data-contract.js");

const tools = [
  { name: "git_status", pluginIdentifier: "mcp/unreal-agent", description: "Git status", parametersJsonSchema: { type: "object" } },
  { name: "evidence_first_validate", pluginIdentifier: "mcp/evidence-first", description: "Validate", parametersJsonSchema: { type: "object" } },
];
const gitStatus = { schemaVersion: 1, kind: "git_observation", status: "observed", action: "status", ok: true,
  workspaceRoot: "C:/Game", pageStart: 1, pageEnd: 55, total: 55, returnedCount: 55, complete: true, items: [] };

function planRound(text = "PLAN_BODY") {
  return [
    ChatMessage.from({ role: "assistant", content: [{ type: "text", text },
      { type: "toolCallRequest", toolCallRequest: { type: "function", id: "git", name: "git_status", arguments: {} } }] }),
    ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "git",
      content: JSON.stringify([{ type: "text", text: JSON.stringify(gitStatus) }]) }] }),
  ];
}
function interruptedHistory() {
  const history = Chat.from([{ role: "user", content: "어떻게 고칠건지 계획세워봐" }]);
  history.append(ChatMessage.create("assistant", "[Observed incomplete tool invocations v1]\n" + JSON.stringify({
    authority: "historical_runtime_observation_only", retainedLimit: 16, omittedItemsAtLeast: 0,
    items: [{ callKey: "x:1:a:0", toolName: "apply_edit_bundle", resultObservation: "not_observed", effect: "unknown" }] })));
  return history;
}
const reasoningRow = { identifier: "NeedsRuntimeProof", status: "found", source: "reasoning", round: 0,
  evidence: "Wait — counterEvidence for P2: the schema says at least one item required for P0/P1." };
const visibleRow = { identifier: "SyncAttackTuning", status: "found", source: "visible", round: 0,
  evidence: "SyncAttackTuning copies child tuning into base fields." };

test("an answered round reports only what ran and what did not", () => {
  const delivery = new DeliveryController(tools);
  delivery.bindExecution(tools, { kind: "explain", requiredEffects: new Set(), mutationState: "none" }, () => [reasoningRow, visibleRow]);
  const report = delivery.failedToolRequest(interruptedHistory(), planRound(), "tool_arguments_output_limit",
    { answerShownChars: 2799, toolName: "evidence_first_validate" });
  assert.match(report.text, /^\[응답 상태 · 미완료\]/u);
  assert.match(report.text, /`evidence_first_validate`은\(는\) 인수가 출력 생성 한도에서 잘려 실행되지 않았습니다/u);
  assert.match(report.text, /같은 응답에서 완료된 조회: git status \(항목 1–55\/55\)/u);
  for (const leaked of ["부분 조사 보고", "미검증 검토 후보", "NeedsRuntimeProof", "SyncAttackTuning", "PLAN_BODY",
    TASK_CONTINUITY_HEADER, '"authority"', "apply_edit_bundle"])
    assert.equal(report.text.includes(leaked), false, leaked);
  assert.equal(report.evidenceCount, 1);
});

test("an answered round never reports earlier rounds as this round's lookups", () => {
  const history = Chat.from([{ role: "user", content: "plan" }]);
  for (const message of planRound("EARLIER")) history.append(message);
  const report = new DeliveryController(tools).failedToolRequest(history, [], "tool_arguments_parse_failure",
    { answerShownChars: ANSWERED_ROUND_MIN_CHARS });
  assert.match(report.text, /도구 요청은\(는\) 인수를 해석할 수 없어 실행되지 않았습니다/u);
  assert.doesNotMatch(report.text, /완료된 조회/u);
  assert.equal(report.evidenceCount, 0);
  assert.ok(report.text.trim().length > 0, "the notice is never empty");
});

test("a short preface keeps the evidence report, without working notes or the continuity frame", () => {
  const history = interruptedHistory();
  assert.ok(renderTaskProgress(history.getMessagesArray()).startsWith(TASK_CONTINUITY_HEADER), "fixture renders a frame for model input");
  const delivery = new DeliveryController(tools);
  delivery.bindExecution(tools, { kind: "explain", requiredEffects: new Set(), mutationState: "none" }, () => [reasoningRow, visibleRow]);
  const report = delivery.failedToolRequest(history, planRound("파일을 확인하겠습니다."), "tool_arguments_output_limit",
    { answerShownChars: ANSWERED_ROUND_MIN_CHARS - 1, toolName: "evidence_first_validate" });
  assert.match(report.text, /^부분 조사 보고 \(미완료\)/u);
  assert.match(report.text, /- git status \(항목 1–55\/55\)/u);
  assert.match(report.text, /SyncAttackTuning/u);
  assert.doesNotMatch(report.text, /NeedsRuntimeProof|counterEvidence/u);
  assert.equal(report.text.includes(TASK_CONTINUITY_HEADER), false);
  assert.doesNotMatch(report.text, /"authority"|historicalUnresolvedItems/u);
  assert.match(report.text, /결과를 받지 못한 이전 도구 요청 1건 \(적용 여부 불명 1건\): apply_edit_bundle/u);
  assert.match(report.text, /도구 요청 `evidence_first_validate` 인수를 완성하지 못해 해당 요청은 실행되지 않았습니다/u);
});

test("a mutation objective still states that the change was not applied", () => {
  const delivery = new DeliveryController(tools);
  delivery.bindExecution(tools, { kind: "mutate", requiredEffects: new Set(["mutation"]), mutationState: "none" }, () => []);
  const report = delivery.failedToolRequest(Chat.empty(), planRound(), "tool_arguments_output_limit", { answerShownChars: 2799 });
  assert.match(report.text, /^요청한 변경은 적용되지 않았습니다:/u);
  assert.match(report.text, /\[응답 상태 · 미완료\]/u);
});

test("non-git action observations keep their existing fact text", () => {
  const history = Chat.empty();
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id: "list", name: "list_directory", arguments: { path: "Source" } } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "list",
    content: JSON.stringify({ ok: true, action: "list", path: "Source", pageStart: 1, pageEnd: 3 }) }] }));
  const report = evidenceBackedPartialReport(history, [], "reason");
  assert.match(report.text, /- list Source page 1–3/u);
});

"use strict";

// Conversation 1794590083146: turn 1 ended with a 2,300-character report and
// the harness notice "[제안 코드 검증 범위] ..." shown after it. The window
// stored the notice as its own record under the partial-response marker, the
// next turn took that record for "the previous answer", and the report (the
// P0/P1/P2 list the user's follow-up "p1까지 수정" referred to) left the input.
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const core = require("../src/direct-compaction-core.js");
const { ASSISTANT_MARKER, DELIVERED_NOTICE_MARKER, retainVisibleResponse } = require("../src/continuity-assistant-evidence.js");
const { ContextManager, createInputAssembler } = require("../dist/context-manager");
const { BudgetBroker } = require("../dist/budget-broker");
const { readConfig } = require("../dist/execution-config");

const PARTIAL_MARKER = "[Delivered partial assistant response; unverified]";
const REPORT = `**적 관련 설계 진단 결과**\n### P0\n- TakeDamage 권한 체크 없음\n### P1\n- 캡슐 크기 부적절\n${"세부 ".repeat(300)}\nREPORT_END`;
const NOTICE = "\n\n[제안 코드 검증 범위] C++ 예시 1개는 별도 컴파일하지 않은 제안입니다.";
const message = (role, text, index) => ({ role, text, index, hasFiles: false, toolRequests: [], toolResults: [] });
const serialized = (role, text) => ({ role, content: [{ type: "text", text }] });

function previousResponse(messages, options = {}) {
  const result = core.buildCheckpoint(messages, { maxCurrentTurnMessages: 0, checkpointPolicy: "mandatory", maxCheckpointChars: 22000, ...options });
  return { result, previous: JSON.parse(result.assistantCheckpoint.slice(ASSISTANT_MARKER.length + 1)).previousResponse };
}

test("a notice stored after the report does not replace the report as the previous answer", () => {
  for (const marker of [PARTIAL_MARKER, DELIVERED_NOTICE_MARKER]) {
    const { result, previous } = previousResponse([
      message("user", "적 설계 문제를 찾아 P0를 수정해", 0), message("assistant", REPORT, 1),
      message("assistant", `${marker}\n${NOTICE}`, 2), message("user", "p1까지 수정 하고 빌드까지 해봐", 3),
    ]);
    assert.match(previous.text, /캡슐 크기 부적절/u, marker);
    assert.match(previous.text, /REPORT_END/u);
    assert.match(previous.text, /제안 코드 검증 범위/u, "the notice the user saw stays with the answer");
    assert.equal(previous.messageIndex, 1);
    assert.equal(previous.final, true, "a harness notice does not make a completed answer partial");
    assert.equal(result.memory.previousTurnFinalResponseEvidence.messageIndex, 1);
  }
  assert.deepEqual(core.previousAnswerTail([
    message("user", "u", 0), message("assistant", REPORT, 1), message("assistant", `${PARTIAL_MARKER}\n${NOTICE}`, 2), message("user", "n", 3),
  ], 3).map(item => [item.index, item.partial, item.notice]), [[1, false, false], [2, true, true]]);
});

test("an interrupted answer keeps its partial status, alone or after a completed part", () => {
  const alone = previousResponse([
    message("user", "Write the report", 0), message("assistant", `${PARTIAL_MARKER}\nSTART of a report cut mid`, 1), message("user", "이어서", 2),
  ]).previous;
  assert.equal(alone.final, false);
  assert.match(alone.text, /START of a report cut mid/u);
  const continued = previousResponse([
    message("user", "Write the report", 0), message("assistant", "FIRST_PART complete.", 1),
    message("assistant", `${PARTIAL_MARKER}\n and a continuation that was cut`, 2), message("user", "이어서", 3),
  ]).previous;
  assert.equal(continued.final, false, "unfinished prose after the answer is not a notice");
  assert.match(continued.text, /FIRST_PART complete\. and a continuation that was cut/u);
});

test("a turn that ended with tool activity has no answer tail", () => {
  const messages = [message("user", "Audit", 0), message("assistant", "PROGRESS so far", 1),
    { ...message("assistant", "", 2), toolRequests: [{ id: "r", name: "read_file", arguments: {} }] },
    { ...message("tool", "", 3), toolResults: [{ toolCallId: "r", content: "{}" }] }, message("user", "계속", 4)];
  assert.deepEqual(core.previousAnswerTail(messages, 4), []);
  assert.equal(previousResponse(messages).previous.final, false);
});

test("sealing stores a harness notice under its own marker and stays idempotent", () => {
  const user = serialized("user", "적 설계 문제를 찾아줘");
  const source = [user, serialized("assistant", REPORT), serialized("assistant", NOTICE)];
  const sealed = retainVisibleResponse(source, [user, serialized("assistant", REPORT)]);
  assert.equal(sealed.length, 3);
  assert.equal(sealed[1].content[0].text, REPORT, "the answer message is not rewritten");
  assert.equal(sealed[2].content[0].text, `${DELIVERED_NOTICE_MARKER}\n${NOTICE}`);
  assert.deepEqual(retainVisibleResponse(source, sealed), sealed, "restoring the sealed window adds nothing");
  // Prose that continues the answer is still a partial response.
  const cut = retainVisibleResponse([user, serialized("assistant", "FIRST_PART complete."), serialized("assistant", " More text that was")],
    [user, serialized("assistant", "FIRST_PART complete.")]);
  assert.equal(cut[2].content[0].text, `${PARTIAL_MARKER}\n More text that was`);
  // A window sealed before the notice marker existed is read the same way.
  const legacy = [user, serialized("assistant", REPORT), serialized("assistant", `${PARTIAL_MARKER}\n${NOTICE}`)];
  assert.deepEqual(retainVisibleResponse(source, legacy), legacy);
});

function fixture() {
  const config = readConfig({ getPluginConfig() { return { get: key => ({ contextManagementMode: "deterministic",
    workingInputTargetTokens: 6000, workingInputTriggerTokens: 8000, workingInputScaling: "fixed",
    maxOutputReserve: 2048, safetyMarginTokens: 512 })[key] }; } });
  const source = { async getContextLength() { return 32768; },
    async applyPromptTemplate(chat) { return chat.toString(); },
    async countTokens(text) { return Math.ceil(text.length / 4); } };
  const assemble = createInputAssembler({ tokenSource: source, config, roundTools: [], roundScopeInstructions: [],
    getRoundOutputReserve: () => 2048, modelInputId: "notice", noteEnabled: false, getNote: () => null,
    historicalAvailabilityLedger: [] });
  return { assemble, manager: new ContextManager(config, new BudgetBroker(config)) };
}

test("turn-start compaction keeps the report and the notice after it, not the notice alone", async () => {
  const { manager, assemble } = fixture();
  const request = n => ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
    type: "function", id: `call_${n}`, name: "read_file_range", arguments: { path: `Source/File${n}.cpp` } } }] });
  const result = n => ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: `call_${n}`,
    content: JSON.stringify({ ok: true, path: `Source/File${n}.cpp`, text: `body ${n} ${"x".repeat(4000)}` }) }] });
  const history = Chat.from([{ role: "system", content: "invariant" }, { role: "user", content: "적 설계 문제를 찾아 P0를 수정해" }]);
  for (let n = 0; n < 12; n++) { history.append(request(n)); history.append(result(n)); }
  history.append("assistant", REPORT);
  history.append("assistant", `${PARTIAL_MARKER}\n${NOTICE}`);
  history.append("user", "p1까지 수정 하고 빌드까지 해봐");
  const compacted = await manager.enforceLowWater(history, assemble, { deepen: true, keepLastAssistantAnswer: true, hasReadTools: false });
  assert.equal(compacted.changed, true, JSON.stringify(compacted.telemetry?.candidateMeasurements));
  const texts = compacted.history.getMessagesArray().filter(m => m.isAssistantMessage()).map(m => m.getText());
  assert.ok(texts.includes(REPORT), "the report itself is retained");
  assert.ok(texts.some(text => text.includes("제안 코드 검증 범위") && text.startsWith(PARTIAL_MARKER)), "the notice stays where the user saw it");
  const evidence = JSON.parse(texts.find(text => text.startsWith(ASSISTANT_MARKER)).slice(ASSISTANT_MARKER.length + 1));
  assert.match(evidence.previousResponse.text, /캡슐 크기 부적절/u);
  assert.equal(evidence.previousResponse.final, true);
});

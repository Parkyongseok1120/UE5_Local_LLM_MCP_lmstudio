"use strict";

// Behavioral contracts for the bounded continuity/guidance plan. These fixtures
// use public functions and a deterministic tokenizer; they do not score a live
// model's reasoning or prove that a running LM Studio plugin loaded this build.
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { buildObjectiveContinuity, guidanceObjective, objectiveIdentity } = require("../src/continuity-objectives.js");
const { buildContinuityMemory, extractPriorContinuityState } = require("../src/continuity-memory.js");
const { mandatoryContinuity, renderBudgetedCheckpoint } = require("../src/checkpoint-budget.js");
const { selectGuidanceIntent } = require("../dist/guidance-topic-selection");
const { readConfig } = require("../dist/execution-config");
const { ContextManager } = require("../dist/context-manager");
const { BudgetBroker } = require("../dist/budget-broker");
const { EvidenceManager } = require("../dist/evidence-manager");
const { prepareRoundInput } = require("../dist/round-input");
const { DeliveryController } = require("../dist/delivery-controller");

function message(role, text, index) {
  return { role, text, index, toolRequests: [], toolResults: [] };
}
function memory(messages, previousState = null, facts = {}) {
  return buildContinuityMemory(messages, facts, { previousState });
}
function checkpoint(state) {
  // At the mandatory floor, optional raw tail and archived prose cannot rescue
  // a missing task anchor. This checks the smallest usable continuity capsule.
  return renderBudgetedCheckpoint(state, 1, true);
}
function restore(text) {
  const result = extractPriorContinuityState([message("system", text, 0)]);
  assert.ok(result, "The generated checkpoint must restore as a complete JSON object.");
  return result;
}
const selectedList = [
  "1. ORBIT_API_731: 설치된 엔진의 UPROPERTY 선언을 확인한다.",
  "2. ORBIT_LIFE_284: 소유자가 끝날 때 타이머를 해제한다.",
  "3. ORBIT_STYLE_906: 들여쓰기를 정리한다.",
].join("\n");
const selectFirstTwo = "1번부터 2번까지 진행해";

test("numbered user selection keeps original item meaning in the mandatory checkpoint", () => {
  const state = memory([
    message("user", "캐릭터 코드 개선 계획을 세워줘", 0),
    message("assistant", selectedList, 1),
    message("user", selectFirstTwo, 2),
  ]);
  const rendered = checkpoint(state);
  assert.match(rendered, /ORBIT_API_731/);
  assert.match(rendered, /ORBIT_LIFE_284/);
  assert.ok(rendered.includes(selectFirstTwo), "Preserve the selecting user utterance.");
});

test("selected item meaning survives a second compaction with renumbered local history", () => {
  const first = memory([
    message("user", "캐릭터 코드 개선 계획을 세워줘", 41),
    message("assistant", selectedList, 42),
    message("user", selectFirstTwo, 43),
  ]);
  const second = memory([message("user", "계속해", 1)], restore(checkpoint(first)));
  const restored = restore(checkpoint(second));
  const retained = JSON.stringify(mandatoryContinuity(restored));
  assert.match(retained, /ORBIT_API_731/);
  assert.match(retained, /ORBIT_LIFE_284/);
  assert.ok(retained.includes(selectFirstTwo));
});

test("assistant replanning does not replace the already selected numbered work", () => {
  const state = memory([
    message("user", "캐릭터 코드 개선 계획을 세워줘", 0),
    message("assistant", selectedList, 1),
    message("user", selectFirstTwo, 2),
    message("assistant", "새 계획:\n1. REPLAN_OTHER_118: UI 색상만 변경한다.\n2. REPLAN_OTHER_209: 이름을 바꾼다.", 3),
    message("user", "계속해", 4),
  ]);
  const retained = checkpoint(state);
  assert.match(retained, /ORBIT_API_731/);
  assert.match(retained, /ORBIT_LIFE_284/);
});

test("competing source lists preserve both candidate meanings when the numbered reference is ambiguous", () => {
  const state = memory([
    message("user", "두 대안을 비교할 목록을 제시해줘", 0),
    message("assistant", "대안 A:\n1. CANDIDATE_A_338: 서버 검증을 확인한다.\n2. CANDIDATE_A_440: RPC 소유권을 확인한다.\n\n대안 B:\n1. CANDIDATE_B_551: UI 이벤트를 확인한다.\n2. CANDIDATE_B_662: 애니메이션 구독을 확인한다.", 1),
    message("user", "위의 1번과 2번 진행해", 2),
  ]);
  const retained = checkpoint(state);
  assert.match(retained, /CANDIDATE_A_338/);
  assert.match(retained, /CANDIDATE_B_551/);
  assert.ok(retained.includes("위의 1번과 2번 진행해"));
  // A deterministic ambiguity record is allowed; automatic tool denial or an
  // automatically generated approval question is not required by this test.
});

test("a legacy checkpoint without original numbered content cannot invent API guidance", () => {
  const prior = { schemaVersion: 2, activeObjective: { text: "1번부터 2번까지 진행해", messageIndex: 42 } };
  const chosen = selectGuidanceIntent(guidanceObjective([message("user", "계속해", 0)], prior));
  assert.equal(chosen.primary, "core");
  assert.notEqual(chosen.reason, "user_objective_verified");
  assert.ok(buildObjectiveContinuity([message("user", "계속해", 0)], prior).activeObjective);
});

test("complete selected task evidence can supply API intent after a follow-up turn", () => {
  const state = memory([
    message("user", "캐릭터 코드 개선 계획을 세워줘", 0),
    message("assistant", selectedList, 1),
    message("user", "1번 진행해", 2),
  ]);
  const previous = restore(checkpoint(state));
  const selected = selectGuidanceIntent(guidanceObjective([message("user", "계속해", 0)], previous));
  assert.equal(selected.primary, "debugging");
  assert.notEqual(selected.omitted, true);
});

test("continuing a numbered selection keeps one identity with and without compaction", () => {
  const initial = [message("user", "캐릭터 코드 개선 계획을 세워줘", 0),
    message("assistant", selectedList, 1), message("user", "1번 진행해", 2)];
  const selected = buildObjectiveContinuity(initial);
  const continued = buildObjectiveContinuity([...initial,
    message("assistant", "선택한 작업을 확인 중입니다.", 3), message("user", "계속해", 4)]);
  const prior = restore(checkpoint(memory(initial)));
  const compacted = buildObjectiveContinuity([message("user", "계속해", 0)], prior);
  assert.equal(objectiveIdentity(continued), objectiveIdentity(selected));
  assert.equal(objectiveIdentity(compacted), objectiveIdentity(selected));
});

test("an explicit new objective retires the old selected task without inferring completion", () => {
  const messages = [message("user", "캐릭터 코드 개선 계획을 세워줘", 0),
    message("assistant", selectedList, 1), message("user", "1번 진행해", 2),
    message("user", "이제 별도 작업으로 들여쓰기 정리해", 3)];
  const objective = buildObjectiveContinuity(messages);
  assert.notEqual(objective.taskSelection?.status, "active");
  assert.notEqual(objective.taskSelection?.completion, "completed");
  assert.equal(selectGuidanceIntent(guidanceObjective(messages)).primary, "code-style");
});

test("a later reference exclusion applies even while a selected task remains active", () => {
  const messages = [message("user", "캐릭터 코드 개선 계획을 세워줘", 0),
    message("assistant", selectedList, 1), message("user", "1번 진행해", 2),
    message("user", "참고 문서를 추가하지 마. API 계약은 계속 확인해.", 3)];
  assert.equal(selectGuidanceIntent(guidanceObjective(messages)).omitted, true);
});

test("a selected task's reference exclusion persists through continue and compaction", () => {
  const messages = [message("user", "캐릭터 코드 개선 계획을 세워줘", 0),
    message("assistant", selectedList, 1), message("user", "1번 진행해", 2),
    message("user", "참고 문서를 추가하지 마. API 계약은 계속 확인해.", 3)];
  const continued = [...messages, message("assistant", "확인 중입니다.", 4), message("user", "계속해", 5)];
  assert.equal(selectGuidanceIntent(guidanceObjective(continued)).omitted, true);
  const prior = restore(checkpoint(memory(messages)));
  assert.equal(selectGuidanceIntent(guidanceObjective([message("user", "계속해", 0)], prior)).omitted, true);
});

test("a numeric runtime range is not a numbered task selection", () => {
  const requested = "타임아웃을 1-2초 범위로 설정하는 API를 검토해";
  const messages = [message("user", "기존 캐릭터 코드를 확인해", 0),
    message("assistant", selectedList, 1), message("user", requested, 2)];
  const objective = buildObjectiveContinuity(messages);
  assert.equal(objective.activeObjective?.text, requested);
  assert.notEqual(objective.taskSelection?.status, "active");
});

test("unresolved factual work is preserved at the mandatory floor without inferring completion", () => {
  const messages = [message("user", "API와 수명 계약을 확인해", 0), message("assistant", "모두 확인했습니다.", 1)];
  const state = memory(messages, null, { openQuestionEvidence: [
    { text: "UNRESOLVED_CALLBACK_773: 소유자 종료 후 콜백의 호출 여부는 미확인", messageIndex: 0 },
  ] });
  assert.match(checkpoint(state), /UNRESOLVED_CALLBACK_773/);
});

test("a no-edit constraint does not erase a positive API review request", () => {
  for (const text of [
    "코드 수정하지 말고 언리얼 API를 확인해",
    "기존 동작은 바꾸지 않고 API 계약을 검토해",
    "Do not edit code. Review the Unreal API contract.",
  ]) {
    const chosen = selectGuidanceIntent({ text, reason: "user_objective_verified" });
    assert.equal(chosen.primary, "debugging", text);
    assert.notEqual(chosen.omitted, true, text);
  }
});

test("explicit reference exclusions still win over positive API intent", () => {
  for (const text of ["참고 문서를 추가하지 마. API 계약은 확인해.", "Do not add references. Review the API contract."])
    assert.equal(selectGuidanceIntent({ text, reason: "user_objective_verified" }).omitted, true, text);
  assert.notEqual(selectGuidanceIntent({ text: "API 검토는 하지 말고 들여쓰기 정리해", reason: "user_objective_verified" }).primary, "debugging");
});

async function prepareFixture(overrides = {}, extra = {}) {
  const config = readConfig({ getPluginConfig: () => ({ get: key => ({
    projectEngine: "unreal", contextManagementMode: "hybrid", showDebugInfo: true,
    maxOutputReserve: 4096, designGuidanceMode: "auto", designGuidanceMaxTokens: 2048,
    ...overrides,
  })[key] }) });
  const history = Chat.from([{ role: "user", content: "코드 수정하지 말고 API 계약을 확인해" }]);
  const events = [];
  const tokenSource = {
    async getContextLength() { return 65536; },
    async applyPromptTemplate(chat) { return chat.toString(); },
    async countTokens(text) { return Math.ceil(text.length / 4); },
  };
  const result = await prepareRoundInput({
    ctl: { debug(event) { events.push(event); }, guardAbort() {} }, config, tokenSource,
    contextManager: new ContextManager(config, new BudgetBroker(config)),
    evidenceManager: new EvidenceManager(null, []), workingContext: null,
    workingHistory: history, visibleHistory: history, preProjectionHistory: history, postProjectionHistory: history,
    roundTools: [], modelTools: [], roundScopeInstructions: [], scopeInstructions: [],
    roundOutputReserve: 4096, activeNote: null, noteEnabled: false, historicalAvailabilityLedger: [],
    semanticSummaryCooldownUntilRound: 99, objectiveFingerprint: "fixture-objective",
    finalizing: false, boundedAudit: false, researchRecoveryEpisodeStarted: false,
    guidanceIntent: { primary: "debugging", reason: "explicit_task_topic" },
    guidanceScope: { engine: "unreal", source: "config" },
    finalizationTrigger: null, projectionApplied: false, executionId: "behavior-fixture",
    modelInputId: "behavior-fixture:input-1", roundIndex: 0, ...extra,
  });
  return { result, events, history };
}

for (const [name, config, options] of [
  ["disabled", { designGuidanceMode: "off" }, {}],
  ["zero allowance", { designGuidanceMaxTokens: 0 }, {}],
  ["finalization", {}, { finalizing: true, finalizationTrigger: "context_budget" }],
]) test(`each ${name} input records one guidance outcome without injecting optional guidance`, async () => {
  const { result, events, history } = await prepareFixture(config, options);
  assert.equal(result.kind, "ready");
  const guidance = events.filter(event => event.event === "design_guidance_input");
  assert.equal(guidance.length, 1, "Every actual prepared model input needs an observable outcome.");
  assert.equal(guidance[0].modelInputId, "behavior-fixture:input-1");
  assert.notEqual(guidance[0].reason, "included");
  assert.doesNotMatch(result.modelInput.toString(), /\[Optional design reference/);
  assert.doesNotMatch(history.toString(), /\[Optional design reference/);
});

test("enabled guidance is measured once and does not mutate conversation history", async () => {
  const { result, events, history } = await prepareFixture();
  assert.equal(result.kind, "ready");
  const guidance = events.filter(event => event.event === "design_guidance_input");
  assert.equal(guidance.length, 1);
  assert.equal(guidance[0].reason, "included");
  assert.equal(guidance[0].finalInputTokens, result.finalMeasurement.inputTokens);
  assert.equal(result.finalMeasurement.exact, true);
  assert.doesNotMatch(history.toString(), /\[Optional design reference/);
});

test("a fluent EOS report does not establish that selected work was completed", () => {
  const history = Chat.from([{ role: "user", content: selectFirstTwo }]);
  const reply = ChatMessage.create("assistant", "모든 항목을 수정했습니다.");
  const outcome = new DeliveryController().evaluate(history,
    { messages: [reply], finishReason: "eosFound" }, false, null, 0);
  assert.equal(outcome.generationCompleted, true);
  assert.equal(outcome.reportDelivered, true);
  assert.equal(outcome.objectiveSatisfied, null);
  assert.equal(outcome.taskCompleted, false);
});


for (const pressure of [false, true])
test(`raw tool evidence is ${pressure ? "projected under measured pressure" : "retained while input budget permits"}`, async () => {
  const { WorkingContext } = require("../src/working-context");
  const history = Chat.from([{ role: "user", content: "Review Foo's body" }]);
  const body = "UNIQUE_FULL_BODY_MARKER " + "void Foo() { Work(); }\n".repeat(pressure ? 11000 : 500);
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest:
    { type: "function", id: "body-call", name: "read_file", arguments: { path: "Source/Foo.cpp" } } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "body-call",
    content: JSON.stringify({ ok: true, content: body, path: "Source/Foo.cpp", sha256: "a".repeat(64) }) }] }));
  const wc = new WorkingContext({ conversation: "pressure", lineage: "pressure", repository: "fixture", workspace: "fixture" });
  try {
    wc.captureReturned(history, () => true);
    wc.captureExposure(history, "previous-input", true);
    const tools = [{ name: "read_file", pluginIdentifier: "mcp/unreal-agent" }];
    const manager = new EvidenceManager(wc, tools);
    const { result, events } = await prepareFixture({ designGuidanceMode: "off", toolResultProjectionChars: 512 }, {
      workingContext: wc, evidenceManager: manager, workingHistory: history, visibleHistory: history,
      preProjectionHistory: history, postProjectionHistory: history,
      // No model execution; the exact deterministic tokenizer measures the full input.
      roundIndex: 1,
    });
    assert.equal(result.kind, "ready");
    const projections = events.filter(event => event.event === "working_context_projection" && event.reason === "measured_input_pressure");
    if (pressure) {
      assert.equal(projections.length, 1);
      assert.equal(projections[0].changed, true);
      assert.ok(result.modelInput.toString().length < history.toString().length / 2);
    } else {
      assert.equal(projections.length, 0);
      const delivered = result.modelInput.getMessagesArray().flatMap(message => message.getToolCallResults());
      assert.equal(JSON.parse(delivered.find(item => item.toolCallId === "body-call").content).content, body);
    }
    assert.equal(result.finalMeasurement.exact, true);
    assert.ok(result.finalMeasurement.remainingTokens >= 0);
  } finally { wc.close(); }
});

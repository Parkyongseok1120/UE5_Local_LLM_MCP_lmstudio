"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { ChatMessage } = require("@lmstudio/sdk");
const { classifyExecutionOutcome, DeliveryController } = require("../dist/delivery-controller");
const { ExecutionState } = require("../dist/execution-state");
const { classifyRunOutcome } = require("../scripts/eval-audit-pressure.cjs");

for (const [finishReason, reportText, expected] of [
  ["eosFound", "Report", "completed"], ["stopStringFound", "Report", "completed"],
  ["unknown", "Progress", "failed"], [undefined, "Progress", "failed"],
  ["generation_repetition_paused", "Progress", "failed"], ["toolCalls", "Progress", "failed"],
  ["eosFound", "", "no_answer"], ["maxPredictedTokensReached", "", "truncated"],
  ["maxPredictedTokensReached", "Cut off", "truncated"], ["userStopped", "Progress", "canceled"],
  ["eosFound", '<tool_call>{"name":"read_file","arguments":{}}</tool_call>', "failed"],
  ["eosFound", '<tool_call>{"name":"read_file","arguments":{}}', "failed"],
  ["eosFound", '```xml\n<tool_call>{"name":"read_file","arguments":{}}\n```', "completed"],
  ["eosFound", 'Example `<tool_call></tool_call>`\n<tool_call>{"name":"read_file"}', "failed"],
  ["eosFound", 'Syntax: `<tool_call>{`', "completed"],
]) test(`runtime and legacy evaluation agree on ${finishReason}: ${reportText || "empty"}`, () => {
  const actual = classifyExecutionOutcome({ finishReason, reportText });
  const evaluated = classifyRunOutcome({ visibleAnswer: reportText, trace: { roundFinishReasons: [finishReason] } });
  assert.deepEqual(evaluated, actual);
  assert.equal(actual.executionOutcome, expected);
  assert.equal(actual.reportCompleted, expected === "completed");
  assert.equal(actual.objectiveSatisfied, null);
  assert.equal(actual.taskCompleted, false);
});

test("current evaluation uses the final attempt, not concatenated progress or prior finalizations", () => {
  const terminalOutcome = classifyExecutionOutcome({ reportText: "", finishReason: "eosFound" });
  const result = classifyRunOutcome({ terminalOutcome, visibleAnswer: "Earlier progress",
    boundedFinalizations: [{ deliveryState: "complete", finishReason: "eosFound" }] });
  assert.equal(result, terminalOutcome);
  assert.equal(result.reportPresent, false);
  assert.equal(result.executionOutcome, "no_answer");
});

test("partial fallback is delivered without promoting a failed generation or unknown objective", () => {
  const result = classifyExecutionOutcome({ reportText: "", finishReason: "maxPredictedTokensReached",
    partialReportDelivered: true, reason: "reasoning_recovery_exhausted" });
  assert.equal(result.executionOutcome, "truncated");
  assert.equal(result.reason, "reasoning_recovery_exhausted");
  assert.equal(result.reportPresent, true);
  assert.equal(result.reportDelivered, true);
  assert.equal(result.reportCompleted, false);
  assert.equal(result.deliveryState, "partial");
  assert.equal(result.taskCompleted, false);
});

test("delivery owner resets prior attempts and finalizes an immutable result exactly once", () => {
  const owner = new DeliveryController();
  owner.observe({ messages: [ChatMessage.create("assistant", "Earlier report")], finishReason: "eosFound" });
  owner.beginRound();
  owner.stop("input_blocked");
  const result = owner.finish();
  assert.equal(result.reportPresent, false);
  assert.equal(result.reason, "input_blocked");
  assert.ok(Object.isFrozen(result));
  assert.equal(owner.finish({ failure: new Error("too late") }), result);
});

test("structured intent cannot disappear when the terminal owner records an ordinary round", () => {
  const owner = new DeliveryController();
  const message = ChatMessage.from({ role: "assistant", content: [
    { type: "text", text: "I will inspect A.h next." },
    { type: "toolCallRequest", toolCallRequest: { type: "function", id: "pending", name: "read_file", arguments: {} } },
  ] });
  owner.observe({ messages: [message], finishReason: "eosFound" });
  const result = owner.finish();
  assert.equal(result.reportCompleted, false);
  assert.equal(result.reportState, "unresolved_tool_intent");
});

test("external cancellation overrides a phase timeout while an external deadline stays timed out", () => {
  for (const externalTimeout of [false, true]) {
    const owner = new DeliveryController();
    owner.observe({ messages: [], finishReason: "userStopped" }, true);
    const result = owner.finish({ canceled: !externalTimeout, timedOut: externalTimeout });
    assert.equal(result.executionOutcome, externalTimeout ? "timed_out" : "canceled");
  }
});

test("cancellation, timeout and provider failure cannot be promoted by a report override", () => {
  for (const [extra, expected] of [ [{ canceled: true }, "canceled"], [{ timedOut: true }, "timed_out"],
    [{ failure: new Error("transport") }, "failed"] ]) {
    const outcome = classifyExecutionOutcome({ reportText: "Report", finishReason: "eosFound",
      delivery: { deliveryState: "complete" }, objectiveSatisfied: true, ...extra });
    assert.equal(outcome.executionOutcome, expected);
    assert.equal(outcome.reportCompleted, false);
    assert.equal(outcome.taskCompleted, false);
  }
});

test("execution owns admission while recovery retains a copied plan and lifetime retry count", () => {
  const state = new ExecutionState();
  const toolNames = ["read_file"], fingerprints = new Set(["already-returned"]);
  assert.equal(state.resolveInputPressure(true), "recover");
  assert.equal(state.resolveInputPressure(false), "finalize");
  state.startReplan({ reason: "raw_tool_intent", toolNames, outputReserve: 800,
    instruction: "Retry once", blockedFingerprints: fingerprints });
  toolNames.push("write_file"); fingerprints.clear();
  state.recovery.blockedFingerprints.clear();
  assert.equal(state.recovery.allowsTool("write_file"), false);
  assert.equal(state.recovery.blockedFingerprints.has("already-returned"), true);
  assert.equal(state.recovery.outputReserve, 800);
  assert.equal(state.recovery.planningAttempts, 1);
  state.finishReplan(); state.endRecovery();
  assert.equal(state.canStartReadRecovery, false);
  assert.equal(state.resolveInputPressure(true), "finalize");
  state.startReasoningRecovery();
  assert.equal(state.resolveInputPressure(true), "stop");
  state.finishReasoningRecovery();
  assert.equal(state.recovery.planningAttempts, 1);
  assert.throws(() => state.startReasoningRecovery(), /EXHAUSTED/);
});

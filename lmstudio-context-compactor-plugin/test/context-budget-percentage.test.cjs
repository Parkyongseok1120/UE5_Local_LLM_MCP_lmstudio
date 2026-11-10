"use strict";
// Revised plan 2026-11-10, stage 1 (D1-D4, D6): the context budget as shares of
// the loaded context length. Legacy modes must keep their numbers.
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { BudgetBroker, resolveSessionBudget, applySessionBudget, budgetShares } = require("../dist/budget-broker");
const { readConfig, percent } = require("../dist/execution-config");
const { shouldCompactContext } = require("../dist/context-manager");
const { sessionBudgetConfig } = require("../dist/session-budget");
const { fakeController, handlePredictionLoop } = require("./helpers/prediction-loop-fixture.cjs");

const configOf = overrides => readConfig({ getPluginConfig() { return { get: key => overrides[key] }; } });
const percentage = (extra = {}) => configOf({ contextManagementMode: "hybrid", contextBudgetMode: "percentage", ...extra });
function water(config, contextLength, inputTokens = 0, floor = 0) {
  const session = applySessionBudget(config, resolveSessionBudget(config, contextLength, "model"));
  return new BudgetBroker(session).watermarks({ contextLength, outputReserve: session.maxOutputReserve, inputTokens }, floor, true);
}

test("the default mode is legacy and saved Auto/Fixed keep their meaning", () => {
  assert.equal(configOf({}).contextBudgetMode, "legacy_auto");
  assert.equal(configOf({ workingInputScaling: "fixed" }).contextBudgetMode, "absolute");
  assert.equal(percentage().contextBudgetMode, "percentage");
  // Percentage is not combined with the legacy working context: it runs as legacy and says so.
  const combined = configOf({ contextManagementMode: "legacy", contextBudgetMode: "percentage" });
  assert.equal(combined.contextBudgetMode, "legacy_auto");
  assert.deepEqual(resolveSessionBudget(combined, 84224, "model").adjustments, ["percentage_unsupported_with_legacy_working_context"]);
  const legacy = configOf({ maxOutputReserve: 8192, safetyMarginTokens: 2048 });
  const budget = resolveSessionBudget(legacy, 84224, "model");
  assert.equal(budget.outputReserveTokens, 8192); assert.equal(budget.requested, null);
  assert.equal(applySessionBudget(legacy, budget).maxOutputReserve, 8192);
});

test("percentage mode resolves output and safety from the loaded context", () => {
  const config = percentage();
  const rows = [
    [8192, 2048, 512, ["output_floor", "safety_floor"]],
    [32768, 4096, 655, ["output_floor"]],
    [84224, 8422, 1684, []],
    [131072, 13107, 2621, []],
    [262144, 16384, 5242, ["output_cap"]],
  ];
  for (const [C, output, safety, adjustments] of rows) {
    const budget = resolveSessionBudget(config, C, "model");
    assert.deepEqual([budget.outputReserveTokens, budget.safetyMarginTokens, budget.adjustments], [output, safety, adjustments], `C=${C}`);
  }
  const budget = resolveSessionBudget(config, 131072, "model");
  assert.deepEqual([budget.requested.targetTokens, budget.requested.triggerTokens], [72089, 98304]);
  // Every reader of the output reserve follows the session value; a continuation limit that followed it still does.
  const applied = applySessionBudget(config, budget);
  assert.equal(applied.maxOutputReserve, 13107);
  assert.equal(applied.outputRecoveryMaxTokens, 13107);
  assert.equal(applied.sessionContextLength, 131072);
  assert.equal(config.maxOutputReserve, 8192, "the saved configuration is not rewritten");
  assert.equal(applySessionBudget(percentage({ outputRecoveryMaxTokens: 2000 }), budget).outputRecoveryMaxTokens, 2000);
});

test("percentage watermarks keep the requested shares and name every adjustment", () => {
  const config = percentage();
  const rows = [
    [32768, 22467, 18022, ["tool_result_reserve"]],
    [84224, 63168, 46323, []],
    [131072, 98304, 72089, []],
    [262144, 196608, 144179, []],
  ];
  for (const [C, high, low, adjustments] of rows) {
    const w = water(config, C);
    assert.deepEqual([w.effectiveHighWaterTokens, w.effectiveLowWaterTokens, w.budgetAdjustments], [high, low, adjustments], `C=${C}`);
    assert.equal(w.budgetMode, "percentage");
  }
  // 8,192 with the review tool schema (5,689 tokens measured): no call fits, and that is reported.
  assert.equal(water(config, 8192, 5689).nextActionFit, false);
  // Mandatory input above the target is named, not hidden.
  const floor = water(config, 84224, 50000, 50000);
  assert.equal(floor.targetUnreachable, true);
  assert.ok(floor.budgetAdjustments.includes("mandatory_floor"));
  assert.equal(floor.effectiveLowWaterTokens, 56000, "working space above the floor");
});

test("at the recorded 84,224 context the defaults land beside the legacy numbers", () => {
  const legacy = configOf({ contextManagementMode: "hybrid", workingInputTargetTokens: 46000, workingInputTriggerTokens: 56000,
    maxOutputReserve: 8192, safetyMarginTokens: 2048 });
  const before = water(legacy, 84224);
  assert.deepEqual([before.effectiveHighWaterTokens, before.effectiveLowWaterTokens, before.budgetMode], [63626, 47719, "legacy_auto"]);
  const after = water(percentage(), 84224);
  assert.equal(before.effectiveHighWaterTokens - after.effectiveHighWaterTokens, 458);
  assert.equal(before.effectiveLowWaterTokens - after.effectiveLowWaterTokens, 1396);
});

test("shares are validated without rewriting what the user saved", () => {
  assert.equal(percent("55.26", 55, 10, 90), 55.3);
  assert.equal(percent("abc", 55, 10, 90), 55);
  assert.equal(percent(150, 55, 10, 90), 90);
  assert.equal(percent(-3, 55, 10, 90), 10);
  const inverted = percentage({ contextTargetPercent: 80, contextTriggerPercent: 60 });
  assert.equal(inverted.contextTargetPercent, 80);
  assert.deepEqual(budgetShares(inverted), { target: 55, trigger: 75, invalid: true });
  assert.ok(resolveSessionBudget(inverted, 84224, "model").adjustments.includes("invalid_target_trigger_defaults"));
  assert.equal(water(percentage({ contextTargetPercent: 40.5, contextTriggerPercent: 70 }), 84224).requestedTargetTokens, 34110);
});

test("the remaining-token threshold is not a second trigger in percentage mode", () => {
  // 84,224 context: input 58,000 is under high water; about 4,500 tokens remain after the legacy reserve.
  const measured = config => ({ exact: true, contextLength: 84224, outputReserve: config.maxOutputReserve, inputTokens: 58000, messageCount: 4,
    remainingTokens: 84224 - 58000 - config.maxOutputReserve - config.safetyMarginTokens });
  const legacy = configOf({ contextManagementMode: "hybrid", maxOutputReserve: 16384, softRemainingTokens: 9000 });
  assert.ok(measured(legacy).remainingTokens < 9000);
  assert.equal(shouldCompactContext(measured(legacy), legacy, true), true, "legacy keeps its soft threshold");
  const base = percentage({ softRemainingTokens: 1_000_000 });
  const config = applySessionBudget(base, resolveSessionBudget(base, 84224, "model"));
  assert.equal(shouldCompactContext(measured(config), config, true), false);
  assert.equal(shouldCompactContext({ ...measured(config), inputTokens: 64000 }, config, true), true, "above the trigger share");
});

test("the session budget event reports the fallback source and previews percentage in legacy mode", async () => {
  const events = [];
  const legacy = configOf({ contextManagementMode: "hybrid", showDebugInfo: true });
  const out = await sessionBudgetConfig(legacy, { async getContextLength() { throw new Error("offline"); } }, value => events.push(value));
  assert.equal(events[0].event, "context_budget");
  assert.equal(events[0].contextLengthSource, "configured_fallback");
  assert.equal(events[0].contextLength, legacy.assumedContextLength);
  assert.equal(events[0].percentagePreview.requested.targetPercent, 55);
  assert.equal(out.maxOutputReserve, legacy.maxOutputReserve);
});

test("a percentage execution caps the model call with the resolved output reserve", async () => {
  const calls = [];
  const model = { identifier: "percent-model", async getContextLength() { return 32768; },
    async applyPromptTemplate(chat) { return chat.toString(); }, async countTokens(text) { return Math.ceil(String(text).length / 4); },
    async act(_chat, _tools, options) {
      calls.push(options.maxTokens);
      options.onMessage(ChatMessage.create("assistant", "answer"));
      options.onPredictionCompleted?.({ stats: { stopReason: "eosFound" } });
    } };
  const ctl = fakeController(Chat.from([{ role: "user", content: "hello" }]), model,
    { contextManagementMode: "deterministic", contextBudgetMode: "percentage", maxOutputReserve: 8192 });
  await handlePredictionLoop(ctl);
  assert.deepEqual(calls, [4096], "10% of 32,768 is 3,276; the floor makes it 4,096, not the configured 8,192");
  const event = ctl.debugValues.find(value => value.event === "context_budget");
  assert.equal(event.mode, "percentage");
  assert.deepEqual(event.adjustments, ["output_floor"]);
  const measurement = ctl.debugValues.find(value => value.event === "direct_context_measurement");
  assert.equal(measurement.outputReserve, 4096);
});

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

test("an explicit continuation limit equal to the old reserve is not treated as automatic", () => {
  const explicit = percentage({ maxOutputReserve: 8192, outputRecoveryMaxTokens: 8192 });
  for (const contextLength of [32768, 131072]) {
    const applied = applySessionBudget(explicit, resolveSessionBudget(explicit, contextLength, "model"));
    assert.equal(applied.outputRecoveryMaxTokens, 8192, `explicit cap at C=${contextLength}`);
  }
  const automatic = percentage({ maxOutputReserve: 8192, outputRecoveryMaxTokens: 0 });
  const applied = applySessionBudget(automatic, resolveSessionBudget(automatic, 131072, "model"));
  assert.equal(applied.outputRecoveryMaxTokens, 13107);
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
  assert.equal(percent(150, 55, 10, 90), 55);
  assert.equal(percent(-3, 55, 10, 90), 55);
  assert.equal(percent(100, 55, 10, 90), 55);
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

test("percentage report continuation preserves explicit caps and follows automatic reserves", async () => {
  for (const [savedLimit, expected] of [[8192, 8192], [0, 13107]]) {
    const calls = [];
    const model = { identifier: "percentage-continuation", async getContextLength() { return 131072; },
      async applyPromptTemplate(chat) { return chat.toString(); }, async countTokens() { return 100; },
      async act(_chat, tools, options) {
        calls.push(options.maxTokens);
        if (calls.length > 1) assert.deepEqual(tools, [], "report continuation must be tool-free");
        options.onPredictionCompleted({ stats: { stopReason: calls.length === 1 ? "maxPredictedTokensReached" : "eosFound",
          promptTokensCount: 100, predictedTokensCount: calls.length === 1 ? options.maxTokens : 20 } });
        options.onMessage(ChatMessage.create("assistant", calls.length === 1 ? "Partial report." : "Completed report."));
      } };
    const ctl = fakeController(Chat.from([{ role: "user", content: "Write a report." }]), model,
      { contextManagementMode: "deterministic", contextBudgetMode: "percentage", maxOutputReserve: 8192,
        outputRecoveryMode: "on", outputRecoveryMaxTokens: savedLimit, reportReviewMode: "off" });
    await handlePredictionLoop(ctl);
    assert.deepEqual(calls, [13107, expected], `saved continuation limit ${savedLimit}`);
    assert.equal(ctl.debugValues.find(value => value.event === "output_recovery").completionAccepted, true);
  }
});

test("percentage reasoning recovery grows from the session reserve and ignores the report continuation cap", async () => {
  const calls = [];
  const model = { identifier: "percentage-reasoning", async getContextLength() { return 131072; },
    async applyPromptTemplate(chat) { return chat.toString(); }, async countTokens() { return 100; },
    async act(_chat, _tools, options) {
      calls.push(options.maxTokens);
      if (calls.length === 1) {
        options.onPredictionFragment({ roundIndex: 0, content: "UNFINISHED-THOUGHT", reasoningType: "reasoning",
          tokensCount: options.maxTokens, containsDrafted: false, isStructural: false });
        options.onPredictionCompleted({ stats: { stopReason: "maxPredictedTokensReached", promptTokensCount: 100,
          predictedTokensCount: options.maxTokens } });
        return;
      }
      options.onPredictionCompleted({ stats: { stopReason: "eosFound", promptTokensCount: 100, predictedTokensCount: 20 } });
      options.onMessage(ChatMessage.create("assistant", "Which input binding should I use?"));
    } };
  const ctl = fakeController(Chat.from([{ role: "user", content: "Make a small change." }]), model,
    { contextManagementMode: "deterministic", contextBudgetMode: "percentage", maxOutputReserve: 8192,
      reasoningRecoveryMode: "on", outputRecoveryMode: "off", outputRecoveryMaxTokens: 256, reportReviewMode: "off" });
  await handlePredictionLoop(ctl);
  assert.deepEqual(calls, [13107, 26214]);
  assert.deepEqual(ctl.debugValues.filter(value => value.event === "direct_round_observation").map(value => value.callPurpose),
    ["research", "reasoning_recovery"]);
});

test("percentage bounded finalization takes the lower of the session and audit caps", async () => {
  for (const [contextLength, expected] of [[32768, 4096], [131072, 6000]]) {
    const calls = [];
    const model = { identifier: "percentage-bounded", async getContextLength() { return contextLength; },
      async applyPromptTemplate(chat) { return chat.toString(); }, async countTokens() { return 100; },
      async act(_chat, tools, options) {
        calls.push(options.maxTokens);
        if (calls.length === 1) {
          options.onPredictionCompleted({ stats: { stopReason: "maxPredictedTokensReached", promptTokensCount: 100,
            predictedTokensCount: options.maxTokens } });
          return;
        }
        assert.deepEqual(tools, []);
        options.onMessage(ChatMessage.create("assistant", "Partial audit report: evidence is incomplete."));
        options.onPredictionCompleted({ stats: { stopReason: "eosFound", promptTokensCount: 100, predictedTokensCount: 20 } });
      } };
    const ctl = fakeController(Chat.from([{ role: "user", content: "Audit the project." }]), model,
      { contextManagementMode: "deterministic", contextBudgetMode: "percentage", auditCompletionMode: "bounded",
        auditFinalMaxTokens: 6000, reportReviewMode: "off" },
      [{ name: "read_file", pluginIdentifier: "mcp/unreal-agent", description: "Read source", parametersJsonSchema: { type: "object" } }]);
    await handlePredictionLoop(ctl);
    assert.deepEqual(calls, [contextLength === 32768 ? 4096 : 13107, expected]);
    assert.equal(ctl.debugValues.find(e => e.event === "bounded_audit_finalization").toolCount, 0);
  }
});

test("a smaller measured context limits dispatch and the next execution re-resolves the reserve", async () => {
  let contextReads = 0;
  const calls = [];
  const model = { identifier: "percentage-context-change", async getContextLength() { return ++contextReads === 1 ? 131072 : 32768; },
    async applyPromptTemplate(chat) { return chat.toString(); }, async countTokens() { return 22000; },
    async act(_chat, _tools, options) {
      calls.push(options.maxTokens);
      assert.ok(22000 + options.maxTokens + 2621 <= 32768, "dispatch obeys the newly measured limit");
      options.onMessage(ChatMessage.create("assistant", "Report."));
      options.onPredictionCompleted({ stats: { stopReason: "eosFound", promptTokensCount: 22000, predictedTokensCount: 20 } });
    } };
  const settings = { contextManagementMode: "deterministic", contextBudgetMode: "percentage", reportReviewMode: "off", designGuidanceMode: "off" };
  const ctl = fakeController(Chat.from([{ role: "user", content: "Report available evidence." }]), model, settings);
  await handlePredictionLoop(ctl);
  assert.ok(calls.length > 0);
  const measurement = ctl.debugValues.find(e => e.event === "direct_context_measurement");
  assert.deepEqual(measurement.workingContext.contextLengthChanged, { resolved: 131072, measured: 32768 });
  const next = await sessionBudgetConfig(configOf(settings), model, () => {});
  assert.equal(next.maxOutputReserve, 4096);
  assert.equal(next.safetyMarginTokens, 655);
});

// D5: what returns after a compaction is counted before the compaction.
const { WorkingContext } = require("../src/working-context.js");
const { ContextManager, createInputAssembler } = require("../dist/context-manager");
const { EvidenceManager } = require("../dist/evidence-manager");
const { prepareRoundInput } = require("../dist/round-input");
const charSource = contextLength => ({ async getContextLength() { return contextLength; },
  async applyPromptTemplate(chat) { return chat.toString(); }, async countTokens(text) { return Math.ceil(String(text).length / 4); } });
function prose(tokens, parts) {
  const history = Chat.from([{ role: "user", content: "Review the replication contracts" }]);
  for (let i = 0; i < parts; i++)
    history.append(ChatMessage.from({ role: "assistant", content: `progress ${i} ${"p".repeat(Math.floor(tokens * 4 / parts))}` }));
  return history;
}
async function round(config, history, guidanceRetention) {
  const events = [];
  const store = new WorkingContext({ conversation: "percent", lineage: `percent-${Math.random()}`, repository: "fixture", workspace: "fixture" });
  const tools = [{ name: "read_file", pluginIdentifier: "mcp/unreal-agent" }];
  try {
    const result = await prepareRoundInput({
      ctl: { debug(event) { events.push(event); }, guardAbort() {} }, config, tokenSource: charSource(32768),
      contextManager: new ContextManager(config, new BudgetBroker(config)),
      evidenceManager: new EvidenceManager(store, tools), workingContext: store,
      workingHistory: history, visibleHistory: history, preProjectionHistory: history, postProjectionHistory: history,
      roundTools: tools, modelTools: tools, roundScopeInstructions: [], scopeInstructions: [],
      roundOutputReserve: config.maxOutputReserve, activeNote: null, noteEnabled: false, historicalAvailabilityLedger: [],
      semanticSummaryCooldownUntilRound: 99, objectiveFingerprint: "percent", finalizing: false, boundedAudit: true,
      researchRecoveryEpisodeStarted: false, finalizationTrigger: null, projectionApplied: false, executionId: "percent",
      modelInputId: "percent:input-1", roundIndex: 3, guidanceRetention });
    return { result, low: events.filter(event => event.event === "context_low_water").at(-1),
      guidance: events.find(event => event.event === "design_guidance_input") };
  } finally { store.close(); }
}
const kept = tokens => ({ kept: { ids: ["fixture"], instruction: `[Optional design reference]\n${"g".repeat(2500 * 4)}`,
  selectionScope: "unscoped", ...(tokens === undefined ? {} : { tokens }) } });
const guided = extra => {
  const base = configOf({ contextManagementMode: "hybrid", showDebugInfo: true, designGuidanceMode: "core", designGuidanceMaxTokens: 6000,
    toolResultProjectionChars: 512, projectEngine: "unreal", ...extra });
  return applySessionBudget(base, resolveSessionBudget(base, 32768, "model"));
};

test("percentage compaction leaves room for the guidance that returns, so the round ends at the target", async () => {
  const config = guided({ contextBudgetMode: "percentage" });
  const w = new BudgetBroker(config).watermarks({ contextLength: 32768, outputReserve: config.maxOutputReserve, inputTokens: 0 }, 0, true, 2048);
  const reserved = await round(config, prose(w.effectiveHighWaterTokens + 2000, 40), kept(2500));
  assert.equal(reserved.result.kind, "ready");
  assert.equal(reserved.low.reservedOptionalTokens, 2500);
  assert.equal(reserved.low.compactionTargetTokens, reserved.low.effectiveLowWaterTokens - 2500);
  assert.ok(reserved.low.finalExactInputTokens <= reserved.low.compactionTargetTokens);
  assert.equal(reserved.guidance.reason, "sticky_kept");
  assert.ok(reserved.result.finalMeasurement.inputTokens <= reserved.low.effectiveLowWaterTokens, "target share met with the guidance in");
  // An unknown size cannot be reserved: reject the optional reference if it would undo this compaction.
  const unreserved = await round(config, prose(w.effectiveHighWaterTokens + 2000, 40), kept(undefined));
  assert.equal(unreserved.low.reservedOptionalTokens, 0);
  assert.equal(unreserved.guidance.reason, "sticky_dropped");
  assert.equal(unreserved.guidance.appliedLowWaterTokens, unreserved.low.effectiveLowWaterTokens);
  assert.ok(unreserved.result.finalMeasurement.inputTokens <= unreserved.low.effectiveLowWaterTokens);
});

test("the reservation neither starts a cut nor changes the legacy modes", async () => {
  const config = guided({ contextBudgetMode: "percentage" });
  const w = new BudgetBroker(config).watermarks({ contextLength: 32768, outputReserve: config.maxOutputReserve, inputTokens: 0 }, 0, true, 2048);
  // Under the trigger: retained guidance alone does not compact, whatever its size.
  const calm = await round(config, prose(w.effectiveHighWaterTokens - 3000, 40), kept(2500));
  assert.equal(calm.low.selectedCandidate, "unchanged");
  const legacy = guided({});
  const lw = new BudgetBroker(legacy).watermarks({ contextLength: 32768, outputReserve: legacy.maxOutputReserve, inputTokens: 0 }, 0, true, 2048);
  const before = await round(legacy, prose(lw.effectiveHighWaterTokens + 2000, 40), kept(2500));
  assert.equal(before.low.reservedOptionalTokens, 0);
  assert.equal(before.low.compactionTargetTokens, before.low.effectiveLowWaterTokens);
});

test("a reservation takes at most half of the room above the mandatory floor", async () => {
  const config = guided({ contextBudgetMode: "percentage" });
  const assemble = createInputAssembler({ tokenSource: charSource(32768), config, roundTools: [], roundScopeInstructions: [],
    getRoundOutputReserve: () => config.maxOutputReserve, modelInputId: "percent", noteEnabled: false, getNote: () => null,
    historicalAvailabilityLedger: [] });
  const manager = new ContextManager(config, new BudgetBroker(config));
  const result = await manager.enforceLowWater(prose(26000, 40), assemble, { hasReadTools: false, reservedOptionalTokens: 1_000_000 });
  const t = result.telemetry;
  assert.equal(t.requestedOptionalTokens, 1_000_000);
  assert.equal(t.reservedOptionalTokens, Math.floor((t.effectiveLowWaterTokens - result.floorMeasurement.inputTokens) / 2));
  assert.ok(t.finalExactInputTokens <= t.compactionTargetTokens);
  assert.notEqual(t.selectedCandidate, "mandatory_floor");
});

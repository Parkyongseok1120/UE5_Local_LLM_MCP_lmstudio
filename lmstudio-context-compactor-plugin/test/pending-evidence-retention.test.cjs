"use strict";

// A result returned just before a cancel is still awaiting its first consumer
// when the user says "continue". Compaction must retain that exchange instead
// of failing the resumed execution with CONTEXT_PENDING_EVIDENCE_OMITTED.
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { WorkingContext } = require("../src/working-context.js");
const { ContextManager, createInputAssembler } = require("../dist/context-manager");
const { BudgetBroker } = require("../dist/budget-broker");
const { readConfig } = require("../dist/execution-config");

function fixture(store, required) {
  const config = readConfig({ getPluginConfig() { return { get: key => ({ contextManagementMode: "deterministic",
    workingInputTargetTokens: 4000, workingInputTriggerTokens: 6500, workingInputScaling: "fixed",
    maxOutputReserve: 2048, safetyMarginTokens: 512 })[key] }; } });
  const source = { async getContextLength() { return 32768; },
    async applyPromptTemplate(chat, opts = {}) { return chat.toString() + JSON.stringify(opts.toolDefinitions || []); },
    async countTokens(text) { return Math.ceil(text.length / 4); } };
  const assemble = createInputAssembler({ tokenSource: source, config, roundTools: [], roundScopeInstructions: [],
    getRoundOutputReserve: () => 2048, modelInputId: "resume", noteEnabled: false, getNote: () => null,
    historicalAvailabilityLedger: [] });
  const manager = new ContextManager(config, new BudgetBroker(config), {},
    required ? history => store.pendingResultIndexes(history) : undefined);
  return { manager, assemble };
}

function interruptedHistory() {
  const history = Chat.from([{ role: "system", content: "invariant" },
    { role: "user", content: "Audit the renderer lifecycle." },
    { role: "assistant", content: `Earlier analysis ${"old ".repeat(9000)}` }]);
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
    type: "function", id: "r1", name: "read_file", arguments: { path: "Renderer.cpp" } } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "r1",
    content: JSON.stringify({ ok: true, kind: "workspace_file_observation", path: "Renderer.cpp",
      text: `PENDING_FACT ${"y".repeat(3000)}` }) }] }));
  history.append("user", "계속해서 진행해줘");
  return history;
}

function storeWithPendingResult(history) {
  const store = new WorkingContext({ conversation: "resume", lineage: "resume", workspace: "w", repository: "r" });
  assert.equal(store.captureReturned(history, () => true), 1);
  assert.equal(store.pendingResultIndexes(history).length, 1);
  return store;
}

test("compaction retains a pending result from the interrupted turn", async () => {
  const history = interruptedHistory();
  const store = storeWithPendingResult(history);
  const { manager, assemble } = fixture(store, true);
  const result = await manager.enforceLowWater(history, assemble, { hasReadTools: false, force: true });
  assert.equal(result.changed, true);
  assert.match(result.history.toString(), /PENDING_FACT/);
  assert.doesNotMatch(result.history.toString(), /(?:old ){1000}/);
  assert.doesNotThrow(() => store.captureExposure(result.history, "resume", false));
  store.close();
});

test("without pending retention the omission is detected and can be released to the archive", async () => {
  const history = interruptedHistory();
  const store = storeWithPendingResult(history);
  const { manager, assemble } = fixture(store, false);
  const result = await manager.enforceLowWater(history, assemble, { hasReadTools: false, force: true });
  assert.doesNotMatch(result.history.toString(), /PENDING_FACT/);
  assert.throws(() => store.captureExposure(result.history, "resume", false), /CONTEXT_PENDING_EVIDENCE_OMITTED/);
  assert.equal(store.releaseOmittedPending(result.history).length, 1);
  assert.doesNotThrow(() => store.captureExposure(result.history, "resume", false));
  assert.equal(store.refsValid(), true);
  store.close();
});

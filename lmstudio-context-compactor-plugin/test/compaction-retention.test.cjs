"use strict";

// Compaction keeps the most recent complete turns and the prior answer, and
// the optional semantic summary call is off unless explicitly enabled.
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat } = require("@lmstudio/sdk");
const core = require("../src/direct-compaction-core.js");
const { ASSISTANT_MARKER } = require("../src/continuity-assistant-evidence.js");
const { ContextManager, createInputAssembler } = require("../dist/context-manager");
const { BudgetBroker } = require("../dist/budget-broker");
const { readConfig } = require("../dist/execution-config");

const message = (role, text, index) => ({ role, text, index, toolRequests: [], toolResults: [] });

function fixture(overrides = {}) {
  const config = readConfig({ getPluginConfig() { return { get: key => ({ contextManagementMode: "deterministic",
    workingInputTargetTokens: 6000, workingInputTriggerTokens: 8000, workingInputScaling: "fixed",
    maxOutputReserve: 2048, safetyMarginTokens: 512, ...overrides })[key] }; } });
  const source = { async getContextLength() { return 32768; },
    async applyPromptTemplate(chat) { return chat.toString(); },
    async countTokens(text) { return Math.ceil(text.length / 4); } };
  const assemble = createInputAssembler({ tokenSource: source, config, roundTools: [], roundScopeInstructions: [],
    getRoundOutputReserve: () => 2048, modelInputId: "retention", noteEnabled: false, getNote: () => null,
    historicalAvailabilityLedger: [] });
  return { config, assemble, manager: new ContextManager(config, new BudgetBroker(config)) };
}

test("compaction first tries keeping the most recent complete turns verbatim", async () => {
  const { manager, assemble } = fixture();
  const history = Chat.from([{ role: "system", content: "invariant" },
    { role: "user", content: "first request" }, { role: "assistant", content: `FIRST_ANSWER ${"a ".repeat(20000)}` },
    { role: "user", content: "second request" }, { role: "assistant", content: "SECOND_ANSWER kept verbatim" },
    { role: "user", content: "third request" }, { role: "assistant", content: "THIRD_ANSWER kept verbatim" },
    { role: "user", content: "계속해줘" }]);
  const result = await manager.enforceLowWater(history, assemble, { hasReadTools: false });
  assert.equal(result.success, true);
  assert.match(result.telemetry.selectedCandidate, /^recent_turns_/);
  assert.match(result.history.toString(), /SECOND_ANSWER kept verbatim/);
  assert.match(result.history.toString(), /THIRD_ANSWER kept verbatim/);
  assert.doesNotMatch(result.history.toString(), /(?:a ){5000}/);
});

test("the mandatory floor keeps a bounded excerpt of the previous answer in assistant evidence", () => {
  const answer = `ANSWER_HEAD ${"detail ".repeat(2000)} ANSWER_TAIL`;
  const result = core.buildCheckpoint([
    message("user", "Audit the harness.", 0), message("assistant", answer, 1), message("user", "계속해서 진행해줘", 2),
  ], { maxCurrentTurnMessages: 0, checkpointPolicy: "mandatory", maxCheckpointChars: 22000 });
  assert.ok(result.assistantCheckpoint.startsWith(ASSISTANT_MARKER));
  const evidence = JSON.parse(result.assistantCheckpoint.slice(ASSISTANT_MARKER.length + 1));
  assert.match(evidence.previousResponse.text, /^ANSWER_HEAD/);
  assert.match(evidence.previousResponse.text, /ANSWER_TAIL$/);
  assert.ok(evidence.previousResponse.text.length <= 3000);
  assert.equal(evidence.previousResponse.final, true);
  // Assistant-authored prose stays out of the system checkpoint.
  assert.doesNotMatch(result.checkpoint, /ANSWER_HEAD/);
});

test("an interrupted turn's last visible progress is kept when it ended in tool activity", () => {
  const result = core.buildCheckpoint([
    message("user", "Audit the harness.", 0),
    message("assistant", "PROGRESS: three files reviewed so far.", 1),
    { role: "assistant", text: "", index: 2, toolRequests: [{ id: "r", name: "read_file", arguments: {} }], toolResults: [] },
    { role: "tool", text: "", index: 3, toolRequests: [], toolResults: [{ toolCallId: "r", content: "{}" }] },
    message("user", "이어서 해줘", 4),
  ], { maxCurrentTurnMessages: 0, checkpointPolicy: "mandatory" });
  const evidence = JSON.parse(result.assistantCheckpoint.slice(ASSISTANT_MARKER.length + 1));
  assert.match(evidence.previousResponse.text, /three files reviewed/);
  assert.equal(evidence.previousResponse.final, false);
});

test("the semantic handoff call is off unless explicitly enabled", () => {
  const defaults = readConfig({ getPluginConfig() { return { get: () => undefined }; } });
  assert.equal(defaults.semanticHandoff, false);
  assert.equal(defaults.contextManagementMode, "hybrid");
  const enabled = readConfig({ getPluginConfig() { return { get: key => key === "semanticHandoff" ? true : undefined }; } });
  assert.equal(enabled.semanticHandoff, true);
});

test("volatile runtime data sits before the latest user message, keeping earlier turns' prefix stable", () => {
  const { composeModelHistory } = require("../dist/context-manager");
  const config = readConfig({ getPluginConfig() { return { get: () => undefined }; } });
  const history = Chat.from([{ role: "system", content: "stable system" },
    { role: "user", content: "first request" }, { role: "assistant", content: "first answer" },
    { role: "user", content: "second request" }]);
  const render = facts => composeModelHistory(history, null, config, ["scope"], false, undefined, facts).history
    .getMessagesArray().map(m => `${m.getRole()}:${m.getText()}`);
  const a = render(["Runtime read-result budget facts: A"]), b = render(["Runtime read-result budget facts: B"]);
  assert.deepEqual(a.slice(0, 3), b.slice(0, 3));
  assert.deepEqual(a.slice(0, 3), ["system:stable system\n\nscope", "user:first request", "assistant:first answer"]);
  assert.equal(a[3], "assistant:Runtime read-result budget facts: A");
  assert.equal(a.at(-1), "user:second request");
  assert.equal(history.getMessagesArray().length, 4, "runtime facts are never written into the source history");
});

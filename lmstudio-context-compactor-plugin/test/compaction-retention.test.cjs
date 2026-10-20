"use strict";

// Compaction keeps the most recent complete turns and the prior answer, and
// the optional semantic summary call is off unless explicitly enabled.
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
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

// Plan W-P0/W-P2 (F1): refs gain a row per completed call. Once the current
// turn has complete tool exchanges the data slot sits before the newest one,
// so earlier exchanges stay in the unchanged prefix of the next round, and the
// input still ends with tool results (a trailing assistant message would be
// rendered as an unfinished answer for the model to continue).
test("per-round data sits before the newest complete tool exchange, keeping earlier ones in a stable prefix", () => {
  const { composeModelHistory } = require("../dist/context-manager");
  const config = readConfig({ getPluginConfig() { return { get: () => undefined }; } });
  const request = n => ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
    type: "function", id: `call_${n}`, name: "read_file_range", arguments: { path: `Source/File${n}.cpp` } } }] });
  const result = n => ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: `call_${n}`,
    content: JSON.stringify({ ok: true, path: `Source/File${n}.cpp`, text: `body ${n}` }) }] });
  // system -> previous turn -> latest user -> (assistant tool request -> tool result) x rounds
  const historyAt = (rounds, pending = false) => {
    const history = Chat.from([{ role: "system", content: "stable system" },
      { role: "user", content: "previous request" }, { role: "assistant", content: "previous answer" },
      { role: "user", content: "latest request" }]);
    for (let n = 1; n <= rounds; n++) { history.append(request(n)); history.append(result(n)); }
    if (pending) history.append(request(rounds + 1));
    return history;
  };
  const shape = m => JSON.stringify([m.getRole(), m.getText(), m.getToolCallRequests(), m.getToolCallResults()]);
  const compose = (rounds, pending) => composeModelHistory(historyAt(rounds, pending), null, config, ["scope"], true)
    .history.getMessagesArray();
  const isRefs = m => m.getText().startsWith("[Available tool-call refs]");
  for (const k of [1, 2, 3]) {
    const roundK = compose(k), roundNext = compose(k + 1);
    const refsAt = roundK.findIndex(isRefs);
    assert.ok(refsAt > 0 && roundNext.some(isRefs), "refs are rendered in both rounds");
    assert.equal(roundK[refsAt].getRole(), "assistant");
    const after = roundK.slice(refsAt + 1).filter(m => !(m.getRole() === "assistant" && !m.getToolCallRequests().length));
    assert.deepEqual(after.map(m => m.getRole()), ["assistant", "tool"], "only the newest exchange follows the data slot");
    assert.equal(roundK.at(-1).getRole(), "tool", "the input ends with a tool result, never with assistant data");
    assert.deepEqual(roundNext.slice(0, refsAt).map(shape), roundK.slice(0, refsAt).map(shape),
      "system, earlier turns and every exchange before round k's newest one are an unchanged prefix of round k+1");
  }
  // Before the turn has an exchange, and while a request awaits its result,
  // the slot stays before the latest user message (pairs are never split).
  for (const messages of [compose(0), compose(1, true)]) {
    const latestUser = messages.findIndex(m => m.isUserMessage() && m.getText() === "latest request");
    assert.equal(messages.findIndex(isRefs) === -1 || messages.findIndex(isRefs) < latestUser, true);
  }
  assert.equal(historyAt(2).getMessagesArray().length, 8, "data is never written into the source history");
});

// Measured on the hybrid-attention model (plan W-P1): the server reused the
// prefix only when each input extended the previous one exactly. With the
// execution's shown blocks, every round's input is a prefix of the next.
test("same-anchor retries append new observations without rewriting already shown blocks", () => {
  const { composeModelHistory } = require("../dist/context-manager");
  const config = readConfig({ getPluginConfig() { return { get: () => undefined }; } });
  const history = Chat.from([{ role: "system", content: "system" }, { role: "user", content: "request" }]);
  const shown = new Map();
  const compose = facts => composeModelHistory(history, null, config, ["scope"], false, undefined, facts, shown);
  const first = compose(["observation A"]);
  shown.set(first.dataBlock.anchor, first.dataBlock.parts);
  const retry = compose(["observation B"]);
  assert.deepEqual(shown.get("user"), ["observation A"], "candidate assembly cannot commit state");
  assert.deepEqual(retry.dataBlock.parts, ["observation A", "observation B"]);
  assert.deepEqual(retry.history.getMessagesArray().map(m => m.getText()),
    ["system\n\nscope", "observation A", "observation B", "request"]);
  shown.set(retry.dataBlock.anchor, retry.dataBlock.parts);
  const repeat = compose(["observation B"]);
  assert.equal(repeat.dataBlock, undefined, "unchanged retries do not accumulate data");
  assert.equal(repeat.history.toString(), retry.history.toString());
  assert.equal(history.length, 2);
});

test("with shown data blocks each round's model input is an exact prefix of the next", () => {
  const { composeModelHistory } = require("../dist/context-manager");
  const config = readConfig({ getPluginConfig() { return { get: () => undefined }; } });
  const request = n => ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
    type: "function", id: `call_${n}`, name: "read_file_range", arguments: { path: `Source/File${n}.cpp` } } }] });
  const result = n => ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: `call_${n}`,
    content: JSON.stringify({ ok: true, text: `body ${n}` }) }] });
  const history = Chat.from([{ role: "system", content: "stable system" },
    { role: "user", content: "previous request" }, { role: "assistant", content: "previous answer" },
    { role: "user", content: "latest request" }]);
  const shape = m => JSON.stringify([m.getRole(), m.getText(), m.getToolCallRequests(), m.getToolCallResults()]);
  const shown = new Map();
  let previous = null;
  for (let round = 0; round <= 4; round++) {
    if (round > 0) { history.append(request(round)); history.append(result(round)); }
    const facts = [`Runtime read-result budget facts: ${round < 3 ? "ample" : "low"}`];
    const composed = composeModelHistory(history, null, config, ["scope"], true, undefined, facts, shown);
    const messages = composed.history.getMessagesArray();
    assert.equal(messages.at(-1).getRole(), round ? "tool" : "user", "the input never ends with assistant data");
    if (previous) assert.deepEqual(messages.slice(0, previous.length).map(shape), previous.map(shape),
      `round ${round - 1}'s input is an exact prefix of round ${round}'s`);
    if (composed.dataBlock) shown.set(composed.dataBlock.anchor, composed.dataBlock.parts);
    previous = messages;
  }
  const dataTexts = previous.filter(m => m.getRole() === "assistant" && !m.getToolCallRequests().length
    && m.getText() !== "previous answer").map(m => m.getText());
  // Each completed call's ref row and each distinct fact text is shown exactly once.
  for (let n = 1; n <= 3; n++) assert.equal(dataTexts.join("\n").split(`tool-call:call_${n} `).length - 1, 1);
  assert.equal(dataTexts.filter(text => text === "Runtime read-result budget facts: ample").length, 1);
  assert.equal(dataTexts.filter(text => text === "Runtime read-result budget facts: low").length, 1);
  assert.equal(history.getMessagesArray().length, 12, "data blocks are never written into the source history");
});

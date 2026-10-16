"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { createPredictionLoopHandler } = require("../dist/prediction-loop.js");
const { ContinuityNoteStore } = require("../dist/continuity-model-notes.js");

function controller(history, model, directory, config = {}, abort = new AbortController()) {
  const blocks = [], debug = [], statuses = [];
  const values = { contextManagementMode: "hybrid", semanticHandoff: false, designGuidanceMode: "off",
    outputRecoveryMode: "off", outputRecoverySeconds: 1, maxOutputReserve: 8192,
    workingInputTargetTokens: 40000, workingInputTriggerTokens: 56000, showDebugInfo: true, ...config };
  return { abortSignal: abort.signal, guardAbort() { abort.signal.throwIfAborted(); },
    getPluginConfig() { return { get: key => values[key] }; }, getWorkingDirectory() { return directory; },
    async pullHistory() { return history; }, async tokenSource() { return model; },
    async startToolUseSession() { return { tools: [], [Symbol.dispose]() {} }; },
    async requestConfirmToolCall() { return { type: "deny" }; }, debug(value) { debug.push(value); },
    createStatus(state) { statuses.push(state); return { setText() {}, setState() {}, remove() {} }; },
    createToolStatus() { return { setStatus() {} }; },
    createContentBlock(options = {}) { const block = { options, text: "" }; blocks.push(block);
      return { appendText(text) { block.text += text; }, appendToolRequest() {}, replaceToolRequest() {}, appendToolResult() {},
        setStyle(style) { block.options.style = style; } }; }, blocks, events: debug, statuses };
}
function fragment(options, content, reasoningType = "none") {
  options.onPredictionFragment({ roundIndex: 0, content, reasoningType, tokensCount: 10,
    containsDrafted: false, isStructural: false });
}
function finish(options, text, stopReason = "eosFound") {
  if (text !== null) options.onMessage(ChatMessage.create("assistant", text));
  options.onPredictionCompleted({ stats: { stopReason, predictedTokensCount: stopReason === "eosFound" ? 20 : 8192 } });
}
const baseModel = { identifier: "continuation-fixture", async getContextLength() { return 84224; },
  async applyPromptTemplate(chat) { return chat.toString(); }, async countTokens() { return 1000; } };
function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "report-continuation-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const state = { scope: { conversation: "report", lineage: "first", parentLineage: null } };
  const makeHandler = () => createPredictionLoopHandler(new ContinuityNoteStore({ directory: path.join(directory, "notes"), durable: false }),
    undefined, { restore(history) { return { modelHistory: history, scope: state.scope, reason: "verified" }; } },
    { root: path.join(directory, "archive") }, () => ({}));
  return { directory, state, makeHandler };
}
function hostHistory(ctl) {
  const history = Chat.from([{ role: "user", content: "보고서를 자세하게 작성해줘" }]);
  for (const block of ctl.blocks) if (block.text && block.options.includeInContext !== false)
    history.append(ChatMessage.create("assistant", block.text));
  history.append(ChatMessage.create("user", "이어서"));
  return history;
}

for (const mode of ["completed", "truncated", "stream-canceled"]) test(`next turn retains delivered ${mode} prose after process recreation`, async t => {
  const { directory, state, makeHandler } = fixture(t);
  const text = "REPORT_BODY_13_3_LAST_PARAGRAPH";
  const abort = new AbortController();
  const model = { ...baseModel, async act(_chat, _tools, options) {
    fragment(options, "PRIVATE_REASONING", "reasoning");
    fragment(options, text);
    if (mode === "stream-canceled") { abort.abort(new Error("user canceled")); throw abort.signal.reason; }
    finish(options, text, mode === "completed" ? "eosFound" : "maxPredictedTokensReached");
  } };
  const ctl = controller(Chat.from([{ role: "user", content: "보고서를 자세하게 작성해줘" }]), model, directory, {}, abort);
  await makeHandler()(ctl).catch(error => { if (mode !== "stream-canceled") throw error; });
  state.scope = { conversation: "report", lineage: "second", parentLineage: "first" };
  let input;
  const next = controller(hostHistory(ctl), { ...baseModel, async act(chat, _tools, options) {
    input = chat.toString(); finish(options, "continued");
  } }, directory);
  await makeHandler()(next);
  assert.equal(next.events.find(e => e.event === "working_context_restore").status, "restored_remeasure_required");
  assert.ok(input.includes(text));
  assert.equal(input.split(text).length - 1, 1);
  assert.ok(!input.includes("PRIVATE_REASONING"));
});

test("one automatic continuation receives the prefix and appends without a second thinking block or rewrite timer", async t => {
  const { directory, makeHandler } = fixture(t);
  let calls = 0;
  const prefix = "# 보고서\n```cpp\nvoid Test() {", suffix = " return; }\n```\n완료";
  const model = { ...baseModel, async act(chat, tools, options) {
    calls++;
    if (calls === 1) {
      fragment(options, "FIRST_REASONING", "reasoning"); fragment(options, prefix);
      finish(options, prefix, "maxPredictedTokensReached"); return;
    }
    assert.deepEqual(tools, []);
    assert.ok(chat.getMessagesArray().some(m => m.getText() === prefix));
    assert.match(chat.toString(), /continue/i);
    fragment(options, "CONTINUATION_REASONING", "reasoning");
    // The former one-second rewrite timer must not abort normal continuation.
    await new Promise(resolve => setTimeout(resolve, 1100));
    assert.equal(options.signal.aborted, false);
    fragment(options, suffix); finish(options, suffix);
  } };
  const ctl = controller(Chat.from([{ role: "user", content: "보고서를 자세하게 작성해줘" }]), model, directory, { outputRecoveryMode: "on" });
  await makeHandler()(ctl);
  assert.equal(calls, 2);
  const visible = ctl.blocks.filter(b => b.options.includeInContext !== false);
  assert.deepEqual(visible.map(b => b.text), [prefix + suffix]);
  assert.equal(ctl.blocks.filter(b => b.options.style?.type === "thinking").length, 1);
  assert.equal(ctl.events.find(e => e.event === "execution_outcome").executionOutcome, "completed");
});

test("empty continuation retains a partial report and never starts a third call", async t => {
  const { directory, makeHandler } = fixture(t); let calls = 0;
  const model = { ...baseModel, async act(_chat, _tools, options) {
    calls++; finish(options, calls === 1 ? "already delivered report" : "", calls === 1 ? "maxPredictedTokensReached" : "eosFound");
  } };
  const ctl = controller(Chat.from([{ role: "user", content: "보고서를 자세하게 작성해줘" }]), model, directory, { outputRecoveryMode: "on" });
  await makeHandler()(ctl);
  assert.equal(calls, 2);
  const outcome = ctl.events.find(e => e.event === "execution_outcome");
  assert.equal(outcome.reportPresent, true);
  assert.equal(outcome.reportCompleted, false);
  assert.equal(outcome.deliveryState, "partial");
  assert.ok(ctl.statuses.some(s => /이어 쓸 본문이 생성되지/.test(s.text)));
  assert.ok(!ctl.statuses.some(s => /제한 시간/.test(s.text)));
});

for (const stop of ["maxPredictedTokensReached", "userStopped"]) test(`interrupted continuation (${stop}) survives restart exactly once`, async t => {
  const { directory, state, makeHandler } = fixture(t); let calls = 0;
  const abort = new AbortController();
  const prefix = "DELIVERED_PREFIX", suffix = "_PARTIAL_SUFFIX";
  const model = { ...baseModel, async act(_chat, _tools, options) {
    calls++;
    if (calls === 1) { finish(options, prefix, "maxPredictedTokensReached"); return; }
    fragment(options, suffix);
    if (stop === "userStopped") { abort.abort(new Error("user canceled")); throw abort.signal.reason; }
    finish(options, suffix, stop);
  } };
  const ctl = controller(Chat.from([{ role: "user", content: "보고서를 자세하게 작성해줘" }]), model, directory, { outputRecoveryMode: "on" }, abort);
  await makeHandler()(ctl).catch(error => { if (stop !== "userStopped") throw error; });
  assert.equal(calls, 2);
  assert.equal(ctl.events.find(e => e.event === "execution_outcome").reportCompleted, false);
  assert.equal(ctl.blocks.filter(b => b.options.includeInContext !== false).map(b => b.text).join(""), prefix + suffix);
  state.scope = { conversation: "report", lineage: "second", parentLineage: "first" };
  let input;
  await makeHandler()(controller(hostHistory(ctl), { ...baseModel, async act(chat, _tools, options) {
    input = chat.toString(); finish(options, "done");
  } }, directory));
  assert.equal(input.split(prefix).length - 1, 1);
  assert.equal(input.split(suffix).length - 1, 1);
});

test("a continuation with no measured input headroom stops without another model call", async t => {
  const { directory, makeHandler } = fixture(t); let calls = 0;
  const prefix = "REPORT_WITH_NO_REMAINING_HEADROOM";
  const model = { ...baseModel, async countTokens(text) { return text.includes(prefix) ? 84224 : 1000; },
    async act(_chat, _tools, options) { calls++; finish(options, prefix, "maxPredictedTokensReached"); } };
  const ctl = controller(Chat.from([{ role: "user", content: "보고서를 자세하게 작성해줘" }]), model, directory, { outputRecoveryMode: "on" });
  await makeHandler()(ctl);
  assert.equal(calls, 1);
  assert.equal(ctl.events.find(e => e.event === "execution_outcome").reportCompleted, false);
  assert.ok(ctl.blocks.some(b => b.text === prefix));
  assert.ok(ctl.events.some(e => e.event === "report_continuation_blocked"));
});

test("recovered long partial reports retain the exact ending through subsequent compaction", () => {
  const { retainVisibleResponse } = require("../src/continuity-assistant-evidence.js");
  const { buildCheckpoint } = require("../src/direct-compaction-core.js");
  const user = { role: "user", content: [{ type: "text", text: "Write detailed report" }] };
  const report = { role: "assistant", content: [{ type: "text", text: "START " + "x".repeat(18000) + " END_MARKER" }] };
  const restored = retainVisibleResponse([user, report], [user]);
  restored.push({ role: "user", content: [{ type: "text", text: "이어서" }] });
  const normalized = restored.map((m, index) => ({ role: m.role, index, text: m.content.map(p => p.text).join(""),
    toolRequests: [], toolResults: [], hasFiles: false }));
  const checkpoint = buildCheckpoint(normalized, { recentCompleteTurns: 0, maxCurrentTurnMessages: 0,
    checkpointPolicy: "mandatory", maxCheckpointChars: 12000 });
  assert.match(checkpoint.assistantCheckpoint, /END_MARKER/);
  assert.equal(checkpoint.memory.previousTurnFinalResponseEvidence.final, false);
  assert.equal(checkpoint.memory.previousTurnFinalResponseEvidence.present, true);
  const again = buildCheckpoint([
    { role: "system", text: checkpoint.checkpoint },
    { role: "assistant", text: checkpoint.assistantCheckpoint },
    { role: "user", text: "이어서" },
  ].map((m, index) => ({ ...m, index, toolRequests: [], toolResults: [], hasFiles: false })),
  { recentCompleteTurns: 0, maxCurrentTurnMessages: 0, checkpointPolicy: "mandatory", maxCheckpointChars: 12000 });
  assert.match(again.assistantCheckpoint, /END_MARKER/);
  assert.equal(again.memory.previousTurnFinalResponseEvidence.final, false);
});

"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { handlePredictionLoop } = require("../dist/prediction-loop.js");

function fakeController(history, tokenSource, overrides = {}) {
  const blocks = [];
  const config = {
    enabled: true,
    observeOnly: false,
    softRemainingTokens: 14000,
    hardRemainingTokens: 8000,
    maxOutputReserve: 4096,
    safetyMarginTokens: 1024,
    assumedContextLength: 32768,
    recentCompleteTurns: 0,
    compactAboveMessageCount: 4,
    maxCheckpointChars: 12000,
    maxToolResultChars: 1200,
    ...overrides,
  };
  const session = {
    tools: [],
    disposed: false,
    [Symbol.dispose]() { this.disposed = true; },
  };
  const ctl = {
    abortSignal: new AbortController().signal,
    guardAbort() {},
    getPluginConfig() { return { get: (key) => config[key] }; },
    async pullHistory() { return history; },
    async tokenSource() { return tokenSource; },
    async startToolUseSession() { return session; },
    async requestConfirmToolCall() { return { type: "allow" }; },
    debug(value) { ctl.debugValue = value; },
    createContentBlock(options) {
      const block = { options, text: "", requests: [], results: [] };
      blocks.push(block);
      return {
        appendText(text) { block.text += text; },
        appendToolRequest(request) { block.requests.push(request); },
        appendToolResult(result) { block.results.push(result); },
      };
    },
    blocks,
    session,
    debugValue: null,
  };
  return ctl;
}

test("prediction loop calls the directly selected model with compacted history", async () => {
  const latest = "Analyze the current Cinematic system only.";
  const history = Chat.from([
    { role: "system", content: "system" },
    { role: "user", content: "old objective" },
    { role: "assistant", content: "old ".repeat(5000) },
    { role: "user", content: latest },
  ]);
  let receivedHistory;
  const selectedModel = {
    identifier: "qwen/qwen3.8-27b",
    async getContextLength() { return 32768; },
    async applyPromptTemplate(chat) { return chat.toString(); },
    async countTokens() { return 25000; },
    async act(chat, tools, options) {
      receivedHistory = chat;
      assert.deepEqual(tools, []);
      options.onMessage(ChatMessage.create("assistant", "direct answer"));
      return {};
    },
  };
  const ctl = fakeController(history, selectedModel);
  await handlePredictionLoop(ctl);

  assert.ok(receivedHistory instanceof Chat);
  assert.match(receivedHistory.toString(), /Context memory/);
  assert.match(receivedHistory.toString(), new RegExp(latest.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.doesNotMatch(receivedHistory.toString(), /old old old old old/);
  assert.equal(ctl.blocks.at(-1).text, "direct answer");
  assert.equal(ctl.session.disposed, true);
  assert.equal(ctl.debugValue.compacted, true);
});

test("low-pressure history passes through without proxy model selection or sampling overrides", async () => {
  const history = Chat.from([{ role: "user", content: "hello" }]);
  let receivedHistory;
  let receivedOptions;
  const selectedModel = {
    identifier: "qwen/qwen3.8-27b",
    async getContextLength() { return 65536; },
    async applyPromptTemplate(chat) { return chat.toString(); },
    async countTokens() { return 100; },
    async act(chat, _tools, options) { receivedHistory = chat; receivedOptions = options; return {}; },
  };
  const ctl = fakeController(history, selectedModel);
  await handlePredictionLoop(ctl);

  assert.equal(receivedHistory, history);
  for (const key of [
    "temperature",
    "topP",
    "topK",
    "minP",
    "reasoningEffort",
    "targetModel",
    "maxPredictionRounds",
    "allowParallelToolExecution",
  ]) {
    assert.equal(Object.prototype.hasOwnProperty.call(receivedOptions, key), false);
  }
  assert.equal(ctl.debugValue.compacted, false);
});

test("the compactor cannot be selected recursively as the token source", async () => {
  const history = Chat.from([{ role: "user", content: "hello" }]);
  const ctl = fakeController(history, { identifier: "codex/unreal-context-compactor", async act() {} });
  await assert.rejects(() => handlePredictionLoop(ctl), /Select the actual Qwen\/LLM/);
});

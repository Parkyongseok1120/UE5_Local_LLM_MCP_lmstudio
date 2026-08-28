"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { handlePredictionLoop } = require("../dist/prediction-loop.js");
const { runOneToolRound } = require("../dist/round-loop.js");

function fakeController(history, tokenSource, overrides = {}, tools = []) {
  const blocks = [];
  const config = {
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
    tools,
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
    debugValues: [],
    debugValue: null,
  };
  ctl.debug = (value) => {
    ctl.debugValue = value;
    ctl.debugValues.push(value);
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

test("inexact measurement activates the configured message-count fallback", async () => {
  const messages = [];
  for (let index = 0; index < 12; index += 1) {
    messages.push({ role: "user", content: `short request ${index}` });
    messages.push({ role: "assistant", content: `short response ${index}` });
  }
  const history = Chat.from(messages);
  let receivedHistory;
  const selectedModel = {
    identifier: "qwen/qwen3.8-27b",
    async act(chat) { receivedHistory = chat; return {}; },
  };
  const ctl = fakeController(history, selectedModel, { compactAboveMessageCount: 24 });

  await handlePredictionLoop(ctl);

  assert.equal(ctl.debugValue.exactMeasurement, false);
  assert.equal(ctl.debugValue.messageCount, 24);
  assert.equal(ctl.debugValue.compacted, true);
  assert.notEqual(receivedHistory, history);
  assert.match(receivedHistory.toString(), /Context memory/);
});

test("inexact fallback escalates a still-pressured current tool turn to bounded retention", async () => {
  const history = Chat.from([
    { role: "user", content: "old completed request" },
    { role: "assistant", content: "old completed answer" },
    { role: "user", content: "inspect both current files, then report" },
  ]);
  const tool = {
    name: "read_file",
    description: "Read one project file.",
    parametersJsonSchema: { type: "object", properties: { path: { type: "string" } } },
    pluginIdentifier: "mcp/unreal-agent",
  };
  const actHistories = [];
  let actCount = 0;
  const selectedModel = {
    identifier: "qwen/qwen3.8-27b",
    async act(chat, _tools, options) {
      actHistories.push(Chat.from(chat));
      actCount += 1;
      if (actCount > 2) {
        options.onMessage(ChatMessage.create("assistant", "bounded fallback report"));
        options.onRoundEnd(0);
        return {};
      }
      const marker = actCount === 1 ? "OLD_FALLBACK_TOOL_RESULT" : "NEW_FALLBACK_TOOL_RESULT";
      const request = {
        id: `fallback-read-${actCount}`,
        type: "function",
        name: "read_file",
        arguments: { path: `File${actCount}.cpp` },
      };
      const callId = 500 + actCount;
      await options.guardToolCall(0, callId, {
        toolCallRequest: request,
        allow() {},
        allowAndOverrideParameters() {},
        deny() {},
      });
      options.onToolCallRequestFinalized(0, callId, { toolCallRequest: request });
      options.onMessage(ChatMessage.from({
        role: "assistant",
        content: [{ type: "toolCallRequest", toolCallRequest: request }],
      }));
      options.onMessage(ChatMessage.from({
        role: "tool",
        content: [{
          type: "toolCallResult",
          toolCallId: request.id,
          content: JSON.stringify({ ok: true, marker }),
        }],
      }));
      options.onRoundEnd(0);
      throw options.signal.reason;
    },
  };
  const ctl = fakeController(history, selectedModel, { compactAboveMessageCount: 4 }, [tool]);

  await handlePredictionLoop(ctl);

  assert.equal(actCount, 3);
  assert.equal(ctl.debugValues[2].exactMeasurement, false);
  assert.equal(ctl.debugValues[2].compacted, true);
  assert.match(actHistories[2].toString(), /NEW_FALLBACK_TOOL_RESULT/u);
  assert.doesNotMatch(actHistories[2].toString(), /OLD_FALLBACK_TOOL_RESULT/u);
  assert.equal(actHistories[2].getMessagesArray().filter((message) => (
    message.getToolCallRequests().length > 0
  )).length, 1);
  assert.equal(actHistories[2].getMessagesArray().filter((message) => (
    message.getToolCallResults().length > 0
  )).length, 1);
});

test("handler activation ignores a legacy nested enabled=false value", async () => {
  const history = Chat.from([
    { role: "user", content: "old request" },
    { role: "assistant", content: "old ".repeat(5000) },
    { role: "user", content: "current request" },
  ]);
  let receivedHistory;
  const selectedModel = {
    identifier: "qwen/qwen3.8-27b",
    async getContextLength() { return 32768; },
    async applyPromptTemplate(chat) { return chat.toString(); },
    async countTokens() { return 30000; },
    async act(chat) { receivedHistory = chat; return {}; },
  };
  const ctl = fakeController(history, selectedModel, { enabled: false });
  await handlePredictionLoop(ctl);

  assert.notEqual(receivedHistory, history);
  assert.match(receivedHistory.toString(), /Context memory/);
  assert.equal(ctl.debugValue.compacted, true);
});

test("observe-only remains the explicit no-mutation mode", async () => {
  const history = Chat.from([
    { role: "user", content: "old request" },
    { role: "assistant", content: "old ".repeat(5000) },
    { role: "user", content: "current request" },
  ]);
  let receivedHistory;
  const selectedModel = {
    identifier: "qwen/qwen3.8-27b",
    async getContextLength() { return 32768; },
    async applyPromptTemplate(chat) { return chat.toString(); },
    async countTokens() { return 30000; },
    async act(chat) { receivedHistory = chat; return {}; },
  };
  const ctl = fakeController(history, selectedModel, { observeOnly: true });
  await handlePredictionLoop(ctl);

  assert.equal(receivedHistory, history);
  assert.equal(ctl.debugValue.observeOnly, true);
  assert.equal(ctl.debugValue.compacted, false);
});

test("tool rounds are captured once, remeasured, and compacted before the next act", async () => {
  const history = Chat.from([{ role: "user", content: "inspect both files, then report" }]);
  const oldNoise = `${"OLD_ALREADY_READ ".repeat(1600)}OLD_ALREADY_READ_END`;
  const newNoise = `${"NEW_UNREAD ".repeat(2200)}NEW_UNREAD_END`;
  const actHistories = [];
  const measuredToolDefinitions = [];
  let actCount = 0;
  const tool = {
    name: "read_file",
    description: "Read one project file.",
    parametersJsonSchema: {
      type: "object",
      properties: { path: { type: "string" } },
      required: ["path"],
      additionalProperties: false,
    },
    pluginIdentifier: "mcp/unreal-agent",
  };

  const emitToolRound = async (options, callId, requestId, path, noise) => {
    const request = { id: requestId, type: "function", name: "read_file", arguments: { path } };
    let guardResult = "";
    await options.guardToolCall(0, callId, {
      toolCallRequest: request,
      allow() { guardResult = "allow"; },
      allowAndOverrideParameters() { guardResult = "override"; },
      deny() { guardResult = "deny"; },
    });
    assert.equal(guardResult, "allow");
    options.onToolCallRequestFinalized(0, callId, { toolCallRequest: request });
    options.onMessage(ChatMessage.from({
      role: "assistant",
      content: [{ type: "toolCallRequest", toolCallRequest: request }],
    }));
    options.onMessage(ChatMessage.from({
      role: "tool",
      content: [{
        type: "toolCallResult",
        toolCallId: requestId,
        content: JSON.stringify({ ok: true, noise }),
      }],
    }));
    options.onRoundEnd(0);
    assert.equal(options.signal.aborted, true);
    throw options.signal.reason;
  };

  const selectedModel = {
    identifier: "qwen/qwen3.8-27b",
    async getContextLength() { return 40000; },
    async applyPromptTemplate(chat, options) {
      measuredToolDefinitions.push(options.toolDefinitions);
      return chat.toString();
    },
    async countTokens(prompt) { return Math.ceil(prompt.length / 2); },
    async act(chat, tools, options) {
      actHistories.push(Chat.from(chat));
      assert.equal(tools[0], tool);
      actCount += 1;
      if (actCount === 1) return emitToolRound(options, 101, "old-read", "Old.cpp", oldNoise);
      if (actCount === 2) return emitToolRound(options, 101, "new-read", "New.cpp", newNoise);
      options.onMessage(ChatMessage.create("assistant", "final report"));
      options.onRoundEnd(0);
      return {};
    },
  };
  const ctl = fakeController(history, selectedModel, {}, [tool]);
  await handlePredictionLoop(ctl);

  assert.equal(actCount, 3);
  assert.equal(ctl.debugValues.length, 3);
  assert.equal(ctl.debugValues[2].compacted, true);
  assert.match(actHistories[2].toString(), /Context memory/);
  assert.match(actHistories[2].toString(), /NEW_UNREAD_END/);
  assert.doesNotMatch(actHistories[2].toString(), /OLD_ALREADY_READ_END/);
  assert.equal(actHistories[2].getMessagesArray().filter((message) => (
    message.getToolCallRequests().some((request) => request.id === "new-read")
  )).length, 1);
  assert.equal(actHistories[2].getMessagesArray().filter((message) => (
    message.getToolCallResults().some((result) => result.toolCallId === "new-read")
  )).length, 1);
  assert.deepEqual(ctl.blocks.flatMap((block) => block.requests).map((request) => request.callId), [101, 101]);
  assert.deepEqual(ctl.blocks.flatMap((block) => block.results).map((result) => result.callId), [101, 101]);
  assert.ok(measuredToolDefinitions.length >= actCount);
  for (const definitions of measuredToolDefinitions) {
    assert.deepEqual(definitions, [{
      type: "function",
      function: {
        name: tool.name,
        description: tool.description,
        parameters: tool.parametersJsonSchema,
      },
    }]);
  }
  assert.equal(ctl.blocks.at(-1).text, "final report");
  assert.equal(ctl.session.disposed, true);
});

test("a denied tool call keeps the SDK confirmation call ID in emitted messages", async () => {
  const history = Chat.from([{ role: "user", content: "Do not run the proposed tool." }]);
  const request = {
    id: "denied-read",
    type: "function",
    name: "read_file",
    arguments: { path: "Denied.cpp" },
  };
  const tool = {
    name: "read_file",
    description: "Read one project file.",
    parametersJsonSchema: { type: "object", properties: {} },
    pluginIdentifier: "mcp/unreal-agent",
  };
  let actCount = 0;
  const selectedModel = {
    identifier: "qwen/qwen3.8-27b",
    async act(_chat, _tools, options) {
      actCount += 1;
      if (actCount > 1) {
        options.onMessage(ChatMessage.create("assistant", "denial acknowledged"));
        options.onRoundEnd(0);
        return {};
      }
      let guardResult = "";
      await options.guardToolCall(0, 303, {
        toolCallRequest: request,
        allow() { guardResult = "allow"; },
        allowAndOverrideParameters() { guardResult = "override"; },
        deny() { guardResult = "deny"; },
      });
      assert.equal(guardResult, "deny");
      // LM Studio 1.5.0 returns before onToolCallRequestFinalized on denial.
      options.onMessage(ChatMessage.from({
        role: "assistant",
        content: [{ type: "toolCallRequest", toolCallRequest: request }],
      }));
      options.onMessage(ChatMessage.from({
        role: "tool",
        content: [{
          type: "toolCallResult",
          toolCallId: request.id,
          content: JSON.stringify({ error: "denied in test" }),
        }],
      }));
      options.onRoundEnd(0);
      throw options.signal.reason;
    },
  };
  const ctl = fakeController(history, selectedModel, {}, [tool]);
  ctl.requestConfirmToolCall = async () => ({ type: "deny", denyReason: "denied in test" });

  await handlePredictionLoop(ctl);

  assert.equal(actCount, 2);
  assert.deepEqual(ctl.blocks.flatMap((block) => block.requests).map((item) => item.callId), [303]);
  assert.deepEqual(ctl.blocks.flatMap((block) => block.results).map((item) => item.callId), [303]);
  assert.equal(ctl.blocks.at(-1).text, "denial acknowledged");
});

test("allowed ID-less tool calls register once across guard and finalized callbacks", async () => {
  const history = Chat.from([{ role: "user", content: "Read both anonymous requests." }]);
  const tool = {
    name: "read_file",
    description: "Read one project file.",
    parametersJsonSchema: { type: "object", properties: {} },
    pluginIdentifier: "mcp/unreal-agent",
  };
  const requests = [
    { type: "function", name: "read_file", arguments: { path: "A.cpp" } },
    { type: "function", name: "read_file", arguments: { path: "B.cpp" } },
  ];
  let actCount = 0;
  const selectedModel = {
    identifier: "qwen/qwen3.8-27b",
    async act(_chat, _tools, options) {
      actCount += 1;
      if (actCount > 1) {
        options.onMessage(ChatMessage.create("assistant", "anonymous reads complete"));
        options.onRoundEnd(0);
        return {};
      }
      for (const [index, request] of requests.entries()) {
        const callId = 401 + index;
        let guardResult = "";
        await options.guardToolCall(0, callId, {
          toolCallRequest: request,
          allow() { guardResult = "allow"; },
          allowAndOverrideParameters() { guardResult = "override"; },
          deny() { guardResult = "deny"; },
        });
        assert.equal(guardResult, "allow");
        // Match SDK 1.5.0 ordering: guard first, finalized only if allowed.
        options.onToolCallRequestFinalized(0, callId, { toolCallRequest: request });
      }
      options.onMessage(ChatMessage.from({
        role: "assistant",
        content: requests.map((toolCallRequest) => ({ type: "toolCallRequest", toolCallRequest })),
      }));
      options.onMessage(ChatMessage.from({
        role: "tool",
        content: requests.map((_request, index) => ({
          type: "toolCallResult",
          content: JSON.stringify({ ok: true, index }),
        })),
      }));
      options.onRoundEnd(0);
      throw options.signal.reason;
    },
  };
  const ctl = fakeController(history, selectedModel, {}, [tool]);

  await handlePredictionLoop(ctl);

  assert.equal(actCount, 2);
  assert.deepEqual(ctl.blocks.flatMap((block) => block.requests).map((item) => item.callId), [401, 402]);
  assert.deepEqual(ctl.blocks.flatMap((block) => block.results).map((item) => item.callId), [401, 402]);
  assert.equal(ctl.blocks.at(-1).text, "anonymous reads complete");
});

test("round control never swallows an external abort", async () => {
  const parentAbort = new AbortController();
  const reason = new Error("user cancelled generation");
  parentAbort.abort(reason);
  const tokenSource = {
    async act(_history, _tools, options) {
      assert.equal(options.signal.aborted, true);
      throw options.signal.reason;
    },
  };

  const captured = await runOneToolRound(
    tokenSource,
    Chat.empty(),
    [],
    parentAbort.signal,
    {
      onToolCallRequestFinalized() {},
      async guardToolCall() {},
    },
  );

  assert.equal(captured.continueAfterTools, false);
  assert.equal(captured.failure, reason);
  assert.deepEqual(captured.messages, []);
});

test("round control never swallows an unrelated lookalike error", async () => {
  const lookalike = new Error("not the local boundary sentinel");
  lookalike.name = "ContextCompactorRoundBoundary";
  const tokenSource = {
    async act() { throw lookalike; },
  };

  const captured = await runOneToolRound(
    tokenSource,
    Chat.empty(),
    [],
    new AbortController().signal,
    {
      onToolCallRequestFinalized() {},
      async guardToolCall() {},
    },
  );

  assert.equal(captured.continueAfterTools, false);
  assert.equal(captured.failure, lookalike);
});

test("the compactor cannot be selected recursively as the token source", async () => {
  const history = Chat.from([{ role: "user", content: "hello" }]);
  const ctl = fakeController(history, { identifier: "codex/unreal-context-compactor", async act() {} });
  await assert.rejects(() => handlePredictionLoop(ctl), /Select the actual Qwen\/LLM/);
});

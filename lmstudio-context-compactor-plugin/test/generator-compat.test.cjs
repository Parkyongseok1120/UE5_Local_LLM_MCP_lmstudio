"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { Chat } = require("@lmstudio/sdk");
const core = require("../src/compaction-core");

function activeCheckpoint(stateRoot) {
  const sessionDirs = fs.readdirSync(stateRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory());
  assert.equal(sessionDirs.length, 1, "Expected one isolated compactor session");
  return JSON.parse(fs.readFileSync(
    path.join(stateRoot, sessionDirs[0].name, "active-checkpoint.json"),
    "utf8",
  ));
}

function controllerFor(model, config, stateRoot, emitted, toolDefinitions) {
  return {
    client: { llm: { async listLoaded() { return [model]; } } },
    abortSignal: new AbortController().signal,
    getPluginConfig() { return { get(key) { return config[key]; } }; },
    getWorkingDirectory() { return stateRoot; },
    getToolDefinitions() { return toolDefinitions; },
    fragmentGenerated(content, opts) { emitted.push({ kind: "fragment", content, opts }); },
    toolCallGenerationStarted(info) { emitted.push({ kind: "start", info }); },
    toolCallGenerationNameReceived(name) { emitted.push({ kind: "name", name }); },
    toolCallGenerationArgumentFragmentGenerated(content) { emitted.push({ kind: "args", content }); },
    toolCallGenerationEnded(request) { emitted.push({ kind: "end", request }); },
    toolCallGenerationFailed(error) { emitted.push({ kind: "failure", error: error.message }); },
  };
}

test("default mode preserves multiple tool calls and fragment metadata", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-generator-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const model = {
      identifier: "test-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        assert.equal(opts.temperature, 0.1);
        opts.onPredictionFragment({
          content: "OK",
          tokensCount: 2,
          containsDrafted: true,
          reasoningType: "none",
          isStructural: false,
        });
        for (const [callId, id, name] of [
          [1, "call-a", "read_file"],
          [2, "call-b", "read_file_range"],
        ]) {
          opts.onToolCallRequestStart(callId, { toolCallId: id });
          opts.onToolCallRequestNameReceived(callId, name);
          opts.onToolCallRequestArgumentFragmentGenerated(callId, "{}");
          opts.onToolCallRequestEnd(callId, {
            toolCallRequest: { id, type: "function", name, arguments: {} },
          });
        }
        return { async result() { return {}; } };
      },
    };
    const config = {
      enabled: true,
      observeOnly: false,
      strictToolControlPlane: false,
      targetModel: "",
    };
    const controller = {
      client: { llm: { async listLoaded() { return [model]; } } },
      abortSignal: new AbortController().signal,
      getPluginConfig() { return { get(key) { return config[key]; } }; },
      getWorkingDirectory() { return stateRoot; },
      getToolDefinitions() { return [{ type: "function", function: { name: "read_file" } }]; },
      fragmentGenerated(content, opts) { emitted.push({ kind: "fragment", content, opts }); },
      toolCallGenerationStarted(info) { emitted.push({ kind: "start", info }); },
      toolCallGenerationNameReceived(name) { emitted.push({ kind: "name", name }); },
      toolCallGenerationArgumentFragmentGenerated(content) { emitted.push({ kind: "args", content }); },
      toolCallGenerationEnded(request) { emitted.push({ kind: "end", request }); },
      toolCallGenerationFailed(error) { emitted.push({ kind: "failure", error }); },
    };
    const history = Chat.empty();
    history.append("system", "rules");
    history.append("user", "use two independent read tools");

    await generate(controller, history);

    assert.equal(emitted.filter((event) => event.kind === "start").length, 2);
    assert.equal(emitted.filter((event) => event.kind === "end").length, 2);
    assert.equal(emitted.filter((event) => event.kind === "failure").length, 0);
    const fragment = emitted.find((event) => event.kind === "fragment");
    assert.equal(fragment.content, "OK");
    assert.equal(fragment.opts.tokenCount, 2);
    assert.equal(fragment.opts.containsDrafted, true);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("tool call stream is identical before and after forced context compaction", async () => {
  const { generate } = require("../dist/generator.js");
  const requests = [
    { callId: 1, request: { id: "call-a", type: "function", name: "read_file", arguments: { path: "project://Source/A.cpp" } } },
    { callId: 2, request: { id: "call-b", type: "function", name: "read_file_range", arguments: { path: "project://Source/B.cpp", startLine: 10, endLine: 20 } } },
  ];
  const toolDefinitions = [
    { type: "function", function: { name: "read_file" } },
    { type: "function", function: { name: "read_file_range" } },
  ];

  async function runScenario(forceCompaction) {
    const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-equivalence-"));
    process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
    try {
      const emitted = [];
      const captured = { chats: [], rawTools: null };
      const model = {
        identifier: "equivalence-model",
        async applyPromptTemplate(chat) {
          return JSON.stringify(core.snapshotMessages(chat.getMessagesArray()));
        },
        async countTokens(value) { return String(value || "").length; },
        async getContextLength() { return 24_000; },
        respond(chat, opts) {
          captured.chats.push(core.snapshotMessages(chat.getMessagesArray()));
          captured.rawTools = opts.rawTools;
          opts.onPredictionFragment({
            content: "calling tools",
            tokensCount: 3,
            containsDrafted: false,
            reasoningType: "none",
            isStructural: false,
          });
          for (const { callId, request } of requests) {
            opts.onToolCallRequestStart(callId, { toolCallId: request.id });
            opts.onToolCallRequestNameReceived(callId, request.name);
            const args = JSON.stringify(request.arguments);
            const midpoint = Math.floor(args.length / 2);
            opts.onToolCallRequestArgumentFragmentGenerated(callId, args.slice(0, midpoint));
            opts.onToolCallRequestArgumentFragmentGenerated(callId, args.slice(midpoint));
            opts.onToolCallRequestEnd(callId, { toolCallRequest: request, rawContent: args });
          }
          return { async result() { return {}; } };
        },
      };
      const config = {
        enabled: true,
        observeOnly: false,
        strictToolControlPlane: false,
        targetModel: "",
        softRemainingTokens: 10_000,
        hardRemainingTokens: 5_000,
        maxOutputReserve: 512,
        normalToolResultReserve: 512,
        buildToolResultReserve: 1_000,
        recentCompleteTurns: 1,
        minimumTurnsBetweenCompactions: 0,
        targetRemainingTokensAfterCompaction: 12_000,
      };
      const controller = controllerFor(model, config, stateRoot, emitted, toolDefinitions);
      const history = Chat.empty();
      history.append("system", "rules");
      history.append("user", "objective");
      if (forceCompaction) {
        for (let index = 0; index < 8; index += 1) {
          history.append("assistant", `old-${index}-${"x".repeat(3_500)}`);
          history.append("user", `follow-up-${index}`);
        }
      }
      history.append("user", "use two independent read tools");
      const originalLength = history.length;

      await generate(controller, history);

      assert.equal(history.length, originalLength, "The visible LM Studio history was mutated");
      const sessionDir = fs.readdirSync(stateRoot, { withFileTypes: true })
        .find((entry) => entry.isDirectory());
      const telemetry = fs.readFileSync(path.join(stateRoot, sessionDir.name, "events.jsonl"), "utf8")
        .trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);
      return { emitted, captured, originalLength, telemetry };
    } finally {
      delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
      fs.rmSync(stateRoot, { recursive: true, force: true });
    }
  }

  const beforeLimit = await runScenario(false);
  const afterLimit = await runScenario(true);

  assert.deepEqual(afterLimit.emitted, beforeLimit.emitted);
  assert.deepEqual(afterLimit.captured.rawTools, beforeLimit.captured.rawTools);
  assert.equal(afterLimit.emitted.filter((event) => event.kind === "start").length, 2);
  assert.equal(afterLimit.emitted.filter((event) => event.kind === "end").length, 2);
  assert.equal(afterLimit.emitted.filter((event) => event.kind === "failure").length, 0);
  assert.ok(afterLimit.captured.chats[0].length < afterLimit.originalLength);
  assert.ok(afterLimit.captured.chats[0].some((message) => message.text.includes("Conversation checkpoint")));
  assert.equal(afterLimit.captured.chats[0].at(-1).text, "use two independent read tools");
  assert.ok(afterLimit.telemetry.some((event) => event.type === "compaction_decision" && event.applied === true));
  assert.equal(beforeLimit.telemetry.some((event) => event.type === "compaction_decision" && event.applied === true), false);
  const routedMeasurement = afterLimit.telemetry.find((event) => event.type === "context_measurement");
  assert.equal(routedMeasurement?.proxyActive, true);
  assert.equal(routedMeasurement?.targetModel, "equivalence-model");
});

test("anonymous multi-tool checkpoints clear one tool result at a time", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-anonymous-tools-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    let generation = 0;
    const model = {
      identifier: "anonymous-tool-model",
      async applyPromptTemplate(chat) {
        return JSON.stringify(core.snapshotMessages(chat.getMessagesArray()));
      },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_chat, opts) {
        if (generation === 0) {
          for (const [callId, name] of [[1, "read_file"], [2, "read_file_range"]]) {
            opts.onToolCallRequestStart(callId, {});
            opts.onToolCallRequestNameReceived(callId, name);
            opts.onToolCallRequestArgumentFragmentGenerated(callId, "{}");
            opts.onToolCallRequestEnd(callId, {
              toolCallRequest: { type: "function", name, arguments: {} },
              rawContent: "{}",
            });
          }
        } else {
          opts.onPredictionFragment({ content: "continue" });
        }
        generation += 1;
        return { async result() { return {}; } };
      },
    };
    const config = {
      enabled: true,
      observeOnly: false,
      strictToolControlPlane: false,
      targetModel: "",
    };
    const emitted = [];
    const controller = controllerFor(
      model,
      config,
      stateRoot,
      emitted,
      [{ type: "function", function: { name: "read_file" } }],
    );
    const initial = Chat.empty();
    initial.append("system", "rules");
    initial.append("user", "run two tools");
    await generate(controller, initial);

    let checkpoint = activeCheckpoint(stateRoot);
    assert.equal(checkpoint.pendingToolCalls.length, 2);
    assert.deepEqual(
      checkpoint.pendingToolCalls.map((pending) => pending.observedAnonymousToolResultCount),
      [0, 1],
    );

    function historyWithResults(resultCount) {
      return Chat.from({
        messages: [
          { role: "system", content: [{ type: "text", text: "rules" }] },
          { role: "user", content: [{ type: "text", text: "run two tools" }] },
          {
            role: "assistant",
            content: [
              { type: "toolCallRequest", toolCallRequest: { type: "function", name: "read_file", arguments: {} } },
              { type: "toolCallRequest", toolCallRequest: { type: "function", name: "read_file_range", arguments: {} } },
            ],
          },
          {
            role: "tool",
            content: Array.from({ length: resultCount }, (_value, index) => ({
              type: "toolCallResult",
              content: `result-${index}`,
            })),
          },
        ],
      });
    }

    await generate(controller, historyWithResults(1));
    checkpoint = activeCheckpoint(stateRoot);
    assert.equal(checkpoint.pendingToolCalls.length, 1);
    assert.equal(checkpoint.pendingToolCalls[0].name, "read_file_range");

    await generate(controller, historyWithResults(2));
    checkpoint = activeCheckpoint(stateRoot);
    assert.deepEqual(checkpoint.pendingToolCalls, []);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("forced compaction keeps an SDK tool request and result as a complete pair", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-tool-pair-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    let captured = null;
    const model = {
      identifier: "tool-pair-model",
      async applyPromptTemplate(chat) {
        return JSON.stringify(core.snapshotMessages(chat.getMessagesArray()));
      },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 50_000; },
      respond(chat, opts) {
        captured = core.snapshotMessages(chat.getMessagesArray());
        opts.onPredictionFragment({ content: "continued" });
        return { async result() { return {}; } };
      },
    };
    const config = {
      enabled: true,
      observeOnly: false,
      strictToolControlPlane: false,
      targetModel: "",
      softRemainingTokens: 1_000_000,
      hardRemainingTokens: 5_000,
      maxOutputReserve: 512,
      normalToolResultReserve: 512,
      recentCompleteTurns: 1,
      minimumTurnsBetweenCompactions: 0,
      targetRemainingTokensAfterCompaction: 20_000,
    };
    const controller = controllerFor(model, config, stateRoot, [], []);
    const history = Chat.from({
      messages: [
        { role: "system", content: [{ type: "text", text: "rules" }] },
        { role: "user", content: [{ type: "text", text: "objective" }] },
        { role: "assistant", content: [{ type: "text", text: `old-${"x".repeat(25_000)}` }] },
        { role: "user", content: [{ type: "text", text: "old follow-up" }] },
        {
          role: "assistant",
          content: [{
            type: "toolCallRequest",
            toolCallRequest: { id: "pair-1", type: "function", name: "read_file", arguments: { path: "A.cpp" } },
          }],
        },
        {
          role: "tool",
          content: [{ type: "toolCallResult", toolCallId: "pair-1", content: "file contents" }],
        },
        { role: "user", content: [{ type: "text", text: "continue after the tool result" }] },
      ],
    });

    await generate(controller, history);

    assert.ok(captured);
    assert.equal(core.isCompleteToolPair(captured), true);
    assert.ok(captured.some((message) => message.toolCalls.some((call) => call.id === "pair-1")));
    assert.ok(captured.some((message) => message.toolResults.some((result) => result.toolCallId === "pair-1")));
    assert.equal(captured.at(-1).text, "continue after the tool result");
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

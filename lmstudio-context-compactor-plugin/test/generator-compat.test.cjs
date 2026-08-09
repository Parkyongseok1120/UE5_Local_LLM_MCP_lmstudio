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
    .filter((entry) => (
      entry.isDirectory()
      && !String(entry.name || "").startsWith(".")
      && String(entry.name || "") !== "_base"
    ));
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
    assert.equal(activeCheckpoint(stateRoot).sourceMessageCount, history.length);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("RAG search tool calls receive a stable compactor session id", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-rag-session-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let sawArchitectureGate = false;
    let architectureMaxTokens = 0;
    const model = {
      identifier: "rag-session-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        architectureMaxTokens = opts.maxTokens;
        sawArchitectureGate = _history.getMessagesArray().some(
          (message) => message.getRole() === "system"
            && message.getText().includes("[UNREAL_ARCHITECTURE_VALIDATION_GATE]"),
        );
        opts.onToolCallRequestStart(1, { toolCallId: "rag-1" });
        opts.onToolCallRequestNameReceived(1, "unreal_rag_search");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"query":"lobby"}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "rag-1",
            type: "function",
            name: "unreal_rag_search",
            arguments: { query: "lobby" },
          },
        });
        opts.onToolCallRequestStart(2, { toolCallId: "architecture-1" });
        opts.onToolCallRequestNameReceived(2, "unreal_architecture_reasoning");
        opts.onToolCallRequestArgumentFragmentGenerated(2, '{"proposal":{"decision":"lobby"}}');
        opts.onToolCallRequestEnd(2, {
          toolCallRequest: {
            id: "architecture-1",
            type: "function",
            name: "unreal_architecture_reasoning",
            arguments: { proposal: { decision: "lobby" } },
          },
        });
        return { async result() { return {}; } };
      },
    };
    const config = {
      enabled: true,
      observeOnly: false,
      strictToolControlPlane: false,
      targetModel: "",
    };
    const tools = ["unreal_rag_search", "unreal_architecture_reasoning"].map((name) => ({
      type: "function",
      function: {
        name,
        parameters: { type: "object", properties: { query: { type: "string" }, sessionId: { type: "string" } } },
      },
    }));
    const controller = controllerFor(model, config, stateRoot, emitted, tools);
    const history = Chat.empty();
    history.append("system", "rules");
    history.append("user", "investigate the lobby architecture");

    await generate(controller, history);

    const ends = emitted.filter((event) => event.kind === "end");
    assert.equal(ends.length, 2);
    assert.ok(ends[0].request.arguments.sessionId);
    assert.equal(ends[0].request.arguments.query, "lobby");
    assert.equal(ends[1].request.arguments.sessionId, ends[0].request.arguments.sessionId);
    const args = emitted.filter((event) => event.kind === "args");
    assert.equal(JSON.parse(args[0].content).sessionId, ends[0].request.arguments.sessionId);
    assert.equal(JSON.parse(args[1].content).sessionId, ends[0].request.arguments.sessionId);
    assert.equal(sawArchitectureGate, true);
    assert.equal(architectureMaxTokens, 8192);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("recovery checkpoint bypasses a stale required work-tool gate", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-recovery-control-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const model = {
      identifier: "recovery-control-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        opts.onToolCallRequestStart(1, { toolCallId: "checkpoint-1" });
        opts.onToolCallRequestNameReceived(1, "unreal_task_checkpoint");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"action":"record"}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "checkpoint-1",
            type: "function",
            name: "unreal_task_checkpoint",
            arguments: { action: "record" },
          },
        });
        return { async result() { return {}; } };
      },
    };
    const controller = controllerFor(
      model,
      { strictToolControlPlane: false },
      stateRoot,
      emitted,
      [{ type: "function", function: { name: "unreal_task_checkpoint" } }],
    );
    const history = Chat.empty();
    history.append("user", "continue");
    history.append("assistant", JSON.stringify({ requiredNextTool: "read_file" }));

    await generate(controller, history);

    assert.equal(emitted.filter((event) => event.kind === "end").length, 1);
    assert.equal(emitted.filter((event) => event.kind === "failure").length, 0);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

for (const stopReason of ["contextLengthReached", "maxPredictedTokensReached"]) {
  test(`unsafe prediction stop ${stopReason} discards buffered output`, async () => {
    const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-stop-"));
    process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
    try {
      const { generate } = require("../dist/generator.js");
      const emitted = [];
      const model = {
        identifier: "stop-reason-model",
        async applyPromptTemplate() { return "formatted"; },
        async countTokens(value) { return String(value || "").length; },
        async getContextLength() { return 100_000; },
        respond(_history, opts) {
          opts.onPredictionFragment({ content: "partial output that must not escape" });
          opts.onToolCallRequestStart(1, { toolCallId: "partial-call" });
          opts.onToolCallRequestNameReceived(1, "write_file");
          return { async result() { return { stats: { stopReason } }; } };
        },
      };
      const controller = controllerFor(model, {}, stateRoot, emitted, []);
      const history = Chat.empty();
      history.append("user", "continue safely");

      await assert.rejects(generate(controller, history), new RegExp(stopReason));
      assert.deepEqual(emitted, []);
      const sessionDir = fs.readdirSync(stateRoot, { withFileTypes: true })
        .find((entry) => entry.isDirectory());
      const events = fs.readFileSync(path.join(stateRoot, sessionDir.name, "events.jsonl"), "utf8")
        .trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);
      const completion = events.find((event) => event.type === "prediction_completion");
      assert.equal(completion.stopReason, stopReason);
      assert.equal(completion.outputCommitted, false);
    } finally {
      delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
      fs.rmSync(stateRoot, { recursive: true, force: true });
    }
  });
}

test("observe-only mode fails closed at the hard context threshold", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-observe-hard-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    let respondCalled = false;
    const model = {
      identifier: "observe-only-model",
      async applyPromptTemplate() { return "x".repeat(3_000); },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 10_000; },
      respond() {
        respondCalled = true;
        return { async result() { return {}; } };
      },
    };
    const controller = controllerFor(model, { observeOnly: true }, stateRoot, [], []);
    const history = Chat.empty();
    history.append("user", "do not truncate");

    await assert.rejects(generate(controller, history), /compaction is not active/i);
    assert.equal(respondCalled, false);
    assert.ok(activeCheckpoint(stateRoot));
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("required checkpoint persistence blocks generation before model output", async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-persist-"));
  const blockedRoot = path.join(temp, "not-a-directory");
  fs.writeFileSync(blockedRoot, "block");
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = blockedRoot;
  try {
    const { generate } = require("../dist/generator.js");
    let respondCalled = false;
    const model = {
      identifier: "persistence-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond() {
        respondCalled = true;
        return { async result() { return {}; } };
      },
    };
    const controller = controllerFor(model, { requireCheckpointPersistence: true }, blockedRoot, [], []);
    const history = Chat.empty();
    history.append("user", "persist before responding");

    await assert.rejects(generate(controller, history), /could not be persisted/i);
    assert.equal(respondCalled, false);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(temp, { recursive: true, force: true });
  }
});

test("pending tool checkpoint is durable before buffered tool output is committed", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-pending-persist-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  const checkpointStore = require("../dist/checkpoint-store.js");
  const originalSave = checkpointStore.saveCheckpoint;
  let saveCount = 0;
  checkpointStore.saveCheckpoint = async (...args) => {
    saveCount += 1;
    if (saveCount === 2) throw new Error("injected pending checkpoint failure");
    return originalSave(...args);
  };
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const model = {
      identifier: "pending-persistence-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        opts.onPredictionFragment({ content: "calling tool" });
        opts.onToolCallRequestStart(1, { toolCallId: "call-1" });
        opts.onToolCallRequestNameReceived(1, "read_file");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"path":"A.cpp"}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "call-1",
            type: "function",
            name: "read_file",
            arguments: { path: "A.cpp" },
          },
        });
        return { async result() { return {}; } };
      },
    };
    const controller = controllerFor(
      model,
      { requireCheckpointPersistence: true },
      stateRoot,
      emitted,
      [{ type: "function", function: { name: "read_file" } }],
    );
    const history = Chat.empty();
    history.append("user", "read safely");

    await assert.rejects(
      generate(controller, history),
      /pending_tool_calls.*injected pending checkpoint failure/i,
    );
    assert.deepEqual(emitted, []);
  } finally {
    checkpointStore.saveCheckpoint = originalSave;
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
  assert.equal(
    afterLimit.captured.chats[0].filter((message) => message.role === "user" && message.text === "objective").length,
    0,
    "obsolete first-turn objective must not remain pinned after mid-chat goal changes",
  );
  assert.equal(
    afterLimit.captured.chats[0].some((message) => String(message.text || "").startsWith("old-0-")),
    false,
    "goal-change compaction should drop early assistant dumps",
  );
  assert.ok(afterLimit.telemetry.some((event) => event.type === "compaction_decision" && event.applied === true));
  assert.ok(
    afterLimit.telemetry.some((event) => event.type === "compaction_decision" && (
      event.effectiveAction === "soft_compact" || event.effectiveAction === "hard_compact"
    )),
  );
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

    await assert.rejects(
      generate(controller, historyWithResults(1)),
      /prior tool call.*still lack a result/i,
    );
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

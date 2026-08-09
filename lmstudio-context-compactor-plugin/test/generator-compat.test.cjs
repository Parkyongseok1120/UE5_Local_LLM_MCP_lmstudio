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

function controllerFor(model, config, stateRoot, emitted, toolDefinitions, workingDirectory = stateRoot) {
  return {
    client: { llm: { async listLoaded() { return [model]; } } },
    abortSignal: new AbortController().signal,
    getPluginConfig() { return { get(key) { return config[key]; } }; },
    getWorkingDirectory() { return workingDirectory; },
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
    assert.equal(architectureMaxTokens, 6144);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("architecture validation is forced after enough unique direct-source reads", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-architecture-force-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let capturedTools = null;
    let sawSubmissionGate = false;
    const model = {
      identifier: "architecture-force-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(history, opts) {
        capturedTools = opts.rawTools;
        sawSubmissionGate = history.getMessagesArray().some(
          (message) => message.getRole() === "system"
            && message.getText().includes("[UNREAL_ARCHITECTURE_SUBMISSION_REQUIRED]"),
        );
        opts.onToolCallRequestStart(1, { toolCallId: "architecture-forced" });
        opts.onToolCallRequestNameReceived(1, "unreal_architecture_reasoning");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"proposal":{"decision":"self-derived"}}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "architecture-forced",
            type: "function",
            name: "unreal_architecture_reasoning",
            arguments: { proposal: { decision: "self-derived" } },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const config = {
      enabled: true,
      observeOnly: false,
      strictToolControlPlane: false,
      targetModel: "",
      architectureEvidenceReadThreshold: 4,
    };
    const tools = [
      {
        type: "function",
        function: { name: "read_file", parameters: { type: "object", properties: {} } },
      },
      {
        type: "function",
        function: {
          name: "unreal_architecture_reasoning",
          parameters: {
            type: "object",
            properties: { proposal: { type: "object" }, symbols: { type: "array" } },
          },
        },
      },
    ];
    const messages = [
      { role: "system", content: [{ type: "text", text: "rules" }] },
      { role: "user", content: [{ type: "text", text: "validate the templates_lobby architecture" }] },
    ];
    for (let index = 0; index < 4; index += 1) {
      const id = `read-${index}`;
      messages.push({
        role: "assistant",
        content: [{
          type: "toolCallRequest",
          toolCallRequest: {
            id,
            type: "function",
            name: "read_file",
            arguments: { path: `Source/Feature${index}.cpp` },
          },
        }],
      });
      messages.push({
        role: "tool",
        content: [{ type: "toolCallResult", toolCallId: id, content: `source-${index}` }],
      });
    }
    const history = Chat.from({ messages });

    await generate(controllerFor(model, config, stateRoot, emitted, tools), history);

    assert.equal(sawSubmissionGate, true);
    assert.equal(capturedTools.force, true);
    assert.ok(
      capturedTools.tools[0].function.parameters.required.includes("proposal"),
    );
    assert.deepEqual(
      capturedTools.tools.map((tool) => tool.function.name),
      ["unreal_architecture_reasoning"],
    );
    assert.equal(emitted.filter((event) => event.kind === "end").length, 1);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("header-only architecture evidence keeps implementation discovery available", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-architecture-headers-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let capturedTools = null;
    const model = {
      identifier: "architecture-header-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        capturedTools = opts.rawTools;
        opts.onToolCallRequestStart(1, { toolCallId: "read-implementation" });
        opts.onToolCallRequestNameReceived(1, "read_file");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"path":"Source/Feature.cpp"}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "read-implementation",
            type: "function",
            name: "read_file",
            arguments: { path: "Source/Feature.cpp" },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const tools = [
      {
        type: "function",
        function: { name: "read_file", parameters: { type: "object", properties: {} } },
      },
      {
        type: "function",
        function: {
          name: "unreal_architecture_reasoning",
          parameters: {
            type: "object",
            properties: { proposal: { type: "object" } },
          },
        },
      },
    ];
    const messages = [
      { role: "system", content: [{ type: "text", text: "rules" }] },
      { role: "user", content: [{ type: "text", text: "validate the templates_lobby architecture" }] },
    ];
    for (let index = 0; index < 4; index += 1) {
      const id = `header-${index}`;
      messages.push({
        role: "assistant",
        content: [{
          type: "toolCallRequest",
          toolCallRequest: {
            id,
            type: "function",
            name: "read_file",
            arguments: { path: `Source/Feature${index}.h` },
          },
        }],
      });
      messages.push({
        role: "tool",
        content: [{ type: "toolCallResult", toolCallId: id, content: `header-${index}` }],
      });
    }

    await generate(
      controllerFor(model, {
        enabled: true,
        targetModel: "",
        architectureEvidenceReadThreshold: 4,
        architectureEvidenceHardLimit: 8,
      }, stateRoot, emitted, tools),
      Chat.from({ messages }),
    );

    assert.notEqual(capturedTools.force, true);
    assert.deepEqual(
      capturedTools.tools.map((tool) => tool.function.name),
      ["read_file", "unreal_architecture_reasoning"],
    );
    assert.equal(emitted.filter((event) => event.kind === "end").length, 1);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

function fullArchitectureToolSchema() {
  const array = { type: "array", items: { type: "string" } };
  return {
    type: "function",
    function: {
      name: "unreal_architecture_reasoning",
      parameters: {
        type: "object",
        properties: {
          proposal: {
            type: "object",
            properties: {
              decision: { type: "string" },
              invariants: { ...array },
              impactedSurfaces: { ...array },
              validationPlan: { ...array },
              alternatives: { type: "array", items: { type: "object" } },
              selectedAlternative: { type: "string" },
              selectionRationale: { type: "string" },
              implementationFiles: { ...array },
              ownership: {
                type: "object",
                properties: Object.fromEntries([
                  "stateOwner", "dataOwner", "lifecycleOwner", "failurePolicy", "recoveryPolicy",
                ].map((field) => [field, { type: "string" }])),
              },
              networking: {
                type: "object",
                properties: {
                  authorityOwner: { type: "string" },
                  clientInitiated: { type: "boolean" },
                  requestPath: { ...array },
                  rpcOwner: { type: "string" },
                  owningConnection: { type: "string" },
                  serverValidation: { type: "string" },
                  replicatedState: { ...array },
                },
              },
              stateInventory: { type: "array", items: { type: "object" } },
              lifecycleTransitions: { type: "array", items: { type: "object" } },
              migrationPlan: { ...array },
              validationMatrix: {
                type: "array",
                items: { type: "object", properties: { invariant: { type: "string" } } },
              },
              implementationSlices: {
                type: "array",
                items: {
                  type: "object",
                  properties: { invariants: { ...array } },
                },
              },
            },
          },
        },
      },
    },
  };
}

function completeArchitectureProposal(decision = "replanned") {
  return {
    decision,
    invariants: ["server authority"],
    impactedSurfaces: ["existing runtime surface"],
    validationPlan: ["static validation"],
    alternatives: [{ name: "A" }, { name: "B" }],
    selectedAlternative: "A",
    selectionRationale: "source-grounded fit",
    implementationFiles: ["Source/Feature.cpp"],
    ownership: {
      stateOwner: "server state owner",
      dataOwner: "existing data owner",
      lifecycleOwner: "existing lifecycle owner",
      failurePolicy: "reject invalid requests",
      recoveryPolicy: "rebuild from authoritative state",
    },
    networking: {
      authorityOwner: "server authority",
      clientInitiated: true,
      requestPath: ["client", "owned RPC surface", "server authority"],
      rpcOwner: "owned RPC surface",
      owningConnection: "requesting client connection",
      serverValidation: "validate caller and state",
      replicatedState: ["authoritative state"],
    },
    stateInventory: [{ state: "authoritative state" }],
    lifecycleTransitions: [{ event: "join" }],
    migrationPlan: ["migrate one bounded slice"],
    validationMatrix: [{ invariant: "server authority" }],
    implementationSlices: [{ invariants: ["server authority"] }],
  };
}

function failedFullReplanHistory(
  extraReads = 0,
  initialReads = 0,
  extraDiscoveryTool = "read_file",
  rejection = {},
) {
  const messages = [
    { role: "system", content: [{ type: "text", text: "rules" }] },
    {
      role: "user",
      content: [{
        type: "text",
        text: "Validate templates_lobby authoritative multiplayer architecture with alternatives, ownership, lifecycle, migration order, and implementation slices.",
      }],
    },
  ];
  for (let index = 0; index < initialReads; index += 1) {
    const id = `initial-read-${index}`;
    messages.push({
      role: "assistant",
      content: [{
        type: "toolCallRequest",
        toolCallRequest: {
          id,
          type: "function",
          name: "read_file",
          arguments: { path: `Source/Initial${index}.cpp` },
        },
      }],
    });
    messages.push({
      role: "tool",
      content: [{ type: "toolCallResult", toolCallId: id, content: `initial-${index}` }],
    });
  }
  messages.push(
    {
      role: "assistant",
      content: [{
        type: "toolCallRequest",
        toolCallRequest: {
          id: "architecture-rejected",
          type: "function",
          name: "unreal_architecture_reasoning",
          arguments: { proposal: { decision: "rejected" } },
        },
      }],
    },
    {
      role: "tool",
      content: [{
        type: "toolCallResult",
        toolCallId: "architecture-rejected",
        content: JSON.stringify({
          ok: false,
          proposalRevision: "rejected-r1",
          errorCode: rejection.errorCode || "ARCHITECTURE_PROPOSAL_INVALID",
          requiredChangedPaths: rejection.requiredChangedPaths || [],
          proposalValidation: {
            ok: false,
            repairStrategy: "full_replan",
            designContract: { requiresFullReplan: true },
          },
          repairSubmission: { mode: "fullProposal" },
          requiredNextAction: "submit_full_architecture_proposal",
          nextActionIsTool: false,
        }),
      }],
    },
  );
  for (let index = 0; index < extraReads; index += 1) {
    const id = `replan-read-${index}`;
    messages.push({
      role: "assistant",
      content: [{
        type: "toolCallRequest",
        toolCallRequest: {
          id,
          type: "function",
          name: extraDiscoveryTool,
          arguments: {
            path: extraDiscoveryTool === "list_directory"
              ? `Content/Discovery${index}`
              : `Source/Replan${index}.cpp`,
          },
        },
      }],
    });
    messages.push({
      role: "tool",
      content: [{ type: "toolCallResult", toolCallId: id, content: `implementation-${index}` }],
    });
  }
  return Chat.from({ messages });
}


test("latest exact-path validator result clears a persisted full-replan mode", () => {
  const { architectureGateStatus } = require("../dist/generator.js");
  const history = Chat.from({
    messages: [
      { role: "system", content: [{ type: "text", text: "rules" }] },
      {
        role: "user",
        content: [{ type: "text", text: "Validate templates_lobby architecture." }],
      },
      {
        role: "assistant",
        content: [{
          type: "toolCallRequest",
          toolCallRequest: {
            id: "full-replan",
            type: "function",
            name: "unreal_architecture_reasoning",
            arguments: { proposal: { decision: "bad owner" } },
          },
        }],
      },
      {
        role: "tool",
        content: [{
          type: "toolCallResult",
          toolCallId: "full-replan",
          content: JSON.stringify({
            ok: false,
            proposalRevision: "r1",
            proposalValidation: {
              ok: false,
              repairStrategy: "full_replan",
              designContract: { requiresFullReplan: true },
            },
            repairSubmission: { mode: "fullProposal" },
          }),
        }],
      },
      {
        role: "assistant",
        content: [{
          type: "toolCallRequest",
          toolCallRequest: {
            id: "exact-repair",
            type: "function",
            name: "unreal_architecture_reasoning",
            arguments: { proposal: { decision: "fixed owner, ancillary issues remain" } },
          },
        }],
      },
      {
        role: "tool",
        content: [{
          type: "toolCallResult",
          toolCallId: "exact-repair",
          content: JSON.stringify({
            ok: false,
            errorCode: "ARCHITECTURE_PROPOSAL_INVALID",
            proposalRevision: "r2",
            proposalValidation: {
              ok: false,
              repairStrategy: "exact_paths",
              designContract: { requiresFullReplan: false },
            },
            repairSubmission: {
              mode: "proposalRepairs",
              requiredJsonPaths: ["assetMigration"],
            },
          }),
        }],
      },
    ],
  });
  const checkpoint = {
    architectureProposal: {
      validationOk: false,
      repairStrategy: "full_replan",
      repairMode: "fullProposal",
      requiresFullReplan: true,
    },
  };

  const status = architectureGateStatus(history, checkpoint);

  assert.equal(status.lastRepairStrategy, "exact_paths");
  assert.equal(status.lastRepairMode, "proposalRepairs");
  assert.equal(status.requiresFullProposal, false);
});


test("deleted architecture call is abandoned without clearing unresolved write calls", () => {
  const { reconcilePendingToolCalls } = require("../dist/generator.js");
  const result = reconcilePendingToolCalls(
    [
      { id: "deleted-architecture", name: "unreal_architecture_reasoning" },
      { id: "missing-write", name: "write_file" },
      { id: "active-architecture", name: "unreal_architecture_reasoning" },
      { id: "completed-read", name: "read_file" },
    ],
    [
      {
        toolCalls: [{ id: "active-architecture", name: "unreal_architecture_reasoning" }],
        toolResults: [{ toolCallId: "completed-read", name: "read_file" }],
      },
    ],
  );

  assert.deepEqual(result.abandonedIds, ["deleted-architecture"]);
  assert.deepEqual(result.matchedIds, ["completed-read"]);
  assert.deepEqual(
    result.remainingPending.map((pending) => pending.id),
    ["missing-write", "active-architecture"],
  );
});


test("full architecture replan reopens bounded source discovery with a complete contract schema", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-architecture-refill-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let capturedTools = null;
    const model = {
      identifier: "architecture-refill-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        capturedTools = opts.rawTools;
        opts.onToolCallRequestStart(1, { toolCallId: "refill-read" });
        opts.onToolCallRequestNameReceived(1, "read_file");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"path":"Source/Authority.cpp"}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "refill-read",
            type: "function",
            name: "read_file",
            arguments: { path: "Source/Authority.cpp" },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const tools = [
      { type: "function", function: { name: "read_file", parameters: { type: "object" } } },
      { type: "function", function: { name: "replace_in_file", parameters: { type: "object" } } },
      fullArchitectureToolSchema(),
    ];

    await generate(
      controllerFor(model, {
        enabled: true,
        targetModel: "",
        architectureReplanEvidenceReadBudget: 4,
      }, stateRoot, emitted, tools),
      failedFullReplanHistory(0, 5),
    );

    assert.notEqual(capturedTools.force, true);
    assert.deepEqual(
      capturedTools.tools.map((tool) => tool.function.name),
      ["read_file", "unreal_architecture_reasoning"],
    );
    const proposalSchema = capturedTools.tools[1].function.parameters.properties.proposal;
    assert.ok(proposalSchema.required.includes("ownership"));
    assert.ok(proposalSchema.required.includes("networking"));
    assert.ok(proposalSchema.required.includes("migrationPlan"));
    assert.ok(proposalSchema.required.includes("implementationSlices"));
    assert.deepEqual(
      proposalSchema.properties.networking.required,
      [
        "authorityOwner", "clientInitiated", "requestPath", "rpcOwner",
        "owningConnection", "serverValidation", "replicatedState",
      ],
    );
    assert.equal(emitted.filter((event) => event.kind === "end").length, 1);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("full architecture replan is forced again after the bounded evidence refill", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-architecture-reforce-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let capturedTools = null;
    const model = {
      identifier: "architecture-reforce-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        capturedTools = opts.rawTools;
        opts.onToolCallRequestStart(1, { toolCallId: "reforced-proposal" });
        opts.onToolCallRequestNameReceived(1, "unreal_architecture_reasoning");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"proposal":{"decision":"replanned"}}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "reforced-proposal",
            type: "function",
            name: "unreal_architecture_reasoning",
            arguments: { proposal: completeArchitectureProposal("replanned") },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };

    await generate(
      controllerFor(model, {
        enabled: true,
        targetModel: "",
        architectureReplanEvidenceReadBudget: 4,
      }, stateRoot, emitted, [
        { type: "function", function: { name: "read_file", parameters: { type: "object" } } },
        fullArchitectureToolSchema(),
      ]),
      failedFullReplanHistory(4, 5),
    );

    assert.equal(capturedTools.force, true);
    assert.deepEqual(
      capturedTools.tools.map((tool) => tool.function.name),
      ["unreal_architecture_reasoning"],
    );
    assert.ok(
      capturedTools.tools[0].function.parameters.properties.proposal.required.includes("networking"),
    );
    assert.equal(emitted.filter((event) => event.kind === "end").length, 1);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("short recovery follow-up preserves the staged network contract", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-architecture-contract-continuity-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let capturedTools = null;
    const model = {
      identifier: "architecture-contract-continuity-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        capturedTools = opts.rawTools;
        const proposal = completeArchitectureProposal("continued-replan");
        opts.onToolCallRequestStart(1, { toolCallId: "continued-replan" });
        opts.onToolCallRequestNameReceived(1, "unreal_architecture_reasoning");
        opts.onToolCallRequestArgumentFragmentGenerated(1, JSON.stringify({ proposal }));
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "continued-replan",
            type: "function",
            name: "unreal_architecture_reasoning",
            arguments: { proposal },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const history = failedFullReplanHistory(4, 5);
    history.append("user", "Continue this validation.");

    await generate(
      controllerFor(model, {
        enabled: true,
        targetModel: "",
        architectureReplanEvidenceReadBudget: 4,
      }, stateRoot, emitted, [
        { type: "function", function: { name: "read_file", parameters: { type: "object" } } },
        fullArchitectureToolSchema(),
      ]),
      history,
    );

    const proposalSchema = capturedTools.tools.find(
      (tool) => tool.function.name === "unreal_architecture_reasoning",
    ).function.parameters.properties.proposal;
    assert.ok(proposalSchema.required.includes("ownership"));
    assert.ok(proposalSchema.required.includes("networking"));
    assert.deepEqual(
      proposalSchema.properties.networking.required,
      [
        "authorityOwner", "clientInitiated", "requestPath", "rpcOwner",
        "owningConnection", "serverValidation", "replicatedState",
      ],
    );
    const checkpoint = activeCheckpoint(stateRoot);
    assert.equal(checkpoint.architectureProposal.stagedContractRequired, true);
    assert.equal(checkpoint.architectureProposal.networkedContractRequired, true);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("malformed full-replan payload is withheld and repaired once before commit", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-architecture-payload-repair-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let predictionCount = 0;
    let sawRepairRule = false;
    const model = {
      identifier: "architecture-payload-repair-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(history, opts) {
        predictionCount += 1;
        if (predictionCount === 2) {
          sawRepairRule = history.getMessagesArray().some((message) => (
            message.getRole() === "system"
            && message.getText().includes("[UNREAL_ARCHITECTURE_PAYLOAD_REPAIR_REQUIRED]")
            && message.getText().includes("proposal.networking.authorityOwner")
          ));
        }
        const proposal = predictionCount === 1
          ? { decision: "schema-incomplete" }
          : completeArchitectureProposal("schema-repaired");
        opts.onToolCallRequestStart(1, { toolCallId: `payload-repair-${predictionCount}` });
        opts.onToolCallRequestNameReceived(1, "unreal_architecture_reasoning");
        opts.onToolCallRequestArgumentFragmentGenerated(1, JSON.stringify({ proposal }));
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: `payload-repair-${predictionCount}`,
            type: "function",
            name: "unreal_architecture_reasoning",
            arguments: { proposal },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };

    await generate(
      controllerFor(model, {
        enabled: true,
        targetModel: "",
        architectureReplanEvidenceReadBudget: 4,
      }, stateRoot, emitted, [
        { type: "function", function: { name: "read_file", parameters: { type: "object" } } },
        fullArchitectureToolSchema(),
      ]),
      failedFullReplanHistory(4, 5),
    );

    assert.equal(predictionCount, 2);
    assert.equal(sawRepairRule, true);
    assert.equal(emitted.filter((event) => event.kind === "end").length, 1);
    assert.equal(
      emitted.find((event) => event.kind === "end").request.arguments.proposal.decision,
      "schema-repaired",
    );
    assert.equal(activeCheckpoint(stateRoot).pendingToolCalls.length, 1);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("architecture payload repair is bounded and leaves the validator required", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-architecture-payload-fail-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let predictionCount = 0;
    const model = {
      identifier: "architecture-payload-fail-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        predictionCount += 1;
        const proposal = { decision: `still-incomplete-${predictionCount}` };
        opts.onToolCallRequestStart(1, { toolCallId: `payload-fail-${predictionCount}` });
        opts.onToolCallRequestNameReceived(1, "unreal_architecture_reasoning");
        opts.onToolCallRequestArgumentFragmentGenerated(1, JSON.stringify({ proposal }));
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: `payload-fail-${predictionCount}`,
            type: "function",
            name: "unreal_architecture_reasoning",
            arguments: { proposal },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };

    await assert.rejects(
      generate(
        controllerFor(model, {
          enabled: true,
          targetModel: "",
          architectureReplanEvidenceReadBudget: 4,
        }, stateRoot, emitted, [
          { type: "function", function: { name: "read_file", parameters: { type: "object" } } },
          fullArchitectureToolSchema(),
        ]),
        failedFullReplanHistory(4, 5),
      ),
      /after one bounded payload repair.*required JSON-schema paths/i,
    );

    assert.equal(predictionCount, 2);
    assert.deepEqual(emitted, []);
    assert.equal(activeCheckpoint(stateRoot).requiredNextTool.name, "unreal_architecture_reasoning");
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("directory-only replan discovery consumes the bounded refill budget", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-architecture-discovery-budget-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let capturedTools = null;
    const model = {
      identifier: "architecture-discovery-budget-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        capturedTools = opts.rawTools;
        opts.onToolCallRequestStart(1, { toolCallId: "discovery-budget-proposal" });
        opts.onToolCallRequestNameReceived(1, "unreal_architecture_reasoning");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"proposal":{"decision":"replanned"}}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "discovery-budget-proposal",
            type: "function",
            name: "unreal_architecture_reasoning",
            arguments: { proposal: completeArchitectureProposal("replanned") },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };

    await generate(
      controllerFor(model, {
        enabled: true,
        targetModel: "",
        architectureReplanEvidenceReadBudget: 4,
      }, stateRoot, emitted, [
        { type: "function", function: { name: "list_directory", parameters: { type: "object" } } },
        fullArchitectureToolSchema(),
      ]),
      failedFullReplanHistory(4, 5, "list_directory"),
    );

    assert.equal(capturedTools.force, true);
    assert.deepEqual(
      capturedTools.tools.map((tool) => tool.function.name),
      ["unreal_architecture_reasoning"],
    );
    assert.equal(emitted.filter((event) => event.kind === "end").length, 1);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("unchanged replan core forces an immediate complete resubmission with negative paths", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-architecture-core-unchanged-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let capturedTools = null;
    let sawCoreRule = false;
    const model = {
      identifier: "architecture-core-unchanged-model",
      async applyPromptTemplate(history) {
        sawCoreRule = history.getMessagesArray().some((message) => (
          message.getRole() === "system"
          && message.getText().includes("[UNREAL_ARCHITECTURE_CORE_CHANGE_REQUIRED]")
          && message.getText().includes("stateInventory")
          && message.getText().includes("assetMigration")
        ));
        return "formatted";
      },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        capturedTools = opts.rawTools;
        opts.onToolCallRequestStart(1, { toolCallId: "core-changed-proposal" });
        opts.onToolCallRequestNameReceived(1, "unreal_architecture_reasoning");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"proposal":{"decision":"replanned"}}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "core-changed-proposal",
            type: "function",
            name: "unreal_architecture_reasoning",
            arguments: { proposal: completeArchitectureProposal("replanned") },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const history = failedFullReplanHistory(0, 5, "read_file", {
      errorCode: "ARCHITECTURE_PROPOSAL_REPLAN_CORE_UNCHANGED",
      requiredChangedPaths: ["stateInventory", "assetMigration"],
    });

    await generate(
      controllerFor(model, {
        enabled: true,
        targetModel: "",
        architectureReplanEvidenceReadBudget: 4,
      }, stateRoot, emitted, [
        { type: "function", function: { name: "read_file", parameters: { type: "object" } } },
        fullArchitectureToolSchema(),
      ]),
      history,
    );

    assert.equal(capturedTools.force, true);
    assert.deepEqual(
      capturedTools.tools.map((tool) => tool.function.name),
      ["unreal_architecture_reasoning"],
    );
    assert.equal(sawCoreRule, true);
    assert.deepEqual(
      activeCheckpoint(stateRoot).architectureProposal.unchangedCorePaths,
      ["stateInventory", "assetMigration"],
    );
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("architecture final text gets one forced validator recovery in the same turn", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-architecture-final-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let predictionCount = 0;
    const model = {
      identifier: "architecture-final-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        predictionCount += 1;
        if (predictionCount === 1) {
          opts.onPredictionFragment({ content: "unvalidated final design" });
          return { async result() { return { stats: { stopReason: "eosFound" } }; } };
        }
        assert.equal(opts.rawTools.force, true);
        assert.deepEqual(
          opts.rawTools.tools.map((tool) => tool.function.name),
          ["unreal_architecture_reasoning"],
        );
        opts.onToolCallRequestStart(1, { toolCallId: "architecture-recovery" });
        opts.onToolCallRequestNameReceived(1, "unreal_architecture_reasoning");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"proposal":{"decision":"replanned"}}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "architecture-recovery",
            type: "function",
            name: "unreal_architecture_reasoning",
            arguments: { proposal: completeArchitectureProposal("replanned") },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const tools = [fullArchitectureToolSchema()];
    const history = Chat.empty();
    history.append("system", "rules");
    history.append("user", "validate the templates_lobby architecture");

    await generate(
      controllerFor(model, { enabled: true, targetModel: "" }, stateRoot, emitted, tools),
      history,
    );

    assert.equal(predictionCount, 2);
    assert.equal(emitted.filter((event) => event.kind === "fragment").length, 0);
    assert.equal(emitted.filter((event) => event.kind === "end").length, 1);
    assert.equal(
      emitted.find((event) => event.kind === "end").request.name,
      "unreal_architecture_reasoning",
    );
    const checkpoint = activeCheckpoint(stateRoot);
    assert.equal(checkpoint.pendingToolCalls.length, 1);
    assert.equal(checkpoint.pendingToolCalls[0].name, "unreal_architecture_reasoning");
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("architecture final recovery is bounded and remains fail closed", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-architecture-final-fail-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let predictionCount = 0;
    const model = {
      identifier: "architecture-final-fail-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        predictionCount += 1;
        opts.onPredictionFragment({ content: `unvalidated design ${predictionCount}` });
        return { async result() { return { stats: { stopReason: "eosFound" } }; } };
      },
    };
    const history = Chat.empty();
    history.append("system", "rules");
    history.append("user", "validate the templates_lobby architecture");

    await assert.rejects(
      generate(
        controllerFor(
          model,
          { enabled: true, targetModel: "" },
          stateRoot,
          emitted,
          [fullArchitectureToolSchema()],
        ),
        history,
      ),
      /Architecture final output was discarded.*proposalValidation\.ok=true/i,
    );

    assert.equal(predictionCount, 2);
    assert.deepEqual(emitted, []);
    assert.equal(activeCheckpoint(stateRoot).requiredNextTool.name, "unreal_architecture_reasoning");
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("one LM Studio conversation keeps one checkpoint session across mutable lineage", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-conversation-session-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const model = {
      identifier: "conversation-session-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        opts.onPredictionFragment({ content: "done" });
        return { async result() { return {}; } };
      },
    };
    const workingDirectory = "C:\\Users\\dev\\.lmstudio\\working-directories\\1786265188981";
    const controller = controllerFor(model, {}, stateRoot, [], [], workingDirectory);
    const first = Chat.empty();
    first.append("system", "rules");
    first.append("user", "inspect the project");
    first.append("assistant", "partial tool turn A");
    await generate(controller, first);

    const continued = Chat.empty();
    continued.append("system", "rules");
    continued.append("user", "inspect the project");
    continued.append("assistant", "same turn finalized with different content and tools");
    continued.append("user", "continue");
    await generate(controller, continued);

    const sessionDirs = fs.readdirSync(stateRoot, { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && entry.name !== "_base");
    assert.equal(sessionDirs.length, 1);
    assert.equal(activeCheckpoint(stateRoot).objective, "continue");
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

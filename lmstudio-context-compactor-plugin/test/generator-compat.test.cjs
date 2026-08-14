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

test("GUI abort cancels an in-flight prediction without waiting for the backend result", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-abort-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const abortController = new AbortController();
    let predictionStarted;
    const started = new Promise((resolve) => { predictionStarted = resolve; });
    let cancelCalled = false;
    const model = {
      identifier: "abort-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond() {
        predictionStarted();
        return {
          cancel() { cancelCalled = true; },
          result() { return new Promise(() => {}); },
        };
      },
    };
    const controller = controllerFor(
      model,
      { requireCheckpointPersistence: false },
      stateRoot,
      emitted,
      [],
    );
    controller.abortSignal = abortController.signal;
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "Explain the current state." }] },
    ] });

    const generation = generate(controller, history);
    await started;
    abortController.abort(new Error("user stopped generation"));
    await assert.rejects(
      Promise.race([
        generation,
        new Promise((_resolve, reject) => setTimeout(
          () => reject(new Error("abort did not return within 1000ms")),
          1000,
        )),
      ]),
      /user stopped generation/,
    );
    assert.equal(cancelCalled, true);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("feature intent atomic rule survives repeated generator preparation without duplicate ceremony", () => {
  const { injectFeatureIntentAtomicRule } = require("../dist/generator.js");
  const history = Chat.empty();
  history.append("system", "base rules");
  history.append("user", "implement the bounded feature");

  assert.equal(injectFeatureIntentAtomicRule(history), true);
  assert.equal(injectFeatureIntentAtomicRule(history), true);

  const systemText = history.getMessagesArray()
    .filter((message) => message.getRole() === "system")
    .map((message) => message.getText())
    .join("\n");
  assert.equal((systemText.match(/\[UNREAL_FEATURE_INTENT_ATOMIC_GATE\]/g) || []).length, 1);
  assert.match(systemText, /one unreal_feature_intent_resolve model-facing call/);
  assert.match(systemText, /Never call unreal_task_define_slices separately/);
});

test("task route ownership rule distinguishes available and missing planners", () => {
  const { injectTaskRouteOwnershipRule } = require("../dist/generator.js");
  const available = Chat.empty();
  available.append("system", "base rules");
  available.append("user", "implement the feature");
  assert.equal(injectTaskRouteOwnershipRule(available, true), true);
  assert.equal(injectTaskRouteOwnershipRule(available, true), true);
  const availableText = available.getMessagesArray().map((message) => message.getText()).join("\n");
  assert.equal((availableText.match(/\[UNREAL_TASK_ROUTE_OWNERSHIP_GATE\]/g) || []).length, 1);
  assert.match(availableText, /call unreal_agent_plan once/);
  assert.match(availableText, /Never construct, guess, or repair taskAuthorization/);

  const missing = Chat.empty();
  missing.append("system", "base rules");
  missing.append("user", "implement the feature");
  assert.equal(injectTaskRouteOwnershipRule(missing, false), true);
  const missingText = missing.getMessagesArray().map((message) => message.getText()).join("\n");
  assert.match(missingText, /mcp\/unreal-rag planner provider is missing/);
  assert.match(missingText, /do not claim implementation or attempt writes/);
});

test("server-owned task authorization is injected into eligible tool calls", () => {
  const { enrichToolRequestControl } = require("../dist/generator.js");
  const ownership = { taskSessionId: "task-session-1", ownerCapability: "owner-capability-1" };
  const request = {
    id: "sketch-1",
    type: "function",
    name: "unreal_code_sketch_claim_validate",
    arguments: { sketch: "void Test() {}" },
  };
  const tools = [{
    type: "function",
    function: {
      name: "unreal_code_sketch_claim_validate",
      parameters: {
        type: "object",
        properties: {
          sketch: { type: "string" },
          taskAuthorization: { type: "object" },
        },
      },
    },
  }];

  const enriched = enrichToolRequestControl(
    request,
    "compactor-session-1",
    { taskRouteOwnership: ownership },
    "implement the feature",
    tools,
  );

  assert.deepEqual(enriched.arguments.taskAuthorization, ownership);
  assert.equal(enriched.arguments.sketch, "void Test() {}");
});

test("active write task exposes a detached read-only side-query route and preserves its gate", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-side-query-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const ownership = { taskSessionId: "task-side-query", ownerCapability: "owner-side-query" };
    const route = {
      ok: true,
      taskAuthorization: ownership,
      toolRoute: {
        routeHash: "route-side-query",
        phase: "verifier",
        activeTools: ["unreal_code_sketch_claim_validate"],
        pendingGates: ["unreal_code_sketch_claim_validate"],
      },
      requiredNextTool: "unreal_code_sketch_claim_validate",
      control: {
        version: 1,
        phase: "unreal_agent_plan",
        status: "NeedsAction",
        nextAction: "unreal_code_sketch_claim_validate",
        nextActionIsTool: true,
      },
    };
    const history = Chat.from({ messages: [
      { role: "system", content: [{ type: "text", text: "base rules" }] },
      { role: "user", content: [{ type: "text", text: "로컬 입력을 검증하고 필요한 최소 수정 후 빌드해" }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "plan-side", type: "function", name: "unreal_agent_plan", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "plan-side", name: "unreal_agent_plan", content: JSON.stringify(route),
      }] },
      { role: "user", content: [{ type: "text", text: "지금 프로젝트 구조만 알려줘" }] },
    ] });
    const tools = [
      { type: "function", function: { name: "list_directory", parameters: { type: "object", properties: { path: { type: "string" } } } } },
      { type: "function", function: { name: "read_file", parameters: { type: "object", properties: { path: { type: "string" } } } } },
      { type: "function", function: { name: "list_active_tasks", parameters: { type: "object", properties: {} } } },
      { type: "function", function: { name: "unreal_code_sketch_claim_validate", parameters: { type: "object", properties: {} } } },
      { type: "function", function: { name: "apply_edit_bundle", parameters: { type: "object", properties: {} } } },
    ];
    const model = {
      identifier: "side-query-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(modelHistory, opts) {
        assert.equal(opts.rawTools.force, undefined);
        assert.deepEqual(
          opts.rawTools.tools.map((tool) => tool.function.name).sort(),
          ["list_directory", "read_file"],
        );
        const systemText = modelHistory.getMessagesArray()
          .filter((message) => message.getRole() === "system")
          .map((message) => message.getText()).join("\n");
        assert.match(systemText, /UNREAL_DETACHED_SIDE_QUERY/);
        opts.onToolCallRequestStart(1, { toolCallId: "side-list" });
        opts.onToolCallRequestNameReceived(1, "list_directory");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"path":"project://"}');
        opts.onToolCallRequestEnd(1, { toolCallRequest: {
          id: "side-list",
          type: "function",
          name: "list_directory",
          arguments: { path: "project://" },
        } });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    const end = emitted.find((event) => event.kind === "end");
    assert.ok(end);
    assert.deepEqual(end.request.arguments, { path: "project://" });
    const checkpoint = activeCheckpoint(stateRoot);
    assert.equal(checkpoint.objective, "로컬 입력을 검증하고 필요한 최소 수정 후 빌드해");
    assert.equal(checkpoint.sideQuery.request, "지금 프로젝트 구조만 알려줘");
    assert.equal(checkpoint.requiredNextTool.name, "unreal_code_sketch_claim_validate");
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("detached read injects ownership fields only when its schema declares them", () => {
  const { enrichToolRequestControl } = require("../dist/generator.js");
  const ownership = { taskSessionId: "task-observe", ownerCapability: "owner-observe" };
  const tools = [{
    type: "function",
    function: {
      name: "read_file",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string" },
          sessionId: { type: "string" },
          taskAuthorization: { type: "object" },
          taskObservation: { type: "object" },
        },
      },
    },
  }];
  const enriched = enrichToolRequestControl(
    { name: "read_file", arguments: { path: "project://README.md" } },
    "compactor-observe",
    {
      taskRouteOwnership: ownership,
      sideQuery: { active: true, request: "구조만 알려줘" },
    },
    "구조만 알려줘",
    tools,
  );

  assert.equal(enriched.arguments.sessionId, "compactor-observe");
  assert.deepEqual(enriched.arguments.taskAuthorization, ownership);
  assert.equal(enriched.arguments.taskObservation.mode, "detached_read_only");
  assert.match(enriched.arguments.taskObservation.requestHash, /^[a-f0-9]{64}$/);
});

test("two unchanged active-task listings are bounded within one user turn", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-control-boundary-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let advertisedTools = [];
    let systemText = "";
    const model = {
      identifier: "control-boundary-model",
      async getContextLength() { return 100000; },
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      respond(modelHistory, opts) {
        advertisedTools = (opts.rawTools?.tools || []).map((tool) => tool.function?.name || tool.name);
        systemText = modelHistory.getMessagesArray()
          .filter((message) => message.getRole() === "system")
          .map((message) => message.getText()).join("\n");
        return { async result() { return { stats: { stopReason: "endOfSequence" } }; } };
      },
    };
    const repeatedPayload = {
      ok: true,
      tasks: [{ taskSessionId: "task-1", status: "running", activeSliceId: "slice-a" }],
    };
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "프로젝트 기능 구현을 계속해" }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "list-1", type: "function", name: "list_active_tasks", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "list-1", name: "list_active_tasks",
        content: JSON.stringify({ ...repeatedPayload, updatedAt: "2026-08-13T01:00:00Z" }),
      }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "list-2", type: "function", name: "list_active_tasks", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "list-2", name: "list_active_tasks",
        content: JSON.stringify({ ...repeatedPayload, updatedAt: "2026-08-13T01:00:05Z" }),
      }] },
    ] });
    const tools = ["list_active_tasks", "read_file"].map((name) => ({
      type: "function",
      function: { name, parameters: { type: "object", properties: {} } },
    }));

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    assert.deepEqual(advertisedTools, ["read_file"]);
    assert.match(systemText, /UNREAL_UNCHANGED_CONTROL_BOUNDARY/);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("provider-qualified required feature gate is forced with server-owned auth", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-required-feature-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const ownership = { taskSessionId: "task-feature-1", ownerCapability: "owner-feature-1" };
    const model = {
      identifier: "required-feature-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        assert.equal(opts.rawTools.force, true);
        assert.deepEqual(
          opts.rawTools.tools.map((tool) => tool.function.name),
          ["mcp/unreal-rag/unreal_feature_intent_resolve"],
        );
        assert.equal(opts.rawTools.tools[0].function.parameters.required.includes("taskAuthorization"), false);
        opts.onToolCallRequestStart(1, { toolCallId: "feature-1" });
        opts.onToolCallRequestNameReceived(1, "mcp/unreal-rag/unreal_feature_intent_resolve");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"selectedIntentId":"bounded_local"}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "feature-1",
            type: "function",
            name: "mcp/unreal-rag/unreal_feature_intent_resolve",
            arguments: { selectedIntentId: "bounded_local" },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const route = {
      ok: true,
      taskAuthorization: ownership,
      toolRoute: {
        routeHash: "route-feature-1",
        phase: "planner",
        activeTools: ["read_file", "unreal_feature_intent_resolve"],
        selectedSlice: { sliceId: "task", files: [] },
      },
      requiredNextTool: "unreal_feature_intent_resolve",
      requiredNextToolArgs: { taskAuthorization: ownership },
      control: {
        version: 1,
        phase: "unreal_agent_plan",
        status: "NeedsAction",
        nextAction: "unreal_feature_intent_resolve",
        nextActionIsTool: true,
        retryPolicy: "none",
      },
    };
    const messages = [
      { role: "user", content: [{ type: "text", text: "implement the bounded local feature" }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "plan-feature-1", type: "function", name: "unreal_agent_plan", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "plan-feature-1", name: "unreal_agent_plan", content: JSON.stringify(route),
      }] },
    ];
    for (const [index, file] of [
      "Source/Demo/RuleEngine.h",
      "Source/Demo/RuleEngine.cpp",
      "Source/Demo/Controller.h",
    ].entries()) {
      messages.push({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: `required-feature-read-${index}`, type: "function", name: "read_file", arguments: { path: file },
      } }] });
      messages.push({ role: "tool", content: [{
        type: "toolCallResult",
        toolCallId: `required-feature-read-${index}`,
        name: "read_file",
        content: JSON.stringify({ ok: true, path: file, content: `// ${file}` }),
      }] });
    }
    const history = Chat.from({ messages });
    const tools = [{
      type: "function",
      function: { name: "read_file", parameters: { type: "object", properties: { path: { type: "string" } } } },
    }, {
      type: "function",
      function: {
        name: "mcp/unreal-rag/unreal_feature_intent_resolve",
        parameters: {
          type: "object",
          properties: {
            selectedIntentId: { type: "string" },
            taskAuthorization: { type: "object" },
          },
          required: ["taskAuthorization"],
        },
      },
    }];

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    const end = emitted.find((event) => event.kind === "end");
    assert.ok(end);
    assert.equal(end.request.arguments.selectedIntentId, "bounded_local");
    assert.deepEqual(end.request.arguments.taskAuthorization, ownership);
    assert.equal(emitted.some((event) => event.kind === "failure"), false);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("shared system-rule upsert replaces dynamic server requirements without adding a system message", () => {
  const { injectServerRequiredToolRule } = require("../dist/generator.js");
  const history = Chat.empty();
  history.append("system", "base rules");
  history.append("user", "implement the bounded feature");

  assert.equal(injectServerRequiredToolRule(history, "read_file", { path: "Source/A.cpp" }), true);
  assert.equal(injectServerRequiredToolRule(history, "build_unreal_project", { target: "DemoEditor" }), true);

  const systemMessages = history.getMessagesArray().filter((message) => message.getRole() === "system");
  const systemText = systemMessages.map((message) => message.getText()).join("\n");
  assert.equal(systemMessages.length, 1);
  assert.equal((systemText.match(/\[UNREAL_SERVER_REQUIRED_TOOL\]/g) || []).length, 1);
  assert.doesNotMatch(systemText, /Source\/A\.cpp/);
  assert.match(systemText, /build_unreal_project/);
  assert.match(systemText, /DemoEditor/);
});

test("control v2 disables local handoffs and projects only server allowed schemas", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-control-v2-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const model = {
      identifier: "control-v2-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        assert.deepEqual(
          (opts.rawTools?.tools || []).map((tool) => tool.function.name),
          ["read_file"],
        );
        assert.equal(opts.rawTools?.force, undefined);
        opts.onPredictionFragment({ content: "continue with bounded discovery", reasoningType: "none" });
        return { async result() { return { stats: { stopReason: "eosFound" } }; } };
      },
    };
    const payload = {
      ok: true,
      taskAuthorization: {
        taskSessionId: "task-control-v2",
        ownerCapability: "owner-control-v2",
      },
      control: {
        version: 2,
        epoch: 12,
        taskSessionId: "task-control-v2",
        routeHash: "route-control-v2",
        phase: "planner",
        disposition: "continue",
        allowedTools: ["read_file"],
        retryPolicy: { sameSemanticInput: "allowed" },
      },
      nestedLegacy: {
        requiredNextTool: "unreal_evidence_first_contract",
        nextAction: "unreal_feature_intent_resolve",
        nextActionIsTool: true,
      },
    };
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "implement the missing feature completely" }] },
      { role: "tool", content: [{
        type: "toolCallResult",
        toolCallId: "route-v2",
        name: "unreal_agent_plan",
        content: JSON.stringify(payload),
      }] },
    ] });
    const tools = [
      "read_file",
      "search_files",
      "unreal_evidence_first_contract",
      "unreal_feature_intent_resolve",
      "unreal_agent_plan",
    ].map((name) => ({
      type: "function",
      function: { name, parameters: { type: "object", properties: {} } },
    }));

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    assert.equal(emitted.some((event) => event.kind === "failure"), false);
    assert.match(emitted.find((event) => event.kind === "fragment").content, /bounded discovery/);
    const persisted = activeCheckpoint(stateRoot);
    assert.equal(persisted.serverControl.epoch, 12);
    assert.equal(persisted.requiredNextTool, null);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("control v2 required tool is forced as one exact schema with server arguments", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-control-v2-required-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const model = {
      identifier: "control-v2-required-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        assert.equal(opts.rawTools.force, true);
        assert.deepEqual(opts.rawTools.tools.map((tool) => tool.function.name), ["replace_in_file"]);
        opts.onToolCallRequestStart(1, { toolCallId: "write-v2" });
        opts.onToolCallRequestNameReceived(1, "replace_in_file");
        opts.onToolCallRequestArgumentFragmentGenerated(1, "{}");
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "write-v2",
            type: "function",
            name: "replace_in_file",
            arguments: { oldText: "old", newText: "new" },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const ownership = { taskSessionId: "task-control-v2", ownerCapability: "owner-control-v2" };
    const payload = {
      ok: true,
      taskAuthorization: ownership,
      control: {
        version: 2,
        epoch: 13,
        taskSessionId: "task-control-v2",
        routeHash: "route-control-v2",
        phase: "implementation",
        disposition: "require_tool",
        requiredTool: { name: "replace_in_file", args: { path: "Source/Demo/Rule.cpp" } },
        allowedTools: ["replace_in_file"],
        retryPolicy: { sameSemanticInput: "allowed" },
      },
    };
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "apply the bounded fix" }] },
      { role: "tool", content: [{
        type: "toolCallResult",
        toolCallId: "route-v2-required",
        name: "unreal_task_status",
        content: JSON.stringify(payload),
      }] },
    ] });
    const tools = [{
      type: "function",
      function: {
        name: "replace_in_file",
        parameters: {
          type: "object",
          properties: {
            path: { type: "string" },
            oldText: { type: "string" },
            newText: { type: "string" },
            taskAuthorization: { type: "object" },
          },
          required: ["path", "oldText", "newText", "taskAuthorization"],
        },
      },
    }, {
      type: "function",
      function: { name: "read_file", parameters: { type: "object", properties: {} } },
    }];

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    const end = emitted.find((event) => event.kind === "end");
    assert.equal(end.request.arguments.path, "Source/Demo/Rule.cpp");
    assert.deepEqual(end.request.arguments.taskAuthorization, ownership);
    assert.equal(emitted.some((event) => event.kind === "failure"), false);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("control v2 recovers an exact server-owned read when the chat catalog drops its schema", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-control-v2-read-recovery-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let predictionCount = 0;
    const model = {
      identifier: "control-v2-read-recovery-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        predictionCount += 1;
        assert.equal(opts.rawTools.force, true);
        assert.deepEqual(opts.rawTools.tools.map((tool) => tool.function.name), ["read_file"]);
        assert.deepEqual(opts.rawTools.tools[0].function.parameters.required, []);
        opts.onToolCallRequestStart(1, { toolCallId: "read-recovered-v2" });
        opts.onToolCallRequestNameReceived(1, "read_file");
        opts.onToolCallRequestArgumentFragmentGenerated(1, "{}");
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "read-recovered-v2",
            type: "function",
            name: "read_file",
            arguments: {},
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const ownership = { taskSessionId: "task-control-v2-read", ownerCapability: "owner-control-v2-read" };
    const requiredPath = "Source/Project_MJS/Public/Animation/CPlayerCharacterAnimInstance.h";
    const payload = {
      ok: false,
      taskAuthorization: ownership,
      control: {
        version: 2,
        epoch: 1,
        taskSessionId: "task-control-v2-read",
        routeHash: "route-control-v2-read",
        phase: "planner",
        disposition: "require_tool",
        requiredTool: { name: "read_file", args: { path: requiredPath } },
        allowedTools: ["read_file"],
        retryPolicy: { sameSemanticInput: "forbidden" },
      },
    };
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "continue the implementation" }] },
      { role: "tool", content: [{
        type: "toolCallResult",
        toolCallId: "feature-intent-v2-read",
        name: "unreal_feature_intent_resolve",
        content: JSON.stringify(payload),
      }] },
    ] });
    const staleChatTools = [{
      type: "function",
      function: { name: "unreal_feature_intent_resolve", parameters: { type: "object", properties: {} } },
    }];

    await generate(controllerFor(model, {}, stateRoot, emitted, staleChatTools), history);

    assert.equal(predictionCount, 1);
    const end = emitted.find((event) => event.kind === "end");
    assert.ok(end);
    assert.equal(end.request.name, "read_file");
    assert.equal(end.request.arguments.path, requiredPath);
    assert.ok(end.request.arguments.sessionId);
    const sessionDir = fs.readdirSync(stateRoot, { withFileTypes: true })
      .find((entry) => entry.isDirectory() && !entry.name.startsWith(".") && entry.name !== "_base");
    const events = fs.readFileSync(path.join(stateRoot, sessionDir.name, "events.jsonl"), "utf8")
      .trim().split(/\r?\n/).map((line) => JSON.parse(line));
    assert.ok(events.some((event) => (
      event.type === "server_control_read_schema_recovered"
      && event.requiredTool === "read_file"
      && event.epoch === 1
    )));
    assert.ok(events.some((event) => (
      event.type === "context_measurement"
      && event.serverControlReadSchemaRecovered === true
      && event.requiredToolSchemaMissing === false
    )));
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("control v2 still blocks a missing mutation schema before model invocation", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-control-v2-write-missing-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let predictionCount = 0;
    const model = {
      identifier: "control-v2-write-missing-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond() {
        predictionCount += 1;
        throw new Error("target model must not run without the required mutation schema");
      },
    };
    const payload = {
      ok: true,
      taskAuthorization: { taskSessionId: "task-control-v2-write", ownerCapability: "owner-control-v2-write" },
      control: {
        version: 2,
        epoch: 2,
        taskSessionId: "task-control-v2-write",
        routeHash: "route-control-v2-write",
        phase: "executor",
        disposition: "require_tool",
        requiredTool: { name: "replace_in_file", args: { path: "Source/Demo/Foo.cpp" } },
        allowedTools: ["replace_in_file"],
        retryPolicy: { sameSemanticInput: "allowed" },
      },
    };
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "continue the implementation" }] },
      { role: "tool", content: [{
        type: "toolCallResult",
        toolCallId: "status-v2-write",
        name: "unreal_task_status",
        content: JSON.stringify(payload),
      }] },
    ] });

    await assert.rejects(
      generate(controllerFor(model, {}, stateRoot, emitted, [{
        type: "function",
        function: { name: "read_file", parameters: { type: "object", properties: {} } },
      }]), history),
      /requires replace_in_file, but its MCP schema is not present/,
    );
    assert.equal(predictionCount, 0);
    assert.equal(emitted.some((event) => event.kind === "end"), false);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("required feature intent is suspended until completion-audit evidence is grounded", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-feature-refill-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let systemText = "";
    const ownership = { taskSessionId: "task-feature-refill", ownerCapability: "owner-feature-refill" };
    const model = {
      identifier: "feature-refill-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(modelHistory, opts) {
        systemText = modelHistory.getMessagesArray()
          .filter((message) => message.getRole() === "system")
          .map((message) => message.getText()).join("\n");
        assert.equal(opts.rawTools.force, true);
        assert.deepEqual(
          opts.rawTools.tools.map((tool) => tool.function.name).sort(),
          ["read_file", "search_files"],
        );
        opts.onToolCallRequestStart(1, { toolCallId: "feature-refill-read" });
        opts.onToolCallRequestNameReceived(1, "read_file");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"path":"Source/O_Mock/GomokuRuleEngine.h"}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "feature-refill-read",
            type: "function",
            name: "read_file",
            arguments: { path: "Source/O_Mock/GomokuRuleEngine.h" },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const route = {
      ok: true,
      taskAuthorization: ownership,
      toolRoute: {
        routeHash: "route-feature-refill",
        phase: "planner",
        activeTools: ["read_file", "search_files", "unreal_feature_intent_resolve"],
        selectedSlice: { sliceId: "task", files: [] },
      },
      requiredNextTool: "unreal_feature_intent_resolve",
      requiredNextToolArgs: { taskAuthorization: ownership },
      control: {
        version: 1,
        phase: "unreal_agent_plan",
        status: "NeedsAction",
        nextAction: "unreal_feature_intent_resolve",
        nextActionIsTool: true,
      },
    };
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "Check current implementation status and implement the earliest incomplete feature." }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "plan-feature-refill", type: "function", name: "unreal_agent_plan", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "plan-feature-refill", name: "unreal_agent_plan", content: JSON.stringify(route),
      }] },
    ] });
    const tools = [
      { type: "function", function: { name: "read_file", parameters: { type: "object", properties: { path: { type: "string" } } } } },
      { type: "function", function: { name: "search_files", parameters: { type: "object", properties: { query: { type: "string" } } } } },
      { type: "function", function: { name: "unreal_feature_intent_resolve", parameters: { type: "object", properties: {} } } },
      { type: "function", function: { name: "unreal_get_active_project", parameters: { type: "object", properties: {} } } },
      { type: "function", function: { name: "unreal_set_active_project", parameters: { type: "object", properties: {} } } },
      { type: "function", function: { name: "unreal_agent_plan", parameters: { type: "object", properties: {} } } },
    ];

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    assert.match(systemText, /UNREAL_FEATURE_INTENT_EVIDENCE_REFILL/);
    assert.equal(emitted.find((event) => event.kind === "end").request.name, "read_file");
    const sessionDir = fs.readdirSync(stateRoot, { withFileTypes: true })
      .find((entry) => entry.isDirectory() && !entry.name.startsWith(".") && entry.name !== "_base");
    const events = fs.readFileSync(path.join(stateRoot, sessionDir.name, "events.jsonl"), "utf8")
      .trim().split(/\r?\n/).map((line) => JSON.parse(line));
    assert.ok(events.some((event) => (
      event.type === "context_measurement"
      && event.featureIntentEvidenceRefillActive === true
      && event.featureIntentEvidenceReady === false
      && event.effectiveFeatureIntentEvidenceReadThreshold === 6
    )));
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("empty symbol lookup cannot substitute for the sixth direct source read", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-direct-read-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const ownership = { taskSessionId: "task-direct-read", ownerCapability: "owner-direct-read" };
    const model = {
      identifier: "direct-read-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        const names = opts.rawTools.tools.map((tool) => tool.function.name);
        assert.ok(names.includes("read_file"));
        assert.ok(names.includes("unreal_symbol_lookup"));
        assert.equal(names.includes("unreal_feature_intent_resolve"), false);
        opts.onToolCallRequestStart(1, { toolCallId: "sixth-real-read" });
        opts.onToolCallRequestNameReceived(1, "read_file");
        opts.onToolCallRequestArgumentFragmentGenerated(
          1,
          '{"path":"Source/O_Mock/GomokuGameState.cpp"}',
        );
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "sixth-real-read",
            type: "function",
            name: "read_file",
            arguments: { path: "Source/O_Mock/GomokuGameState.cpp" },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const route = {
      ok: true,
      taskAuthorization: ownership,
      toolRoute: {
        routeHash: "route-direct-read",
        phase: "planner",
        activeTools: ["read_file", "unreal_symbol_lookup", "unreal_feature_intent_resolve"],
        selectedSlice: { sliceId: "task", files: [] },
      },
      requiredNextTool: "unreal_feature_intent_resolve",
      requiredNextToolArgs: { taskAuthorization: ownership },
      control: {
        version: 1,
        phase: "unreal_agent_plan",
        status: "NeedsAction",
        nextAction: "unreal_feature_intent_resolve",
        nextActionIsTool: true,
      },
    };
    const messages = [
      { role: "user", content: [{ type: "text", text: "Check current implementation status and implement the earliest incomplete feature." }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "plan-direct-read", type: "function", name: "unreal_agent_plan", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "plan-direct-read", name: "unreal_agent_plan", content: JSON.stringify(route),
      }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "evidence-contract", type: "function", name: "evidence_first_contract", arguments: { mode: "codegen" },
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "evidence-contract", name: "evidence_first_contract", content: '{"ok":true}',
      }] },
    ];
    const paths = [
      "Source/O_Mock/GomokuRuleEngine.h",
      "Source/O_Mock/GomokuRuleEngine.cpp",
      "Source/O_Mock/GomokuTypes.h",
      "Source/O_Mock/GomokuGameMode.h",
      "Source/O_Mock/GomokuGameMode.cpp",
    ];
    paths.forEach((filePath, index) => {
      const id = `direct-read-${index}`;
      messages.push({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id, type: "function", name: "read_file", arguments: { path: filePath },
      } }] });
      messages.push({ role: "tool", content: [{
        type: "toolCallResult", toolCallId: id, name: "read_file", content: '{"ok":true}',
      }] });
    });
    messages.push({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
      id: "empty-symbol", type: "function", name: "unreal_symbol_lookup", arguments: { query: "MissingType" },
    } }] });
    messages.push({ role: "tool", content: [{
      type: "toolCallResult", toolCallId: "empty-symbol", name: "unreal_symbol_lookup", content: '{"matches":[]}',
    }] });
    const history = Chat.from({ messages });
    const tools = [
      { type: "function", function: { name: "read_file", parameters: { type: "object", properties: { path: { type: "string" } } } } },
      { type: "function", function: { name: "unreal_symbol_lookup", parameters: { type: "object", properties: { query: { type: "string" } } } } },
      { type: "function", function: { name: "unreal_feature_intent_resolve", parameters: { type: "object", properties: {} } } },
      { type: "function", function: { name: "evidence_first_contract", parameters: { type: "object", properties: {} } } },
    ];

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    assert.equal(emitted.find((event) => event.kind === "end").request.name, "read_file");
    const sessionDir = fs.readdirSync(stateRoot, { withFileTypes: true })
      .find((entry) => entry.isDirectory() && !entry.name.startsWith(".") && entry.name !== "_base");
    const events = fs.readFileSync(path.join(stateRoot, sessionDir.name, "events.jsonl"), "utf8")
      .trim().split(/\r?\n/).map((line) => JSON.parse(line));
    const measurement = events.find((event) => event.type === "context_measurement");
    assert.equal(measurement.directSourceFileEvidenceCount, 5);
    assert.equal(measurement.featureIntentEvidenceReady, false);
    assert.equal(measurement.featureIntentEvidenceRefillActive, true);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

for (const recoveryCase of [
  {
    label: "accepts an equivalent workspace-prefixed target",
    modelPath: "Git/Demo/Source/Demo/PlayerController.h",
    committedPath: "Git/Demo/Source/Demo/PlayerController.h",
    serverBoundPath: false,
  },
  {
    label: "binds a nearby wrong file to the server-owned target",
    modelPath: "Git/Demo/Source/Demo/GameMode.h",
    committedPath: "Source/Demo/PlayerController.h",
    serverBoundPath: true,
  },
]) test(`completion audit repairs payload and ${recoveryCase.label}`, async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-feature-target-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const ownership = { taskSessionId: "task-feature-target", ownerCapability: "owner-feature-target" };
    let predictionCount = 0;
    const frontier = {
      milestone: "local_play",
      candidateFeature: "player input",
      declarationEvidence: [{ sourcePath: "Source/Demo/RuleEngine.h", locator: "TryPlaceStone" }],
      implementationEvidence: [{ sourcePath: "Source/Demo/RuleEngine.cpp", locator: "TryPlaceStone" }],
      implementedBehavior: ["Rule validation exists."],
      unmetBehavior: {
        statement: "Handle a local player's board click and place a legal stone",
        sourcePath: "Source/Demo/PlayerController.cpp",
        locator: "HandleBoardClick",
        evidenceType: "direct_source",
      },
      priorCandidatesComplete: ["rule validation"],
    };
    const emitCall = (opts, id, name, args) => {
      opts.onToolCallRequestStart(1, { toolCallId: id });
      opts.onToolCallRequestNameReceived(1, name);
      opts.onToolCallRequestArgumentFragmentGenerated(1, JSON.stringify(args));
      opts.onToolCallRequestEnd(1, {
        toolCallRequest: { id, type: "function", name, arguments: args },
      });
    };
    const model = {
      identifier: "feature-target-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        predictionCount += 1;
        const roles = _history.getMessagesArray().map((message) => message.getRole());
        const firstNonSystem = roles.findIndex((role) => role !== "system");
        assert.equal(
          firstNonSystem < 0 ? false : roles.slice(firstNonSystem).includes("system"),
          false,
          "Feature recovery must not append a system message after conversation history",
        );
        assert.equal(roles.filter((role) => role === "system").length, 1);
        const names = opts.rawTools.tools.map((tool) => tool.function.name);
        if (predictionCount <= 2) {
          assert.deepEqual(names, ["unreal_feature_intent_resolve"]);
          const required = opts.rawTools.tools[0].function.parameters.required;
          assert.ok(required.includes("completionFrontier"));
          assert.ok(required.includes("slices"));
          assert.equal(required.includes("taskAuthorization"), false);
          emitCall(opts, `feature-target-${predictionCount}`, "unreal_feature_intent_resolve", {
            slices: [{
              sliceId: "player_input",
              files: [
                "Source/Demo/PlayerController.h",
                "Source/Demo/PlayerController.cpp",
              ],
            }],
            targetFiles: [
              "Source/Demo/PlayerController.h",
              "Source/Demo/PlayerController.cpp",
            ],
            ...(predictionCount === 2 ? { completionFrontier: frontier } : {}),
          });
        } else if (predictionCount === 3) {
          assert.deepEqual(names.sort(), ["read_file", "read_file_range"]);
          emitCall(opts, "feature-target-read", "read_file", {
            path: recoveryCase.modelPath,
          });
        } else if (predictionCount === 4) {
          assert.deepEqual(names, ["unreal_feature_intent_resolve"]);
          emitCall(opts, `feature-target-resume-${predictionCount}`, "unreal_feature_intent_resolve", {
            targetFiles: ["Source/Demo/WrongTarget.cpp"],
            slices: [{ sliceId: "wrong", files: ["Source/Demo/WrongTarget.cpp"] }],
            completionFrontier: frontier,
          });
        } else if (predictionCount === 5) {
          assert.deepEqual(names.sort(), ["read_file", "read_file_range"]);
          emitCall(opts, "feature-target-second-read", "read_file", {
            path: "Source/Demo/GameMode.h",
          });
        } else if (predictionCount === 6 || predictionCount === 7) {
          assert.ok(names.includes("read_file"));
          assert.ok(names.includes("unreal_feature_intent_resolve"));
          assert.notEqual(opts.rawTools.force, true);
          emitCall(
            opts,
            `feature-post-read-discovery-${predictionCount}`,
            "read_file",
            {
              path: predictionCount === 6
                ? "Source/Demo/BoardActor.cpp"
                : "Source/Demo/BoardActor.h",
            },
          );
        } else {
          assert.equal(predictionCount, 8);
          assert.deepEqual(names, ["unreal_feature_intent_resolve"]);
          assert.equal(opts.rawTools.force, true);
          emitCall(opts, "feature-after-rediscovery", "unreal_feature_intent_resolve", {
            targetFiles: ["Source/Demo/RuleEngine.h", "Source/Demo/RuleEngine.cpp"],
            slices: [{
              sliceId: "new_candidate",
              files: ["Source/Demo/RuleEngine.h", "Source/Demo/RuleEngine.cpp"],
            }],
            completionFrontier: frontier,
          });
        }
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const route = {
      ok: true,
      taskAuthorization: ownership,
      toolRoute: {
        routeHash: "route-feature-target",
        phase: "planner",
        activeTools: ["read_file", "read_file_range", "unreal_feature_intent_resolve"],
        selectedSlice: { sliceId: "task", files: [] },
      },
      requiredNextTool: "unreal_feature_intent_resolve",
      requiredNextToolArgs: { taskAuthorization: ownership },
      control: {
        version: 1,
        phase: "unreal_agent_plan",
        status: "NeedsAction",
        nextAction: "unreal_feature_intent_resolve",
        nextActionIsTool: true,
      },
    };
    const messages = [
      { role: "system", content: [{ type: "text", text: "Base Qwen-compatible system prompt." }] },
      { role: "user", content: [{ type: "text", text: "Check current implementation status and implement the earliest incomplete feature." }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "plan-feature-target", type: "function", name: "unreal_agent_plan", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "plan-feature-target", name: "unreal_agent_plan", content: JSON.stringify(route),
      }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "evidence-feature-target", type: "function", name: "evidence_first_contract", arguments: { mode: "codegen" },
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "evidence-feature-target", name: "evidence_first_contract", content: '{"ok":true}',
      }] },
    ];
    for (const [index, filePath] of [
      "Source/Demo/RuleEngine.h",
      "Source/Demo/RuleEngine.cpp",
      "Source/Demo/GameState.h",
      "Source/Demo/GameState.cpp",
      "Source/Demo/GameMode.h",
      "Source/Demo/GameMode.cpp",
    ].entries()) {
      const id = `feature-target-read-${index}`;
      messages.push({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id, type: "function", name: "read_file", arguments: { path: filePath },
      } }] });
      messages.push({ role: "tool", content: [{
        type: "toolCallResult", toolCallId: id, name: "read_file", content: '{"ok":true}',
      }] });
    }
    const frontierEvidenceRow = {
      type: "object",
      properties: { sourcePath: { type: "string" }, locator: { type: "string" } },
      required: ["sourcePath", "locator"],
    };
    const tools = [
      { type: "function", function: { name: "read_file", parameters: { type: "object", properties: { path: { type: "string" } }, required: ["path"] } } },
      { type: "function", function: { name: "read_file_range", parameters: { type: "object", properties: { path: { type: "string" } }, required: ["path"] } } },
      {
        type: "function",
        function: {
          name: "unreal_feature_intent_resolve",
          parameters: {
            type: "object",
            properties: {
              completionFrontier: {
                type: "object",
                properties: {
                  milestone: { type: "string" },
                  candidateFeature: { type: "string" },
                  declarationEvidence: { type: "array", minItems: 1, items: frontierEvidenceRow },
                  implementationEvidence: { type: "array", minItems: 1, items: frontierEvidenceRow },
                  implementedBehavior: { type: "array" },
                  unmetBehavior: {
                    type: "object",
                    properties: {
                      statement: { type: "string" }, sourcePath: { type: "string" },
                      locator: { type: "string" }, evidenceType: { type: "string" },
                    },
                    required: ["statement", "sourcePath", "locator", "evidenceType"],
                  },
                  priorCandidatesComplete: { type: "array" },
                },
                required: [
                  "milestone", "candidateFeature", "declarationEvidence", "implementationEvidence",
                  "implementedBehavior", "unmetBehavior", "priorCandidatesComplete",
                ],
              },
              slices: {
                type: "array", minItems: 1, items: {
                  type: "object",
                  properties: {
                    sliceId: { type: "string" }, files: { type: "array", minItems: 1 },
                  },
                  required: ["sliceId", "files"],
                },
              },
              targetFiles: { type: "array" },
              taskAuthorization: { type: "object" },
            },
            required: ["taskAuthorization"],
          },
        },
      },
      { type: "function", function: { name: "evidence_first_contract", parameters: { type: "object", properties: {} } } },
    ];

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), Chat.from({ messages }));

    assert.equal(predictionCount, 3);
    const committed = emitted.filter((event) => event.kind === "end");
    assert.equal(committed.length, 1);
    assert.equal(committed[0].request.name, "read_file");
    assert.equal(committed[0].request.arguments.path, recoveryCase.committedPath);
    const sessionDir = fs.readdirSync(stateRoot, { withFileTypes: true })
      .find((entry) => entry.isDirectory() && !entry.name.startsWith(".") && entry.name !== "_base");
    const telemetry = fs.readFileSync(path.join(stateRoot, sessionDir.name, "events.jsonl"), "utf8")
      .trim().split(/\r?\n/).map((line) => JSON.parse(line));
    const recovery = telemetry.find((event) => (
      event.type === "feature_intent_target_evidence_recovery_completed"
    ));
    assert.equal(recovery.serverBoundPath, recoveryCase.serverBoundPath);
    assert.equal(recovery.requestedPath, "source/demo/playercontroller.h");
    const checkpoint = activeCheckpoint(stateRoot);
    assert.equal(checkpoint.requiredNextTool.name, "unreal_feature_intent_resolve");
    assert.deepEqual(
      checkpoint.requiredNextTool.args.targetFiles,
      [
        "Source/Demo/PlayerController.h",
        "Source/Demo/PlayerController.cpp",
      ],
    );
    assert.equal(
      telemetry.some((event) => event.type === "feature_intent_resume_locked"),
      true,
    );

    const withFirstRead = [
      ...messages,
      { role: "assistant", content: [{
        type: "toolCallRequest", toolCallRequest: committed[0].request,
      }] },
      { role: "tool", content: [{
        type: "toolCallResult",
        toolCallId: committed[0].request.id,
        name: committed[0].request.name,
        content: '{"ok":true}',
      }] },
    ];
    emitted.length = 0;
    await generate(
      controllerFor(model, {}, stateRoot, emitted, tools),
      Chat.from({ messages: withFirstRead }),
    );
    assert.equal(predictionCount, 5);
    const secondCommitted = emitted.filter((event) => event.kind === "end");
    assert.equal(secondCommitted.length, 1);
    assert.equal(secondCommitted[0].request.name, "read_file");
    assert.equal(
      secondCommitted[0].request.arguments.path,
      "Source/Demo/PlayerController.cpp",
    );
    assert.deepEqual(
      activeCheckpoint(stateRoot).requiredNextTool.args.targetFiles,
      [
        "Source/Demo/PlayerController.h",
        "Source/Demo/PlayerController.cpp",
      ],
    );

    const withBothReads = [
      ...withFirstRead,
      { role: "assistant", content: [{
        type: "toolCallRequest", toolCallRequest: secondCommitted[0].request,
      }] },
      { role: "tool", content: [{
        type: "toolCallResult",
        toolCallId: secondCommitted[0].request.id,
        name: secondCommitted[0].request.name,
        content: '{"ok":true}',
      }] },
    ];
    emitted.length = 0;
    await generate(
      controllerFor(model, {}, stateRoot, emitted, tools),
      Chat.from({ messages: withBothReads }),
    );
    assert.equal(predictionCount, 6);
    const postReadDiscovery = emitted.filter((event) => event.kind === "end");
    assert.equal(postReadDiscovery.length, 1);
    assert.equal(postReadDiscovery[0].request.name, "read_file");
    assert.equal(postReadDiscovery[0].request.arguments.path, "Source/Demo/BoardActor.cpp");
    const rediscoveryCheckpoint = activeCheckpoint(stateRoot);
    assert.equal(rediscoveryCheckpoint.requiredNextTool, null);
    assert.equal(rediscoveryCheckpoint.featureIntentResume.mode, "rediscover_after_target_read");
    assert.equal(Object.hasOwn(rediscoveryCheckpoint.featureIntentResume, "args"), false);

    const withFirstRediscovery = [
      ...withBothReads,
      { role: "assistant", content: [{
        type: "toolCallRequest", toolCallRequest: postReadDiscovery[0].request,
      }] },
      { role: "tool", content: [{
        type: "toolCallResult",
        toolCallId: postReadDiscovery[0].request.id,
        name: postReadDiscovery[0].request.name,
        content: '{"ok":true}',
      }] },
    ];
    emitted.length = 0;
    await generate(
      controllerFor(model, {}, stateRoot, emitted, tools),
      Chat.from({ messages: withFirstRediscovery }),
    );
    assert.equal(predictionCount, 7);
    const secondDiscovery = emitted.filter((event) => event.kind === "end");
    assert.equal(secondDiscovery.length, 1);
    assert.equal(secondDiscovery[0].request.name, "read_file");
    assert.equal(secondDiscovery[0].request.arguments.path, "Source/Demo/BoardActor.h");

    const withSecondRediscovery = [
      ...withFirstRediscovery,
      { role: "assistant", content: [{
        type: "toolCallRequest", toolCallRequest: secondDiscovery[0].request,
      }] },
      { role: "tool", content: [{
        type: "toolCallResult",
        toolCallId: secondDiscovery[0].request.id,
        name: secondDiscovery[0].request.name,
        content: '{"ok":true}',
      }] },
    ];
    emitted.length = 0;
    await generate(
      controllerFor(model, {}, stateRoot, emitted, tools),
      Chat.from({ messages: withSecondRediscovery }),
    );
    assert.equal(predictionCount, 8);
    const resumed = emitted.filter((event) => event.kind === "end");
    assert.equal(resumed.length, 1);
    assert.equal(resumed[0].request.name, "unreal_feature_intent_resolve");
    assert.deepEqual(
      resumed[0].request.arguments.targetFiles,
      ["Source/Demo/RuleEngine.h", "Source/Demo/RuleEngine.cpp"],
    );
    assert.equal(resumed[0].request.arguments.slices[0].sliceId, "new_candidate");
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("an active route keeps an explicitly server-required replan tool", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-required-replan-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const ownership = { taskSessionId: "task-required-replan", ownerCapability: "owner-required-replan" };
    const model = {
      identifier: "required-replan-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        assert.equal(opts.rawTools.force, true);
        assert.deepEqual(opts.rawTools.tools.map((tool) => tool.function.name), ["unreal_agent_plan"]);
        opts.onToolCallRequestStart(1, { toolCallId: "required-replan" });
        opts.onToolCallRequestNameReceived(1, "unreal_agent_plan");
        opts.onToolCallRequestArgumentFragmentGenerated(1, "{}");
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "required-replan",
            type: "function",
            name: "unreal_agent_plan",
            arguments: {},
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "Implement the earliest incomplete local-play feature." }] },
      { role: "assistant", content: [{ type: "text", text: JSON.stringify({
        ok: true,
        taskAuthorization: ownership,
        requiredNextTool: "unreal_agent_plan",
        requiredNextToolArgs: { taskAuthorization: ownership },
        toolRoute: {
          routeHash: "route-required-replan",
          phase: "planner",
          activeTools: ["read_file", "unreal_agent_plan"],
          selectedSlice: { sliceId: "task", files: [] },
        },
      }) }] },
    ] });
    const tools = [
      { type: "function", function: { name: "read_file", parameters: { type: "object", properties: {} } } },
      { type: "function", function: {
        name: "unreal_agent_plan",
        parameters: { type: "object", properties: { request: { type: "string" } } },
      } },
    ];

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    const end = emitted.find((event) => event.kind === "end");
    assert.ok(end);
    assert.equal(end.request.name, "unreal_agent_plan");
    assert.equal(end.request.arguments.request, "Implement the earliest incomplete local-play feature.");
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("evidence-first contract is forced once after task routing and before business discovery", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-evidence-first-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let systemText = "";
    const ownership = { taskSessionId: "task-evidence-first", ownerCapability: "owner-evidence-first" };
    const model = {
      identifier: "evidence-first-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(modelHistory, opts) {
        systemText = modelHistory.getMessagesArray()
          .filter((message) => message.getRole() === "system")
          .map((message) => message.getText()).join("\n");
        assert.equal(opts.rawTools.force, true);
        assert.deepEqual(opts.rawTools.tools.map((tool) => tool.function.name), ["evidence_first_contract"]);
        opts.onToolCallRequestStart(1, { toolCallId: "evidence-first-contract" });
        opts.onToolCallRequestNameReceived(1, "evidence_first_contract");
        opts.onToolCallRequestArgumentFragmentGenerated(1, "{}");
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "evidence-first-contract",
            type: "function",
            name: "evidence_first_contract",
            arguments: {},
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const route = {
      ok: true,
      taskAuthorization: ownership,
      toolRoute: {
        routeHash: "route-evidence-first",
        phase: "planner",
        activeTools: ["read_file", "unreal_feature_intent_resolve"],
        selectedSlice: { sliceId: "task", files: [] },
      },
      requiredNextTool: "unreal_feature_intent_resolve",
      control: {
        version: 1,
        phase: "unreal_agent_plan",
        status: "NeedsAction",
        nextAction: "unreal_feature_intent_resolve",
        nextActionIsTool: true,
      },
    };
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "현재 O-Mock 프로젝트의 구현 상태를 먼저 확인하고, 오목 규칙과 로컬 플레이부터 시작하는 개발 순서에서 아직 완료되지 않은 가장 앞 단계의 핵심 기능 하나를 실제로 완성해줘. 문서나 계획만 만드는 데 그치지 말고 기능 구현을 우선해. 기존 동작과 현재 상태 소유권은 깨지 말고, 필요한 자동화 테스트와 Unreal 빌드까지 실행해서 결과를 알려줘." }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "plan-evidence-first", type: "function", name: "unreal_agent_plan", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "plan-evidence-first", name: "unreal_agent_plan", content: JSON.stringify(route),
      }] },
    ] });
    const tools = [
      { type: "function", function: { name: "read_file", parameters: { type: "object", properties: {} } } },
      { type: "function", function: { name: "unreal_feature_intent_resolve", parameters: { type: "object", properties: {} } } },
      {
        type: "function",
        function: {
          name: "evidence_first_contract",
          parameters: {
            type: "object",
            properties: { mode: { type: "string" } },
            required: ["mode"],
          },
        },
      },
    ];

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    assert.match(systemText, /EVIDENCE_FIRST_CONTRACT_REQUIRED/);
    const end = emitted.find((event) => event.kind === "end");
    assert.equal(end.request.name, "evidence_first_contract");
    assert.deepEqual(end.request.arguments, { mode: "codegen" });
    const sessionDir = fs.readdirSync(stateRoot, { withFileTypes: true })
      .find((entry) => entry.isDirectory() && !entry.name.startsWith(".") && entry.name !== "_base");
    const events = fs.readFileSync(path.join(stateRoot, sessionDir.name, "events.jsonl"), "utf8")
      .trim().split(/\r?\n/).map((line) => JSON.parse(line));
    assert.ok(events.some((event) => (
      event.type === "context_measurement" && event.evidenceFirstContractForced === true
    )));
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("bounded source evidence clears a fake RAG action and hands off to feature intent", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-feature-handoff-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const ownership = { taskSessionId: "task-feature-handoff", ownerCapability: "owner-feature-handoff" };
    const model = {
      identifier: "feature-handoff-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        assert.equal(opts.rawTools.force, true);
        assert.deepEqual(
          opts.rawTools.tools.map((tool) => tool.function.name),
          ["unreal_feature_intent_resolve"],
        );
        opts.onToolCallRequestStart(1, { toolCallId: "feature-handoff-1" });
        opts.onToolCallRequestNameReceived(1, "unreal_feature_intent_resolve");
        opts.onToolCallRequestArgumentFragmentGenerated(1, JSON.stringify({
          selectedIntentId: "bounded_local",
          slices: [{ sliceId: "input", files: ["Source/Demo/Controller.cpp"] }],
          activeSliceId: "input",
        }));
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "feature-handoff-1",
            type: "function",
            name: "unreal_feature_intent_resolve",
            arguments: {
              selectedIntentId: "bounded_local",
              slices: [{ sliceId: "input", files: ["Source/Demo/Controller.cpp"] }],
              activeSliceId: "input",
            },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const route = {
      ok: true,
      taskAuthorization: ownership,
      toolRoute: {
        routeHash: "route-feature-handoff",
        phase: "planner",
        activeTools: ["read_file", "unreal_rag_search", "unreal_feature_intent_resolve"],
        selectedSlice: { sliceId: "task", files: [] },
      },
      control: {
        version: 1,
        phase: "unreal_agent_plan",
        status: "NeedsAction",
        nextAction: "discover_bounded_feature_slice",
        nextActionIsTool: false,
      },
    };
    const fakeRagHandoff = {
      ok: true,
      requiredNextAction: "read_project_source_or_answer",
      control: {
        version: 1,
        phase: "unreal_rag_search",
        status: "NeedsAction",
        nextAction: "read_project_source_or_answer",
        nextActionIsTool: true,
      },
    };
    const messages = [
      { role: "user", content: [{ type: "text", text: "현재 구현 상태를 확인하고 가장 앞선 미완성 기능을 구현해줘" }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "plan-handoff", type: "function", name: "unreal_agent_plan", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "plan-handoff", name: "unreal_agent_plan", content: JSON.stringify(route),
      }] },
    ];
    for (const [index, file] of [
      "Source/Demo/RuleEngine.h",
      "Source/Demo/RuleEngine.cpp",
      "Source/Demo/GameState.h",
      "Source/Demo/GameState.cpp",
      "Source/Demo/Controller.h",
      "Source/Demo/Controller.cpp",
    ].entries()) {
      messages.push({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: `read-${index}`, type: "function", name: "read_file", arguments: { path: file },
      } }] });
      messages.push({ role: "tool", content: [{
        type: "toolCallResult",
        toolCallId: `read-${index}`,
        name: "read_file",
        content: JSON.stringify({ ok: true, path: file, content: `// ${file}` }),
      }] });
    }
    messages.push({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
      id: "rag-handoff", type: "function", name: "unreal_rag_search", arguments: { request: "input" },
    } }] });
    messages.push({ role: "tool", content: [{
      type: "toolCallResult",
      toolCallId: "rag-handoff",
      name: "unreal_rag_search",
      content: JSON.stringify(fakeRagHandoff),
    }] });
    const history = Chat.from({ messages });
    const tools = [
      { type: "function", function: { name: "read_file", parameters: { type: "object", properties: {} } } },
      { type: "function", function: { name: "unreal_rag_search", parameters: { type: "object", properties: {} } } },
      {
        type: "function",
        function: {
          name: "unreal_feature_intent_resolve",
          parameters: {
            type: "object",
            properties: {
              selectedIntentId: { type: "string" },
              slices: { type: "array" },
              activeSliceId: { type: "string" },
              taskAuthorization: { type: "object" },
            },
            required: ["selectedIntentId", "slices", "activeSliceId", "taskAuthorization"],
          },
        },
      },
    ];

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    const end = emitted.find((event) => event.kind === "end");
    assert.ok(end);
    assert.equal(end.request.name, "unreal_feature_intent_resolve");
    assert.deepEqual(end.request.arguments.taskAuthorization, ownership);
    const sessionDir = fs.readdirSync(stateRoot, { withFileTypes: true })
      .find((entry) => entry.isDirectory() && !entry.name.startsWith(".") && entry.name !== "_base");
    const events = fs.readFileSync(path.join(stateRoot, sessionDir.name, "events.jsonl"), "utf8")
      .trim().split(/\r?\n/).map((line) => JSON.parse(line));
    assert.ok(events.some((event) => event.type === "invalid_required_tool_contract_cleared"));
    assert.ok(events.some((event) => (
      event.type === "context_measurement" && event.featureIntentDiscoveryHandoffForced === true
    )));
    assert.ok(events.some((event) => (
      event.type === "context_measurement"
      && event.featureCompletionAuditRequired === true
      && event.effectiveFeatureIntentEvidenceReadThreshold === 6
    )));
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("frontier payload repair with no required reads forces Feature Intent", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-frontier-repair-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const ownership = { taskSessionId: "task-frontier-repair", ownerCapability: "owner-frontier-repair" };
    let predictionCount = 0;
    const model = {
      identifier: "frontier-repair-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        predictionCount += 1;
        assert.equal(opts.rawTools.force, true);
        assert.deepEqual(
          opts.rawTools.tools.map((tool) => tool.function.name),
          ["unreal_feature_intent_resolve"],
        );
        const args = { selectedIntentId: "bounded_local" };
        opts.onToolCallRequestStart(1, { toolCallId: "frontier-repair-submit" });
        opts.onToolCallRequestNameReceived(1, "unreal_feature_intent_resolve");
        opts.onToolCallRequestArgumentFragmentGenerated(1, JSON.stringify(args));
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "frontier-repair-submit",
            type: "function",
            name: "unreal_feature_intent_resolve",
            arguments: args,
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const route = {
      ok: true,
      taskAuthorization: ownership,
      featureCompletionAudit: { required: true, status: "pending" },
      toolRoute: {
        routeHash: "route-frontier-repair",
        phase: "planner",
        activeTools: ["read_file", "unreal_feature_intent_resolve"],
        selectedSlice: {
          sliceId: "local-play",
          files: ["Source/Demo/GameState.cpp"],
        },
      },
    };
    const blocked = {
      ok: false,
      errorCode: "FEATURE_FRONTIER_UNPROVEN",
      nextAction: "repair_feature_completion_frontier",
      nextActionIsTool: false,
      featureFrontierRecovery: {
        kind: "repair_completion_frontier",
        requiredReads: [],
        requiredFields: ["completionFrontier.unmetBehavior.statement"],
      },
      taskAuthorization: ownership,
    };
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "현재 구현 상태를 확인하고 가장 앞의 미완료 핵심 기능을 실제로 구현해줘" }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "frontier-plan", type: "function", name: "unreal_agent_plan", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "frontier-plan", name: "unreal_agent_plan", content: JSON.stringify(route),
      }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "frontier-evidence-contract", type: "function", name: "evidence_first_contract", arguments: { mode: "codegen" },
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "frontier-evidence-contract", name: "evidence_first_contract", content: JSON.stringify({ ok: true }),
      }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "frontier-blocked-call", type: "function", name: "unreal_feature_intent_resolve", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "frontier-blocked-call", name: "unreal_feature_intent_resolve", content: JSON.stringify(blocked),
      }] },
    ] });
    const tools = [
      { type: "function", function: {
        name: "read_file",
        parameters: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
      } },
      { type: "function", function: {
        name: "unreal_feature_intent_resolve",
        parameters: {
          type: "object",
          properties: {
            selectedIntentId: { type: "string" },
            taskAuthorization: { type: "object" },
          },
          required: ["selectedIntentId", "taskAuthorization"],
        },
      } },
      { type: "function", function: {
        name: "evidence_first_contract",
        parameters: { type: "object", properties: { mode: { type: "string" } } },
      } },
    ];

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    assert.equal(predictionCount, 1);
    const ends = emitted.filter((event) => event.kind === "end");
    assert.equal(ends.length, 1);
    assert.equal(ends[0].request.name, "unreal_feature_intent_resolve");
    assert.deepEqual(ends[0].request.arguments.taskAuthorization, ownership);
    assert.equal(ends.some((event) => event.request.name === "read_file"), false);
    const sessionDir = fs.readdirSync(stateRoot, { withFileTypes: true })
      .find((entry) => entry.isDirectory() && !entry.name.startsWith(".") && entry.name !== "_base");
    const events = fs.readFileSync(path.join(stateRoot, sessionDir.name, "events.jsonl"), "utf8")
      .trim().split(/\r?\n/).map((line) => JSON.parse(line));
    assert.ok(events.some((event) => (
      event.type === "context_measurement"
        && event.featureFrontierRecoveryActive === true
        && event.featureFrontierRepairToolForced === true
        && event.exactRequiredToolForced === true
    )));
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("source-contradicted frontier gets two bounded discovery calls before Feature Intent", async () => {
  for (const discoveryCount of [0, 2]) {
    const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), `context-compactor-frontier-semantic-${discoveryCount}-`));
    process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
    try {
      const { generate } = require("../dist/generator.js");
      const emitted = [];
      const ownership = {
        taskSessionId: `task-frontier-semantic-${discoveryCount}`,
        ownerCapability: "owner-frontier-semantic",
      };
      const shouldForceFeature = discoveryCount === 2;
      const model = {
        identifier: "frontier-semantic-model",
        async applyPromptTemplate() { return "formatted"; },
        async countTokens(value) { return String(value || "").length; },
        async getContextLength() { return 100_000; },
        respond(_history, opts) {
          if (shouldForceFeature) {
            assert.equal(opts.rawTools.force, true);
            assert.deepEqual(
              opts.rawTools.tools.map((tool) => tool.function.name),
              ["unreal_feature_intent_resolve"],
            );
            const args = { selectedIntentId: "bounded_local" };
            opts.onToolCallRequestStart(1, { toolCallId: "semantic-frontier-submit" });
            opts.onToolCallRequestNameReceived(1, "unreal_feature_intent_resolve");
            opts.onToolCallRequestArgumentFragmentGenerated(1, JSON.stringify(args));
            opts.onToolCallRequestEnd(1, {
              toolCallRequest: {
                id: "semantic-frontier-submit",
                type: "function",
                name: "unreal_feature_intent_resolve",
                arguments: args,
              },
            });
          } else {
            assert.notEqual(opts.rawTools.force, true);
            const names = opts.rawTools.tools.map((tool) => tool.function.name);
            assert.ok(names.includes("read_file"));
            assert.ok(names.includes("unreal_feature_intent_resolve"));
            const args = { path: "Source/Demo/NextCandidate.cpp" };
            opts.onToolCallRequestStart(1, { toolCallId: "semantic-discovery-read" });
            opts.onToolCallRequestNameReceived(1, "read_file");
            opts.onToolCallRequestArgumentFragmentGenerated(1, JSON.stringify(args));
            opts.onToolCallRequestEnd(1, {
              toolCallRequest: {
                id: "semantic-discovery-read",
                type: "function",
                name: "read_file",
                arguments: args,
              },
            });
          }
          return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
        },
      };
      const route = {
        ok: true,
        taskAuthorization: ownership,
        featureCompletionAudit: { required: true, status: "pending" },
        toolRoute: {
          routeHash: `route-frontier-semantic-${discoveryCount}`,
          phase: "planner",
          activeTools: ["read_file", "unreal_feature_intent_resolve"],
          selectedSlice: {
            sliceId: "local-play",
            files: ["Source/Demo/BoardActor.cpp"],
          },
        },
      };
      const blocked = {
        ok: false,
        errorCode: "FEATURE_FRONTIER_UNPROVEN",
        nextAction: "repair_feature_completion_frontier",
        nextActionIsTool: false,
        featureFrontierRecovery: {
          kind: "rediscover_feature_candidate",
          requiredReads: [],
          semanticDiscoveryRequired: true,
          maxDiscoveryCalls: 2,
          issues: ["explicit no-call claim was contradicted by verified source"],
        },
        taskAuthorization: ownership,
      };
      const messages = [
        { role: "user", content: [{ type: "text", text: "Check current implementation status and implement the earliest incomplete core feature." }] },
        { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
          id: "semantic-plan", type: "function", name: "unreal_agent_plan", arguments: {},
        } }] },
        { role: "tool", content: [{
          type: "toolCallResult", toolCallId: "semantic-plan", name: "unreal_agent_plan", content: JSON.stringify(route),
        }] },
        { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
          id: "semantic-contract", type: "function", name: "evidence_first_contract", arguments: { mode: "codegen" },
        } }] },
        { role: "tool", content: [{
          type: "toolCallResult", toolCallId: "semantic-contract", name: "evidence_first_contract", content: JSON.stringify({ ok: true }),
        }] },
        { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
          id: "semantic-blocked-call", type: "function", name: "unreal_feature_intent_resolve", arguments: {},
        } }] },
        { role: "tool", content: [{
          type: "toolCallResult", toolCallId: "semantic-blocked-call", name: "unreal_feature_intent_resolve", content: JSON.stringify(blocked),
        }] },
      ];
      for (let index = 0; index < discoveryCount; index += 1) {
        const id = `semantic-read-${index}`;
        messages.push(
          { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
            id, type: "function", name: "read_file", arguments: { path: `Source/Demo/Candidate${index}.cpp` },
          } }] },
          { role: "tool", content: [{
            type: "toolCallResult", toolCallId: id, name: "read_file", content: JSON.stringify({ ok: true }),
          }] },
        );
      }
      const tools = [
        { type: "function", function: {
          name: "read_file",
          parameters: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
        } },
        { type: "function", function: {
          name: "unreal_feature_intent_resolve",
          parameters: {
            type: "object",
            properties: {
              selectedIntentId: { type: "string" },
              taskAuthorization: { type: "object" },
            },
            required: ["selectedIntentId", "taskAuthorization"],
          },
        } },
        { type: "function", function: {
          name: "evidence_first_contract",
          parameters: { type: "object", properties: { mode: { type: "string" } } },
        } },
      ];

      await generate(controllerFor(model, {}, stateRoot, emitted, tools), Chat.from({ messages }));

      const end = emitted.find((event) => event.kind === "end");
      assert.ok(end);
      assert.equal(
        end.request.name,
        shouldForceFeature ? "unreal_feature_intent_resolve" : "read_file",
      );
      const sessionDir = fs.readdirSync(stateRoot, { withFileTypes: true })
        .find((entry) => entry.isDirectory() && !entry.name.startsWith(".") && entry.name !== "_base");
      const events = fs.readFileSync(path.join(stateRoot, sessionDir.name, "events.jsonl"), "utf8")
        .trim().split(/\r?\n/).map((line) => JSON.parse(line));
      assert.ok(events.some((event) => (
        event.type === "context_measurement"
          && event.featureFrontierSemanticRediscoveryActive === !shouldForceFeature
          && event.featureFrontierRepairToolForced === shouldForceFeature
      )));
    } finally {
      delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
      fs.rmSync(stateRoot, { recursive: true, force: true });
    }
  }
});

test("terminal repeated frontier blocker does not force the rejected gate again", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-frontier-repeat-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const ownership = { taskSessionId: "task-frontier-repeat", ownerCapability: "owner-frontier-repeat" };
    let predictionCount = 0;
    const model = {
      identifier: "frontier-repeat-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        predictionCount += 1;
        assert.notEqual(opts.rawTools.force, true);
        const names = opts.rawTools.tools.map((tool) => tool.function.name);
        assert.ok(names.includes("read_file"));
        assert.ok(names.includes("unreal_feature_intent_resolve"));
        opts.onPredictionFragment({ content: "Inspect a different candidate before retrying the gate." });
        return { async result() { return { stats: { stopReason: "stop" } }; } };
      },
    };
    const route = {
      ok: true,
      taskAuthorization: ownership,
      featureCompletionAudit: { required: true, status: "pending" },
      toolRoute: {
        routeHash: "route-frontier-repeat",
        phase: "planner",
        activeTools: ["read_file", "unreal_feature_intent_resolve"],
        selectedSlice: {
          sliceId: "local-play",
          files: [],
        },
      },
    };
    const repeated = {
      ok: false,
      errorCode: "REPEATED_GATE_BLOCKER",
      validationErrorCode: "FEATURE_FRONTIER_UNPROVEN",
      retryable: false,
      nextAction: "repair_feature_completion_frontier",
      nextActionIsTool: false,
      featureFrontierRecovery: {
        kind: "repair_completion_frontier",
        requiredReads: [],
      },
      taskAuthorization: ownership,
      gateCompletion: {
        errorCode: "REPEATED_GATE_BLOCKER",
        validationErrorCode: "FEATURE_FRONTIER_UNPROVEN",
        retryable: false,
      },
    };
    const repeatedMessages = [
      { role: "user", content: [{ type: "text", text: "가장 앞의 미완료 핵심 기능을 실제로 구현해줘" }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "repeat-plan", type: "function", name: "unreal_agent_plan", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "repeat-plan", name: "unreal_agent_plan", content: JSON.stringify(route),
      }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "repeat-contract", type: "function", name: "evidence_first_contract", arguments: { mode: "codegen" },
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "repeat-contract", name: "evidence_first_contract", content: JSON.stringify({ ok: true }),
      }] },
    ];
    for (const [index, filePath] of [
      "Source/Demo/RuleEngine.h",
      "Source/Demo/RuleEngine.cpp",
      "Source/Demo/GameState.h",
      "Source/Demo/GameState.cpp",
      "Source/Demo/GameMode.h",
      "Source/Demo/GameMode.cpp",
    ].entries()) {
      const id = `repeat-read-${index}`;
      repeatedMessages.push({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id, type: "function", name: "read_file", arguments: { path: filePath },
      } }] });
      repeatedMessages.push({ role: "tool", content: [{
        type: "toolCallResult", toolCallId: id, name: "read_file", content: JSON.stringify({ ok: true }),
      }] });
    }
    repeatedMessages.push(
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "repeat-frontier", type: "function", name: "unreal_feature_intent_resolve", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "repeat-frontier", name: "unreal_feature_intent_resolve", content: JSON.stringify(repeated),
      }] },
    );
    const history = Chat.from({ messages: repeatedMessages });
    const tools = [
      { type: "function", function: {
        name: "read_file",
        parameters: { type: "object", properties: { path: { type: "string" } }, required: ["path"] },
      } },
      { type: "function", function: {
        name: "unreal_feature_intent_resolve",
        parameters: { type: "object", properties: {} },
      } },
      { type: "function", function: {
        name: "evidence_first_contract",
        parameters: { type: "object", properties: { mode: { type: "string" } } },
      } },
    ];

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    assert.equal(predictionCount, 1);
    assert.equal(emitted.some((event) => event.kind === "end"), false);
    const sessionDir = fs.readdirSync(stateRoot, { withFileTypes: true })
      .find((entry) => entry.isDirectory() && !entry.name.startsWith(".") && entry.name !== "_base");
    const events = fs.readFileSync(path.join(stateRoot, sessionDir.name, "events.jsonl"), "utf8")
      .trim().split(/\r?\n/).map((line) => JSON.parse(line));
    assert.ok(events.some((event) => (
      event.type === "context_measurement"
        && event.featureFrontierRecoveryActive === false
        && event.featureFrontierRepairToolForced === false
    )));
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("deferred Feature Intent resume does not override a newer checkpoint handoff", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-feature-checkpoint-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let predictionCount = 0;
    const model = {
      identifier: "feature-checkpoint-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        predictionCount += 1;
        if (predictionCount === 1) {
          opts.onPredictionFragment({ content: "seed" });
        } else {
          assert.equal(opts.rawTools.force, true);
          assert.deepEqual(
            opts.rawTools.tools.map((tool) => tool.function.name),
            ["unreal_task_checkpoint"],
          );
          opts.onToolCallRequestStart(1, { toolCallId: "budget-checkpoint" });
          opts.onToolCallRequestNameReceived(1, "unreal_task_checkpoint");
          opts.onToolCallRequestArgumentFragmentGenerated(1, JSON.stringify({ action: "record" }));
          opts.onToolCallRequestEnd(1, {
            toolCallRequest: {
              id: "budget-checkpoint",
              type: "function",
              name: "unreal_task_checkpoint",
              arguments: { action: "record" },
            },
          });
        }
        return { async result() { return { stats: { stopReason: predictionCount === 1 ? "stop" : "toolCalls" } }; } };
      },
    };
    const initial = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "Continue the active implementation task." }] },
    ] });
    await generate(controllerFor(model, {}, stateRoot, emitted, []), initial);

    const sessionDir = fs.readdirSync(stateRoot, { withFileTypes: true })
      .find((entry) => entry.isDirectory() && !entry.name.startsWith(".") && entry.name !== "_base");
    const checkpointPath = path.join(stateRoot, sessionDir.name, "active-checkpoint.json");
    const seeded = JSON.parse(fs.readFileSync(checkpointPath, "utf8"));
    seeded.featureIntentResume = {
      args: { selectedIntentId: "deferred-feature" },
      observedResultCount: 0,
    };
    seeded.requiredNextTool = {
      name: "unreal_feature_intent_resolve",
      reference: "feature_intent_target_evidence_resume",
      args: { selectedIntentId: "deferred-feature" },
    };
    fs.writeFileSync(checkpointPath, `${JSON.stringify(seeded, null, 2)}\n`, "utf8");

    const ownership = { taskSessionId: "task-budget", ownerCapability: "owner-budget" };
    const budgetBlock = {
      ok: false,
      errorCode: "TASK_PHASE_TOOL_BUDGET_EXHAUSTED",
      nextAction: "unreal_task_checkpoint",
      nextActionIsTool: true,
      nextActionArgs: { action: "record", taskAuthorization: ownership },
      taskAuthorization: ownership,
      control: {
        version: 1,
        taskId: ownership.taskSessionId,
        phase: "read_file_range",
        status: "Blocked",
        nextAction: "unreal_task_checkpoint",
        nextActionIsTool: true,
        retryPolicy: "once",
      },
    };
    const continued = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "Continue the active implementation task." }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "budgeted-read", type: "function", name: "read_file_range", arguments: { path: "Source/Demo/Rules.cpp" },
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "budgeted-read", name: "read_file_range", content: JSON.stringify(budgetBlock),
      }] },
    ] });
    const tools = [
      { type: "function", function: { name: "read_file_range", parameters: { type: "object", properties: {} } } },
      { type: "function", function: { name: "unreal_feature_intent_resolve", parameters: { type: "object", properties: {} } } },
      { type: "function", function: {
        name: "unreal_task_checkpoint",
        parameters: { type: "object", properties: { action: { type: "string" }, taskAuthorization: { type: "object" } } },
      } },
    ];
    emitted.length = 0;
    await generate(controllerFor(model, {}, stateRoot, emitted, tools), continued);

    assert.equal(predictionCount, 2);
    const end = emitted.find((event) => event.kind === "end");
    assert.equal(end.request.name, "unreal_task_checkpoint");
    assert.equal(end.request.arguments.action, "record");
    assert.deepEqual(end.request.arguments.taskAuthorization, ownership);
    assert.deepEqual(activeCheckpoint(stateRoot).featureIntentResume.args, {
      selectedIntentId: "deferred-feature",
    });
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});


test("post-read Feature Intent reevaluation does not resurrect stale semantic args after checkpoint", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-feature-reevaluate-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let predictionCount = 0;
    const model = {
      identifier: "feature-reevaluate-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        predictionCount += 1;
        if (predictionCount === 1) {
          opts.onPredictionFragment({ content: "seed" });
          return { async result() { return { stats: { stopReason: "stop" } }; } };
        }
        const names = opts.rawTools.tools.map((tool) => tool.function.name);
        assert.ok(names.includes("read_file"));
        assert.ok(names.includes("list_directory"));
        assert.equal(names.includes("unreal_feature_intent_resolve"), false);
        opts.onToolCallRequestStart(1, { toolCallId: "next-candidate-list" });
        opts.onToolCallRequestNameReceived(1, "list_directory");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"path":"Source/Demo/Tests"}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "next-candidate-list",
            type: "function",
            name: "list_directory",
            arguments: { path: "Source/Demo/Tests" },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    await generate(
      controllerFor(model, {}, stateRoot, emitted, []),
      Chat.from({ messages: [
        { role: "user", content: [{ type: "text", text: "Implement the earliest incomplete local-play feature." }] },
      ] }),
    );

    const sessionDir = fs.readdirSync(stateRoot, { withFileTypes: true })
      .find((entry) => entry.isDirectory() && !entry.name.startsWith(".") && entry.name !== "_base");
    const checkpointPath = path.join(stateRoot, sessionDir.name, "active-checkpoint.json");
    const ownership = { taskSessionId: "task-reevaluate", ownerCapability: "owner-reevaluate" };
    const route = {
      routeHash: "route-reevaluate",
      phase: "planner",
      activeTools: ["list_directory", "read_file", "unreal_feature_intent_resolve"],
      selectedSlice: { sliceId: "task", files: [] },
    };
    const seeded = JSON.parse(fs.readFileSync(checkpointPath, "utf8"));
    seeded.taskRouteOwnership = ownership;
    seeded.toolRoute = route;
    seeded.featureIntentResume = {
      mode: "rediscover_after_target_read",
      observedResultCount: 0,
      observedDiscoveryResultCount: 1,
      maxDiscoveryCalls: 2,
    };
    seeded.requiredNextTool = {
      name: "unreal_feature_intent_resolve",
      reference: "stale-checkpoint-handoff",
      args: {
        taskAuthorization: ownership,
        targetFiles: ["Source/Demo/AlreadyDisproved.cpp"],
        completionFrontier: { candidateFeature: "already disproved" },
      },
    };
    fs.writeFileSync(checkpointPath, `${JSON.stringify(seeded, null, 2)}\n`, "utf8");

    const checkpointResult = {
      ok: true,
      taskAuthorization: ownership,
      toolRoute: route,
      requiredNextTool: "unreal_feature_intent_resolve",
      requiredNextToolArgs: { taskAuthorization: ownership },
      control: {
        version: 1,
        taskId: ownership.taskSessionId,
        phase: "unreal_task_checkpoint",
        status: "NeedsAction",
        nextAction: "unreal_feature_intent_resolve",
        nextActionIsTool: true,
        retryPolicy: "none",
      },
    };
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "Implement the earliest incomplete local-play feature." }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "evidence-contract", type: "function", name: "evidence_first_contract", arguments: { mode: "codegen" },
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "evidence-contract", name: "evidence_first_contract", content: '{"ok":true}',
      }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "candidate-target-read", type: "function", name: "read_file", arguments: { path: "Source/Demo/AlreadyDisproved.cpp" },
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "candidate-target-read", name: "read_file", content: '{"ok":true,"content":"implemented"}',
      }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "budget-checkpoint", type: "function", name: "unreal_task_checkpoint", arguments: { action: "record" },
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "budget-checkpoint", name: "unreal_task_checkpoint", content: JSON.stringify(checkpointResult),
      }] },
    ] });
    const tools = [
      { type: "function", function: { name: "list_directory", parameters: { type: "object", properties: { path: { type: "string" } } } } },
      { type: "function", function: { name: "read_file", parameters: { type: "object", properties: { path: { type: "string" } } } } },
      { type: "function", function: { name: "unreal_feature_intent_resolve", parameters: { type: "object", properties: { taskAuthorization: { type: "object" } } } } },
      { type: "function", function: { name: "evidence_first_contract", parameters: { type: "object", properties: {} } } },
    ];
    emitted.length = 0;
    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    assert.equal(predictionCount, 2);
    const end = emitted.find((event) => event.kind === "end");
    assert.equal(end.request.name, "list_directory");
    const checkpoint = activeCheckpoint(stateRoot);
    assert.equal(checkpoint.requiredNextTool, null);
    assert.equal(checkpoint.featureIntentResume.mode, "rediscover_after_target_read");
    assert.equal(Object.hasOwn(checkpoint.featureIntentResume, "args"), false);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});


test("active project bootstrap forces planner with the exact current user goal", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-active-project-plan-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const exactGoal = "현재 O-Mock 프로젝트에서 가장 앞선 미완성 기능을 실제로 구현해줘";
    const model = {
      identifier: "active-project-plan-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        assert.equal(opts.rawTools.force, true);
        assert.deepEqual(
          opts.rawTools.tools.map((tool) => tool.function.name),
          ["mcp/unreal-rag/unreal_agent_plan"],
        );
        opts.onToolCallRequestStart(1, { toolCallId: "plan-after-active-project" });
        opts.onToolCallRequestNameReceived(1, "unreal_agent_plan");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"request":"continue"}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "plan-after-active-project",
            type: "function",
            name: "unreal_agent_plan",
            arguments: { request: "continue" },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const bootstrap = {
      activeProject: "C:/Projects/O-Mock/O_Mock.uproject",
      projectContext: { ok: true, projectName: "O_Mock" },
      requiredNextTool: "unreal_agent_plan",
      control: {
        version: 1,
        phase: "unreal_get_active_project",
        status: "NeedsAction",
        nextAction: "unreal_agent_plan",
        nextActionIsTool: true,
        retryPolicy: "none",
      },
    };
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: exactGoal }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "active-project-1", type: "function", name: "unreal_get_active_project", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult",
        toolCallId: "active-project-1",
        name: "unreal_get_active_project",
        content: JSON.stringify(bootstrap),
      }] },
    ] });
    const tools = [{
      type: "function",
      function: {
        name: "mcp/unreal-rag/unreal_agent_plan",
        parameters: {
          type: "object",
          properties: { request: { type: "string" } },
          required: ["request"],
        },
      },
    }];

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    const end = emitted.find((event) => event.kind === "end");
    assert.ok(end);
    assert.equal(end.request.name, "unreal_agent_plan");
    assert.equal(end.request.arguments.request, exactGoal);
    assert.equal(emitted.some((event) => event.kind === "failure"), false);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("complete zero-result basename search lets a new Feature target reach the validator", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-feature-new-target-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const ownership = { taskSessionId: "task-feature-new-target", ownerCapability: "owner-feature-new-target" };
    const target = "Source/Demo/Tests/Stage1LocalPlay.spec.cpp";
    const model = {
      identifier: "feature-new-target-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        assert.equal(opts.rawTools.force, true);
        assert.deepEqual(
          opts.rawTools.tools.map((tool) => tool.function.name),
          ["unreal_feature_intent_resolve"],
        );
        const args = {
          slices: [{ sliceId: "new_stage", files: [target] }],
          completionFrontier: {
            milestone: "local play",
            candidateFeature: "local play behavior",
            declarationEvidence: [],
            implementationEvidence: [],
            implementedBehavior: [],
            unmetBehavior: {
              statement: "Implement one missing local-play behavior",
              sourcePath: target,
              locator: "new file",
              evidenceType: "direct_source",
            },
            priorCandidatesComplete: [],
          },
        };
        opts.onToolCallRequestStart(1, { toolCallId: "feature-new-target-submit" });
        opts.onToolCallRequestNameReceived(1, "unreal_feature_intent_resolve");
        opts.onToolCallRequestArgumentFragmentGenerated(1, JSON.stringify(args));
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "feature-new-target-submit",
            type: "function",
            name: "unreal_feature_intent_resolve",
            arguments: args,
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const route = {
      ok: true,
      taskAuthorization: ownership,
      featureCompletionAudit: { required: true, status: "pending" },
      toolRoute: {
        routeHash: "route-feature-new-target",
        phase: "planner",
        activeTools: ["read_file", "search_files", "unreal_feature_intent_resolve"],
        selectedSlice: { sliceId: "task", files: [] },
      },
      requiredNextTool: "unreal_feature_intent_resolve",
      requiredNextToolArgs: { taskAuthorization: ownership },
      control: {
        version: 1,
        phase: "unreal_agent_plan",
        status: "NeedsAction",
        nextAction: "unreal_feature_intent_resolve",
        nextActionIsTool: true,
      },
    };
    const messages = [
      { role: "user", content: [{ type: "text", text: "Check current implementation status and implement the earliest incomplete feature." }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "new-target-plan", type: "function", name: "unreal_agent_plan", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "new-target-plan", name: "unreal_agent_plan", content: JSON.stringify(route),
      }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "new-target-contract", type: "function", name: "evidence_first_contract", arguments: { mode: "codegen" },
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "new-target-contract", name: "evidence_first_contract", content: '{"ok":true}',
      }] },
    ];
    for (const [index, filePath] of [
      "Source/Demo/RuleEngine.h", "Source/Demo/RuleEngine.cpp",
      "Source/Demo/GameState.h", "Source/Demo/GameState.cpp",
      "Source/Demo/GameMode.h", "Source/Demo/GameMode.cpp",
    ].entries()) {
      const id = `new-target-evidence-${index}`;
      messages.push(
        { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
          id, type: "function", name: "read_file", arguments: { path: filePath },
        } }] },
        { role: "tool", content: [{
          type: "toolCallResult", toolCallId: id, name: "read_file", content: '{"ok":true}',
        }] },
      );
    }
    messages.push(
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "new-target-missing-read", type: "function", name: "read_file", arguments: { path: target },
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "new-target-missing-read", name: "read_file", content: JSON.stringify({
          ok: false,
          errorCode: "READ_TARGET_NOT_FOUND",
          requiredNextTool: "search_files",
          requiredNextToolArgs: {
            query: "Stage1LocalPlay.spec.cpp", path: "project://Source", matchFileNames: true,
          },
          nextAction: "search_files",
          nextActionIsTool: true,
        }),
      }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "new-target-zero-search", type: "function", name: "search_files", arguments: {
          query: "Stage1LocalPlay.spec.cpp", path: "project://Source", matchFileNames: true,
        },
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "new-target-zero-search", name: "search_files", content: JSON.stringify({
          ok: true, searchComplete: true, incompleteReasons: [], results: [], fileNameResults: [],
        }),
      }] },
    );
    const tools = [
      { type: "function", function: { name: "read_file", parameters: { type: "object", properties: { path: { type: "string" } } } } },
      { type: "function", function: { name: "search_files", parameters: { type: "object", properties: {} } } },
      { type: "function", function: {
        name: "unreal_feature_intent_resolve",
        parameters: {
          type: "object",
          properties: {
            slices: { type: "array" },
            taskAuthorization: { type: "object" },
          },
          required: ["taskAuthorization"],
        },
      } },
      { type: "function", function: { name: "evidence_first_contract", parameters: { type: "object", properties: {} } } },
    ];

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), Chat.from({ messages }));

    const end = emitted.find((event) => event.kind === "end");
    assert.ok(end);
    assert.equal(end.request.name, "unreal_feature_intent_resolve");
    assert.deepEqual(end.request.arguments.taskAuthorization, ownership);
    const sessionDir = fs.readdirSync(stateRoot, { withFileTypes: true })
      .find((entry) => entry.isDirectory() && !entry.name.startsWith(".") && entry.name !== "_base");
    const events = fs.readFileSync(path.join(stateRoot, sessionDir.name, "events.jsonl"), "utf8")
      .trim().split(/\r?\n/).map((line) => JSON.parse(line));
    assert.ok(events.some((event) => (
      event.type === "feature_intent_new_target_absence_proven"
        && event.targetFiles.includes("source/demo/tests/stage1localplay.spec.cpp")
    )));
    assert.equal(
      events.some((event) => event.type === "feature_intent_target_evidence_recovery_started"),
      false,
    );
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("fresh write task exposes only unreal_get_active_project before planner", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-initial-active-project-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let advertisedTools = [];
    let bootstrapRule = "";
    const model = {
      identifier: "initial-active-project-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(history, opts) {
        assert.equal(opts.rawTools.force, true);
        advertisedTools = (opts.rawTools?.tools || []).map((tool) => tool.function?.name || tool.name);
        bootstrapRule = history.getMessagesArray()
          .filter((message) => message.getRole() === "system")
          .map((message) => message.getText()).join("\n");
        opts.onToolCallRequestStart(1, { toolCallId: "initial-active-project-1" });
        opts.onToolCallRequestNameReceived(1, "unreal_get_active_project");
        opts.onToolCallRequestArgumentFragmentGenerated(1, "{}");
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "initial-active-project-1",
            type: "function",
            name: "unreal_get_active_project",
            arguments: {},
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const tools = [
      "unreal_get_active_project",
      "get_workspace_info",
      "unreal_agent_plan",
      "read_file",
      "apply_edit_bundle",
    ].map((name) => ({
      type: "function",
      function: { name, parameters: { type: "object", properties: {} } },
    }));
    const history = Chat.empty();
    history.append("system", "rules");
    history.append("user", "현재 O-Mock 프로젝트의 구현 상태를 먼저 확인하고, 오목 규칙과 로컬 플레이부터 시작하는 개발 순서에서 아직 완료되지 않은 가장 앞 단계의 핵심 기능 하나를 실제로 완성해줘. 문서나 계획만 만드는 데 그치지 말고 기능 구현을 우선해. 기존 동작과 현재 상태 소유권은 깨지 말고, 필요한 자동화 테스트와 Unreal 빌드까지 실행해서 결과를 알려줘.");

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    assert.deepEqual(advertisedTools, ["unreal_get_active_project"]);
    assert.match(bootstrapRule, /Do not call workspace, directory, read, search/);
    assert.equal(
      emitted.some((event) => event.kind === "end" && event.request.name === "unreal_get_active_project"),
      true,
    );
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("natural-language independent system design requires architecture validation", () => {
  const { requiresArchitectureValidation } = require("../dist/generator.js");
  const tools = [{ type: "function", function: { name: "unreal_architecture_reasoning" } }];

  assert.equal(
    requiresArchitectureValidation(
      "작은 독립 시스템으로 설계하고 구현해줘",
      tools,
    ),
    true,
  );
  assert.equal(
    requiresArchitectureValidation(
      "Create a standalone move history subsystem for local play",
      tools,
    ),
    true,
  );
});

test("bounded pre-route discovery forces one planner handoff for write goals", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-pre-route-planner-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let advertisedTools = [];
    let forced = false;
    let sawHandoffRule = false;
    const model = {
      identifier: "pre-route-planner-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(history, opts) {
        advertisedTools = (opts.rawTools?.tools || []).map((tool) => tool.function?.name || tool.name);
        forced = opts.rawTools?.force === true;
        sawHandoffRule = history.getMessagesArray().some(
          (message) => message.getRole() === "system"
            && message.getText().includes("[UNREAL_PRE_ROUTE_PLANNER_HANDOFF]"),
        );
        opts.onToolCallRequestStart(1, { toolCallId: "plan-after-discovery" });
        opts.onToolCallRequestNameReceived(1, "unreal_agent_plan");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"request":"implement bounded rule fix"}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "plan-after-discovery",
            type: "function",
            name: "unreal_agent_plan",
            arguments: { request: "implement bounded rule fix" },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const tools = ["read_file", "list_directory", "unreal_agent_plan", "evidence_record"].map((name) => ({
      type: "function",
      function: { name, parameters: { type: "object", properties: {} } },
    }));
    const messages = [
      { role: "system", content: [{ type: "text", text: "rules" }] },
      { role: "user", content: [{ type: "text", text: "implement bounded rule fix" }] },
    ];
    for (let index = 0; index < 6; index += 1) {
      const id = `pre-route-read-${index}`;
      messages.push({
        role: "assistant",
        content: [{ type: "toolCallRequest", toolCallRequest: {
          id,
          type: "function",
          name: "read_file",
          arguments: { path: `Source/Rule${index}.cpp` },
        } }],
      });
      messages.push({
        role: "tool",
        content: [{ type: "toolCallResult", toolCallId: id, content: `source-${index}` }],
      });
    }

    const history = Chat.from({ messages });
    await generate(
      controllerFor(model, { preRouteDiscoveryLimit: 6 }, stateRoot, emitted, tools),
      history,
    );

    assert.deepEqual(advertisedTools, ["unreal_agent_plan"]);
    assert.equal(forced, true);
    assert.equal(sawHandoffRule, true);
    assert.equal(
      emitted.some((event) => event.kind === "end" && event.request.name === "unreal_agent_plan"),
      true,
    );
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("unrouted agent catalog removes mutation schemas before prediction", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-unrouted-agent-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let advertisedTools = [];
    let routeRule = "";
    const model = {
      identifier: "unrouted-agent-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(history, opts) {
        advertisedTools = (opts.rawTools?.tools || []).map((tool) => tool.function?.name || tool.name);
        routeRule = history.getMessagesArray()
          .filter((message) => message.getRole() === "system")
          .map((message) => message.getText()).join("\n");
        opts.onToolCallRequestStart(1, { toolCallId: "read-1" });
        opts.onToolCallRequestNameReceived(1, "read_file");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: { id: "read-1", type: "function", name: "read_file", arguments: {} },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const tools = ["get_active_project", "read_file", "apply_edit_bundle"].map((name) => ({
      type: "function",
      function: { name, parameters: { type: "object", properties: {} } },
    }));
    const history = Chat.empty();
    history.append("system", "rules");
    history.append("user", "implement O-Mock rules");
    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    assert.deepEqual(advertisedTools, ["get_active_project", "read_file"]);
    assert.match(routeRule, /mcp\/unreal-rag planner provider is missing/);
    assert.equal(emitted.some((event) => event.kind === "end" && event.request.name === "read_file"), true);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("routed work catalog hides checkpoint until server explicitly requires it", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-routed-checkpoint-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let advertisedTools = [];
    const model = {
      identifier: "routed-checkpoint-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        advertisedTools = (opts.rawTools?.tools || []).map((tool) => tool.function?.name || tool.name);
        opts.onToolCallRequestStart(1, { toolCallId: "write-1" });
        opts.onToolCallRequestNameReceived(1, "replace_in_file");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: { id: "write-1", type: "function", name: "replace_in_file", arguments: {} },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const route = {
      ok: true,
      taskAuthorization: {
        taskSessionId: "task-routed-checkpoint",
        ownerCapability: "owner-routed-checkpoint",
      },
      toolRoute: {
        routeHash: "route-routed-checkpoint",
        phase: "executor",
        activeTools: ["replace_in_file"],
      },
      control: {
        version: 1,
        phase: "unreal_agent_plan",
        status: "NeedsAction",
        nextAction: "continue_with_current_tool_route",
        nextActionIsTool: false,
        retryPolicy: "none",
      },
    };
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "implement the bounded change" }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "plan-1", type: "function", name: "unreal_agent_plan", arguments: { request: "implement" },
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "plan-1", name: "unreal_agent_plan", content: JSON.stringify(route),
      }] },
    ] });
    const tools = [
      "replace_in_file",
      "unreal_rag_search",
      "write_file",
      "unreal_task_checkpoint",
      "get_active_project",
      "evidence_record",
    ].map((name) => ({
      type: "function",
      function: { name, parameters: { type: "object", properties: {} } },
    }));

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    assert.deepEqual(advertisedTools, ["replace_in_file", "get_active_project", "evidence_record"]);
    assert.equal(emitted.some((event) => event.kind === "end" && event.request.name === "replace_in_file"), true);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("stale pre-route Agent catalog forces exactly one read-only catalog refresh", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-catalog-refresh-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let advertisedTools = [];
    let forced = false;
    let refreshRulePresent = false;
    const model = {
      identifier: "catalog-refresh-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(history, opts) {
        advertisedTools = (opts.rawTools?.tools || []).map((tool) => tool.function?.name || tool.name);
        forced = opts.rawTools?.force === true;
        refreshRulePresent = history.getMessagesArray().some(
          (message) => message.getRole() === "system"
            && message.getText().includes("[UNREAL_TOOL_CATALOG_REFRESH]"),
        );
        opts.onToolCallRequestStart(1, { toolCallId: "catalog-refresh-1" });
        opts.onToolCallRequestNameReceived(1, "get_active_project");
        opts.onToolCallRequestArgumentFragmentGenerated(1, "{}");
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "catalog-refresh-1",
            type: "function",
            name: "get_active_project",
            arguments: {},
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const route = {
      ok: true,
      taskAuthorization: {
        taskSessionId: "task-catalog-refresh",
        ownerCapability: "owner-catalog-refresh",
      },
      toolRoute: {
        routeHash: "route-catalog-refresh",
        phase: "executor",
        activeTools: ["apply_edit_bundle", "static_validate_project"],
      },
      control: {
        version: 1,
        phase: "code_sketch_claim_validate",
        status: "NeedsAction",
        nextAction: "implement_next_slice",
        nextActionIsTool: false,
        retryPolicy: "none",
      },
    };
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "implement the bounded change" }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "route-1", type: "function", name: "code_sketch_claim_validate", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "route-1", name: "code_sketch_claim_validate", content: JSON.stringify(route),
      }] },
    ] });
    const staleTools = ["get_active_project", "unreal_rag_health", "read_file"].map((name) => ({
      type: "function",
      function: { name, parameters: { type: "object", properties: {} } },
    }));

    await generate(controllerFor(model, {}, stateRoot, emitted, staleTools), history);

    assert.deepEqual(advertisedTools, ["get_active_project"]);
    assert.equal(forced, true);
    assert.equal(refreshRulePresent, true);
    assert.equal(emitted.some((event) => event.kind === "end" && event.request.name === "get_active_project"), true);
    assert.deepEqual(activeCheckpoint(stateRoot).catalogRefresh, {
      routeHash: "route-catalog-refresh",
      attempts: 1,
      status: "requested",
      tool: "get_active_project",
      requestedAt: activeCheckpoint(stateRoot).catalogRefresh.requestedAt,
    });
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("stale Agent catalog fails closed after the single refresh instead of polling health or reads", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-catalog-refresh-bounded-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let respondCount = 0;
    const model = {
      identifier: "catalog-refresh-bounded-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        respondCount += 1;
        opts.onToolCallRequestStart(1, { toolCallId: "catalog-refresh-bounded-1" });
        opts.onToolCallRequestNameReceived(1, "get_active_project");
        opts.onToolCallRequestArgumentFragmentGenerated(1, "{}");
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "catalog-refresh-bounded-1",
            type: "function",
            name: "get_active_project",
            arguments: {},
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const route = {
      ok: true,
      taskAuthorization: {
        taskSessionId: "task-catalog-refresh-bounded",
        ownerCapability: "owner-catalog-refresh-bounded",
      },
      toolRoute: {
        routeHash: "route-catalog-refresh-bounded",
        phase: "executor",
        activeTools: ["apply_edit_bundle"],
      },
      control: {
        version: 1,
        phase: "code_sketch_claim_validate",
        status: "NeedsAction",
        nextAction: "implement_next_slice",
        nextActionIsTool: false,
        retryPolicy: "none",
      },
    };
    const baseMessages = [
      { role: "user", content: [{ type: "text", text: "implement the bounded change" }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "route-bounded-1", type: "function", name: "code_sketch_claim_validate", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult", toolCallId: "route-bounded-1", name: "code_sketch_claim_validate", content: JSON.stringify(route),
      }] },
    ];
    const staleTools = ["get_active_project", "unreal_rag_health", "read_file"].map((name) => ({
      type: "function",
      function: { name, parameters: { type: "object", properties: {} } },
    }));

    await generate(
      controllerFor(model, {}, stateRoot, emitted, staleTools),
      Chat.from({ messages: baseMessages }),
    );
    assert.equal(respondCount, 1);

    const continued = Chat.from({ messages: [
      ...baseMessages,
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "catalog-refresh-bounded-1", type: "function", name: "get_active_project", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult",
        toolCallId: "catalog-refresh-bounded-1",
        name: "get_active_project",
        content: JSON.stringify({ ok: true, activeProject: "O-Mock" }),
      }] },
    ] });
    await assert.rejects(
      generate(controllerFor(model, {}, stateRoot, emitted, staleTools), continued),
      /did not expose .* mutation schemas after one bounded refresh/,
    );
    assert.equal(respondCount, 1, "A failed refresh must not start another model prediction");
    assert.equal(activeCheckpoint(stateRoot).catalogRefresh.status, "failed");
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("atomic output streams reasoning progress but withholds final text until completion", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-reasoning-progress-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let releasePrediction;
    let signalPredictionStarted;
    let reasoningWasImmediate = false;
    let finalWasBuffered = false;
    const predictionBarrier = new Promise((resolve) => { releasePrediction = resolve; });
    const predictionStarted = new Promise((resolve) => { signalPredictionStarted = resolve; });
    const model = {
      identifier: "reasoning-progress-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        opts.onPredictionFragment({
          content: "Inspecting project structure...",
          tokensCount: 3,
          containsDrafted: false,
          reasoningType: "reasoning",
          isStructural: false,
        });
        opts.onPredictionFragment({
          content: "Complete.",
          tokensCount: 1,
          containsDrafted: false,
          reasoningType: "none",
          isStructural: false,
        });
        reasoningWasImmediate = emitted.some(
          (event) => event.kind === "fragment" && event.content.includes("Inspecting"),
        );
        finalWasBuffered = !emitted.some(
          (event) => event.kind === "fragment" && event.content.includes("Complete"),
        );
        signalPredictionStarted();
        return { async result() { await predictionBarrier; return { stats: { stopReason: "eosFound" } }; } };
      },
    };
    const history = Chat.empty();
    history.append("user", "inspect and explain");
    const running = generate(controllerFor(model, {}, stateRoot, emitted, []), history);
    await predictionStarted;

    assert.equal(reasoningWasImmediate, true);
    assert.equal(finalWasBuffered, true);
    assert.deepEqual(emitted.map((event) => event.content), ["Inspecting project structure..."]);

    releasePrediction();
    await running;
    assert.deepEqual(
      emitted.filter((event) => event.kind === "fragment").map((event) => event.content),
      ["Inspecting project structure...", "Complete."],
    );
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("silent local-model prediction emits a bounded UI heartbeat", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-heartbeat-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const model = {
      identifier: "heartbeat-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond() {
        return {
          async result() {
            await new Promise((resolve) => setTimeout(resolve, 1_150));
            return { stats: { stopReason: "eosFound" } };
          },
        };
      },
    };
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "Inspect the current source state." }] },
    ] });

    await generate(controllerFor(
      model,
      { predictionHeartbeatSeconds: 1 },
      stateRoot,
      emitted,
      [],
    ), history);

    const heartbeat = emitted.find((event) => (
      event.kind === "fragment" && event.content.includes("[Working: Model reasoning")
    ));
    assert.ok(heartbeat);
    assert.equal(heartbeat.opts.reasoningType, "reasoning");
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("RAG, architecture, and planner calls receive one stable compactor session id", async () => {
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
        opts.onToolCallRequestStart(3, { toolCallId: "planner-1" });
        opts.onToolCallRequestNameReceived(3, "unreal_agent_plan");
        opts.onToolCallRequestArgumentFragmentGenerated(3, '{"request":"implement lobby"}');
        opts.onToolCallRequestEnd(3, {
          toolCallRequest: {
            id: "planner-1",
            type: "function",
            name: "unreal_agent_plan",
            arguments: { request: "implement lobby" },
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
    const tools = ["unreal_rag_search", "unreal_architecture_reasoning", "unreal_agent_plan"].map((name) => ({
      type: "function",
      function: {
        name,
        parameters: {
          type: "object",
          properties: {
            query: { type: "string" },
            sessionId: { type: "string" },
            latestUserMessage: { type: "string" },
          },
        },
      },
    }));
    const controller = controllerFor(model, config, stateRoot, emitted, tools);
    const history = Chat.empty();
    history.append("system", "rules");
    history.append("user", "investigate the lobby architecture");
    history.append("assistant", "I will continue from the active objective.");
    history.append("user", "continue");

    await generate(controller, history);

    const ends = emitted.filter((event) => event.kind === "end");
    assert.equal(ends.length, 3);
    assert.ok(ends[0].request.arguments.sessionId);
    assert.equal(ends[0].request.arguments.query, "lobby");
    assert.equal(ends[1].request.arguments.sessionId, ends[0].request.arguments.sessionId);
    assert.equal(ends[2].request.arguments.sessionId, ends[0].request.arguments.sessionId);
    assert.equal(ends[2].request.arguments.request, "implement lobby");
    assert.equal(ends[2].request.arguments.latestUserMessage, "investigate the lobby architecture");
    const args = emitted.filter((event) => event.kind === "args");
    assert.equal(JSON.parse(args[0].content).sessionId, ends[0].request.arguments.sessionId);
    assert.equal(JSON.parse(args[1].content).sessionId, ends[0].request.arguments.sessionId);
    assert.equal(JSON.parse(args[2].content).sessionId, ends[0].request.arguments.sessionId);
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
              scope: {
                type: "object",
                properties: {
                  networked: { type: "boolean" },
                  runtime: { type: "string" },
                  validationLevel: { type: "string" },
                },
              },
              invariants: {
                type: "array",
                items: {
                  type: "object",
                  properties: { id: { type: "string" }, statement: { type: "string" } },
                },
              },
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
                items: {
                  type: "object",
                  properties: {
                    invariant: { type: "string" },
                    invariantId: { type: "string" },
                    checks: { ...array },
                  },
                },
              },
              implementationSlices: {
                type: "array",
                items: {
                  type: "object",
                  properties: {
                    sliceId: { type: "string" },
                    files: { ...array },
                    invariants: { ...array },
                    invariantIds: { ...array },
                    validation: { ...array },
                  },
                },
              },
            },
          },
        },
      },
    },
  };
}

test("architecture evidence blockers reopen bounded discovery without forcing a replan", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-architecture-evidence-refill-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { architectureGateStatus, generate } = require("../dist/generator.js");
    const emitted = [];
    let capturedTools = null;
    const model = {
      identifier: "architecture-evidence-refill-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        capturedTools = opts.rawTools;
        opts.onToolCallRequestStart(1, { toolCallId: "refill-read" });
        opts.onToolCallRequestNameReceived(1, "search_files");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"query":"MissingWorker"}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "refill-read",
            type: "function",
            name: "search_files",
            arguments: { query: "MissingWorker" },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const messages = [
      { role: "system", content: [{ type: "text", text: "rules" }] },
      {
        role: "user",
        content: [{ type: "text", text: "Implement a small independent local move-history system." }],
      },
    ];
    for (let index = 0; index < 4; index += 1) {
      const id = `evidence-read-${index}`;
      messages.push({
        role: "assistant",
        content: [{
          type: "toolCallRequest",
          toolCallRequest: { id, type: "function", name: "read_file", arguments: { path: `Source/${index}.cpp` } },
        }],
      });
      messages.push({
        role: "tool",
        content: [{ type: "toolCallResult", toolCallId: id, content: `source-${index}` }],
      });
    }
    messages.push(
      {
        role: "assistant",
        content: [{
          type: "toolCallRequest",
          toolCallRequest: {
            id: "architecture-evidence-rejected",
            type: "function",
            name: "unreal_architecture_reasoning",
            arguments: { proposal: { decision: "local history" } },
          },
        }],
      },
      {
        role: "tool",
        content: [{
          type: "toolCallResult",
          toolCallId: "architecture-evidence-rejected",
          content: JSON.stringify({
            ok: false,
            errorCode: "ARCHITECTURE_EVIDENCE_INCOMPLETE",
            proposalValidation: {
              ok: false,
              repairStrategy: "evidence_refill",
              designContract: { requiresFullReplan: false, stagedImplementation: true },
              implementationGate: { writesAllowed: false },
            },
            requiredNextAction: "collect_architecture_evidence",
            nextActionIsTool: false,
          }),
        }],
      },
    );

    const history = Chat.from({ messages });
    const evidenceStatus = architectureGateStatus(history, null);
    assert.equal(evidenceStatus.lastRepairStrategy, "evidence_refill");
    assert.equal(evidenceStatus.requiresFullProposal, false);

    await generate(
      controllerFor(model, {
        enabled: true,
        targetModel: "",
        architectureReplanEvidenceReadBudget: 4,
      }, stateRoot, emitted, [
        { type: "function", function: { name: "read_file", parameters: { type: "object" } } },
        { type: "function", function: { name: "search_files", parameters: { type: "object" } } },
        fullArchitectureToolSchema(),
      ]),
      history,
    );

    assert.equal(capturedTools.force, undefined);
    assert.deepEqual(
      capturedTools.tools.map((tool) => tool.function.name),
      ["read_file", "search_files", "unreal_architecture_reasoning"],
    );
    assert.equal(
      capturedTools.tools[2].function.parameters.required?.includes("proposal") || false,
      true,
    );
    assert.equal(emitted.filter((event) => event.kind === "end").length, 1);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("local bounded architecture schema uses explicit scope and invariant ids without strict ceremony", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-local-bound-schema-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let capturedTools = null;
    const proposal = {
      decision: "local move history owner",
      scope: { networked: false, runtime: "local_hotseat", validationLevel: "Bound" },
      invariants: [{ id: "I1", statement: "board remains rule-engine owned" }],
      impactedSurfaces: ["Source/Feature.cpp"],
      validationPlan: ["compile"],
      implementationFiles: ["Source/Feature.cpp"],
      ownership: {
        stateOwner: "local history",
        dataOwner: "rule engine",
        lifecycleOwner: "game state",
        failurePolicy: "no mutation",
        recoveryPolicy: "clear history",
      },
      stateInventory: [{ state: "history" }],
      lifecycleTransitions: [{ event: "undo" }],
      validationMatrix: [{ invariantId: "I1", checks: ["compile"] }],
      implementationSlices: [{
        sliceId: "local-undo",
        files: ["Source/Feature.cpp"],
        invariantIds: ["I1"],
        validation: ["compile"],
      }],
    };
    const model = {
      identifier: "local-bound-schema-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        capturedTools = opts.rawTools;
        opts.onToolCallRequestStart(1, { toolCallId: "local-bound" });
        opts.onToolCallRequestNameReceived(1, "unreal_architecture_reasoning");
        opts.onToolCallRequestArgumentFragmentGenerated(1, JSON.stringify({ proposal }));
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "local-bound",
            type: "function",
            name: "unreal_architecture_reasoning",
            arguments: { proposal },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const messages = [
      { role: "system", content: [{ type: "text", text: "rules" }] },
      {
        role: "user",
        content: [{
          type: "text",
          text: "Implement a small independent local hotseat move-history system. Do not change network or server features.",
        }],
      },
    ];
    for (let index = 0; index < 4; index += 1) {
      const id = `local-read-${index}`;
      messages.push({
        role: "assistant",
        content: [{
          type: "toolCallRequest",
          toolCallRequest: {
            id,
            type: "function",
            name: "read_file",
            arguments: { path: `Source/Local${index}.cpp` },
          },
        }],
      });
      messages.push({
        role: "tool",
        content: [{ type: "toolCallResult", toolCallId: id, content: `source-${index}` }],
      });
    }

    await generate(
      controllerFor(model, { enabled: true, targetModel: "" }, stateRoot, emitted, [
        { type: "function", function: { name: "read_file", parameters: { type: "object" } } },
        fullArchitectureToolSchema(),
      ]),
      Chat.from({ messages }),
    );

    const schema = capturedTools.tools[0].function.parameters.properties.proposal;
    assert.ok(schema.required.includes("scope"));
    assert.ok(schema.required.includes("ownership"));
    assert.equal(schema.required.includes("alternatives"), false);
    assert.equal(schema.required.includes("networking"), false);
    assert.equal(schema.required.includes("migrationPlan"), false);
    assert.deepEqual(schema.properties.invariants.items.required, ["id", "statement"]);
    assert.deepEqual(
      schema.properties.validationMatrix.items.required,
      ["invariantId", "checks"],
    );
    assert.ok(schema.properties.implementationSlices.items.required.includes("invariantIds"));
    assert.equal(emitted.filter((event) => event.kind === "end").length, 1);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("negative network scope does not require the network architecture contract", async () => {
  const { networkedArchitectureContractRequired } = require("../dist/generator.js");
  assert.equal(networkedArchitectureContractRequired(
    "네트워크, 매치메이킹, 서버 기능은 이번에는 건드리지 말고 로컬 2인 기능만 구현해.",
  ), false);
  assert.equal(networkedArchitectureContractRequired(
    "Implement authoritative multiplayer RPC ownership and server replication.",
  ), true);
  assert.equal(networkedArchitectureContractRequired(
    "Do not change network or server features; implement local hotseat only.",
  ), false);
});

test("conversation session is injected into direct evidence tools", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-evidence-session-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const model = {
      identifier: "evidence-session-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        opts.onToolCallRequestStart(1, { toolCallId: "read-1" });
        opts.onToolCallRequestNameReceived(1, "read_file_range");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"path":"A.cpp","startLine":1,"endLine":10}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "read-1",
            type: "function",
            name: "read_file_range",
            arguments: { path: "A.cpp", startLine: 1, endLine: 10 },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const tools = [{
      type: "function",
      function: {
        name: "read_file_range",
        parameters: {
          type: "object",
          properties: { path: { type: "string" }, sessionId: { type: "string" } },
        },
      },
    }];
    const history = Chat.empty();
    history.append("user", "inspect A.cpp");
    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    const end = emitted.find((event) => event.kind === "end");
    assert.ok(end.request.arguments.sessionId);
    assert.equal(JSON.parse(emitted.find((event) => event.kind === "args").content).sessionId, end.request.arguments.sessionId);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("semantic blocker rejects forbidden evidence calls but allows forward mutation", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-semantic-blocker-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let advertisedTools = [];
    const model = {
      identifier: "semantic-blocker-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        advertisedTools = (opts.rawTools?.tools || []).map((tool) => tool.function?.name || tool.name);
        for (const [callId, name] of [[1, "search_files"], [2, "replace_in_file"]]) {
          opts.onToolCallRequestStart(callId, { toolCallId: `call-${callId}` });
          opts.onToolCallRequestNameReceived(callId, name);
          opts.onToolCallRequestArgumentFragmentGenerated(callId, "{}");
          opts.onToolCallRequestEnd(callId, {
            toolCallRequest: { id: `call-${callId}`, type: "function", name, arguments: {} },
          });
        }
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const blocker = {
      ok: false,
      errorCode: "EVIDENCE_STAGNATION_REPEAT",
      taskAuthorization: {
        taskSessionId: "task-semantic-forward",
        ownerCapability: "owner-semantic-forward",
      },
      stopCurrentWorkflow: false,
      stopCurrentPhase: true,
      phaseBoundary: "evidence",
      doNotRetry: ["read_file", "read_file_range", "read_symbol", "search_files"],
      agentInstruction: "Do not call another evidence tool.",
      control: {
        version: 1,
        phase: "search_files",
        status: "Blocked",
        nextActionIsTool: false,
        retryPolicy: "forbidden",
        blockerFingerprint: "loop-1",
      },
    };
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "구현을 완료해줘" }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "old-search", type: "function", name: "search_files", arguments: { query: "RestartMatch" },
      } }] },
      { role: "tool", content: [{ type: "toolCallResult", toolCallId: "old-search", name: "search_files", content: JSON.stringify(blocker) }] },
    ] });
    const tools = ["search_files", "read_file", "replace_in_file"].map((name) => ({
      type: "function",
      function: { name, parameters: { type: "object", properties: {} } },
    }));
    const controller = controllerFor(model, {}, stateRoot, emitted, tools);

    await generate(controller, history);

    assert.deepEqual(advertisedTools, ["replace_in_file"]);
    assert.equal(emitted.some((event) => event.kind === "end" && event.request.name === "search_files"), false);
    assert.equal(emitted.some((event) => event.kind === "failure" && /semantic blocker forbids search_files/.test(event.error)), true);
    assert.equal(emitted.some((event) => event.kind === "end" && event.request.name === "replace_in_file"), true);
    assert.deepEqual(activeCheckpoint(stateRoot).semanticBlocker.forbiddenTools.sort(), [
      "read_file", "read_file_range", "read_symbol", "search_files",
    ]);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("missing read target forces one basename search instead of another read", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-missing-read-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let advertisedTools = [];
    const model = {
      identifier: "missing-read-recovery-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        advertisedTools = (opts.rawTools?.tools || []).map((tool) => tool.function?.name || tool.name);
        const callId = 1;
        opts.onToolCallRequestStart(callId, { toolCallId: `call-${callId}` });
        opts.onToolCallRequestNameReceived(callId, "search_files");
        opts.onToolCallRequestArgumentFragmentGenerated(callId, "{}");
        opts.onToolCallRequestEnd(callId, {
          toolCallRequest: { id: `call-${callId}`, type: "function", name: "search_files", arguments: {} },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const searchArgs = {
      query: "GomokuLocalPlayTest.cpp",
      path: "project://Source",
      matchFileNames: true,
    };
    const blocker = {
      ok: false,
      errorCode: "READ_TARGET_NOT_FOUND",
      stopCurrentWorkflow: false,
      doNotRetry: ["read_file_range"],
      doNotRetryTools: ["read_file_range"],
      requiredNextTool: "search_files",
      requiredNextToolArgs: searchArgs,
      nextAction: "search_files",
      nextActionArgs: searchArgs,
      nextActionIsTool: true,
      agentInstruction: "Search the exact basename once.",
      control: {
        version: 1,
        phase: "read_file_range",
        status: "NeedsAction",
        nextAction: "search_files",
        nextActionIsTool: true,
        retryPolicy: "forbidden",
      },
    };
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "구현을 완료해줘" }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "missing-read", type: "function", name: "read_file_range", arguments: {
          path: "Source/O_Mock/Tests/GomokuLocalPlayTest.cpp", startLine: 380, endLine: 520,
        },
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult",
        toolCallId: "missing-read",
        name: "read_file_range",
        content: JSON.stringify(blocker),
      }] },
    ] });
    const tools = ["read_file_range", "search_files"].map((name) => ({
      type: "function",
      function: {
        name,
        parameters: {
          type: "object",
          properties: {
            query: { type: "string" },
            path: { type: "string" },
            matchFileNames: { type: "boolean" },
          },
        },
      },
    }));

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    assert.deepEqual(advertisedTools, ["search_files"]);
    assert.equal(emitted.some((event) => event.kind === "end" && event.request.name === "read_file_range"), false);
    const search = emitted.find((event) => event.kind === "end" && event.request.name === "search_files");
    assert.ok(search);
    assert.deepEqual(search.request.arguments, searchArgs);
    const checkpoint = activeCheckpoint(stateRoot);
    assert.equal(checkpoint.semanticBlocker.scope, "until_required_tool_success");
    assert.deepEqual(checkpoint.semanticBlocker.forbiddenTools, ["read_file_range"]);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("server workflow stop exposes no tools and emits only a final blocker response", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-workflow-stop-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let advertisedTools = null;
    let modelRespondCalls = 0;
    const model = {
      identifier: "workflow-stop-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(history, opts) {
        modelRespondCalls += 1;
        advertisedTools = (opts.rawTools?.tools || []).map((tool) => tool.function?.name || tool.name);
        return { async result() { return { stats: { stopReason: "eosFound" } }; } };
      },
    };
    const blocker = {
      ok: false,
      errorCode: "LINKER_RECOVERY_SEMANTIC_INVENTION",
      stopCurrentWorkflow: true,
      nextAction: "request_or_locate_semantic_contract",
      nextActionIsTool: false,
      agentInstruction: "Do not invent readiness state.",
      control: {
        version: 1,
        phase: "unreal_code_sketch_claim_validate",
        status: "Blocked",
        nextAction: "request_or_locate_semantic_contract",
        nextActionIsTool: false,
        retryPolicy: "forbidden",
        blockerFingerprint: "semantic-stop-1",
      },
    };
    const history = Chat.from({ messages: [
      { role: "user", content: [{ type: "text", text: "링커 오류를 고쳐줘" }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "gate", type: "function", name: "unreal_code_sketch_claim_validate", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult",
        toolCallId: "gate",
        name: "unreal_code_sketch_claim_validate",
        content: JSON.stringify(blocker),
      }] },
      { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
        id: "later-status", type: "function", name: "unreal_task_status", arguments: {},
      } }] },
      { role: "tool", content: [{
        type: "toolCallResult",
        toolCallId: "later-status",
        name: "unreal_task_status",
        content: JSON.stringify({
          ok: true,
          control: {
            version: 1,
            phase: "unreal_task_status",
            status: "NeedsAction",
            nextAction: "unreal_code_sketch_claim_validate",
            nextActionIsTool: true,
          },
        }),
      }] },
    ] });
    const tools = ["search_files", "read_file", "replace_in_file"].map((name) => ({
      type: "function",
      function: { name, parameters: { type: "object", properties: {} } },
    }));

    await generate(controllerFor(model, {}, stateRoot, emitted, tools), history);

    assert.equal(modelRespondCalls, 0);
    assert.equal(advertisedTools, null);
    assert.equal(emitted.some((event) => event.kind === "end"), false);
    assert.equal(emitted.some((event) => event.kind === "fragment" && /서버 검증에서 중단/.test(event.content)), true);
    assert.equal(emitted.some((event) => event.kind === "fragment" && /<tool_call>/.test(event.content)), false);
    const checkpoint = activeCheckpoint(stateRoot);
    assert.equal(checkpoint.semanticBlocker.scope, "workflow");
    assert.equal(checkpoint.semanticBlocker.stopCurrentWorkflow, true);
    assert.equal(checkpoint.requiredNextTool, null);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

function completeArchitectureProposal(decision = "replanned") {
  return {
    decision,
    scope: { networked: true, runtime: "dedicated_server", validationLevel: "Strict" },
    invariants: [{ id: "I1", statement: "server authority" }],
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
    validationMatrix: [{ invariantId: "I1", checks: ["static validation"] }],
    implementationSlices: [{
      sliceId: "feature",
      files: ["Source/Feature.cpp"],
      invariantIds: ["I1"],
      validation: ["static validation"],
    }],
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
    assert.equal(activeCheckpoint(stateRoot).objective, "inspect the project");
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

test("control plane injects server-owned required arguments", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-required-args-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    const model = {
      identifier: "required-args-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(history, opts) {
        assert.equal(opts.rawTools.force, true);
        assert.deepEqual(opts.rawTools.tools.map((tool) => tool.function.name), ["mcp/unreal-agent/search_files"]);
        assert.equal(history.getMessagesArray().some(
          (message) => message.getRole() === "system"
            && message.getText().includes("[UNREAL_SERVER_REQUIRED_TOOL]")
            && message.getText().includes("HandlePlaceStone"),
        ), true);
        opts.onToolCallRequestStart(1, { toolCallId: "wrong-search" });
        opts.onToolCallRequestNameReceived(1, "search_files");
        opts.onToolCallRequestArgumentFragmentGenerated(1, '{"query":"RestartMatch","path":"project://Source"}');
        opts.onToolCallRequestEnd(1, {
          toolCallRequest: {
            id: "wrong-search",
            type: "function",
            name: "search_files",
            arguments: { query: "RestartMatch", path: "project://Source" },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const history = Chat.empty();
    history.append("user", "continue");
    history.append("assistant", JSON.stringify({
      requiredNextTool: "search_files",
      requiredNextToolArgs: { query: "HandlePlaceStone", path: "project://Source" },
    }));
    await generate(controllerFor(model, {}, stateRoot, emitted, [
      { type: "function", function: { name: "read_file" } },
      { type: "function", function: { name: "unreal_rag_health" } },
      { type: "function", function: {
        name: "mcp/unreal-agent/search_files",
        parameters: {
          type: "object",
          properties: {
            query: { type: "string" },
            path: { type: "string" },
            sessionId: { type: "string" },
          },
        },
      } },
    ]), history);

    const end = emitted.find((event) => event.kind === "end");
    assert.ok(end);
    assert.equal(end.request.arguments.query, "HandlePlaceStone");
    assert.equal(end.request.arguments.path, "project://Source");
    assert.ok(end.request.arguments.sessionId);
    const args = emitted.find((event) => event.kind === "args");
    assert.deepEqual(JSON.parse(args.content), end.request.arguments);
    assert.equal(emitted.some((event) => event.kind === "failure"), false);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("routed required tool missing from the chat catalog stops before model invocation", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-required-schema-missing-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let modelInvocations = 0;
    const ownership = { taskSessionId: "task-stale-catalog", ownerCapability: "owner-stale-catalog" };
    const model = {
      identifier: "required-schema-missing-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond() {
        modelInvocations += 1;
        throw new Error("target model must not run for an impossible required-tool contract");
      },
    };
    const history = Chat.empty();
    history.append("user", "implement the first missing local-play feature");
    history.append("assistant", JSON.stringify({
      ok: true,
      requiredNextTool: "unreal_feature_intent_resolve",
      requiredNextToolArgs: { taskAuthorization: ownership },
      taskAuthorization: ownership,
      toolRoute: {
        routeHash: "route-stale-catalog",
        phase: "planner",
        activeTools: ["read_file", "unreal_feature_intent_resolve"],
        selectedSlice: { sliceId: "task", files: [] },
      },
    }));

    await generate(controllerFor(model, {}, stateRoot, emitted, [{
      type: "function",
      function: { name: "read_file", parameters: { type: "object" } },
    }]), history);

    assert.equal(modelInvocations, 0);
    assert.equal(emitted.filter((event) => event.kind === "end").length, 0);
    assert.equal(emitted.filter((event) => event.kind === "failure").length, 0);
    const final = emitted.find((event) => event.kind === "fragment");
    assert.ok(final);
    assert.match(final.content, /current chat catalog does not expose that tool schema/);
    const checkpoint = activeCheckpoint(stateRoot);
    assert.equal(checkpoint.requiredNextTool.name, "unreal_feature_intent_resolve");
    const sessionDir = fs.readdirSync(stateRoot, { withFileTypes: true })
      .find((entry) => entry.isDirectory());
    const events = fs.readFileSync(path.join(stateRoot, sessionDir.name, "events.jsonl"), "utf8")
      .trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);
    assert.equal(events.some((event) => (
      event.type === "context_measurement"
        && event.requiredToolSchemaMissing === true
        && event.missingRequiredToolName === "unreal_feature_intent_resolve"
    )), true);
    assert.equal(events.some((event) => (
      event.type === "required_tool_schema_missing_final_emitted"
        && event.targetModelInvoked === false
    )), true);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("wrong forced tool name gets one bounded required-tool serialization repair", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-required-tool-repair-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let predictionCount = 0;
    const ownership = { taskSessionId: "task-repair-1", ownerCapability: "owner-repair-1" };
    const model = {
      identifier: "required-tool-repair-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(history, opts) {
        predictionCount += 1;
        assert.equal(opts.rawTools.force, true);
        assert.deepEqual(
          opts.rawTools.tools.map((tool) => tool.function.name),
          ["unreal_feature_intent_resolve"],
        );
        const repaired = predictionCount === 2;
        if (repaired) {
          assert.equal(history.getMessagesArray().some(
            (message) => message.getRole() === "system"
              && message.getText().includes("[UNREAL_SERVER_REQUIRED_TOOL_REPAIR]")
              && message.getText().includes("read_file")
              && message.getText().includes("unreal_feature_intent_resolve"),
          ), true);
        }
        const name = repaired ? "unreal_feature_intent_resolve" : "read_file";
        const args = repaired
          ? { request: "complete local gomoku placement" }
          : { path: "Git/O-Mock/Source/O_Mock/GomokuBoardActor.h" };
        opts.onToolCallRequestStart(predictionCount, { toolCallId: `repair-${predictionCount}` });
        opts.onToolCallRequestNameReceived(predictionCount, name);
        opts.onToolCallRequestArgumentFragmentGenerated(predictionCount, JSON.stringify(args));
        opts.onToolCallRequestEnd(predictionCount, {
          toolCallRequest: {
            id: `repair-${predictionCount}`,
            type: "function",
            name,
            arguments: args,
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const history = Chat.empty();
    history.append("user", "implement the first missing local-play feature");
    history.append("assistant", JSON.stringify({
      requiredNextTool: "unreal_feature_intent_resolve",
      requiredNextToolArgs: { taskAuthorization: ownership },
    }));
    await generate(controllerFor(model, {}, stateRoot, emitted, [{
      type: "function",
      function: {
        name: "unreal_feature_intent_resolve",
        parameters: {
          type: "object",
          properties: {
            request: { type: "string" },
            taskAuthorization: { type: "object" },
          },
          required: ["request", "taskAuthorization"],
        },
      },
    }]), history);

    assert.equal(predictionCount, 2);
    assert.equal(emitted.filter((event) => event.kind === "failure").length, 0);
    const ends = emitted.filter((event) => event.kind === "end");
    assert.equal(ends.length, 1);
    assert.equal(ends[0].request.name, "unreal_feature_intent_resolve");
    assert.deepEqual(ends[0].request.arguments.taskAuthorization, ownership);
    const checkpoint = activeCheckpoint(stateRoot);
    assert.equal(checkpoint.pendingToolCalls.length, 1);
    assert.equal(checkpoint.pendingToolCalls[0].name, "unreal_feature_intent_resolve");
    assert.equal(
      checkpoint.pendingToolCalls.some((call) => call.name === "read_file"),
      false,
    );
    const sessionDir = fs.readdirSync(stateRoot, { withFileTypes: true })
      .find((entry) => entry.isDirectory());
    const events = fs.readFileSync(path.join(stateRoot, sessionDir.name, "events.jsonl"), "utf8")
      .trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);
    assert.equal(events.some((event) => event.type === "server_required_tool_repair_started"), true);
    assert.equal(events.some((event) => event.type === "server_required_tool_repair_completed"), true);
    assert.equal(events.some((event) => event.type === "tool_call_rejected"), false);
    assert.equal(events.some((event) => (
      event.type === "prediction_completion"
        && event.recoveryKind === "required_tool_serialization"
        && event.architectureFinalRecoveryAttempt === false
    )), true);
  } finally {
    delete process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("required-tool serialization repair fails closed after exactly one retry", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "context-compactor-required-tool-fail-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = stateRoot;
  try {
    const { generate } = require("../dist/generator.js");
    const emitted = [];
    let predictionCount = 0;
    const model = {
      identifier: "required-tool-fail-model",
      async applyPromptTemplate() { return "formatted"; },
      async countTokens(value) { return String(value || "").length; },
      async getContextLength() { return 100_000; },
      respond(_history, opts) {
        predictionCount += 1;
        opts.onToolCallRequestStart(predictionCount, { toolCallId: `wrong-${predictionCount}` });
        opts.onToolCallRequestNameReceived(predictionCount, "read_file");
        opts.onToolCallRequestArgumentFragmentGenerated(predictionCount, '{"path":"Source/Wrong.cpp"}');
        opts.onToolCallRequestEnd(predictionCount, {
          toolCallRequest: {
            id: `wrong-${predictionCount}`,
            type: "function",
            name: "read_file",
            arguments: { path: "Source/Wrong.cpp" },
          },
        });
        return { async result() { return { stats: { stopReason: "toolCalls" } }; } };
      },
    };
    const history = Chat.empty();
    history.append("user", "continue the bounded implementation");
    history.append("assistant", JSON.stringify({
      requiredNextTool: "unreal_feature_intent_resolve",
    }));
    await assert.rejects(
      generate(controllerFor(model, {}, stateRoot, emitted, [{
        type: "function",
        function: { name: "unreal_feature_intent_resolve", parameters: { type: "object" } },
      }]), history),
      /after one bounded repair/,
    );

    assert.equal(predictionCount, 2);
    assert.deepEqual(emitted, []);
    const sessionDir = fs.readdirSync(stateRoot, { withFileTypes: true })
      .find((entry) => entry.isDirectory());
    const events = fs.readFileSync(path.join(stateRoot, sessionDir.name, "events.jsonl"), "utf8")
      .trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);
    assert.equal(events.filter((event) => event.type === "server_required_tool_repair_started").length, 1);
    assert.equal(events.filter((event) => event.type === "server_required_tool_repair_failed").length, 1);
    assert.equal(activeCheckpoint(stateRoot).requiredNextTool.name, "unreal_feature_intent_resolve");
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
    { type: "function", function: {
      name: "read_file",
      parameters: { type: "object", properties: { path: { type: "string" }, sessionId: { type: "string" } } },
    } },
    { type: "function", function: {
      name: "read_file_range",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string" },
          startLine: { type: "integer" },
          endLine: { type: "integer" },
          sessionId: { type: "string" },
        },
      },
    } },
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

  const normalizeInjectedSession = (events) => events.map((event) => {
    if (event.kind === "args" && event.content) {
      const parsed = JSON.parse(event.content);
      if (parsed.sessionId) parsed.sessionId = "<conversation-session>";
      return { ...event, content: JSON.stringify(parsed) };
    }
    if (event.kind === "end" && event.request?.arguments?.sessionId) {
      return {
        ...event,
        request: {
          ...event.request,
          arguments: { ...event.request.arguments, sessionId: "<conversation-session>" },
        },
      };
    }
    return event;
  });
  assert.deepEqual(normalizeInjectedSession(afterLimit.emitted), normalizeInjectedSession(beforeLimit.emitted));
  assert.notEqual(
    afterLimit.emitted.find((event) => event.kind === "end").request.arguments.sessionId,
    beforeLimit.emitted.find((event) => event.kind === "end").request.arguments.sessionId,
    "independent LM Studio conversations must not share evidence-cache scope",
  );
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

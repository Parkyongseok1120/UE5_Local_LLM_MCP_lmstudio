"use strict";

// Recovery-behaviour harness: runs the built prediction loop against one model
// adapter with the real unreal-agent runtime in process, a temporary engine and
// project, and per-scenario fault injection or simulated tools. It records the
// raw call arguments, raw results, each model input and every debug event.
// It never installs anything, edits the repository or contacts a network.

const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const PLUGIN_DIR = path.resolve(__dirname, "..", "..");
const REPO = path.resolve(PLUGIN_DIR, "..");
const pluginRequire = require("node:module").createRequire(path.join(PLUGIN_DIR, "package.json"));
const { Chat, ChatMessage, rawFunctionTool } = pluginRequire("@lmstudio/sdk");
const { createPredictionLoopHandler } = require(path.join(PLUGIN_DIR, "dist", "prediction-loop.js"));
const { createDirectRuntime } = require(path.join(REPO, "lmstudio-unreal-agent-mcp", "src", "direct-server.js"));

const { createRuntime: createUnityRuntime } = require(path.join(REPO, "lmstudio-unity-mcp/src/server.js"));

const sha256 = value => crypto.createHash("sha256").update(value).digest("hex");
const PLUGIN_CONFIG = { contextManagementMode: "hybrid", designGuidanceMode: "auto", showDebugInfo: true };

function buildCsWithTrailingMarker() {
  const modules = Array.from({ length: 40 }, (_, i) => `"RecoveryModule${String(i).padStart(2, "0")}"`).join(", ");
  return "using UnrealBuildTool;\r\npublic class Game : ModuleRules\r\n{\r\n\tpublic Game(ReadOnlyTargetRules Target) : base(Target)\r\n\t{\r\n"
    + `\t\tPublicDependencyModuleNames.AddRange(new string[] { ${modules} });\r\n\t}\r\n}\r\n// RECOVERY_MARKER_BUILD_END\r\n`;
}

/** Temporary engine (with the build tool that marks an engine root), project and workspace. */
function buildFixture(fixture, root) {
  const engineRoot = path.join(root, "Engine install");
  const projectRoot = path.join(root, "Game");
  const workspace = path.join(root, "workspace");
  const write = (base, relative, content) => {
    const target = path.join(base, ...relative.split("/"));
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, content === "BUILD_CS_WITH_TRAILING_MARKER" ? buildCsWithTrailingMarker() : content, "utf8");
  };
  for (const [relative, content] of Object.entries(fixture.engineFiles || {})) write(engineRoot, relative, content);
  write(engineRoot, "Engine/Build/Build.version", JSON.stringify(fixture.engineVersion || {}));
  write(engineRoot, "Engine/Build/BatchFiles/Build.bat", "@echo off\r\n");
  write(engineRoot, "Engine/Build/BatchFiles/Build.sh", "#!/bin/sh\n");
  for (const [relative, content] of Object.entries(fixture.projectFiles)) write(projectRoot, relative, content);
  const uproject = path.join(projectRoot, "Game.uproject");
  fs.writeFileSync(uproject, JSON.stringify({ FileVersion: 3, EngineAssociation: "" }), "utf8");
  fs.mkdirSync(workspace, { recursive: true });
  return { engineRoot, projectRoot, workspace, uproject };
}

function matchesFault(fault, name, args) {
  if (!fault || fault.tool !== name) return false;
  const target = String(args?.path || "");
  if (fault.pathIncludes && !target.replace(/\\/g, "/").includes(fault.pathIncludes)) return false;
  if (fault.queryIncludes && !String(args?.query || "").includes(fault.queryIncludes)) return false;
  if (fault.absolute === true && !path.isAbsolute(target)) return false;
  return true;
}

// The simulated partial projection carries what a real one carries: a notice,
// bodyComplete:false and continuation facts, never the omitted text.
function partialProjection(payload) {
  const body = String(payload.content || "");
  const shown = body.slice(0, 512);
  return { ...payload, content: shown, hasMore: true, truncated: true, nextOffsetBytes: Buffer.byteLength(shown, "utf8"),
    bodyComplete: false, coverage: "partial",
    notice: `PARTIAL BODY: chars 0-${shown.length} of ${body.length}; continue with read_file offsetBytes=${Buffer.byteLength(shown, "utf8")}` };
}

function textContent(payload) {
  return [{ type: "text", text: JSON.stringify(payload) }];
}

function createScenarioTools(scenario, runtime, recorder) {
  const simulated = scenario.simulatedTools || {};
  const tools = runtime.tools.map(definition => ({ ...definition, simulated: false }));
  for (const [name, result] of Object.entries(simulated)) {
    if (!tools.some(tool => tool.name === name)) {
      tools.push({ name, description: `Simulated ${name} for recovery scenario ${scenario.id}.`,
        inputSchema: { type: "object", properties: {}, additionalProperties: true }, simulated: true, result });
    }
  }
  let faultApplied = false;
  return tools.map(definition => {
    const tool = rawFunctionTool({
      name: definition.name,
      description: definition.description || "",
      parametersJsonSchema: definition.inputSchema || { type: "object", properties: {} },
      implementation: async args => {
        const entry = { index: recorder.calls.length, name: definition.name, args, simulated: definition.simulated };
        recorder.calls.push(entry);
        let payload;
        if (definition.simulated) payload = definition.result;
        else payload = (await runtime.callTool(definition.name, args, { sessionId: `recovery-${scenario.id}` })).structuredContent;
        if (!faultApplied && matchesFault(scenario.faultCall, definition.name, args)) {
          faultApplied = true;
          entry.faultCall = true;
          if (scenario.faultCall.replaceFirstResult === "partial_projection" && payload?.ok) payload = partialProjection(payload);
        }
        entry.result = payload;
        return textContent(payload);
      },
    });
    tool.pluginIdentifier = definition.simulated ? "mcp/recovery-simulated" : scenario.engine === "unity" ? "mcp/unity-tools" : "mcp/unreal-agent";
    return tool;
  });
}

function controller(history, tokenSource, tools, timeoutMs) {
  const blocks = [];
  const debugValues = [];
  const signal = AbortSignal.timeout(timeoutMs);
  return {
    abortSignal: signal,
    guardAbort() { if (signal.aborted) throw signal.reason; },
    getPluginConfig() { return { get: key => PLUGIN_CONFIG[key] }; },
    getWorkingDirectory() { return ""; },
    async pullHistory() { return history; },
    async tokenSource() { return tokenSource; },
    async startToolUseSession() { return { tools, [Symbol.dispose]() {} }; },
    async requestConfirmToolCall() { return { type: "allow" }; },
    debug(value) { debugValues.push(value); },
    createStatus() { return { setText() {}, setState() {}, remove() {} }; },
    createToolStatus() { return { setText() {}, setState() {}, remove() {} }; },
    createContentBlock(options) {
      const block = { options: { ...options }, text: "" };
      blocks.push(block);
      return {
        appendText(text) { block.text += text; },
        appendToolRequest() {}, appendToolResult() {},
        setStyle(style) { block.options.style = style; },
      };
    },
    blocks,
    debugValues,
  };
}

/**
 * Runs one scenario. `model` is an LM Studio-like adapter
 * ({ identifier, getContextLength, applyPromptTemplate, countTokens, act }).
 */
async function runScenario(scenario, fixture, model, { timeoutMs = 20 * 60 * 1000 } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), `recovery-${scenario.id}-`));
  try {
    const unity = scenario.engine === "unity";
    const paths = buildFixture(unity ? { projectFiles: scenario.projectFiles || {
      "Assets/Scripts/Player.cs": "class Player { /* UNITY_RECOVERY_SOURCE */ }",
      "Packages/manifest.json": "{}", "ProjectSettings/ProjectVersion.txt": "m_EditorVersion: 6000.3.14f1"
    } } : fixture, root);
    const stateRoot = path.join(root, "state");
    const replies = new Map();
    const bridge = { async call(name, args) {
      const key = name + ":" + (args.action || "");
      const choices = scenario.bridgeReplies?.[key] || [{ status: "observed", connection: "connected", editorVersion: "6000.3.14f1" }];
      const index = replies.get(key) || 0; replies.set(key, index + 1);
      const result = choices[Math.min(index, choices.length - 1)];
      if (result.throwCode) throw Object.assign(new Error(result.throwCode), { code: result.throwCode, nextStep: "Retry after reload", retryAfterSeconds: 1 });
      return { editorSessionId: "fixture-session", domainGeneration: 1, ...result };
    } };
    const unityRuntime = unity ? createUnityRuntime({ UNITY_PROJECT_ROOT: paths.projectRoot, ALLOW_COMMANDS: "1" }, bridge) : null;
    const runtime = unity ? { tools: unityRuntime.tools, close: () => unityRuntime.close(),
      callTool: async (name, args) => ({ structuredContent: await unityRuntime.call(name, args) }) } : createDirectRuntime({
      workspaceRoot: paths.workspace, configPath: path.join(root, "agent-mcp.json"), stateRoot,
      env: { AGENT_STATE_ROOT: stateRoot, ALLOW_WRITE: "0", ALLOW_COMMANDS: "0", ALLOW_UNREAL_BUILD: "0",
        UNREAL_ENGINE_ROOT: paths.engineRoot, SHARED_UNREAL_CONFIG: path.join(root, "shared.json") },
      getActiveProject: () => paths.uproject,
    });
    const recorder = { calls: [], inputs: [] };
    const tools = createScenarioTools(scenario, runtime, recorder);
    const prompt = scenario.prompt.replace("{ENGINE_ROOT_ABS}", paths.engineRoot.replace(/\\/g, "/"));
    const history = Chat.empty();
    // An earlier turn replayed against the same runtime that stopped after its
    // tool results without an answer: the shape an interrupted run leaves.
    const priorCalls = [];
    for (const turn of scenario.priorTurns || []) {
      history.append("user", turn.user);
      for (const [index, call] of turn.calls.entries()) {
        const result = (await runtime.callTool(call.tool, call.args, { sessionId: `recovery-${scenario.id}` })).structuredContent;
        const id = `prior-${priorCalls.length}-${index}`;
        priorCalls.push({ name: call.tool, args: call.args, result });
        history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
          toolCallRequest: { type: "function", id, name: call.tool, arguments: call.args } }] }));
        history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
          content: JSON.stringify(textContent(result)) }] }));
      }
    }
    history.append("user", prompt);
    const recorded = {
      identifier: model.identifier,
      getContextLength: () => model.getContextLength(),
      applyPromptTemplate: (chat, options) => model.applyPromptTemplate(chat, options),
      countTokens: text => model.countTokens(text),
      act: (chat, actTools, options) => {
        const text = Chat.from(chat).toString();
        recorder.inputs.push({ sha256: sha256(text), chars: text.length, text });
        return model.act(chat, actTools, options);
      },
    };
    const ctl = controller(history, recorded, tools, timeoutMs);
    let error = null;
    try { await createPredictionLoopHandler()(ctl); } catch (caught) { error = String(caught?.stack || caught); }
    runtime.close();
    const visible = ctl.blocks.filter(block => block.options?.style?.type !== "thinking");
    return {
      scenario: scenario.id, model: model.identifier || null, error,
      prompt, priorCalls, calls: recorder.calls, inputs: recorder.inputs, debugEvents: ctl.debugValues,
      // Content blocks are separate UI blocks; keep them on separate lines.
      answer: visible.map(block => block.text).filter(Boolean).join("\n").trim(),
    };
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

module.exports = { buildCsWithTrailingMarker, buildFixture, partialProjection, runScenario };

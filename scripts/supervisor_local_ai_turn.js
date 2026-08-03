"use strict";

/**
 * Thin same-session local-AI turn runner for supervisor review loops.
 * Reuses the installed unreal-context-compactor generate() + MCP tools.
 * Does not extend stage_campaign_marathon.js.
 */

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const Module = require("node:module");

const REPO = path.resolve(__dirname, "..");
const PLUGIN_ROOT = path.join(
  os.homedir(),
  ".lmstudio",
  "extensions",
  "plugins",
  "codex",
  "unreal-context-compactor",
);
const priorNodePath = process.env.NODE_PATH || "";
process.env.NODE_PATH = [PLUGIN_ROOT, path.join(PLUGIN_ROOT, "node_modules"), priorNodePath]
  .filter(Boolean)
  .join(path.delimiter);
if (typeof Module._initPaths === "function") Module._initPaths();

const { Chat, LMStudioClient } = require("@lmstudio/sdk");
const { Client } = require(path.join(
  REPO, "lmstudio-unreal-agent-mcp", "node_modules",
  "@modelcontextprotocol", "sdk", "dist", "cjs", "client", "index.js",
));
const { StdioClientTransport } = require(path.join(
  REPO, "lmstudio-unreal-agent-mcp", "node_modules",
  "@modelcontextprotocol", "sdk", "dist", "cjs", "client", "stdio.js",
));

const MCP_JSON = path.join(os.homedir(), ".lmstudio", "mcp.json");
const SESSION_FILE = process.env.LOCAL_AI_SESSION_FILE
  || path.join(REPO, "scripts", "local_ai_stage4_session.json");
const WORKSPACE = process.env.E2E_WORKSPACE
  || process.env.STAGE_CAMPAIGN_PROJECT_ROOT
  || "C:\\Users\\sster\\Documents\\Git\\O-Mock";
const MAX_ROUNDS = Number(process.env.LOCAL_AI_MAX_ROUNDS || 16);
const GENERATE_TIMEOUT_MS = Number(process.env.E2E_GENERATE_TIMEOUT_MS || 300_000);
const MAX_PARALLEL_TOOLS = Number(process.env.E2E_MAX_PARALLEL_TOOLS || 3);

const { trimChatHistory, hasUserMessage } = require("./chat_history_trim");

const ALLOW = new Set([
  "unreal_get_active_project", "get_active_project", "get_workspace_info",
  "list_directory", "read_file", "read_file_range", "search_files",
  "write_file", "replace_in_file",
  "unreal_architecture_reason", "unreal_architecture_reasoning",
]);

const SYSTEM = (
  "You are an Unreal Engine 5.x C++ agent for the active O-Mock project. "
  + "Fix GAME CODE only via MCP. ALWAYS read_file/read_file_range before replace_in_file. "
  + "Use bounded patches (newText <= 60 lines). Do not invent taskSessionId. "
  + "After edits, summarize changed files."
);

function mutationOk(text) {
  const raw = String(text || "");
  if (/"ok"\s*:\s*false/i.test(raw)) return false;
  if (/ROLLED BACK|MUTATION_REPEAT_BLOCKED/i.test(raw)) return false;
  if (/"ok"\s*:\s*true/i.test(raw)) return true;
  return false;
}

function makeController(client, model, config, toolDefinitions, capture) {
  return {
    client,
    abortSignal: new AbortController().signal,
    getPluginConfig() { return { get(key) { return config[key]; } }; },
    getWorkingDirectory() { return WORKSPACE; },
    getToolDefinitions() { return toolDefinitions; },
    fragmentGenerated(content) { capture.fragments.push(String(content || "")); },
    toolCallGenerationStarted() {},
    toolCallGenerationNameReceived() {},
    toolCallGenerationArgumentFragmentGenerated() {},
    toolCallGenerationEnded(request) { capture.requests.push(request); },
    toolCallGenerationFailed(error) { capture.failures.push(error); },
  };
}

async function connectMcp(serverName) {
  const mcp = JSON.parse(fs.readFileSync(MCP_JSON, "utf8"));
  const cfg = mcp.mcpServers?.[serverName];
  if (!cfg) throw new Error(`missing mcp server ${serverName}`);
  const agentStateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "local-ai-agent-state-"));
  const transport = new StdioClientTransport({
    command: cfg.command,
    args: cfg.args || [],
    env: {
      ...process.env,
      ...(cfg.env || {}),
      WORKSPACE_ROOT: WORKSPACE,
      MCP_REQUIRE_PLAN_AUTH: "0",
      ALLOW_WRITE: "1",
      ALLOW_EXISTING_SOURCE_WRITE: "1",
      MCP_EXTENDED_TOOLS: "1",
      MCP_ESSENTIAL_TOOLS: "0",
      AGENT_STATE_ROOT: agentStateRoot,
    },
  });
  const client = new Client({ name: `supervisor-${serverName}`, version: "1.0.0" }, { capabilities: {} });
  await client.connect(transport);
  return {
    async listTools() { return (await client.listTools()).tools || []; },
    async callTool(name, args) {
      const result = await client.callTool({ name, arguments: args || {} });
      return (result?.content || [])
        .map((p) => (typeof p.text === "string" ? p.text : JSON.stringify(p)))
        .join("\n");
    },
    async close() { try { await client.close(); } catch { /* ignore */ } },
  };
}

function loadSession() {
  if (!fs.existsSync(SESSION_FILE)) {
    return { history: [{ role: "system", content: [{ type: "text", text: SYSTEM }] }] };
  }
  try {
    const parsed = JSON.parse(fs.readFileSync(SESSION_FILE, "utf8"));
    if (!Array.isArray(parsed.history) || !parsed.history.length) {
      return { history: [{ role: "system", content: [{ type: "text", text: SYSTEM }] }] };
    }
    return parsed;
  } catch {
    return { history: [{ role: "system", content: [{ type: "text", text: SYSTEM }] }] };
  }
}

function saveSession(session) {
  fs.writeFileSync(SESSION_FILE, `${JSON.stringify(session, null, 2)}\n`, "utf8");
}

async function main() {
  const promptArg = process.argv.slice(2).join(" ").trim();
  const prompt = promptArg || (process.stdin.isTTY ? "" : fs.readFileSync(0, "utf8").trim());
  if (!prompt) {
    console.error("usage: node supervisor_local_ai_turn.js \"<prompt>\"");
    process.exit(2);
  }

  const generatePath = path.join(PLUGIN_ROOT, "dist", "generator.js");
  if (!fs.existsSync(generatePath)) throw new Error(`missing ${generatePath}`);
  process.chdir(PLUGIN_ROOT);
  const { generate } = require(generatePath);

  const isolated = fs.mkdtempSync(path.join(os.tmpdir(), "compactor-local-ai-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = isolated;

  const session = loadSession();
  const agent = await connectMcp("unreal-agent");
  const tools = (await agent.listTools()).filter((t) => ALLOW.has(t.name));
  const toolDefinitions = tools.map((def) => ({
    type: "function",
    function: {
      name: def.name,
      description: def.description || def.name,
      parameters: def.inputSchema || { type: "object", properties: {} },
    },
  }));
  const toolImpls = new Map(tools.map((t) => [t.name, (args) => agent.callTool(t.name, args)]));

  const lm = new LMStudioClient();
  const model = (await lm.llm.listLoaded())[0];
  if (!model) throw new Error("no loaded LM Studio model");
  console.log("model", model.identifier);
  console.log("tools", tools.map((t) => t.name).join(", "));

  const config = {
    enabled: true,
    observeOnly: false,
    strictToolControlPlane: false,
    bufferUntilPredictionComplete: true,
    rejectTruncatedPredictions: true,
    requireCheckpointPersistence: true,
    targetModel: model.identifier,
    softRemainingTokens: 14000,
    hardRemainingTokens: 8000,
    maxOutputReserve: 4096,
    safetyMarginTokens: 1024,
    temperature: 0.2,
    normalToolResultReserve: 3000,
    buildToolResultReserve: 8000,
    recentCompleteTurns: 1,
    minimumTurnsBetweenCompactions: 0,
    targetRemainingTokensAfterCompaction: 24000,
  };

  session.history.push({ role: "user", content: [{ type: "text", text: prompt }] });
  const mutations = [];
  let finalText = "";

  for (let round = 1; round <= MAX_ROUNDS; round += 1) {
    // Naive slice(-N) drops user messages and triggers Qwen Jinja 400:
    // "No user query found in messages." Use user+tool-pair preserving trim.
    const trimmed = trimChatHistory(session.history, { maxMessages: 18, keepTail: 12 });
    if (trimmed.trimmed) {
      session.history = trimmed.history;
      console.log(`[trim] reason=${trimmed.reason} history=${session.history.length} hasUser=${hasUserMessage(session.history)}`);
    }

    console.log(`[round ${round}] generate history=${session.history.length}`);
    const capture = { fragments: [], requests: [], failures: [] };
    const controller = makeController(lm, model, config, toolDefinitions, capture);
    const chat = Chat.from({ messages: session.history });
    try {
      await Promise.race([
        generate(controller, chat),
        new Promise((_, reject) => {
          setTimeout(() => reject(new Error(`GENERATE_TIMEOUT after ${GENERATE_TIMEOUT_MS}ms`)), GENERATE_TIMEOUT_MS);
        }),
      ]);
    } catch (error) {
      console.error("generate error:", String(error?.message || error).slice(0, 300));
      break;
    }

    if (capture.failures.length) {
      console.error("toolgen fail:", String(capture.failures[0]?.message || capture.failures[0]).slice(0, 240));
      break;
    }

    const text = capture.fragments.join("");
    if (text) finalText = text;
    if (!capture.requests.length) {
      if (text.trim()) session.history.push({ role: "assistant", content: [{ type: "text", text }] });
      console.log("done (no tool calls)");
      break;
    }

    const assistantContent = [];
    if (text.trim()) assistantContent.push({ type: "text", text });
    for (const req of capture.requests) {
      assistantContent.push({
        type: "toolCallRequest",
        toolCallRequest: {
          id: req.id || req.toolCallId || `call-${round}-${assistantContent.length}`,
          type: "function",
          name: req.name,
          arguments: req.arguments || {},
        },
      });
    }
    session.history.push({ role: "assistant", content: assistantContent });

    const toolContent = [];
    let parallel = 0;
    for (const req of capture.requests) {
      const name = req.name;
      const args = req.arguments || {};
      let resultText;
      if (parallel >= MAX_PARALLEL_TOOLS) {
        resultText = JSON.stringify({
          ok: false,
          errorCode: "PARALLEL_TOOL_BUDGET_EXCEEDED",
          error: `Only ${MAX_PARALLEL_TOOLS} tool calls per round.`,
        });
      } else if (!toolImpls.has(name)) {
        resultText = JSON.stringify({ ok: false, error: `unknown tool ${name}` });
      } else {
        const started = Date.now();
        resultText = await toolImpls.get(name)(args);
        const okMut = (name === "write_file" || name === "replace_in_file") && mutationOk(resultText);
        console.log(`tool ${name} ${args.path || ""} ${Date.now() - started}ms mutOk=${okMut}`);
        if (okMut) mutations.push({ name, path: args.path || null });
        // #region agent log
        try {
          fs.appendFileSync(
            path.join(REPO, "debug-821b0f.log"),
            `${JSON.stringify({
              sessionId: "821b0f",
              runId: process.env.LOCAL_AI_RUN_ID || "local-ai-turn",
              hypothesisId: "H-A",
              location: "supervisor_local_ai_turn.js:tool",
              message: "mcp_tool_result",
              data: {
                name,
                path: args.path || null,
                mutOk: okMut,
                ms: Date.now() - started,
                snippet: String(resultText).slice(0, 180),
              },
              timestamp: Date.now(),
            })}\n`,
          );
        } catch { /* ignore */ }
        // #endregion
      }
      parallel += 1;
      toolContent.push({
        type: "toolCallResult",
        toolCallId: req.id || req.toolCallId || null,
        content: String(resultText),
      });
    }
    session.history.push({ role: "tool", content: toolContent });
    saveSession(session);
  }

  saveSession(session);
  await agent.close();
  const summary = {
    mutations,
    mutationCount: mutations.length,
    finalPreview: finalText.replace(/\s+/g, " ").slice(0, 500),
    sessionFile: SESSION_FILE,
    historyLen: session.history.length,
  };
  console.log(JSON.stringify(summary, null, 2));
  // #region agent log
  try {
    fs.appendFileSync(
      path.join(REPO, "debug-821b0f.log"),
      `${JSON.stringify({
        sessionId: "821b0f",
        runId: process.env.LOCAL_AI_RUN_ID || "local-ai-turn",
        hypothesisId: "H-A",
        location: "supervisor_local_ai_turn.js:done",
        message: "local_ai_turn_done",
        data: {
          mutationCount: mutations.length,
          paths: mutations.map((m) => m.path),
        },
        timestamp: Date.now(),
      })}\n`,
    );
  } catch { /* ignore */ }
  // #endregion
  process.exit(mutations.length > 0 ? 0 : 3);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});

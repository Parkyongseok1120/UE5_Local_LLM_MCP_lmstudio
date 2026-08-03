"use strict";

/**
 * Long-running staged Unreal C++ campaign marathon (drives local LM via MCP).
 * Drives LM Studio local model via unreal-context-compactor generate() + MCP tools.
 */

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { spawn, spawnSync } = require("node:child_process");
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

// Resolve @lmstudio/sdk from the installed context-compactor plugin.
const priorNodePath = process.env.NODE_PATH || "";
process.env.NODE_PATH = [PLUGIN_ROOT, path.join(PLUGIN_ROOT, "node_modules"), priorNodePath]
  .filter(Boolean)
  .join(path.delimiter);
if (typeof Module._initPaths === "function") {
  Module._initPaths();
}

const { Chat, LMStudioClient } = require("@lmstudio/sdk");
const { Client } = require(path.join(
  REPO,
  "lmstudio-unreal-agent-mcp",
  "node_modules",
  "@modelcontextprotocol",
  "sdk",
  "dist",
  "cjs",
  "client",
  "index.js",
));
const { StdioClientTransport } = require(path.join(
  REPO,
  "lmstudio-unreal-agent-mcp",
  "node_modules",
  "@modelcontextprotocol",
  "sdk",
  "dist",
  "cjs",
  "client",
  "stdio.js",
));
const {
  loadStages,
  verifyStage,
  listExistingSourceFiles,
  resolveProjectRoot,
} = require("./stage_campaign_verify");

const DEBUG_LOG = path.join(REPO, "debug-821b0f.log");
const OUT_LOG = path.join(REPO, "scripts", "stage_campaign_marathon.out.log");
const REPORT_JSON = path.join(REPO, "scripts", "stage_campaign_report.json");
const STATE_JSON = path.join(REPO, "scripts", "stage_campaign_state.json");
const MCP_JSON = path.join(os.homedir(), ".lmstudio", "mcp.json");
const DEBUG_SESSION_ID = "821b0f";
const DEBUG_INGEST = "http://127.0.0.1:7430/ingest/0688ca65-d016-4b7d-bcca-51d06f27568c";
/** When required files are still missing, block write_file outside stage allowlist. */
const SCOPE_GUARD = Number(process.env.STAGE_CAMPAIGN_SCOPE_GUARD ?? 1) !== 0;

function workspaceFromMcpConfig() {
  try {
    const mcp = JSON.parse(fs.readFileSync(MCP_JSON, "utf8"));
    return String(mcp?.mcpServers?.["unreal-agent"]?.env?.WORKSPACE_ROOT || "").trim();
  } catch {
    return "";
  }
}

const WORKSPACE = process.env.E2E_WORKSPACE
  || process.env.STAGE_CAMPAIGN_PROJECT_ROOT
  || workspaceFromMcpConfig()
  || resolveProjectRoot();

const MAX_ROUNDS = Number(process.env.STAGE_CAMPAIGN_MAX_ROUNDS || 24);
/** Shorter loop when static verify already passes (audit/mutation-only turn). */
const AUDIT_MAX_ROUNDS = Number(process.env.STAGE_CAMPAIGN_AUDIT_MAX_ROUNDS || 10);
const REMEDIATE_PASSES = Number(process.env.STAGE_CAMPAIGN_REMEDIATE_PASSES || 2);
const STAGE_ATTEMPTS = Number(process.env.STAGE_CAMPAIGN_STAGE_ATTEMPTS || 5);
const STRICT = Number(process.env.STAGE_CAMPAIGN_STRICT ?? 1) !== 0;
const MAX_HOURS = Number(process.env.STAGE_CAMPAIGN_MAX_HOURS || 16);
const START_STAGE = Math.max(2, Number(process.env.STAGE_CAMPAIGN_START_STAGE || 2));
/** Default 0: never skip a stage even if verify already passes — local LM must drive MCP edits. */
const ALLOW_SKIP = Number(process.env.STAGE_CAMPAIGN_ALLOW_SKIP ?? 0) !== 0;
/** Require at least one write/replace tool call per implement/remediate turn when verify failed. */
const REQUIRE_MUTATION = Number(process.env.STAGE_CAMPAIGN_REQUIRE_MUTATION ?? 1) !== 0;
const GENERATE_TIMEOUT_MS = Number(process.env.E2E_GENERATE_TIMEOUT_MS || 300_000);
const MAX_LIST_DIRECTORY_PER_TURN = Number(process.env.E2E_MAX_LIST_PER_TURN || 6);
const MAX_LIST_PATH_DEPTH = Number(process.env.E2E_MAX_LIST_DEPTH || 4);
const MAX_PARALLEL_TOOLS = Number(process.env.E2E_MAX_PARALLEL_TOOLS || 3);

const ALLOW_TOOL_NAMES = new Set([
  "unreal_get_active_project",
  "get_active_project",
  "get_workspace_info",
  "list_directory",
  "read_file",
  "read_file_range",
  "search_files",
  "write_file",
  "replace_in_file",
  "unreal_architecture_reason",
  "unreal_architecture_reasoning",
  "unreal_build",
  "build",
  "unreal_build_project",
]);

const ESSENTIAL_TOOL_NAMES = new Set([
  "get_active_project",
  "unreal_get_active_project",
  "search_files",
  "read_file",
  "read_file_range",
  "write_file",
  "replace_in_file",
]);

const SYSTEM_PROMPT = (
  "You are an Unreal Engine 5.x C++ implementation agent for the active project. "
  + "YOU must implement missing stage code via MCP write_file/replace_in_file. "
  + "ALWAYS read existing source with read_file or search_files before write_file/replace_in_file. "
  + "Use get_active_project / unreal_get_active_project to confirm the intended project is active. "
  + `list_directory is budget-limited (max ${MAX_LIST_DIRECTORY_PER_TURN}/turn). Prefer search_files + read_file. `
  + "Implement ONLY the current stage scope; do not create later-stage files early. "
  + "When requiredFiles are listed, create those paths first before any other new files. "
  + "Legacy mouse/keyboard input: use InputComponent->BindKey / BindAxis. "
  + "Never call InputComponent->BindAction — that requires UEnhancedInputComponent + ETriggerEvent. "
  + "Do NOT call unreal_agent_plan, unreal_task_checkpoint, or invent taskSessionId/taskAuthorization. "
  + "Call write_file/replace_in_file directly with only path/content (no auth fields). "
  + "If a tool errors, report the exact errorCode/message — do not invent files without tools. "
  + "After edits, briefly summarize changed files."
);

function sessionsRoot() {
  return process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR
    || path.join(os.homedir(), ".lmstudio", "unreal-context-compactor", "sessions");
}

function listSessions() {
  const root = sessionsRoot();
  if (!fs.existsSync(root)) return [];
  return fs.readdirSync(root, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .map((e) => {
      const full = path.join(root, e.name);
      return { name: e.name, full, mtime: fs.statSync(full).mtimeMs };
    })
    .sort((a, b) => b.mtime - a.mtime);
}

function logLine(...args) {
  const line = args.map((a) => (typeof a === "string" ? a : JSON.stringify(a))).join(" ");
  console.log(line);
  try { fs.appendFileSync(OUT_LOG, `${line}\n`, "utf8"); } catch { /* ignore */ }
}

function debugLog(hypothesisId, location, message, data) {
  const payload = {
    sessionId: DEBUG_SESSION_ID,
    runId: "stage-campaign",
    hypothesisId,
    location,
    message,
    data: data || {},
    timestamp: Date.now(),
  };
  try { fs.appendFileSync(DEBUG_LOG, `${JSON.stringify(payload)}\n`, "utf8"); } catch { /* ignore */ }
  // #region agent log
  fetch(DEBUG_INGEST, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Debug-Session-Id": DEBUG_SESSION_ID },
    body: JSON.stringify(payload),
  }).catch(() => {});
  // #endregion
  logLine(`[debug] ${message}`, JSON.stringify(data || {}).slice(0, 700));
}

function normalizeRelPath(rawPath) {
  return String(rawPath || "")
    .replace(/^project:\/\//i, "")
    .replace(/\\/g, "/")
    .replace(/^\.\//, "")
    .replace(/\/+$/, "");
}

function mutationToolSucceeded(resultText) {
  const raw = String(resultText || "");
  if (/"ok"\s*:\s*false/i.test(raw)) return false;
  if (/ROLLED BACK|Static validation failed|MUTATION_REPEAT_BLOCKED/i.test(raw)) return false;
  if (/"ok"\s*:\s*true/i.test(raw)) return true;
  return !/isError"\s*:\s*true/i.test(raw);
}

function stageExactAllowlist(stage) {
  const allow = new Set();
  for (const p of [...(stage.requiredFiles || []), ...(stage.allowedWritePaths || [])]) {
    allow.add(normalizeRelPath(p));
  }
  for (const p of [...allow]) {
    const match = p.match(/^(Source\/[^/]+)\//i);
    if (match) {
      const moduleName = match[1].split("/")[1];
      allow.add(`${match[1]}/${moduleName}.Build.cs`);
    }
  }
  return allow;
}

function stageModuleRoots(stage) {
  const roots = new Set();
  for (const p of stageExactAllowlist(stage)) {
    const match = p.match(/^(Source\/[^/]+)/i);
    if (match) roots.add(match[1]);
  }
  return [...roots];
}

/**
 * Scope guard (project-agnostic): while requiredFiles are missing, only allow
 * write_file to required/allowed paths. replace_in_file may still edit existing
 * Source/<module> files so stages can extend prior classes.
 */
function isStageMutationAllowed(stage, toolName, rawPath, missingRequiredFiles) {
  if (!SCOPE_GUARD) return { ok: true };
  const rel = normalizeRelPath(rawPath);
  if (!rel) return { ok: false, reason: "missing path" };
  const allow = stageExactAllowlist(stage);
  if (allow.has(rel)) return { ok: true };
  const modules = stageModuleRoots(stage);
  const underModule = modules.some((root) => rel === root || rel.startsWith(`${root}/`));
  if (!underModule) {
    return { ok: false, reason: `path outside stage Source module(s): ${rel}` };
  }
  const stillMissing = Array.isArray(missingRequiredFiles) && missingRequiredFiles.length > 0;
  if (stillMissing) {
    if (!allow.has(rel)) {
      return {
        ok: false,
        reason: `out-of-stage mutation blocked until requiredFiles exist: ${rel}`,
        allowed: [...allow].slice(0, 40),
        missingRequiredFiles,
      };
    }
  }
  return { ok: true };
}

function extractValidationPreview(resultText) {
  const raw = String(resultText || "");
  const findings = [];
  try {
    const parsed = JSON.parse(raw);
    const list = parsed?.validation?.findings || parsed?.findings || [];
    for (const f of list.slice(0, 8)) {
      findings.push({
        severity: f.severity,
        code: f.code,
        path: f.path,
        line: f.line,
        message: String(f.message || "").slice(0, 180),
      });
    }
  } catch {
    /* ignore */
  }
  return {
    findings,
    preview: raw.slice(0, 1200),
  };
}

function loadState() {
  if (!fs.existsSync(STATE_JSON)) {
    return {
      currentStage: START_STAGE,
      completedStages: [],
      lastError: null,
      updatedAt: null,
      stageAttempts: {},
      stageResults: {},
    };
  }
  try {
    const parsed = JSON.parse(fs.readFileSync(STATE_JSON, "utf8"));
    return {
      currentStage: Math.max(2, Number(parsed.currentStage || START_STAGE)),
      completedStages: Array.isArray(parsed.completedStages) ? parsed.completedStages : [],
      lastError: parsed.lastError || null,
      updatedAt: parsed.updatedAt || null,
      stageAttempts: parsed.stageAttempts || {},
      stageResults: parsed.stageResults || {},
    };
  } catch {
    return {
      currentStage: START_STAGE,
      completedStages: [],
      lastError: null,
      updatedAt: null,
      stageAttempts: {},
      stageResults: {},
    };
  }
}

function saveState(state) {
  state.updatedAt = new Date().toISOString();
  fs.writeFileSync(STATE_JSON, `${JSON.stringify(state, null, 2)}\n`, "utf8");
}

function loadMcpServers() {
  return JSON.parse(fs.readFileSync(MCP_JSON, "utf8")).mcpServers || {};
}

async function createMcpClient(serverName) {
  const cfg = loadMcpServers()[serverName];
  if (!cfg) throw new Error(`mcp.json missing ${serverName}`);
  // Fresh agent state per marathon run avoids stale conversation-scoped tasks
  // that force TASK_ROUTE_OWNERSHIP_REQUIRED on every tool (including reads).
  if (!process.env.STAGE_CAMPAIGN_AGENT_STATE_ROOT) {
    process.env.STAGE_CAMPAIGN_AGENT_STATE_ROOT = fs.mkdtempSync(
      path.join(os.tmpdir(), "stage-campaign-agent-state-"),
    );
  }
  const env = {
    ...process.env,
    ...(cfg.env || {}),
    MCP_REQUIRE_PLAN_AUTH: process.env.STAGE_CAMPAIGN_REQUIRE_PLAN_AUTH || "0",
    ALLOW_WRITE: "1",
    ALLOW_EXISTING_SOURCE_WRITE: "1",
    MCP_EXTENDED_TOOLS: process.env.MCP_EXTENDED_TOOLS || "1",
    MCP_ESSENTIAL_TOOLS: process.env.MCP_ESSENTIAL_TOOLS || "0",
    WORKSPACE_ROOT: process.env.E2E_WORKSPACE || cfg.env?.WORKSPACE_ROOT || WORKSPACE,
    AGENT_STATE_ROOT: process.env.STAGE_CAMPAIGN_AGENT_STATE_ROOT,
  };
  // #region agent log
  debugLog("H6", "mcp:spawn", "spawning MCP with isolated agent state", {
    serverName,
    requirePlanAuth: env.MCP_REQUIRE_PLAN_AUTH,
    workspace: env.WORKSPACE_ROOT,
    agentStateRoot: env.AGENT_STATE_ROOT,
  });
  // #endregion
  const transport = new StdioClientTransport({
    command: cfg.command,
    args: cfg.args || [],
    env,
  });
  const client = new Client({ name: `stage-campaign-${serverName}`, version: "1.0.0" }, { capabilities: {} });
  await client.connect(transport);
  return {
    name: serverName,
    async listTools() { return (await client.listTools()).tools || []; },
    async callTool(name, args) {
      // Mutation auth/budget is owned by MCP (MCP_REQUIRE_PLAN_AUTH). Do not
      // strip or rewrite args here —that would be client/project-specific.
      const result = await client.callTool({ name, arguments: args || {} });
      const text = serializeToolResult(result);
      if (/TASK_ROUTE_OWNERSHIP_REQUIRED|Unknown task session|Task state disappeared|ownerCapability/i.test(text)) {
        // #region agent log
        debugLog("H8", "mcp:ownership", "ownership/session error", {
          tool: name,
          preview: text.slice(0, 280),
        });
        // #endregion
      }
      if (name === "write_file" || name === "replace_in_file" || name === "delete_file" || name === "apply_edit_bundle") {
        const ok = mutationToolSucceeded(text);
        const detail = ok ? null : extractValidationPreview(text);
        // #region agent log
        debugLog("H2", "mcp:mutation", "mutation tool result", {
          tool: name,
          path: args?.path || args?.file || null,
          ok,
          findings: detail?.findings || [],
          preview: text.slice(0, ok ? 220 : 900),
        });
        // #endregion
      }
      return text;
    },
    async close() { try { await client.close(); } catch { /* ignore */ } },
  };
}

async function createAgentClient() {
  return createMcpClient("unreal-agent");
}

function serializeToolResult(result) {
  const parts = result?.content || [];
  return parts.map((part) => (typeof part.text === "string" ? part.text : JSON.stringify(part))).join("\n");
}

function toolResultIsHardError(text) {
  const raw = String(text || "");
  let parsed = null;
  try { parsed = JSON.parse(raw); } catch {
    const match = raw.match(/\{[\s\S]*\}/);
    if (match) {
      try { parsed = JSON.parse(match[0]); } catch { /* ignore */ }
    }
  }
  if (!parsed || typeof parsed !== "object") return null;
  const code = String(parsed.errorCode || "");
  const stop = parsed.stopCurrentWorkflow === true;
  const softStopCodes = new Set([
    "TASK_ROUTE_MISSING",
    "EVIDENCE_STAGNATION",
    "EVIDENCE_STAGNATION_REPEAT",
    "READ_REPEAT_DETECTED",
    "LIST_DIRECTORY_BUDGET_EXCEEDED",
    "LIST_DIRECTORY_DUPLICATE",
    "PARALLEL_TOOL_BUDGET_EXCEEDED",
  ]);
  if (stop && code && !softStopCodes.has(code)) {
    return { code, error: parsed.error || raw.slice(0, 300) };
  }
  if (code && /PERMISSION|UNAUTHORIZED|WORKSPACE_ESCAPE|FATAL/i.test(code)) {
    return { code, error: parsed.error || raw.slice(0, 300) };
  }
  return null;
}

function toLlmToolDefs(mcpTools) {
  return mcpTools.map((def) => ({
    type: "function",
    function: {
      name: def.name,
      description: def.description || def.name,
      parameters: def.inputSchema || { type: "object", properties: {} },
    },
  }));
}

function listPathDepth(rawPath) {
  const normalized = String(rawPath || ".")
    .replace(/\\/g, "/")
    .replace(/^\.\/+/, "")
    .replace(/\/+$/, "");
  if (!normalized || normalized === ".") return 0;
  return normalized.split("/").filter(Boolean).length;
}

function softToolResult(errorCode, error, extras = {}) {
  return JSON.stringify({
    ok: false,
    errorCode,
    error,
    stopCurrentWorkflow: false,
    retryable: false,
    agentInstruction: extras.agentInstruction
      || "Prefer search_files/read_file and continue implementation.",
    ...extras,
  }, null, 2);
}

function wrapToolImpls(toolImpls, turnState, scopeState = null) {
  const wrapped = new Map();
  for (const [name, impl] of toolImpls.entries()) {
    wrapped.set(name, async (args) => {
      if (name === "list_directory") {
        const listPath = String(args?.path || ".");
        const depth = listPathDepth(listPath);
        const key = listPath.replace(/\\/g, "/").replace(/\/+$/, "") || ".";
        if (depth > MAX_LIST_PATH_DEPTH) {
          return softToolResult(
            "LIST_DIRECTORY_DEPTH_EXCEEDED",
            `list_directory depth ${depth} > ${MAX_LIST_PATH_DEPTH}`,
            { path: key },
          );
        }
        if (turnState.listCount >= MAX_LIST_DIRECTORY_PER_TURN) {
          turnState.listBlocked += 1;
          return softToolResult(
            "LIST_DIRECTORY_BUDGET_EXCEEDED",
            `list_directory budget exceeded (${MAX_LIST_DIRECTORY_PER_TURN}/turn).`,
            { path: key },
          );
        }
        if (turnState.listedPaths.has(key)) {
          turnState.listBlocked += 1;
          return softToolResult(
            "LIST_DIRECTORY_DUPLICATE",
            `list_directory already called for path=${key} this turn.`,
            { path: key },
          );
        }
        turnState.listCount += 1;
        turnState.listedPaths.add(key);
      }
      if (
        scopeState?.stage
        && (name === "write_file" || name === "replace_in_file" || name === "apply_edit_bundle")
      ) {
        const targetPath = args?.path || args?.file || "";
        const gate = isStageMutationAllowed(
          scopeState.stage,
          name,
          targetPath,
          scopeState.missingRequiredFiles || [],
        );
        if (!gate.ok) {
          // #region agent log
          debugLog("H1", "scope:block", "stage scope guard blocked mutation", {
            tool: name,
            path: normalizeRelPath(targetPath),
            reason: gate.reason,
            missingRequiredFiles: gate.missingRequiredFiles || scopeState.missingRequiredFiles,
            allowedSample: (gate.allowed || []).slice(0, 12),
          });
          // #endregion
          return softToolResult(
            "STAGE_SCOPE_WRITE_BLOCKED",
            gate.reason || "write outside current stage allowlist",
            {
              path: normalizeRelPath(targetPath),
              allowedWritePaths: [...stageExactAllowlist(scopeState.stage)].slice(0, 40),
              missingRequiredFiles: scopeState.missingRequiredFiles || [],
              agentInstruction:
                "Create/fix ONLY requiredFiles for this stage first via write_file/replace_in_file. "
                + "Do not create later-stage files yet.",
            },
          );
        }
      }
      return impl(args);
    });
  }
  return wrapped;
}

function toolDefsForAttempt(allDefs, attempt) {
  if (attempt <= 1) return allDefs;
  if (attempt === 2) {
    return allDefs.filter((def) => ESSENTIAL_TOOL_NAMES.has(def.function.name));
  }
  return allDefs.filter((def) => ESSENTIAL_TOOL_NAMES.has(def.function.name));
}

function makeController(client, model, config, toolDefinitions, capture) {
  return {
    client,
    abortSignal: new AbortController().signal,
    getPluginConfig() {
      return { get(key) { return config[key]; } };
    },
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

function buildStagePrompt(stage, verifyResult, existingFiles) {
  const gaps = [];
  if (verifyResult.missingFiles?.length) {
    gaps.push(`missingFiles: ${verifyResult.missingFiles.join(", ")}`);
  }
  if (verifyResult.missingSignatures?.length) {
    gaps.push(`missingSignatures: ${verifyResult.missingSignatures.join(", ")}`);
  }
  if (verifyResult.forbiddenHits?.length) {
    gaps.push(`forbiddenHits: ${verifyResult.forbiddenHits.join(", ")}`);
  }
  const allow = [...stageExactAllowlist(stage)];
  const fileList = existingFiles.slice(0, 80).join("\n");
  return [
    stage.implementationPrompt,
    "",
    "CURRENT GAPS:",
    gaps.length ? gaps.join("\n") : "(none —verify and finish any remaining polish)",
    "",
    "ALLOWED WRITE PATHS FOR THIS STAGE (create these first if missing):",
    allow.join("\n") || "(none listed)",
    "",
    "EXISTING Source files:",
    fileList || "(none found)",
    "",
    `Stage goal: ${stage.goal}`,
    "",
    "RULES: Do not create files outside ALLOWED WRITE PATHS until all missingFiles are gone. "
      + "Prefer write_file for missing requiredFiles, then replace_in_file for wiring existing classes.",
  ].join("\n");
}

function buildRemediationPrompt(stage, verifyResult, extraNotes = []) {
  const buildErrors = loadRecentBuildErrors();
  return [
    stage.verifyPrompt,
    "",
    "VERIFICATION / BUILD FEEDBACK:",
    JSON.stringify({
      missingFiles: verifyResult.missingFiles,
      missingSignatures: verifyResult.missingSignatures,
      forbiddenHits: verifyResult.forbiddenHits,
      notes: verifyResult.notes,
      buildErrors: buildErrors.slice(0, 20),
    }, null, 2),
    "",
    "ALLOWED WRITE PATHS:",
    [...stageExactAllowlist(stage)].join("\n"),
    "",
    ...(extraNotes.length ? ["NOTES:", ...extraNotes, ""] : []),
    ...(buildErrors.length ? [
      "GAME CODE BUILD ERRORS (you must fix via MCP write/replace — supervisor will not edit game code):",
      ...buildErrors.slice(0, 12),
      "",
      "Read the failing file with read_file/read_file_range, then apply a bounded replace_in_file.",
      "Do not invent taskSessionId. Prefer exact oldText from the latest read.",
      "",
    ] : []),
    "Fix each gap with read_file then replace_in_file/write_file. Stay within this stage module.",
    "If static validation rolls back a write, fix the reported findings and retry the same required path.",
  ].join("\n");
}

function loadRecentBuildErrors() {
  const candidates = [
    path.join(REPO, "scripts", "omock_stage4_build.log"),
    path.join(REPO, "scripts", "omock_stage_build.log"),
    process.env.STAGE_CAMPAIGN_BUILD_LOG || "",
  ].filter(Boolean);
  const errors = [];
  for (const file of candidates) {
    if (!fs.existsSync(file)) continue;
    let text = "";
    try { text = fs.readFileSync(file, "utf8"); } catch { continue; }
    for (const line of text.split(/\r?\n/)) {
      if (/error\s+[A-Z]?\d+|Error:|error C\d+/i.test(line) && !/0 error/i.test(line)) {
        errors.push(line.trim().slice(0, 300));
      }
    }
    if (errors.length) break;
  }
  return [...new Set(errors)];
}

function runUnrealEditorBuild() {
  const engineRoot = process.env.UNREAL_ENGINE_ROOT
    || "C:\\Program Files\\Epic Games\\UE_5.8";
  const buildBat = path.join(engineRoot, "Engine", "Build", "BatchFiles", "Build.bat");
  const uprojectCandidates = [
    process.env.STAGE_CAMPAIGN_UPROJECT || "",
    ...((() => {
      try {
        return fs.readdirSync(WORKSPACE)
          .filter((n) => n.endsWith(".uproject"))
          .map((n) => path.join(WORKSPACE, n));
      } catch {
        return [];
      }
    })()),
  ].filter((p) => p && fs.existsSync(p));
  const project = uprojectCandidates[0];
  const outLog = path.join(REPO, "scripts", "omock_stage4_build.log");
  if (!project || !fs.existsSync(buildBat)) {
    return {
      ok: false,
      exitCode: 127,
      errors: [`build tooling missing bat=${buildBat} project=${project || "(none)"}`],
    };
  }
  const moduleName = path.basename(project, ".uproject");
  const args = [
    `${moduleName}Editor`,
    "Win64",
    "Development",
    `-Project=${project}`,
    "-WaitMutex",
    "-FromMsBuild",
  ];
  let exitCode = 1;
  let combined = "";
  try {
    const result = spawnSync(buildBat, args, {
      cwd: WORKSPACE,
      encoding: "utf8",
      env: process.env,
      maxBuffer: 40 * 1024 * 1024,
      shell: true,
    });
    exitCode = Number(result.status ?? 1);
    combined = `${result.stdout || ""}\n${result.stderr || ""}`;
    if ((!combined || combined.trim().length < 8) && result.error) {
      combined += `\nspawn_error: ${result.error.message || result.error}`;
    }
    // Fallback: UnrealBuildTool writes a persistent log even when stdio is thin.
    const ubtLog = path.join(os.homedir(), "AppData", "Local", "UnrealBuildTool", "Log.txt");
    if (fs.existsSync(ubtLog)) {
      try {
        const ubt = fs.readFileSync(ubtLog, "utf8");
        if (ubt && ubt.length > combined.length) {
          combined = `${combined}\n----- UBT Log.txt -----\n${ubt.slice(-200000)}`;
        }
      } catch { /* ignore */ }
    }
    fs.writeFileSync(outLog, combined || `exit=${exitCode} (no output captured)\n`, "utf8");
  } catch (error) {
    combined = String(error && error.stack || error);
    fs.writeFileSync(outLog, combined, "utf8");
    exitCode = 1;
  }
  const errors = [];
  for (const line of String(combined).split(/\r?\n/)) {
    if (/error\s+[A-Z]?\d+|:\s*Error:|error C\d+|OtherCompilationError/i.test(line)
      && !/0 Error\(s\)|0 error/i.test(line)) {
      errors.push(line.trim().slice(0, 300));
    }
  }
  const unique = [...new Set(errors)];
  return { ok: exitCode === 0 && unique.length === 0, exitCode, errors: unique };
}

async function runGenerateTurn({
  generate,
  client,
  model,
  config,
  toolDefinitions,
  toolImpls,
  historyMessages,
  prompt,
  stageId,
  label,
  stats,
  scopeState = null,
  maxRounds = MAX_ROUNDS,
  stopAfterSuccessfulMutation = false,
}) {
  historyMessages.push({ role: "user", content: [{ type: "text", text: prompt }] });
  const turnStarted = Date.now();
  const turnTools = [];
  let finalText = "";
  const turnState = { listCount: 0, listBlocked: 0, listedPaths: new Set() };
  const guardedToolImpls = wrapToolImpls(toolImpls, turnState, scopeState);
  const roundLimit = Math.max(1, Number(maxRounds) || MAX_ROUNDS);

  for (let round = 1; round <= roundLimit; round += 1) {
    const capture = { fragments: [], requests: [], failures: [] };
    debugLog("H1", "round:start", `${label} round`, {
      stageId,
      round,
      historyLen: historyMessages.length,
      roundLimit,
      stopAfterSuccessfulMutation,
    });

    // Keep per-stage history bounded so hard_compact cannot balloon until process death.
    // Must preserve latest user query — Qwen Jinja raises 400 if no user remains.
    if (historyMessages.length > 18) {
      const { trimChatHistory, hasUserMessage } = require("./chat_history_trim");
      const trimmed = trimChatHistory(historyMessages, { maxMessages: 18, keepTail: 12 });
      historyMessages.length = 0;
      historyMessages.push(...trimmed.history);
      // #region agent log
      debugLog("H5", "round:trim-history", "user-preserving history trim", {
        stageId,
        round,
        historyLen: historyMessages.length,
        trimmed: trimmed.trimmed,
        reason: trimmed.reason,
        hasUser: hasUserMessage(historyMessages),
      });
      // #endregion
    }

    const genStarted = Date.now();
    let heartbeat = null;
    let timeoutHandle = null;
    const maxGenerateAttempts = 3;
    try {
      let lastError = null;
      for (let attempt = 1; attempt <= maxGenerateAttempts; attempt += 1) {
        capture.fragments.length = 0;
        capture.requests.length = 0;
        capture.failures.length = 0;
        const attemptTools = toolDefsForAttempt(toolDefinitions, attempt);
        const attemptConfig = {
          ...config,
          temperature: attempt === 1 ? config.temperature : Math.min(Number(config.temperature || 0.2), 0.05),
        };
        const controller = makeController(client, model, attemptConfig, attemptTools, capture);
        const chat = Chat.from({ messages: historyMessages });
        heartbeat = setInterval(() => {
          debugLog("H1", "generate:heartbeat", "awaiting generate", {
            stageId,
            round,
            waitedMs: Date.now() - genStarted,
          });
        }, 30_000);
        try {
          await Promise.race([
            generate(controller, chat),
            new Promise((_, reject) => {
              timeoutHandle = setTimeout(() => {
                reject(new Error(`GENERATE_TIMEOUT after ${GENERATE_TIMEOUT_MS}ms`));
              }, GENERATE_TIMEOUT_MS);
              if (typeof timeoutHandle.unref === "function") timeoutHandle.unref();
            }),
          ]);
          lastError = null;
          break;
        } catch (error) {
          lastError = error;
          const errText = String(error?.message || error);
          const transient = /peg-native format|server_error|predict stream returned an error|ECONNRESET|socket hang up/i.test(errText);
          if (!transient || attempt >= maxGenerateAttempts) throw error;
        } finally {
          if (timeoutHandle) clearTimeout(timeoutHandle);
          timeoutHandle = null;
        }
      }
      if (lastError) throw lastError;
    } catch (error) {
      clearInterval(heartbeat);
      // Soft-fail generate: return whatever mutations we already collected instead of
      // killing the whole marathon process mid-hard_compact.
      // #region agent log
      debugLog("H5", "generate:soft-fail", "generate failed; ending turn with partial progress", {
        stageId,
        label,
        round,
        error: String(error?.message || error).slice(0, 300),
        successfulSoFar: turnTools.filter((t) => (
          ["write_file", "replace_in_file", "apply_edit_bundle"].includes(t.name) && t.ok
        )).length,
      });
      // #endregion
      break;
    } finally {
      clearInterval(heartbeat);
    }

    if (capture.failures.length) {
      debugLog("H5", "generate:toolgen-fail", "tool generation failed; ending turn", {
        stageId,
        label,
        error: String(capture.failures[0]?.message || capture.failures[0]).slice(0, 240),
      });
      break;
    }

    const text = capture.fragments.join("");
    if (text) finalText = text;

    if (!capture.requests.length) {
      if (text.trim()) {
        historyMessages.push({ role: "assistant", content: [{ type: "text", text }] });
      }
      break;
    }

    const assistantContent = [];
    if (text.trim()) assistantContent.push({ type: "text", text });
    for (const req of capture.requests) {
      assistantContent.push({
        type: "toolCallRequest",
        toolCallRequest: {
          id: req.id || req.toolCallId || `call-${stageId}-${round}-${assistantContent.length}`,
          type: "function",
          name: req.name,
          arguments: req.arguments || {},
        },
      });
    }
    historyMessages.push({ role: "assistant", content: assistantContent });

    const toolContent = [];
    let parallelIndex = 0;
    for (const req of capture.requests) {
      const name = req.name;
      const args = req.arguments || {};
      const impl = guardedToolImpls.get(name);
      if (!impl) {
        throw new Error(`STAGE_${stageId}_UNKNOWN_TOOL: ${name}`);
      }
      const started = Date.now();
      let resultText;
      if (parallelIndex >= MAX_PARALLEL_TOOLS) {
        resultText = softToolResult(
          "PARALLEL_TOOL_BUDGET_EXCEEDED",
          `Only ${MAX_PARALLEL_TOOLS} tool calls per round.`,
          { tool: name },
        );
      } else {
        resultText = await impl(args);
      }
      if (
        (name === "write_file" || name === "replace_in_file")
        && /DIRECT_BIND_ACTION/i.test(String(resultText || ""))
      ) {
        resultText = `${String(resultText)}\n\nAGENT_HINT: Use InputComponent->BindKey(EKeys::LeftMouseButton, IE_Pressed, this, &Class::Handler) `
          + "for click handling. Do NOT use InputComponent->BindAction.";
      }
      parallelIndex += 1;
      const hard = toolResultIsHardError(resultText);
      const mutationNames = new Set(["write_file", "replace_in_file", "apply_edit_bundle", "delete_file"]);
      const isMutation = mutationNames.has(name);
      const mutationOk = isMutation ? mutationToolSucceeded(resultText) : null;
      turnTools.push({
        name,
        args,
        ms: Date.now() - started,
        hardError: hard,
        ok: isMutation ? mutationOk : !hard,
        path: args?.path || args?.file || null,
      });
      stats.calls.push({
        stageId,
        name,
        ms: Date.now() - started,
        ok: isMutation ? mutationOk : !hard,
        path: args?.path || args?.file || null,
      });
      if (isMutation && mutationOk === false) {
        const detail = extractValidationPreview(resultText);
        const preview = String(resultText || "");
        const boundedPatch = /BOUNDED_PATCH_REQUIRED|patch is too large/i.test(preview);
        // #region agent log
        debugLog(boundedPatch ? "H1" : "H3", "mcp:mutation-fail", "mutation failed with details", {
          stageId,
          tool: name,
          path: args?.path || args?.file || null,
          boundedPatch,
          findings: detail.findings,
          preview: detail.preview.slice(0, 800),
          oldTextChars: String(args?.oldText || "").length,
          newTextChars: String(args?.newText || "").length,
          newTextLines: String(args?.newText || "").split(/\r?\n/).length,
        });
        // #endregion
      }
      if (!isMutation && /EVIDENCE_STAGNATION|Evidence read stagnating/i.test(String(resultText || ""))) {
        // #region agent log
        debugLog("H2", "mcp:evidence-stagnation", "evidence stagnation during stage turn", {
          stageId,
          tool: name,
          path: args?.path || args?.file || null,
          preview: String(resultText).slice(0, 400),
          priorMutationFails: turnTools.filter((t) => (
            ["write_file", "replace_in_file"].includes(t.name) && t.ok === false
          )).length,
        });
        // #endregion
      }
      debugLog("E2E", "tool", "tool call", {
        stageId,
        name,
        ms: Date.now() - started,
        hardError: hard,
        ok: isMutation ? mutationOk : !hard,
        preview: String(resultText).slice(0, isMutation && mutationOk === false ? 500 : 180),
      });
      if (hard) {
        throw new Error(`STAGE_${stageId}_TOOL_HARD_ERROR ${name} ${hard.code}: ${hard.error}`);
      }
      toolContent.push({
        type: "toolCallResult",
        toolCallId: req.id || req.toolCallId || null,
        content: String(resultText),
      });
    }
    historyMessages.push({ role: "tool", content: toolContent });

    const successCount = turnTools.filter((t) => (
      ["write_file", "replace_in_file", "apply_edit_bundle"].includes(t.name) && t.ok
    )).length;
    if (stopAfterSuccessfulMutation && successCount > 0) {
      // #region agent log
      debugLog("H5", "round:early-stop", "stopping audit turn after successful mutation", {
        stageId,
        label,
        round,
        successCount,
      });
      // #endregion
      break;
    }
  }

  const successfulMutations = turnTools.filter((t) => (
    ["write_file", "replace_in_file", "apply_edit_bundle"].includes(t.name) && t.ok
  ));
  return {
    stageId,
    label,
    ms: Date.now() - turnStarted,
    chars: finalText.length,
    preview: finalText.replace(/\s+/g, " ").slice(0, 280),
    toolCalls: turnTools,
    listDirectory: turnState.listCount,
    successfulMutationCount: successfulMutations.length,
    successfulMutationPaths: successfulMutations.map((t) => normalizeRelPath(t.path)).filter(Boolean),
  };
}

async function processStage({
  stage,
  generate,
  client,
  model,
  config,
  toolDefinitions,
  toolImpls,
  historyMessages,
  state,
  stats,
  attemptNum,
}) {
  const stageId = stage.id;
  logLine(`\n===== STAGE ${stageId}: ${stage.name} (attempt ${attemptNum}) =====`);

  // Fresh chat per stage/attempt — accumulated history forces hard_compact and stalls.
  historyMessages.length = 0;
  historyMessages.push({ role: "system", content: [{ type: "text", text: SYSTEM_PROMPT }] });
  // #region agent log
  debugLog("H5", "stage:history-reset", "reset chat history for stage attempt", {
    stageId,
    attemptNum,
    historyLen: historyMessages.length,
  });
  // #endregion

  let verifyResult = verifyStage(stageId, { projectRoot: WORKSPACE });
  const preVerifyOk = verifyResult.ok;
  // #region agent log
  debugLog("H1", "stage:pre", "pre-verify", {
    stageId,
    allowSkip: ALLOW_SKIP,
    requireMutation: REQUIRE_MUTATION,
    scopeGuard: SCOPE_GUARD,
    ok: verifyResult.ok,
    missingFiles: verifyResult.missingFiles,
    missingSignatures: (verifyResult.missingSignatures || []).slice(0, 12),
  });
  // #endregion

  if (verifyResult.ok && ALLOW_SKIP) {
    // #region agent log
    debugLog("H4", "stage:skip", "skipping stage because verify ok + ALLOW_SKIP", { stageId });
    // #endregion
    logLine(`stage ${stageId} already verified —skip (STAGE_CAMPAIGN_ALLOW_SKIP=1)`);
    if (!state.completedStages.includes(stageId)) {
      state.completedStages.push(stageId);
    }
    state.stageResults[stageId] = { ok: true, skipped: true, verifyResult };
    state.currentStage = Math.min(13, stageId + 1);
    saveState(state);
    return { ok: true, skipped: true };
  }

  if (verifyResult.ok && !ALLOW_SKIP) {
    logLine(`stage ${stageId} verify ok but STAGE_CAMPAIGN_ALLOW_SKIP=0 —forcing local LM audit/fix turn`);
  }

  const existingFiles = listExistingSourceFiles(WORKSPACE);
  const scopeState = {
    stage,
    missingRequiredFiles: [...(verifyResult.missingFiles || [])],
  };
  const implPrompt = verifyResult.ok
    ? (
      `Stage ${stageId} (${stage.name}) static verify already passes, but you MUST still use MCP. `
      + "1) get_active_project 2) search_files/read_file under Source/ 3) unreal_architecture_reasoning "
      + "4) apply at least one replace_in_file or write_file improvement for this stage goal. "
      + `Goal: ${stage.goal}\n`
      + (stage.implementationPrompt || "")
      + "\nALLOWED WRITE PATHS:\n"
      + [...stageExactAllowlist(stage)].join("\n")
    )
    : buildStagePrompt(stage, verifyResult, existingFiles);
  logLine("implementation prompt chars", implPrompt.length);
  // #region agent log
  debugLog("H2", "stage:prompt", "local LM implement prompt ready", {
    stageId,
    promptChars: implPrompt.length,
    verifyOk: verifyResult.ok,
    fileCount: existingFiles.length,
    allowCount: stageExactAllowlist(stage).size,
  });
  // #endregion

  let successfulMutationCount = 0;
  const successfulMutationPaths = [];
  const toolsBefore = stats.calls.length;
  const implementTurn = await runGenerateTurn({
    generate,
    client,
    model,
    config,
    toolDefinitions,
    toolImpls,
    historyMessages,
    prompt: implPrompt,
    stageId,
    label: "implement",
    stats,
    scopeState,
    maxRounds: preVerifyOk ? AUDIT_MAX_ROUNDS : MAX_ROUNDS,
    stopAfterSuccessfulMutation: preVerifyOk,
  });
  successfulMutationCount += implementTurn.successfulMutationCount || 0;
  successfulMutationPaths.push(...(implementTurn.successfulMutationPaths || []));
  const implementCalls = stats.calls.slice(toolsBefore);
  // #region agent log
  debugLog("H3", "stage:tools", "post-implement tool usage", {
    stageId,
    toolCount: implementCalls.length,
    mutationCount: implementCalls.filter((c) => (
      c.name === "write_file" || c.name === "replace_in_file" || c.name === "apply_edit_bundle"
    )).length,
    successfulMutationCount,
    successfulMutationPaths: successfulMutationPaths.slice(0, 20),
    toolNames: implementCalls.map((c) => c.name),
  });
  // #endregion

  verifyResult = verifyStage(stageId, { projectRoot: WORKSPACE });
  scopeState.missingRequiredFiles = [...(verifyResult.missingFiles || [])];
  debugLog("VERIFY", "stage:post", "post-implement verify", { stageId, verifyResult });

  let remediatePass = 0;
  while (
    (!verifyResult.ok || (REQUIRE_MUTATION && successfulMutationCount === 0))
    && remediatePass < REMEDIATE_PASSES
  ) {
    remediatePass += 1;
    logLine(`stage ${stageId} remediation pass ${remediatePass}/${REMEDIATE_PASSES}`);
    const extraNotes = [];
    if (REQUIRE_MUTATION && successfulMutationCount === 0) {
      extraNotes.push("No successful write_file/replace_in_file yet — you MUST mutate at least one allowed path.");
    }
    if (verifyResult.missingFiles?.length) {
      extraNotes.push(`Create these required files now: ${verifyResult.missingFiles.join(", ")}`);
    }
    const buildErrors = loadRecentBuildErrors();
    if (buildErrors.length) {
      extraNotes.push("Unreal build failed — treat as GAME_CODE_FAILED and fix via MCP (do not ask supervisor to edit).");
      extraNotes.push(...buildErrors.slice(0, 8));
    }
    const remediatePrompt = buildRemediationPrompt(stage, verifyResult, extraNotes);
    const before = stats.calls.length;
    const remTurn = await runGenerateTurn({
      generate,
      client,
      model,
      config,
      toolDefinitions,
      toolImpls,
      historyMessages,
      prompt: remediatePrompt,
      stageId,
      label: `remediate-${remediatePass}`,
      stats,
      scopeState,
      maxRounds: AUDIT_MAX_ROUNDS,
      stopAfterSuccessfulMutation: preVerifyOk,
    });
    successfulMutationCount += remTurn.successfulMutationCount || 0;
    successfulMutationPaths.push(...(remTurn.successfulMutationPaths || []));
    const remCalls = stats.calls.slice(before);
    // #region agent log
    debugLog("H3", "stage:remediate-tools", "remediation tool usage", {
      stageId,
      remediatePass,
      toolNames: remCalls.map((c) => c.name),
      successfulMutationCount,
      successfulMutationPaths: successfulMutationPaths.slice(0, 20),
    });
    // #endregion
    verifyResult = verifyStage(stageId, { projectRoot: WORKSPACE });
    scopeState.missingRequiredFiles = [...(verifyResult.missingFiles || [])];
    debugLog("VERIFY", "stage:remediate", "post-remediate verify", { stageId, remediatePass, verifyResult });
  }

  const mutationSatisfied = !REQUIRE_MUTATION || successfulMutationCount > 0;
  let ok = Boolean(verifyResult.ok && mutationSatisfied);
  let buildResult = null;

  if (ok && Number(process.env.STAGE_CAMPAIGN_RUN_BUILD ?? 1) !== 0) {
    buildResult = runUnrealEditorBuild();
    // #region agent log
    debugLog("H7", "stage:build", "unreal build after stage mutations", {
      stageId,
      ok: buildResult.ok,
      exitCode: buildResult.exitCode,
      firstError: (buildResult.errors || [])[0] || null,
      errorCount: (buildResult.errors || []).length,
    });
    // #endregion
    logLine(`stage ${stageId} build`, buildResult.ok ? "PASS" : "FAIL", (buildResult.errors || [])[0] || "");
    if (!buildResult.ok) {
      ok = false;
      const buildNotes = [
        "GAME_CODE_FAILED: Unreal build failed. Fix via MCP only — do not ask supervisor to edit C++.",
        ...(buildResult.errors || []).slice(0, 10),
      ];
      const buildPrompt = buildRemediationPrompt(stage, verifyResult, buildNotes);
      const before = stats.calls.length;
      const buildFixTurn = await runGenerateTurn({
        generate,
        client,
        model,
        config,
        toolDefinitions,
        toolImpls,
        historyMessages,
        prompt: buildPrompt,
        stageId,
        label: "build-fix",
        stats,
        scopeState,
        maxRounds: Math.max(AUDIT_MAX_ROUNDS, 12),
        stopAfterSuccessfulMutation: false,
      });
      successfulMutationCount += buildFixTurn.successfulMutationCount || 0;
      successfulMutationPaths.push(...(buildFixTurn.successfulMutationPaths || []));
      debugLog("H7", "stage:build-fix-tools", "build-fix tool usage", {
        stageId,
        toolNames: stats.calls.slice(before).map((c) => c.name),
        successfulMutationCount,
      });
      buildResult = runUnrealEditorBuild();
      ok = Boolean(verifyResult.ok && (!REQUIRE_MUTATION || successfulMutationCount > 0) && buildResult.ok);
      logLine(`stage ${stageId} rebuild`, buildResult.ok ? "PASS" : "FAIL", (buildResult.errors || [])[0] || "");
    }
  }

  if (!ok) {
    // #region agent log
    debugLog("H4", "stage:fail", "stage still failing after local LM turns", {
      stageId,
      verifyOk: verifyResult.ok,
      preVerifyOk,
      successfulMutationCount,
      buildOk: buildResult ? buildResult.ok : null,
      missingFiles: verifyResult.missingFiles,
      missingSignatures: verifyResult.missingSignatures,
    });
    // #endregion
  }
  state.stageResults[stageId] = {
    ok,
    attemptNum,
    remediatePasses: remediatePass,
    verifyResult,
    successfulMutationCount,
    successfulMutationPaths: [...new Set(successfulMutationPaths)],
    build: buildResult
      ? { ok: buildResult.ok, exitCode: buildResult.exitCode, errors: (buildResult.errors || []).slice(0, 8) }
      : null,
    at: new Date().toISOString(),
  };

  if (ok) {
    if (!state.completedStages.includes(stageId)) {
      state.completedStages.push(stageId);
    }
    state.currentStage = Math.min(13, stageId + 1);
    state.lastError = null;
    logLine(`stage ${stageId} PASS`, JSON.stringify({
      successfulMutationCount,
      paths: [...new Set(successfulMutationPaths)].slice(0, 12),
    }));
  } else {
    const failNote = `STAGE_${stageId}_VERIFY_FAIL:${[
      ...(verifyResult.missingFiles || []),
      ...(verifyResult.missingSignatures || []),
      ...(mutationSatisfied ? [] : ["NO_SUCCESSFUL_MUTATION"]),
    ].join(",")}`;
    state.lastError = failNote;
    logLine(`stage ${stageId} FAIL`, failNote);
    if (!STRICT) {
      state.currentStage = Math.min(13, stageId + 1);
    }
  }

  saveState(state);
  return { ok, verifyResult, hardFail: ok ? null : state.lastError, successfulMutationCount };
}

async function main() {
  try { fs.writeFileSync(OUT_LOG, "", "utf8"); } catch { /* ignore */ }

  debugLog("H2", "main:boot", "stage campaign marathon boot", {
    pid: process.pid,
    workspace: WORKSPACE,
    maxRounds: MAX_ROUNDS,
    strict: STRICT,
    startStage: START_STAGE,
    maxHours: MAX_HOURS,
    allowSkip: ALLOW_SKIP,
    requireMutation: REQUIRE_MUTATION,
    role: "local-ai-implements-cursor-fixes-mcp-only",
    generateTimeoutMs: GENERATE_TIMEOUT_MS,
  });

  const isolatedState = fs.mkdtempSync(path.join(os.tmpdir(), "compactor-stage-campaign-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = isolatedState;
  process.env.LMS_CONTEXT_COMPACTOR_DEBUG_LOG = DEBUG_LOG;
  logLine("isolatedState", isolatedState);
  logLine("workspace", WORKSPACE);

  const generatePath = path.join(PLUGIN_ROOT, "dist", "generator.js");
  if (!fs.existsSync(generatePath)) {
    throw new Error(`Installed plugin generator missing: ${generatePath}`);
  }
  const manifest = JSON.parse(fs.readFileSync(path.join(PLUGIN_ROOT, "manifest.json"), "utf8"));
  logLine("plugin", `${manifest.owner}/${manifest.name}`, "rev", manifest.revision);

  process.chdir(PLUGIN_ROOT);
  const { generate } = require(generatePath);

  const stats = { calls: [] };
  const state = loadState();
  const stages = loadStages();
  const stageById = new Map(stages.map((s) => [Number(s.id), s]));

  logLine("connecting MCP...");
  const agent = await createAgentClient();
  const clients = [agent];
  let rag = null;
  try {
    rag = await createMcpClient("unreal-rag");
    clients.push(rag);
    logLine("connected unreal-rag");
  } catch (error) {
    logLine("unreal-rag connect failed:", error?.message || error);
  }

  const mcpTools = [];
  const toolImpls = new Map();
  for (const client of clients) {
    for (const def of await client.listTools()) {
      if (!ALLOW_TOOL_NAMES.has(def.name)) continue;
      if (toolImpls.has(def.name)) continue;
      mcpTools.push(def);
      toolImpls.set(def.name, (args) => client.callTool(def.name, args));
    }
  }
  const toolDefinitions = toLlmToolDefs(mcpTools);
  debugLog("E2E", "main", "tools ready", {
    count: toolDefinitions.length,
    names: toolDefinitions.map((t) => t.function.name),
  });

  // MCP integration smoke (active project + Source read/search).
  async function callToolSafe(name, args) {
    const fn = toolImpls.get(name);
    if (!fn) {
      return { ok: false, error: `missing tool ${name}` };
    }
    try {
      const result = await fn(args || {});
      const text = String(result);
      const looksError = /"ok"\s*:\s*false|"errorCode"\s*:/i.test(text)
        && !/"ok"\s*:\s*true/i.test(text);
      if (looksError) {
        return { ok: false, error: text.slice(0, 300), preview: text.slice(0, 400) };
      }
      return { ok: true, preview: text.slice(0, 400) };
    } catch (error) {
      return { ok: false, error: error?.message || String(error) };
    }
  }

  const sourceModule = (() => {
    try {
      const { detectSourceModule } = require("./stage_campaign_verify");
      return detectSourceModule(WORKSPACE);
    } catch {
      return "";
    }
  })();
  const smokeProbeRel = sourceModule
    ? `Source/${sourceModule}`
    : "Source";
  const smoke = {
    get_active_project: await callToolSafe(
      toolImpls.has("unreal_get_active_project") ? "unreal_get_active_project" : "get_active_project",
      {},
    ),
    search_files: await callToolSafe("search_files", { query: ".h", path: smokeProbeRel }),
    list_directory: await callToolSafe("list_directory", { path: smokeProbeRel }),
    architecture: await callToolSafe(
      toolImpls.has("unreal_architecture_reasoning")
        ? "unreal_architecture_reasoning"
        : "unreal_architecture_reason",
      { query: `Summarize primary gameplay classes under ${smokeProbeRel}` },
    ),
  };
  const activePreview = String(smoke.get_active_project.preview || "").toLowerCase();
  const workspaceLeaf = path.basename(WORKSPACE).toLowerCase();
  const smokeOk = Boolean(
    smoke.get_active_project.ok
    && (smoke.search_files.ok || smoke.list_directory.ok)
    && (activePreview.includes(workspaceLeaf) || activePreview.includes("activeproject") || activePreview.includes(".uproject")),
  );
  debugLog("H1", "mcp:smoke", "MCP smoke", { smokeOk, workspace: WORKSPACE, sourceModule, smoke });
  logLine("mcp smoke", smokeOk ? "PASS" : "FAIL", JSON.stringify({
    activeOk: smoke.get_active_project.ok,
    searchOk: smoke.search_files.ok,
    listOk: smoke.list_directory.ok,
    archOk: smoke.architecture.ok,
  }));
  if (!smokeOk) {
    logLine("WARNING: MCP smoke did not confirm active project — continuing stage verify");
  }
  if (!smoke.architecture.ok) {
    logLine("WARNING: architecture tool smoke failed:", smoke.architecture.error || "missing");
  }

  const lmClient = new LMStudioClient();
  const loaded = await lmClient.llm.listLoaded();
  if (!loaded.length) throw new Error("No model loaded in LM Studio");
  const model = loaded[0];
  logLine("loaded model", model.identifier);

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

  const historyMessages = [
    { role: "system", content: [{ type: "text", text: SYSTEM_PROMPT }] },
  ];

  const startMs = Date.now();
  const maxMs = MAX_HOURS * 3600 * 1000;
  let stage13Attempted = false;
  const stageReport = [];

  while (Date.now() - startMs < maxMs) {
    let allOk = true;
    const loopStartStage = Math.max(START_STAGE, state.currentStage);

    for (let stageId = loopStartStage; stageId <= 13; stageId += 1) {
      if (stageId === 13) stage13Attempted = true;

      const stage = stageById.get(stageId);
      if (!stage) {
        logLine(`missing stage definition for ${stageId}`);
        continue;
      }

      if (ALLOW_SKIP && state.completedStages.includes(stageId)) {
        const quick = verifyStage(stageId, { projectRoot: WORKSPACE });
        if (quick.ok) {
          // #region agent log
          debugLog("H4", "stage:skip-completed", "ALLOW_SKIP continue past completed stage", { stageId });
          // #endregion
          continue;
        }
        state.completedStages = state.completedStages.filter((id) => id !== stageId);
        logLine(`stage ${stageId} regression detected —re-running`);
      } else if (!ALLOW_SKIP && state.completedStages.includes(stageId)) {
        // Skip 금지: prior completed marker must not bypass local LM.
        state.completedStages = state.completedStages.filter((id) => id !== stageId);
        // #region agent log
        debugLog("H4", "stage:force-reaudit", "cleared completed marker because ALLOW_SKIP=0", { stageId });
        // #endregion
        logLine(`stage ${stageId} was marked complete but ALLOW_SKIP=0 —forcing local LM re-run`);
      }

      const prevAttempts = Number(state.stageAttempts[stageId] || 0);
      let attempt = prevAttempts + 1;
      let stageOk = false;

      while (attempt <= STAGE_ATTEMPTS) {
        state.stageAttempts[stageId] = attempt;
        saveState(state);

        const result = await processStage({
          stage,
          generate,
          client: lmClient,
          model,
          config,
          toolDefinitions,
          toolImpls,
          historyMessages,
          state,
          stats,
          attemptNum: attempt,
        });

        stageReport.push({
          stageId,
          attempt,
          ok: result.ok,
          skipped: result.skipped || false,
          hardFail: result.hardFail,
          at: new Date().toISOString(),
        });

        if (result.ok) {
          stageOk = true;
          break;
        }

        if (!STRICT) {
          logLine(`STAGE_CAMPAIGN_STRICT=0 —continuing to next stage after failure`);
          break;
        }

        if (attempt >= STAGE_ATTEMPTS) {
          logLine(`stage ${stageId} exhausted ${STAGE_ATTEMPTS} attempts —moving on with hardFail`);
          state.currentStage = Math.min(13, stageId + 1);
          saveState(state);
          break;
        }

        attempt += 1;
        logLine(`retrying stage ${stageId}, attempt ${attempt}/${STAGE_ATTEMPTS}`);
      }

      if (!stageOk) allOk = false;
    }

    if (allOk && state.completedStages.length >= 12) {
      logLine("all stages 2..13 verified");
      break;
    }

    const incomplete = stages
      .map((s) => Number(s.id))
      .filter((id) => !state.completedStages.includes(id));
    logLine("loop complete —incomplete stages:", incomplete.join(", ") || "none");
    const incompleteFrom2 = incomplete.filter((id) => id >= 2);
    if (incompleteFrom2.length) {
      state.currentStage = Math.min(...incompleteFrom2);
    }
    saveState(state);

    if (Date.now() - startMs >= maxMs) break;
    logLine("continuing remediation loop...");
  }

  if (!stage13Attempted) {
    logLine("forcing stage 13 attempt before exit");
    const stage13 = stageById.get(13);
    if (stage13) {
      await processStage({
        stage: stage13,
        generate,
        client: lmClient,
        model,
        config,
        toolDefinitions,
        toolImpls,
        historyMessages,
        state,
        stats,
        attemptNum: Number(state.stageAttempts[13] || 0) + 1,
      });
      stage13Attempted = true;
    }
  }

  const finalStages = stages.map((s) => {
    const id = Number(s.id);
    const result = state.stageResults?.[id] || null;
    return {
      id,
      name: s.name,
      verify: verifyStage(id, { projectRoot: WORKSPACE }),
      completed: state.completedStages.includes(id),
      stageResult: result
        ? {
          ok: Boolean(result.ok),
          skipped: Boolean(result.skipped),
          successfulMutationCount: Number(result.successfulMutationCount || 0),
          attemptNum: Number(result.attemptNum || 0),
        }
        : null,
    };
  });

  const toolCallTotal = stats.calls.length;
  const lmCompletedIds = finalStages
    .filter((s) => (
      s.completed
      && s.stageResult
      && s.stageResult.ok
      && !s.stageResult.skipped
      && (
        !REQUIRE_MUTATION
        || s.stageResult.successfulMutationCount > 0
        || ALLOW_SKIP
      )
    ))
    .map((s) => s.id);
  // Integrity: never declare campaign success from static verify alone when no tools ran.
  const reportIntegrity = {
    toolCallTotal,
    allowSkip: ALLOW_SKIP,
    requireMutation: REQUIRE_MUTATION,
    staticVerifyAllOk: finalStages.every((s) => s.verify.ok),
    lmCompletedCount: lmCompletedIds.length,
    staleCompletedWithoutTools: !ALLOW_SKIP && toolCallTotal === 0 && state.completedStages.length > 0,
  };
  const campaignOk = ALLOW_SKIP
    ? (
      reportIntegrity.staticVerifyAllOk
      && state.completedStages.length >= 12
    )
    : (
      toolCallTotal > 0
      && reportIntegrity.staticVerifyAllOk
      && lmCompletedIds.length >= 12
      && !reportIntegrity.staleCompletedWithoutTools
    );

  const report = {
    ok: campaignOk,
    workspace: WORKSPACE,
    stage13Attempted,
    completedStages: [...state.completedStages].sort((a, b) => a - b),
    lmCompletedStages: lmCompletedIds.sort((a, b) => a - b),
    lastError: state.lastError,
    strict: STRICT,
    maxHours: MAX_HOURS,
    elapsedMs: Date.now() - startMs,
    pluginRevision: manifest.revision,
    model: model.identifier,
    toolCallTotal,
    reportIntegrity,
    stages: finalStages,
    attempts: stageReport,
    compaction: listSessions()[0]?.name || null,
  };

  fs.writeFileSync(REPORT_JSON, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  debugLog("E2E", "main", "final report", report);
  logLine(JSON.stringify(report, null, 2));

  for (const c of clients) await c.close?.();

  if (!report.ok) process.exitCode = 2;
}

main().catch((error) => {
  debugLog("E2E", "main", "fatal", { error: String(error?.stack || error) });
  console.error(error?.stack || error);
  process.exitCode = 1;
});

process.on("uncaughtException", (error) => {
  try {
    debugLog("E2E", "main", "uncaughtException", { error: String(error?.stack || error) });
    fs.appendFileSync(OUT_LOG, `uncaughtException ${error?.stack || error}\n`, "utf8");
  } catch { /* ignore */ }
  process.exitCode = 1;
});
process.on("unhandledRejection", (reason) => {
  try {
    debugLog("E2E", "main", "unhandledRejection", { error: String(reason?.stack || reason) });
    fs.appendFileSync(OUT_LOG, `unhandledRejection ${reason?.stack || reason}\n`, "utf8");
  } catch { /* ignore */ }
});

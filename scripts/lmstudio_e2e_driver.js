"use strict";

/**
 * Ordered 17-prompt LM Studio local-model marathon through unreal-context-compactor.
 * Uses the plugin generate() path (same code the chat UI generator runs) + real MCP tools.
 * Stops on tool/generator errors so they can be fixed before continuing.
 */

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { spawn } = require("node:child_process");
const { Chat, LMStudioClient } = require("@lmstudio/sdk");
const { Client } = require(path.join(
  __dirname,
  "..",
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
  __dirname,
  "..",
  "lmstudio-unreal-agent-mcp",
  "node_modules",
  "@modelcontextprotocol",
  "sdk",
  "dist",
  "cjs",
  "client",
  "stdio.js",
));

const REPO = path.resolve(__dirname, "..");
const WORKSPACE = process.env.E2E_WORKSPACE || path.join(os.homedir(), "Documents", "Git", "Project_MJS");
const DEBUG_LOG = path.join(REPO, "debug-49b048.log");
const OUT_LOG = path.join(REPO, "scripts", "marathon17.out.log");
const REPORT_JSON = path.join(REPO, "scripts", "lmstudio_marathon_report.json");
const MCP_JSON = path.join(os.homedir(), ".lmstudio", "mcp.json");
const PLUGIN_ROOT = path.join(os.homedir(), ".lmstudio", "extensions", "plugins", "codex", "unreal-context-compactor");
const MAX_ROUNDS = Number(process.env.E2E_MAX_ROUNDS || 16);
const START_AT = Math.max(1, Number(process.env.E2E_START_AT || 1));
const GENERATE_TIMEOUT_MS = Number(process.env.E2E_GENERATE_TIMEOUT_MS || 300_000);
const MAX_LIST_DIRECTORY_PER_TURN = Number(process.env.E2E_MAX_LIST_PER_TURN || 6);
const MAX_LIST_PATH_DEPTH = Number(process.env.E2E_MAX_LIST_DEPTH || 4);
const MAX_PARALLEL_TOOLS = Number(process.env.E2E_MAX_PARALLEL_TOOLS || 3);
const MAX_AVG_LIST_PER_TURN = Number(process.env.E2E_MAX_AVG_LIST || 5);
const ESSENTIAL_TOOL_NAMES = new Set([
  "get_active_project",
  "unreal_get_active_project",
  "search_files",
  "read_file",
  "read_file_range",
]);

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

const PROMPTS = [
  "현재 프로젝트 찾고 코드 구조 전체 적으로 확인해줘",
  "지금 버그있는거 찾기만하고 수정은 하지마.",
  "시네마틱 관련해서 구현된것들 더 구체적으로 알려줘.",
  "플레이어 관련해서 구현된것들  더 구체적으로 알려줘.",
  "적 캐릭터 관련해서 구현된것들 더 구체적으로 알려줘.",
  "서브시스템들의 대해서 구현된것들 더 구체적으로 알려줘.",
  "카메라 시스템 관련해서 구현된것들 더 구체적으로 알려줘.",
  "UI 관련해서 구현된것들 더 구체적으로 알려줘.",
  "플러그인 쪽을 모두 분석해봐.",
  "셰이더 플러그인 쪽 더 구체적으로 알려줘.",
  "로딩 플러그인쪽 더 구체적으로 알려줘.",
  "로딩 플러그인 쪽의 개선 방향 더 구체적으로 알려줘.",
  "로딩 플러그인 쪽의 개선 계획안만 짜고서 수정은 하지말 것.",
  "타겟팅 시스템 관련해서 좀더 알고 싶어.",
  "스킬 시스템은 어떻게 돌아가고 있는거야?",
  "게임 인스턴스 확장이 가능하다면 어떻게 할지 수정은 하지말고 말로만 알려줘",
  "컴뱃 시스템 구조 분석해줘.",
];

const SYSTEM_PROMPT = (
  "You are an Unreal Engine 5.x C++ agent. Use MCP tools before claiming project facts. "
  + "Prefer get_active_project / search_files / read_file / read_file_range. "
  + `list_directory is budget-limited (max ${MAX_LIST_DIRECTORY_PER_TURN}/turn, no duplicate paths). `
  + "For structure: list '.' and 'Source' once, then search_files/read_file. Never recursively list every child folder. "
  + "When the user forbids edits, never write/modify files. Be concrete with real paths. "
  + "Each user turn must end with a complete Korean answer that directly satisfies the request. "
  + "Do not stop on intermediate status lines such as '다음 round에서 계속' or '확인해보겠습니다' alone."
);

/** Per-turn answer requirements: every `anyOf` group needs ≥1 match in final chat text. */
const TURN_ANSWER_CHECKS = {
  1: {
    minChars: 280,
    requireTools: true,
    anyOf: [
      [/Project_MJS|\.uproject/i, /Source/i, /Plugins|Config|Content/i],
    ],
  },
  2: {
    minChars: 200,
    requireTools: true,
    anyOf: [[/버그|결함|위험|문제|issue|bug|crash|null|잘못된/i]],
    noneOf: [/수정했|파일을 썼|write_file|replace_in_file|패치 적용/i],
  },
  3: { minChars: 220, requireTools: true, anyOf: [[/시네마틱|Cinematic|LevelSequence|Sequencer/i]] },
  4: { minChars: 220, requireTools: true, anyOf: [[/플레이어|Player|Character|Pawn/i]] },
  5: { minChars: 220, requireTools: true, anyOf: [[/적|Enemy|AI|NPC/i]] },
  6: { minChars: 220, requireTools: true, anyOf: [[/서브시스템|Subsystem|UGameInstanceSubsystem|UWorldSubsystem/i]] },
  7: { minChars: 220, requireTools: true, anyOf: [[/카메라|Camera|SpringArm|CineCamera/i]] },
  8: { minChars: 220, requireTools: true, anyOf: [[/UI|UMG|Widget|HUD|Slate/i]] },
  9: { minChars: 260, requireTools: true, anyOf: [[/Plugins|플러그인/i]] },
  10: { minChars: 220, requireTools: true, anyOf: [[/셰이더|Shader|Material|HLSL/i]] },
  11: { minChars: 220, requireTools: true, anyOf: [[/로딩|Loading|LoadScreen|Streaming/i]] },
  12: { minChars: 220, requireTools: false, anyOf: [[/개선|방향|병목|최적화|제안/i]] },
  13: {
    minChars: 220,
    requireTools: false,
    anyOf: [[/계획|단계|로드맵|개선/i]],
    noneOf: [/수정했|파일을 썼|write_file|replace_in_file/i],
  },
  14: { minChars: 220, requireTools: true, anyOf: [[/타겟|Target|LockOn|조준/i]] },
  15: { minChars: 220, requireTools: true, anyOf: [[/스킬|Skill|Ability|GAS|GameplayAbility/i]] },
  16: {
    minChars: 220,
    requireTools: false,
    anyOf: [[/GameInstance|게임\s*인스턴스|확장/i]],
    noneOf: [/수정했|파일을 썼|write_file|replace_in_file/i],
  },
  17: { minChars: 260, requireTools: true, anyOf: [[/컴뱃|Combat|Damage|Attack|전투/i]] },
};

const INCOMPLETE_ANSWER_RE = (
  /다음\s*(?:round|라운드)|계속\s*(?:읽|확인|조사)|확인해보겠습니다\.?\s*$|나머지\s*파일|How can I help you/i
);
const INTERNAL_MARKER_RE = /__LM_STUDIO_INTERNAL_[A-Z0-9_]+__|<think>[\s\S]*?<\/think>|redacted_reasoning/gi;

function extractVisibleAnswer(text) {
  const raw = String(text || "");
  const parts = raw.split(/__LM_STUDIO_INTERNAL_[A-Z0-9_]+__/);
  let candidate = (parts.length > 1 ? parts[parts.length - 1] : raw)
    .replace(/<think>[\s\S]*?<\/think>/gi, "")
    .replace(/redacted_reasoning/gi, "")
    .trim();
  const hangulIdx = candidate.search(/[가-힣]/);
  if (hangulIdx > 80) {
    const afterHangul = candidate.slice(hangulIdx).trim();
    if (afterHangul.length >= 160) candidate = afterHangul;
  }
  return candidate;
}

function evaluateAnswerQuality(turnIndex, prompt, finalText, toolCalls) {
  const text = extractVisibleAnswer(finalText);
  const failures = [];
  const spec = TURN_ANSWER_CHECKS[turnIndex] || { minChars: 160, anyOf: [] };
  if (text.length < Number(spec.minChars || 160)) {
    failures.push(`too_short:${text.length}<${spec.minChars}`);
  }
  if (INCOMPLETE_ANSWER_RE.test(text)) {
    failures.push("incomplete_progress_only");
  }
  if (/__LM_STUDIO_INTERNAL_[A-Z0-9_]+__/i.test(String(finalText || ""))
    && text.length < Number(spec.minChars || 160)) {
    failures.push("internal_marker_without_visible_answer");
  }
  if (spec.requireTools && (!Array.isArray(toolCalls) || toolCalls.length === 0)) {
    const hasConcretePaths = /Source\/[A-Za-z0-9_./\\-]+\.(?:h|cpp|cs)/i.test(text);
    if (!hasConcretePaths) failures.push("no_tools_or_paths");
  }
  for (const group of spec.anyOf || []) {
    const ok = group.some((re) => re.test(text));
    if (!ok) {
      failures.push(`missing_required_topic:${group.map((re) => String(re)).join("|")}`);
    }
  }
  for (const re of spec.noneOf || []) {
    if (re.test(text)) failures.push(`forbidden_claim:${String(re)}`);
  }
  return {
    ok: failures.length === 0,
    failures,
    chars: text.length,
    prompt,
    visiblePreview: text.replace(/\s+/g, " ").slice(0, 280),
  };
}

const ALLOW_TOOLS = new Set([
  "unreal_get_active_project",
  "get_active_project",
  "get_workspace_info",
  "list_directory",
  "read_file",
  "read_file_range",
  "search_files",
  "unreal_rag_search",
]);

function logLine(...args) {
  const line = args.map((a) => (typeof a === "string" ? a : JSON.stringify(a))).join(" ");
  console.log(line);
  try { fs.appendFileSync(OUT_LOG, `${line}\n`, "utf8"); } catch { /* ignore lock races */ }
}

function debugLog(hypothesisId, location, message, data) {
  const payload = {
    sessionId: "49b048",
    runId: "marathon-17",
    hypothesisId,
    location,
    message,
    data: data || {},
    timestamp: Date.now(),
  };
  fs.appendFileSync(DEBUG_LOG, `${JSON.stringify(payload)}\n`, "utf8");
  logLine(`[debug] ${message}`, JSON.stringify(data || {}).slice(0, 700));
}

function loadMcpServers() {
  return JSON.parse(fs.readFileSync(MCP_JSON, "utf8")).mcpServers || {};
}

async function createAgentClient() {
  const cfg = loadMcpServers()["unreal-agent"];
  if (!cfg) throw new Error("mcp.json missing unreal-agent");
  const transport = new StdioClientTransport({
    command: cfg.command,
    args: cfg.args || [],
    env: { ...process.env, ...(cfg.env || {}) },
  });
  const client = new Client({ name: "marathon-17", version: "1.0.0" }, { capabilities: {} });
  await client.connect(transport);
  return {
    name: "unreal-agent",
    async listTools() { return (await client.listTools()).tools || []; },
    async callTool(name, args) {
      const result = await client.callTool({ name, arguments: args || {} });
      return serializeToolResult(result);
    },
    async close() { try { await client.close(); } catch { /* ignore */ } },
  };
}

function createRagClient() {
  const cfg = loadMcpServers()["unreal-rag"];
  if (!cfg) throw new Error("mcp.json missing unreal-rag");
  const proc = spawn(cfg.command, cfg.args || [], {
    env: { ...process.env, ...(cfg.env || {}) },
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
  });
  let lineBuffer = "";
  const pending = new Map();
  let nextId = 1;
  proc.stdout.setEncoding("utf8");
  proc.stdout.on("data", (chunk) => {
    lineBuffer += chunk;
    let idx;
    while ((idx = lineBuffer.indexOf("\n")) >= 0) {
      const line = lineBuffer.slice(0, idx).trim();
      lineBuffer = lineBuffer.slice(idx + 1);
      if (!line) continue;
      let msg;
      try { msg = JSON.parse(line); } catch { continue; }
      if (msg.id != null && pending.has(msg.id)) {
        const entry = pending.get(msg.id);
        pending.delete(msg.id);
        if (msg.error) entry.reject(new Error(JSON.stringify(msg.error)));
        else entry.resolve(msg.result);
      }
    }
  });
  proc.stderr.setEncoding("utf8");
  proc.stderr.on("data", (chunk) => {
    if (String(chunk).trim()) process.stderr.write(`[mcp:unreal-rag] ${chunk}`);
  });
  function request(method, params) {
    const id = nextId++;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      proc.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params: params || {} })}\n`);
      setTimeout(() => {
        if (pending.has(id)) {
          pending.delete(id);
          reject(new Error(`MCP timeout unreal-rag ${method}`));
        }
      }, 180_000);
    });
  }
  return {
    name: "unreal-rag",
    async initialize() {
      await request("initialize", {
        protocolVersion: "2024-11-05",
        capabilities: {},
        clientInfo: { name: "marathon-17", version: "1.0.0" },
      });
      proc.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" })}\n`);
    },
    async listTools() { return (await request("tools/list", {})).tools || []; },
    async callTool(name, args) {
      const result = await request("tools/call", { name, arguments: args || {} });
      return serializeToolResult(result);
    },
    close() { try { proc.kill(); } catch { /* ignore */ } },
  };
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
  const recovery = parsed.recoveryActionRequired === true;
  // Soft control-plane signals: return to the model so it can summarize/stop,
  // but do not abort the whole multi-turn marathon/session.
  const softStopCodes = new Set([
    "TASK_ROUTE_MISSING",
    "EVIDENCE_STAGNATION",
    "EVIDENCE_STAGNATION_REPEAT",
    "READ_REPEAT_DETECTED",
  ]);
  if (stop && code && !softStopCodes.has(code)) {
    return { code, error: parsed.error || raw.slice(0, 300), recovery };
  }
  if (code && /PERMISSION|UNAUTHORIZED|WORKSPACE_ESCAPE|FATAL/i.test(code)) {
    return { code, error: parsed.error || raw.slice(0, 300), recovery };
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
      || "Stop repeating this tool. Prefer search_files/read_file and produce the answer.",
    ...extras,
  }, null, 2);
}

function wrapToolImpls(toolImpls, turnState) {
  const wrapped = new Map();
  for (const [name, impl] of toolImpls.entries()) {
    wrapped.set(name, async (args) => {
      if (name === "list_directory") {
        const listPath = String(args?.path || ".");
        const depth = listPathDepth(listPath);
        const key = listPath.replace(/\\/g, "/").replace(/\/+$/, "") || ".";
        // #region agent log
        debugLog("H-LIST", "list_directory:guard", "list_directory guard check", {
          path: key,
          depth,
          used: turnState.listCount,
          maxPerTurn: MAX_LIST_DIRECTORY_PER_TURN,
          seen: [...turnState.listedPaths],
        });
        // #endregion
        if (turnState.listCount >= MAX_LIST_DIRECTORY_PER_TURN) {
          turnState.listBlocked += 1;
          return softToolResult(
            "LIST_DIRECTORY_BUDGET_EXCEEDED",
            `list_directory budget exceeded (${MAX_LIST_DIRECTORY_PER_TURN}/turn).`,
            {
              path: key,
              agentInstruction:
                "Do not call list_directory again this turn. Use search_files or read_file, then answer.",
            },
          );
        }
        if (turnState.listedPaths.has(key)) {
          turnState.listBlocked += 1;
          return softToolResult(
            "LIST_DIRECTORY_DUPLICATE",
            `list_directory already called for path=${key} this turn.`,
            {
              path: key,
              agentInstruction:
                "Reuse prior listing results. Call search_files/read_file next, or answer now.",
            },
          );
        }
        turnState.listCount += 1;
        turnState.listedPaths.add(key);
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
  // attempt 3+: force text-only answer from existing evidence
  return [];
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
    toolCallGenerationEnded(request) {
      capture.requests.push(request);
    },
    toolCallGenerationFailed(error) { capture.failures.push(error); },
  };
}

function readEvents(sessionDir) {
  const p = path.join(sessionDir, "events.jsonl");
  if (!fs.existsSync(p)) return [];
  return fs.readFileSync(p, "utf8").trim().split(/\r?\n/).filter(Boolean).map((line) => {
    try { return JSON.parse(line); } catch { return null; }
  }).filter(Boolean);
}

function sessionHealth(sessionDir, sinceMs) {
  if (!sessionDir) return null;
  const events = readEvents(sessionDir).filter((e) => {
    const at = Date.parse(e.at || "");
    return Number.isFinite(at) && at >= sinceMs - 2000;
  });
  const comps = events.filter((e) => e.type === "compaction_decision");
  const applied = comps.filter((e) => e.applied);
  const tiny = applied.filter((e) => Number(e.postInputTokens) > 0 && Number(e.postInputTokens) < 2000);
  const cpPath = path.join(sessionDir, "active-checkpoint.json");
  const checkpoint = fs.existsSync(cpPath) ? JSON.parse(fs.readFileSync(cpPath, "utf8")) : null;
  return {
    sessionDir,
    applied: applied.length,
    tinyPosts: tiny.length,
    answeringMeta: comps.filter((e) => e.answeringMeta).length,
    last: comps[comps.length - 1] || null,
    objective: checkpoint?.objective || null,
  };
}

async function runUserTurn({
  generate,
  client,
  model,
  config,
  toolDefinitions,
  toolImpls,
  historyMessages,
  prompt,
  turnIndex,
  stats,
  sinceMs,
}) {
  historyMessages.push({ role: "user", content: [{ type: "text", text: prompt }] });
  const turnStarted = Date.now();
  const turnTools = [];
  let finalText = "";
  const turnState = {
    listCount: 0,
    listBlocked: 0,
    listedPaths: new Set(),
  };
  const guardedToolImpls = wrapToolImpls(toolImpls, turnState);

  for (let round = 1; round <= MAX_ROUNDS; round += 1) {
    const capture = { fragments: [], requests: [], failures: [], droppedRequests: 0 };
    debugLog("H1", "round:start", "prediction round", {
      turnIndex,
      round,
      historyLen: historyMessages.length,
      generateTimeoutMs: GENERATE_TIMEOUT_MS,
      pid: process.pid,
      listCount: turnState.listCount,
      listBlocked: turnState.listBlocked,
    });

    try {
      // #region agent log
      const genStarted = Date.now();
      debugLog("H1", "generate:begin", "await generate()", { turnIndex, round, historyLen: historyMessages.length });
      let heartbeatN = 0;
      const heartbeat = setInterval(() => {
        heartbeatN += 1;
        debugLog("H1", "generate:heartbeat", "still awaiting generate()", {
          turnIndex,
          round,
          heartbeatN,
          waitedMs: Date.now() - genStarted,
        });
      }, 30_000);
      let timeoutHandle = null;
      const maxGenerateAttempts = 3;
      try {
        let lastError = null;
        for (let attempt = 1; attempt <= maxGenerateAttempts; attempt += 1) {
          capture.fragments.length = 0;
          capture.requests.length = 0;
          capture.failures.length = 0;
          capture.droppedRequests = 0;
          const attemptTools = toolDefsForAttempt(toolDefinitions, attempt);
          const attemptConfig = {
            ...config,
            temperature: attempt === 1 ? config.temperature : Math.min(Number(config.temperature || 0.2), 0.05),
          };
          // #region agent log
          debugLog("H-PEG", "generate:attempt", "generate attempt tool surface", {
            turnIndex,
            round,
            attempt,
            toolCount: attemptTools.length,
            toolNames: attemptTools.map((t) => t.function.name),
            temperature: attemptConfig.temperature,
          });
          // #endregion
          const controller = makeController(client, model, attemptConfig, attemptTools, capture);
          const chat = Chat.from({ messages: historyMessages });
          try {
            await Promise.race([
              generate(controller, chat),
              new Promise((_, reject) => {
                timeoutHandle = setTimeout(() => {
                  // #region agent log
                  debugLog("H1", "generate:timeout", "generate exceeded timeout", {
                    turnIndex,
                    round,
                    attempt,
                    timeoutMs: GENERATE_TIMEOUT_MS,
                    waitedMs: Date.now() - genStarted,
                  });
                  // #endregion
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
            debugLog("H-PEG", "generate:retry", "generate attempt failed", {
              turnIndex,
              round,
              attempt,
              transient,
              nextToolCount: toolDefsForAttempt(toolDefinitions, attempt + 1).length,
              error: errText.slice(0, 240),
            });
            if (timeoutHandle) {
              clearTimeout(timeoutHandle);
              timeoutHandle = null;
            }
            if (!transient || attempt >= maxGenerateAttempts) throw error;
          }
        }
        if (lastError) throw lastError;
      } finally {
        clearInterval(heartbeat);
        if (timeoutHandle) clearTimeout(timeoutHandle);
      }
      debugLog("H1", "generate:end", "generate returned", {
        turnIndex,
        round,
        waitedMs: Date.now() - genStarted,
        fragmentChars: capture.fragments.join("").length,
        toolRequests: capture.requests.length,
        droppedRequests: capture.droppedRequests || 0,
        failures: capture.failures.length,
      });
      // #endregion
    } catch (error) {
      const err = String(error?.stack || error);
      debugLog("H1", "round:error", "generate failed", { turnIndex, round, error: err.slice(0, 800) });
      throw new Error(`TURN_${turnIndex}_GENERATE_FAILED: ${err}`);
    }

    if (capture.failures.length) {
      throw new Error(`TURN_${turnIndex}_TOOLGEN_FAILED: ${capture.failures[0]?.message || capture.failures[0]}`);
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
          id: req.id || req.toolCallId || `call-${turnIndex}-${round}-${assistantContent.length}`,
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
        throw new Error(`TURN_${turnIndex}_UNKNOWN_TOOL: ${name}`);
      }
      if (/write|replace|apply_patch|create_file|delete_file/i.test(name)) {
        throw new Error(`TURN_${turnIndex}_WRITE_TOOL_BLOCKED: ${name}`);
      }
      const started = Date.now();
      let resultText;
      try {
        if (parallelIndex >= MAX_PARALLEL_TOOLS) {
          // Always return a result for every model tool call so the compactor
          // pending-tool checkpoint cannot stall the next prediction.
          resultText = softToolResult(
            "PARALLEL_TOOL_BUDGET_EXCEEDED",
            `Only ${MAX_PARALLEL_TOOLS} tool calls are executed per model round.`,
            {
              tool: name,
              agentInstruction:
                `This tool was skipped. Re-issue at most ${MAX_PARALLEL_TOOLS} tool calls per round, or answer with current evidence.`,
            },
          );
          // #region agent log
          debugLog("H-PAR", "tool:parallel_skip", "skipped excess parallel tool", {
            turnIndex,
            round,
            name,
            parallelIndex,
            maxParallel: MAX_PARALLEL_TOOLS,
          });
          // #endregion
        } else {
          resultText = await impl(args);
        }
      } catch (error) {
        throw new Error(`TURN_${turnIndex}_TOOL_THROW ${name}: ${error?.message || error}`);
      }
      parallelIndex += 1;
      const hard = toolResultIsHardError(resultText);
      turnTools.push({
        name,
        args,
        ms: Date.now() - started,
        preview: String(resultText).slice(0, 180),
        hardError: hard,
      });
      stats.calls.push({ turnIndex, name, args, ms: Date.now() - started });
      debugLog("E2E", "tool", "tool call", {
        turnIndex,
        name,
        args,
        ms: Date.now() - started,
        hardError: hard,
        preview: String(resultText).slice(0, 220),
      });
      if (hard) {
        throw new Error(`TURN_${turnIndex}_TOOL_HARD_ERROR ${name} ${hard.code}: ${hard.error}`);
      }
      toolContent.push({
        type: "toolCallResult",
        toolCallId: req.id || req.toolCallId || null,
        content: String(resultText),
      });
    }
    historyMessages.push({ role: "tool", content: toolContent });

    const sessions = listSessions();
    const health = sessionHealth(sessions[0]?.full, sinceMs);
    if (health && health.tinyPosts > 8) {
      const lastPost = Number(health.last?.postInputTokens || 0);
      if (lastPost > 0 && lastPost < 100) {
        throw new Error(`TURN_${turnIndex}_COMPACT_AMNESIA tinyPosts=${health.tinyPosts} lastPost=${lastPost}`);
      }
    }
  }

  // #region agent log
  debugLog("H-LIST", "turn:list_stats", "per-turn list_directory stats", {
    turnIndex,
    listCount: turnState.listCount,
    listBlocked: turnState.listBlocked,
    listedPaths: [...turnState.listedPaths],
  });
  // #endregion

  let qualityProbe = evaluateAnswerQuality(turnIndex, prompt, finalText, turnTools);
  if (!qualityProbe.ok) {
    debugLog("E2E", "turn:nudge", "answer incomplete — one completion nudge", {
      turnIndex,
      failures: qualityProbe.failures,
      chars: finalText.length,
      visibleChars: qualityProbe.chars,
    });
    const needsEvidence = qualityProbe.failures.some((f) => f.startsWith("no_tools") || f.startsWith("missing_required"));
    historyMessages.push({
      role: "user",
      content: [{
        type: "text",
        text: needsEvidence
          ? "직전 답변 근거가 부족합니다. search_files 또는 read_file로 관련 소스를 확인한 뒤, 수정하지 말고 한국어 최종 답변만 완성하세요."
          : "도구 호출 없이, 지금까지 확인한 근거만으로 내 직전 요청에 대한 최종 답변을 한국어로 완성해서 출력하세요. 중간 진행 문장만 쓰지 마세요.",
      }],
    });
    const capture = { fragments: [], requests: [], failures: [], droppedRequests: 0 };
    const nudgeTools = needsEvidence ? toolDefsForAttempt(toolDefinitions, 2) : [];
    const controller = makeController(
      client,
      model,
      { ...config, temperature: 0.05 },
      nudgeTools,
      capture,
    );
    const chat = Chat.from({ messages: historyMessages });
    try {
      await Promise.race([
        generate(controller, chat),
        new Promise((_, reject) => setTimeout(() => reject(new Error("NUDGE_TIMEOUT")), GENERATE_TIMEOUT_MS)),
      ]);
      if (capture.requests.length > 0) {
        for (const req of capture.requests.slice(0, MAX_PARALLEL_TOOLS)) {
          const name = String(req.name || "");
          const args = req.arguments || {};
          const impl = guardedToolImpls.get(name);
          const toolCallId = req.id || `nudge-${turnIndex}-${name}`;
          let resultText = softToolResult("UNKNOWN_TOOL", `unknown tool: ${name}`, {});
          if (impl) {
            try { resultText = await impl(args); } catch (error) {
              resultText = softToolResult("TOOL_THROW", String(error?.message || error), { tool: name });
            }
            turnTools.push({
              name,
              args,
              ms: 0,
              preview: String(resultText).slice(0, 180),
              hardError: null,
            });
          }
          historyMessages.push({
            role: "assistant",
            content: [{
              type: "toolCallRequest",
              toolCallRequest: { type: "function", id: toolCallId, name, arguments: args },
            }],
          });
          historyMessages.push({
            role: "tool",
            content: [{ type: "toolCallResult", toolCallId, content: String(resultText) }],
          });
        }
        const capture2 = { fragments: [], requests: [], failures: [], droppedRequests: 0 };
        const controller2 = makeController(client, model, { ...config, temperature: 0.05 }, [], capture2);
        const chat2 = Chat.from({ messages: historyMessages });
        await Promise.race([
          generate(controller2, chat2),
          new Promise((_, reject) => setTimeout(() => reject(new Error("NUDGE2_TIMEOUT")), GENERATE_TIMEOUT_MS)),
        ]);
        const nudged = capture2.fragments.join("").trim();
        if (nudged) {
          finalText = nudged;
          historyMessages.push({ role: "assistant", content: [{ type: "text", text: nudged }] });
        }
      } else {
        const nudged = capture.fragments.join("").trim();
        if (nudged) {
          finalText = nudged;
          historyMessages.push({ role: "assistant", content: [{ type: "text", text: nudged }] });
        }
      }
    } catch (error) {
      debugLog("E2E", "turn:nudge_fail", "completion nudge failed", {
        turnIndex,
        error: String(error?.message || error).slice(0, 400),
      });
    }
  }

  const visible = extractVisibleAnswer(finalText);
  return {
    turnIndex,
    prompt,
    ms: Date.now() - turnStarted,
    chars: visible.length || finalText.length,
    text: visible || finalText,
    rawText: finalText,
    preview: (visible || finalText).replace(/\s+/g, " ").slice(0, 280),
    toolCalls: turnTools,
    listDirectory: turnState.listCount,
    listBlocked: turnState.listBlocked,
  };
}

async function runMultiChatIsolationProbe({
  generate,
  client,
  model,
  config,
  toolDefinitions,
  primarySessionHint,
}) {
  const store = require(path.join(REPO, "lmstudio-context-compactor-plugin", "src", "checkpoint-store.js"));
  const core = require(path.join(REPO, "lmstudio-context-compactor-plugin", "src", "compaction-core.js"));
  const opener = PROMPTS[0];
  const salt = `${WORKSPACE}\n${model.identifier}`;
  const sessionsBefore = listSessions().map((s) => s.name);
  const primaryBefore = primarySessionHint
    ? await store.loadCheckpoint(primarySessionHint).catch(() => null)
    : null;

  const historyB = [
    { role: "system", content: [{ type: "text", text: SYSTEM_PROMPT }] },
    { role: "user", content: [{ type: "text", text: opener }] },
  ];
  const capture = { fragments: [], requests: [], failures: [], droppedRequests: 0 };
  const controller = makeController(client, model, { ...config, temperature: 0.05 }, [], capture);
  const chat = Chat.from({ messages: historyB });
  await Promise.race([
    generate(controller, chat),
    new Promise((_, reject) => setTimeout(() => reject(new Error("ISOLATION_GENERATE_TIMEOUT")), 120_000)),
  ]);

  const sessionsAfter = listSessions();
  const newest = sessionsAfter.find((s) => !sessionsBefore.includes(s.name)) || sessionsAfter[0];
  const checkpointB = newest ? await store.loadCheckpoint(newest.name).catch(() => null) : null;
  const baseKey = core.baseSessionKey(
    [{ role: "system", content: SYSTEM_PROMPT }, { role: "user", content: opener }],
    salt,
  );
  const differentSession = Boolean(newest && primarySessionHint && newest.name !== primarySessionHint);
  const result = {
    ok: Boolean(differentSession && newest),
    baseKey: String(baseKey).slice(0, 12),
    primarySession: primarySessionHint || null,
    secondarySession: newest?.name || null,
    differentSession,
    sessionsBeforeCount: sessionsBefore.length,
    sessionsAfterCount: sessionsAfter.length,
    primaryObjectivePreview: String(primaryBefore?.objective || "").slice(0, 80),
    secondaryObjectivePreview: String(checkpointB?.objective || "").slice(0, 80),
    answerPreview: capture.fragments.join("").replace(/\s+/g, " ").slice(0, 200),
  };
  debugLog("H-SESSION", "isolation:probe", "multi-chat isolation after turn 8", result);
  return result;
}

async function main() {
  try { fs.writeFileSync(OUT_LOG, "", "utf8"); } catch { /* ignore */ }
  try { if (fs.existsSync(DEBUG_LOG)) fs.writeFileSync(DEBUG_LOG, "", "utf8"); } catch { /* ignore */ }

  // #region agent log
  debugLog("H2", "main:boot", "driver boot", {
    pid: process.pid,
    platform: process.platform,
    node: process.version,
    cwd: process.cwd(),
    generateTimeoutMs: GENERATE_TIMEOUT_MS,
    maxRounds: MAX_ROUNDS,
    startAt: START_AT,
    maxListPerTurn: MAX_LIST_DIRECTORY_PER_TURN,
    maxListDepth: MAX_LIST_PATH_DEPTH,
    maxParallelTools: MAX_PARALLEL_TOOLS,
    maxAvgList: MAX_AVG_LIST_PER_TURN,
  });
  for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
    try {
      process.on(sig, () => {
        debugLog("H2", "main:signal", "received process signal", { sig, pid: process.pid });
        try {
          fs.writeFileSync(REPORT_JSON, `${JSON.stringify({
            ok: false,
            hardFail: `PROCESS_${sig}`,
            pid: process.pid,
            at: new Date().toISOString(),
          }, null, 2)}\n`, "utf8");
        } catch { /* ignore */ }
        process.exit(130);
      });
    } catch { /* signal unsupported on platform */ }
  }
  process.on("exit", (code) => {
    try {
      fs.appendFileSync(DEBUG_LOG, `${JSON.stringify({
        sessionId: "49b048",
        runId: "marathon-17",
        hypothesisId: "H2",
        location: "main:exit",
        message: "process exit",
        data: { code, pid: process.pid },
        timestamp: Date.now(),
      })}\n`, "utf8");
    } catch { /* ignore */ }
  });
  // #endregion

  // Isolate compactor state so prior marathon/UI pending tools cannot block generation.
  const isolatedState = fs.mkdtempSync(path.join(os.tmpdir(), "compactor-marathon-"));
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = isolatedState;
  process.env.LMS_CONTEXT_COMPACTOR_DEBUG_LOG = DEBUG_LOG;
  logLine("isolatedState", isolatedState);
  logLine("pid", process.pid);

  const generatePath = path.join(PLUGIN_ROOT, "dist", "generator.js");
  if (!fs.existsSync(generatePath)) {
    throw new Error(`Installed plugin generator missing: ${generatePath}`);
  }
  const manifest = JSON.parse(fs.readFileSync(path.join(PLUGIN_ROOT, "manifest.json"), "utf8"));
  logLine("plugin", manifest.owner + "/" + manifest.name, "rev", manifest.revision);
  // Ensure plugin can resolve its local modules.
  process.chdir(PLUGIN_ROOT);
  const { generate } = require(generatePath);

  const sinceMs = Date.now();
  const stats = { calls: [] };

  logLine("connecting MCP...");
  const agent = await createAgentClient();
  const rag = createRagClient();
  await rag.initialize();
  const clients = [agent, rag];

  const mcpTools = [];
  const toolImpls = new Map();
  for (const client of clients) {
    for (const def of await client.listTools()) {
      if (!ALLOW_TOOLS.has(def.name)) continue;
      if (toolImpls.has(def.name)) continue;
      mcpTools.push(def);
      toolImpls.set(def.name, (args) => client.callTool(def.name, args));
    }
  }
  const toolDefinitions = toLlmToolDefs(mcpTools);
  debugLog("E2E", "main", "tools ready", { count: toolDefinitions.length, names: toolDefinitions.map((t) => t.function.name) });

  const client = new LMStudioClient();
  const loaded = await client.llm.listLoaded();
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
  const turnResults = [];
  let hardFail = null;
  let isolationProbe = null;
  let primarySessionHint = null;

  for (let i = START_AT - 1; i < PROMPTS.length; i += 1) {
    const turnIndex = i + 1;
    const prompt = PROMPTS[i];
    logLine(`\n===== TURN ${turnIndex}/${PROMPTS.length} =====`);
    logLine(prompt);
    try {
      const result = await runUserTurn({
        generate,
        client,
        model,
        config,
        toolDefinitions,
        toolImpls,
        historyMessages,
        prompt,
        turnIndex,
        stats,
        sinceMs,
      });
      const quality = evaluateAnswerQuality(turnIndex, prompt, result.text || result.preview, result.toolCalls);
      result.quality = quality;
      turnResults.push(result);
      debugLog("E2E", "turn:done", "turn completed", {
        turnIndex,
        ms: result.ms,
        chars: result.chars,
        tools: result.toolCalls.map((t) => t.name),
        listDirectory: result.listDirectory,
        listBlocked: result.listBlocked,
        quality,
        preview: result.preview,
      });
      logLine(`quality turn ${turnIndex}`, quality.ok ? "PASS" : `FAIL ${quality.failures.join(",")}`, `chars=${result.chars}`);
      if (!primarySessionHint) {
        primarySessionHint = listSessions()[0]?.name || null;
      }
      if (result.chars < 40 && result.toolCalls.length === 0) {
        hardFail = `TURN_${turnIndex}_EMPTY_RESPONSE`;
        break;
      }
      if (/user has sent an empty message|How can I help you/i.test(result.preview)
        && !/구조|시네마틱|플레이어|플러그인|타겟|스킬|컴뱃|버그/i.test(result.preview)) {
        hardFail = `TURN_${turnIndex}_EMPTY_PROMPT_SYMPTOM`;
        break;
      }
      if (!quality.ok) {
        hardFail = `TURN_${turnIndex}_ANSWER_INCOMPLETE:${quality.failures.join(",")}`;
        break;
      }
      if (result.listDirectory > MAX_LIST_DIRECTORY_PER_TURN) {
        hardFail = `TURN_${turnIndex}_LIST_CAP_BREACH list=${result.listDirectory}`;
        break;
      }
      const listTotal = turnResults.reduce((sum, t) => sum + Number(t.listDirectory || 0), 0);
      const avgList = listTotal / turnResults.length;
      if (turnResults.length >= 3 && avgList > MAX_AVG_LIST_PER_TURN) {
        hardFail = `TURN_${turnIndex}_RESCAN_LOOP avgList=${avgList.toFixed(2)}`;
        break;
      }
      if (turnIndex === 8 && !isolationProbe) {
        logLine("\n===== MULTI-CHAT ISOLATION PROBE (after turn 8) =====");
        isolationProbe = await runMultiChatIsolationProbe({
          generate,
          client,
          model,
          config,
          toolDefinitions,
          primarySessionHint,
        });
        logLine("isolation", JSON.stringify(isolationProbe));
        if (!isolationProbe.ok) {
          hardFail = "MULTI_CHAT_ISOLATION_FAILED";
          break;
        }
      }
    } catch (error) {
      hardFail = String(error?.message || error);
      debugLog("E2E", "turn:fail", "turn failed — stop marathon", { turnIndex, hardFail: hardFail.slice(0, 1000) });
      break;
    }
  }

  const health = sessionHealth(listSessions()[0]?.full, sinceMs);
  const answerPassCount = turnResults.filter((t) => t.quality?.ok).length;
  const report = {
    ok: !hardFail && turnResults.length === PROMPTS.length - (START_AT - 1) && answerPassCount === turnResults.length,
    completedTurns: turnResults.length,
    targetTurns: PROMPTS.length - (START_AT - 1),
    answerPassCount,
    startAt: START_AT,
    hardFail,
    isolationProbe,
    pluginRevision: manifest.revision,
    model: model.identifier,
    performance: {
      p50Ms: percentile(turnResults.map((t) => t.ms), 0.5),
      p95Ms: percentile(turnResults.map((t) => t.ms), 0.95),
      toolCallTotal: stats.calls.length,
      listDirectoryTotal: turnResults.reduce((sum, t) => sum + Number(t.listDirectory || 0), 0),
      listDirectoryBlocked: turnResults.reduce((sum, t) => sum + Number(t.listBlocked || 0), 0),
      avgListDirectoryPerTurn: Number((turnResults.reduce((sum, t) => sum + Number(t.listDirectory || 0), 0) / Math.max(1, turnResults.length)).toFixed(2)),
      maxListDirectoryPerTurn: MAX_LIST_DIRECTORY_PER_TURN,
      maxListPathDepth: MAX_LIST_PATH_DEPTH,
      maxParallelTools: MAX_PARALLEL_TOOLS,
      compaction: health,
    },
    turns: turnResults.map((t) => ({
      turnIndex: t.turnIndex,
      prompt: t.prompt,
      ms: t.ms,
      chars: t.chars,
      preview: t.preview,
      quality: t.quality,
      toolCalls: t.toolCalls,
      listDirectory: t.listDirectory,
      listBlocked: t.listBlocked,
      text: t.text,
    })),
  };

  fs.writeFileSync(REPORT_JSON, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  debugLog("E2E", "main", "final report", report);
  logLine(JSON.stringify(report, null, 2));

  for (const c of clients) await c.close?.();
  try { rag.close(); } catch { /* ignore */ }

  if (!report.ok) process.exitCode = 2;
}

function percentile(values, p) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))];
}

main().catch((error) => {
  debugLog("E2E", "main", "fatal", { error: String(error?.stack || error) });
  console.error(error?.stack || error);
  process.exitCode = 1;
});

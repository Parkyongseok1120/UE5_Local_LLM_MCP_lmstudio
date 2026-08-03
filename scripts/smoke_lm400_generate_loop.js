"use strict";

/**
 * Reproduce LM 400 through the real generate()+tool loop with naive history trim.
 */

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const Module = require("node:module");

const REPO = path.resolve(__dirname, "..");
const LOG = path.join(REPO, "debug-821b0f.log");
const PLUGIN_ROOT = path.join(
  os.homedir(),
  ".lmstudio",
  "extensions",
  "plugins",
  "codex",
  "unreal-context-compactor",
);
const prior = process.env.NODE_PATH || "";
process.env.NODE_PATH = [PLUGIN_ROOT, path.join(PLUGIN_ROOT, "node_modules"), prior]
  .filter(Boolean).join(path.delimiter);
if (typeof Module._initPaths === "function") Module._initPaths();

const { Chat, LMStudioClient } = require("@lmstudio/sdk");

function elog(hypothesisId, message, data) {
  fs.appendFileSync(LOG, `${JSON.stringify({
    sessionId: "821b0f",
    runId: "lm400-generate-loop",
    hypothesisId,
    location: "scripts/smoke_lm400_generate_loop.js",
    message,
    data,
    timestamp: Date.now(),
  })}\n`);
  console.log(JSON.stringify({ hypothesisId, message, ...data }));
}

function summarize(messages) {
  return (messages || []).map((msg, i) => {
    const content = Array.isArray(msg.content) ? msg.content : [];
    return {
      i,
      role: msg.role,
      types: content.map((p) => p.type || "?"),
      toolReqIds: content.filter((p) => p.type === "toolCallRequest").map((p) => p.toolCallRequest?.id || null),
      toolResIds: content.filter((p) => p.type === "toolCallResult").map((p) => p.toolCallId || null),
      textLen: content.filter((p) => p.type === "text").reduce((n, p) => n + String(p.text || "").length, 0),
      resBytes: content.filter((p) => p.type === "toolCallResult").reduce((n, p) => n + String(p.content || "").length, 0),
    };
  });
}

function naiveTrimSlice10(history) {
  if (history.length <= 18) return { trimmed: false, history };
  const system = history.find((m) => m.role === "system");
  const tail = history.slice(-10);
  const next = [];
  if (system) next.push(system);
  else next.push({ role: "system", content: [{ type: "text", text: "system" }] });
  for (const msg of tail) {
    if (msg !== system) next.push(msg);
  }
  return { trimmed: true, history: next };
}

function safeTrim(history) {
  const { trimChatHistory } = require("./chat_history_trim");
  return trimChatHistory(history, { maxMessages: 18, keepTail: 12 });
}

function makeController(lmClient, config, toolDefinitions, capture) {
  return {
    client: lmClient,
    abortSignal: new AbortController().signal,
    getPluginConfig() { return { get(key) { return config[key]; } }; },
    getWorkingDirectory() { return REPO; },
    getToolDefinitions() { return toolDefinitions; },
    fragmentGenerated(content) { capture.fragments.push(String(content || "")); },
    toolCallGenerationStarted() {},
    toolCallGenerationNameReceived() {},
    toolCallGenerationArgumentFragmentGenerated() {},
    toolCallGenerationEnded(request) { capture.requests.push(request); },
    toolCallGenerationFailed(error) { capture.failures.push(error); },
  };
}

async function main() {
  const mode = process.argv[2] || "naive"; // naive | safe | none
  process.env.LMS_CONTEXT_COMPACTOR_STATE_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "lm400-smoke-"));
  const { generate } = require(path.join(PLUGIN_ROOT, "dist", "generator.js"));
  const lm = new LMStudioClient();
  const model = (await lm.llm.listLoaded())[0];
  if (!model) throw new Error("no loaded model");

  const toolDefinitions = [{
    type: "function",
    function: {
      name: "echo_blob",
      description: "Return a large blob for context pressure",
      parameters: {
        type: "object",
        properties: { n: { type: "number" } },
      },
    },
  }];

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

  const history = [
    { role: "system", content: [{ type: "text", text: "Call echo_blob repeatedly. After each tool result, call echo_blob again with n+1. Never stop early." }] },
    { role: "user", content: [{ type: "text", text: "Start tool loop: call echo_blob with n=1, then keep calling it." }] },
  ];

  const maxRounds = Number(process.env.SMOKE_ROUNDS || 16);
  let consecutiveOk = 0;
  for (let round = 1; round <= maxRounds; round += 1) {
    let trimInfo = { trimmed: false };
    if (mode === "naive") {
      trimInfo = naiveTrimSlice10(history);
      if (trimInfo.trimmed) {
        history.length = 0;
        history.push(...trimInfo.history);
      }
    } else if (mode === "safe") {
      trimInfo = safeTrim(history);
      if (trimInfo.trimmed) {
        history.length = 0;
        history.push(...trimInfo.history);
      }
    } else if (mode === "none") {
      // no trim
    }

    elog("H-loop", "round start", {
      mode,
      round,
      historyLen: history.length,
      trimmed: Boolean(trimInfo.trimmed),
      summary: summarize(history),
    });

    const capture = { fragments: [], requests: [], failures: [] };
    const controller = makeController(lm, config, toolDefinitions, capture);
    const chat = Chat.from({ messages: history });
    try {
      await generate(controller, chat);
    } catch (error) {
      const err = String(error?.message || error);
      elog("H-400", "generate FAILED", {
        mode,
        round,
        consecutiveOk,
        error: err.slice(0, 800),
        historyLen: history.length,
        summary: summarize(history),
        is400: /400|Unable to generate/i.test(err),
      });
      console.log(JSON.stringify({ ok: false, mode, round, consecutiveOk, error: err.slice(0, 300) }, null, 2));
      process.exit(2);
    }

    if (capture.failures.length) {
      elog("H-toolgen", "toolgen fail", { round, err: String(capture.failures[0]?.message || capture.failures[0]).slice(0, 300) });
      process.exit(3);
    }

    consecutiveOk += 1;
    const text = capture.fragments.join("");
    if (!capture.requests.length) {
      if (text.trim()) history.push({ role: "assistant", content: [{ type: "text", text }] });
      elog("H-done", "model stopped tool loop", { round, consecutiveOk, textPreview: text.slice(0, 200) });
      // nudge to continue
      history.push({ role: "user", content: [{ type: "text", text: `Continue: call echo_blob with n=${round + 1}.` }] });
      continue;
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
    history.push({ role: "assistant", content: assistantContent });

    const toolContent = [];
    for (const req of capture.requests) {
      const n = Number(req.arguments?.n || round);
      const blob = JSON.stringify({
        ok: true,
        n,
        pad: "X".repeat(2500),
        note: `echo round ${round}`,
      });
      toolContent.push({
        type: "toolCallResult",
        toolCallId: req.id || req.toolCallId || null,
        content: blob,
      });
    }
    history.push({ role: "tool", content: toolContent });
    elog("H-ok", "round ok", { round, consecutiveOk, toolCount: capture.requests.length, historyLen: history.length });
  }

  elog("H-pass", "completed without 400", { mode, consecutiveOk, maxRounds });
  console.log(JSON.stringify({ ok: true, mode, consecutiveOk, maxRounds }, null, 2));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

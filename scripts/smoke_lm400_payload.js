"use strict";

/**
 * Reproduce LM Studio applyPromptTemplate 400 against concrete chat payloads.
 * Writes NDJSON evidence to debug-821b0f.log
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

const priorNodePath = process.env.NODE_PATH || "";
process.env.NODE_PATH = [PLUGIN_ROOT, path.join(PLUGIN_ROOT, "node_modules"), priorNodePath]
  .filter(Boolean)
  .join(path.delimiter);
if (typeof Module._initPaths === "function") Module._initPaths();

const { Chat, LMStudioClient } = require("@lmstudio/sdk");

function log(hypothesisId, message, data) {
  const entry = {
    sessionId: "821b0f",
    runId: "lm400-payload-compare",
    hypothesisId,
    location: "scripts/smoke_lm400_payload.js",
    message,
    data,
    timestamp: Date.now(),
  };
  fs.appendFileSync(LOG, `${JSON.stringify(entry)}\n`);
  console.log(JSON.stringify({ hypothesisId, message, data }, null, 2));
}

function summarize(messages) {
  return (messages || []).map((msg, i) => {
    const content = Array.isArray(msg.content) ? msg.content : [];
    return {
      i,
      role: msg.role,
      types: content.map((p) => p.type || typeof p),
      toolReqIds: content
        .filter((p) => p.type === "toolCallRequest")
        .map((p) => p.toolCallRequest?.id || null),
      toolResIds: content
        .filter((p) => p.type === "toolCallResult")
        .map((p) => p.toolCallId || null),
      textLen: content
        .filter((p) => p.type === "text")
        .reduce((n, p) => n + String(p.text || "").length, 0),
    };
  });
}

function mkSystem() {
  return { role: "system", content: [{ type: "text", text: "You are a test agent." }] };
}
function mkUser(text) {
  return { role: "user", content: [{ type: "text", text }] };
}
function mkAssistantTools(calls, text = "") {
  const content = [];
  if (text) content.push({ type: "text", text });
  for (const call of calls) {
    content.push({
      type: "toolCallRequest",
      toolCallRequest: {
        id: call.id,
        type: "function",
        name: call.name,
        arguments: call.arguments || {},
      },
    });
  }
  return { role: "assistant", content };
}
function mkToolResults(results) {
  return {
    role: "tool",
    content: results.map((r) => ({
      type: "toolCallResult",
      toolCallId: r.id,
      content: r.content || '{"ok":true}',
    })),
  };
}

function buildLongValidHistory(pairs = 8) {
  const msgs = [mkSystem(), mkUser("Stage 4 fix compile errors via MCP.")];
  for (let i = 0; i < pairs; i += 1) {
    const id = `call-valid-${i}`;
    msgs.push(mkAssistantTools([{ id, name: "read_file", arguments: { path: `f${i}.h` } }], `round ${i}`));
    msgs.push(mkToolResults([{ id, content: JSON.stringify({ ok: true, path: `f${i}.h`, body: "x".repeat(200) }) }]));
  }
  msgs.push(mkUser("Continue with replace_in_file."));
  return msgs;
}

/** Mimic old supervisor/marathon slice(-10) that orphans tool results. */
function buildOrphanToolTailHistory() {
  const full = buildLongValidHistory(6);
  // Drop system + early pairs so a tool message can lead the tail without its assistant request.
  const tail = full.slice(-10);
  // Force orphan: prepend a tool result whose call id is not in the tail.
  return [
    mkSystem(),
    mkToolResults([{ id: "orphan-missing-assistant", content: '{"ok":true,"note":"orphan"}' }]),
    ...tail.filter((m) => m.role !== "system"),
  ];
}

/** Mimic new supervisor wipe: system + last user + continue user (duplicate users, no tools). */
function buildAmnesiaContinueHistory() {
  return [
    mkSystem(),
    mkUser("Original long Stage 4 task with many file targets."),
    mkUser("Continue the same task. Re-read target files..."),
  ];
}

/** Mismatched toolCallId between request and result. */
function buildMismatchedToolIds() {
  return [
    mkSystem(),
    mkUser("patch file"),
    mkAssistantTools([{ id: "req-A", name: "replace_in_file", arguments: { path: "a.cpp" } }]),
    mkToolResults([{ id: "req-B", content: '{"ok":true}' }]),
  ];
}

/** Empty content assistant + empty tool. */
function buildEmptyContent() {
  return [
    mkSystem(),
    mkUser("hi"),
    { role: "assistant", content: [] },
    { role: "tool", content: [] },
  ];
}

/** Valid history then naive slice(-10) like marathon (may start mid-pair). */
function buildNaiveSlice10() {
  const full = buildLongValidHistory(8);
  const system = full[0];
  const tail = full.slice(-10);
  return [system, ...tail.filter((m) => m !== system)];
}

async function tryTemplate(model, label, hypothesisId, messages) {
  const summary = summarize(messages);
  let ok = false;
  let error = null;
  let formattedLen = null;
  try {
    const chat = Chat.from({ messages });
    const formatted = await model.applyPromptTemplate(chat);
    formattedLen = String(formatted || "").length;
    ok = true;
  } catch (e) {
    error = String(e?.message || e).slice(0, 500);
  }
  log(hypothesisId, `payload ${label}`, {
    label,
    ok,
    formattedLen,
    error,
    historyLen: messages.length,
    summary,
  });
  return { label, ok, error, formattedLen };
}

async function main() {
  const lm = new LMStudioClient();
  const model = (await lm.llm.listLoaded())[0];
  if (!model) throw new Error("no loaded LM Studio model");
  log("H0", "model loaded", { model: model.identifier });

  const cases = [
    ["VALID_LONG", "H-valid", buildLongValidHistory(8)],
    ["NAIVE_SLICE10", "H-slice10", buildNaiveSlice10()],
    ["ORPHAN_TOOL", "H-orphan", buildOrphanToolTailHistory()],
    ["MISMATCH_IDS", "H-mismatch", buildMismatchedToolIds()],
    ["EMPTY_CONTENT", "H-empty", buildEmptyContent()],
    ["AMNESIA_CONTINUE", "H-amnesia", buildAmnesiaContinueHistory()],
  ];

  const results = [];
  for (const [label, hyp, messages] of cases) {
    results.push(await tryTemplate(model, label, hyp, messages));
  }

  const failing = results.filter((r) => !r.ok);
  const passing = results.filter((r) => r.ok);
  log("H-summary", "payload compare complete", {
    pass: passing.map((r) => r.label),
    fail: failing.map((r) => ({ label: r.label, error: r.error })),
  });
  console.log(JSON.stringify({ pass: passing.map((r) => r.label), fail: failing.map((r) => r.label) }, null, 2));
  process.exit(failing.some((r) => /400|Unable to generate/i.test(String(r.error || ""))) ? 0 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

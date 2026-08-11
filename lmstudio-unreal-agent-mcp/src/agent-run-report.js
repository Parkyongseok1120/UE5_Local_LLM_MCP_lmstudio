"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const cp = require("child_process");
const { ensureStateRootLayout, resolveAgentStateRoot, taskStateDir } = require("./state-root.js");

const VOLATILE_KEYS = new Set([
  "authToken",
  "auth_token",
  "ownerCapability",
  "owner_capability",
  "conversationId",
  "conversation_id",
]);
const TERMINAL = new Set(["completed", "cancelled", "failed", "cancellation_uncertain"]);

function taskIdFrom(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "";
  const control = value.control && typeof value.control === "object" ? value.control : {};
  const auth = value.taskAuthorization && typeof value.taskAuthorization === "object"
    ? value.taskAuthorization
    : value.task_authorization && typeof value.task_authorization === "object"
      ? value.task_authorization
      : {};
  return String(
    control.taskId
      || auth.taskSessionId
      || auth.task_session_id
      || value.taskSessionId
      || value.task_session_id
      || ""
  ).trim();
}

function taskIdForEvent(...values) {
  for (const value of values) {
    const taskId = taskIdFrom(value);
    if (taskId) return taskId;
  }
  return "";
}

function stableArguments(value) {
  if (Array.isArray(value)) return value.map(stableArguments);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.keys(value)
      .sort()
      .filter((key) => !VOLATILE_KEYS.has(key) && !["taskAuthorization", "task_authorization"].includes(key))
      .map((key) => [key, stableArguments(value[key])])
  );
}

function argumentsHash(args) {
  return crypto.createHash("sha256")
    .update(JSON.stringify(stableArguments(args || {})))
    .digest("hex")
    .slice(0, 24);
}

function appendEvent(workspaceRoot, taskSessionId, event) {
  const taskId = String(taskSessionId || "").trim();
  if (!taskId) return false;
  const stateRoot = ensureStateRootLayout(resolveAgentStateRoot(workspaceRoot));
  const dir = taskStateDir(taskId, stateRoot);
  fs.mkdirSync(dir, { recursive: true });
  const record = {
    version: 1,
    timestamp: new Date().toISOString(),
    taskSessionId: taskId,
    ...event,
  };
  fs.appendFileSync(path.join(dir, "run-events.jsonl"), `${JSON.stringify(record)}\n`, "utf8");
  return true;
}

function recordToolStarted(workspaceRoot, { toolName, arguments: args, callId, source = "unreal-agent" }) {
  const taskId = taskIdForEvent(args);
  if (!taskId) return "";
  appendEvent(workspaceRoot, taskId, {
    kind: "tool_started",
    source,
    callId: String(callId || ""),
    tool: String(toolName || "unknown"),
    argumentsHash: argumentsHash(args),
  });
  return taskId;
}

function refreshTerminalReport(workspaceRoot, taskId) {
  if (!taskId) return;
  let state;
  try {
    const stateRoot = resolveAgentStateRoot(workspaceRoot);
    state = JSON.parse(fs.readFileSync(path.join(taskStateDir(taskId, stateRoot), "state.json"), "utf8"));
  } catch {
    return;
  }
  if (!TERMINAL.has(String(state?.status || ""))) return;
  const script = path.join(path.resolve(workspaceRoot), "scripts", "agent_run_report.py");
  if (!fs.existsSync(script)) return;
  const python = String(process.env.PYTHON_EXECUTABLE || process.env.PYTHON || "python");
  cp.spawnSync(python, [script, "--workspace", path.resolve(workspaceRoot), "--task", taskId], {
    encoding: "utf8",
    windowsHide: true,
    timeout: 10000,
  });
}

function recordToolResult(workspaceRoot, {
  toolName,
  arguments: args,
  structured,
  callId,
  source = "unreal-agent",
  durationMs = 0,
}) {
  const payload = structured && typeof structured === "object" ? structured : {};
  const taskId = taskIdForEvent(payload, args);
  if (!taskId) return "";
  const control = payload.control && typeof payload.control === "object" ? payload.control : {};
  appendEvent(workspaceRoot, taskId, {
    kind: "tool_result",
    source,
    callId: String(callId || ""),
    tool: String(toolName || "unknown"),
    argumentsHash: argumentsHash(args),
    ok: payload.ok !== false,
    isError: payload.ok === false,
    errorCode: String(payload.errorCode || ""),
    blockerFingerprint: String(control.blockerFingerprint || ""),
    nextAction: String(control.nextAction || ""),
    nextActionIsTool: control.nextActionIsTool === true,
    durationMs: Math.max(0, Number(durationMs || 0)),
  });
  refreshTerminalReport(workspaceRoot, taskId);
  return taskId;
}

module.exports = {
  argumentsHash,
  recordToolResult,
  recordToolStarted,
  taskIdForEvent,
};

"use strict";

const fs = require("fs");
const path = require("path");
const { taskStateDir, resolveAgentStateRoot, ensureStateRootLayout } = require("./state-root");

const TASK_SESSION_ID_RE = /^[A-Za-z0-9_-]{8,64}$/;

function sanitizeTaskSessionId(taskSessionId) {
  const value = String(taskSessionId || "").trim();
  if (!value) {
    return { ok: false, error: "taskSessionId is required" };
  }
  if (value.includes("..") || value.includes("/") || value.includes("\\")) {
    return { ok: false, error: "taskSessionId must not contain path separators or traversal" };
  }
  if (!TASK_SESSION_ID_RE.test(value)) {
    return {
      ok: false,
      error: "taskSessionId must match [A-Za-z0-9_-]{8,64}",
    };
  }
  return { ok: true, taskSessionId: value };
}

function taskDir(workspaceRoot, taskSessionId, stateRoot = resolveAgentStateRoot(workspaceRoot)) {
  ensureStateRootLayout(stateRoot);
  return taskStateDir(taskSessionId, stateRoot);
}

function readTaskState(_workspaceRoot, taskSessionId, stateRoot = null) {
  stateRoot = stateRoot || resolveAgentStateRoot(_workspaceRoot);
  let dir;
  try {
    dir = taskDir(_workspaceRoot, taskSessionId, stateRoot);
  } catch (error) {
    return null;
  }
  const statePath = path.join(dir, "state.json");
  if (!fs.existsSync(statePath)) {
    const legacyRoot = path.resolve(_workspaceRoot || process.cwd());
    const legacyPath = path.join(legacyRoot, ".agent", "tasks", taskSessionId, "state.json");
    if (fs.existsSync(legacyPath)) {
      return JSON.parse(fs.readFileSync(legacyPath, "utf8"));
    }
    return null;
  }
  try {
    return JSON.parse(fs.readFileSync(statePath, "utf8"));
  } catch {
    return null;
  }
}

function requiredFields(args = {}) {
  return {
    taskSessionId: String(args.taskSessionId || args.task_session_id || "").trim(),
    authToken: String(args.authToken || args.auth_token || args.token || "").trim(),
    planId: String(args.planId || args.plan_id || "").trim(),
    planRevision: String(args.planRevision || args.plan_revision || "").trim(),
    activeSliceId: String(args.activeSliceId || args.active_slice_id || "").trim(),
  };
}

function validateMutationAuth(workspaceRoot, args = {}, options = {}) {
  const requireAll = options.requireAll !== false;
  const fields = requiredFields(args);
  const missing = Object.entries(fields).filter(([, value]) => !value).map(([key]) => key);
  if (requireAll && missing.length) {
    return {
      ok: false,
      error: `Task authorization missing required fields: ${missing.join(", ")}`,
      errorCode: "TASK_AUTH_INCOMPLETE",
    };
  }
  if (!fields.taskSessionId) {
    return { ok: false, error: "taskSessionId is required", errorCode: "TASK_SESSION_REQUIRED" };
  }
  const sanitized = sanitizeTaskSessionId(fields.taskSessionId);
  if (!sanitized.ok) {
    return { ok: false, error: sanitized.error };
  }
  const state = readTaskState(workspaceRoot, sanitized.taskSessionId);
  if (!state) {
    return { ok: false, error: `Unknown task session: ${sanitized.taskSessionId}` };
  }
  const mismatches = [];
  for (const [key, expected] of Object.entries(fields)) {
    if (key === "taskSessionId" || !expected) continue;
    const actual = String(state[key] || state[key.charAt(0).toLowerCase() + key.slice(1)] || "");
    if (actual !== expected) {
      mismatches.push(key);
    }
  }
  if (mismatches.length) {
    return {
      ok: false,
      error: `Task authorization mismatch: ${mismatches.join(", ")}`,
      errorCode: "TASK_AUTH_MISMATCH",
      taskSessionId: sanitized.taskSessionId,
    };
  }
  const status = String(state.status || "");
  if (status !== "running") {
    return {
      ok: false,
      error: `Task session is not writable in status '${status || "unknown"}'`,
      errorCode: status === "cancelled" ? "TASK_CANCELLED" : "TASK_NOT_WRITABLE",
    };
  }
  const writeGate = state.writeGate;
  const writesAllowed = writeGate && typeof writeGate === "object"
    ? writeGate.writesAllowed
    : (writeGate !== undefined ? writeGate : state.writesAllowed);
  if (writesAllowed !== true && writesAllowed !== "true") {
    return { ok: false, error: "Task writeGate denies writes", errorCode: "WRITE_GATE_DENIED" };
  }
  return {
    ok: true,
    taskSessionId: sanitized.taskSessionId,
    state,
    maxFilesPerEdit: Number(state.maxFilesPerEdit || writeGate?.maxFilesPerEdit || 2),
  };
}

module.exports = {
  TASK_SESSION_ID_RE,
  sanitizeTaskSessionId,
  taskDir,
  readTaskState,
  validateMutationAuth,
  requiredFields,
};

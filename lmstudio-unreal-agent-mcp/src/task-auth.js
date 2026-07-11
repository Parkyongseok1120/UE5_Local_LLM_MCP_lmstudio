"use strict";

const fs = require("fs");
const path = require("path");

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

function taskDir(workspaceRoot, taskSessionId) {
  const sanitized = sanitizeTaskSessionId(taskSessionId);
  if (!sanitized.ok) {
    throw new Error(sanitized.error);
  }
  const root = path.resolve(workspaceRoot);
  const dir = path.resolve(root, ".agent", "tasks", sanitized.taskSessionId);
  const tasksRoot = path.resolve(root, ".agent", "tasks");
  if (!dir.startsWith(tasksRoot + path.sep) && dir !== tasksRoot) {
    throw new Error("taskSessionId resolves outside .agent/tasks");
  }
  return dir;
}

function readTaskState(workspaceRoot, taskSessionId) {
  let dir;
  try {
    dir = taskDir(workspaceRoot, taskSessionId);
  } catch (error) {
    return null;
  }
  const statePath = path.join(dir, "state.json");
  if (!fs.existsSync(statePath)) {
    return null;
  }
  try {
    return JSON.parse(fs.readFileSync(statePath, "utf8"));
  } catch {
    return null;
  }
}

function validateMutationAuth(workspaceRoot, args = {}) {
  const taskSessionId = String(args.taskSessionId || args.task_session_id || "").trim();
  if (!taskSessionId) {
    return { ok: true, skipped: true, reason: "no taskSessionId provided" };
  }
  const sanitized = sanitizeTaskSessionId(taskSessionId);
  if (!sanitized.ok) {
    return { ok: false, error: sanitized.error, taskSessionId };
  }
  const state = readTaskState(workspaceRoot, sanitized.taskSessionId);
  if (!state) {
    return { ok: false, error: `Unknown task session: ${sanitized.taskSessionId}` };
  }
  const expected = {
    planId: String(args.planId || args.plan_id || "").trim(),
    planRevision: String(args.planRevision || args.plan_revision || "").trim(),
    activeSliceId: String(args.activeSliceId || args.active_slice_id || "").trim(),
    authToken: String(args.authToken || args.auth_token || args.token || "").trim(),
  };
  const mismatches = [];
  for (const [key, value] of Object.entries(expected)) {
    if (!value) {
      continue;
    }
    const actual = String(state[key] || state[key.charAt(0).toLowerCase() + key.slice(1)] || "");
    if (actual !== value) {
      mismatches.push(key);
    }
  }
  if (state.authToken && expected.authToken && state.authToken !== expected.authToken) {
    mismatches.push("authToken");
  }
  if (mismatches.length) {
    return {
      ok: false,
      error: `Task authorization mismatch: ${mismatches.join(", ")}`,
      taskSessionId: sanitized.taskSessionId,
    };
  }
  return { ok: true, taskSessionId: sanitized.taskSessionId, state };
}

module.exports = {
  TASK_SESSION_ID_RE,
  sanitizeTaskSessionId,
  taskDir,
  readTaskState,
  validateMutationAuth,
};

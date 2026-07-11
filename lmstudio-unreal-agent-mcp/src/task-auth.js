"use strict";

const fs = require("fs");
const path = require("path");

function taskDir(workspaceRoot, taskSessionId) {
  return path.join(workspaceRoot, ".agent", "tasks", String(taskSessionId || ""));
}

function readTaskState(workspaceRoot, taskSessionId) {
  const statePath = path.join(taskDir(workspaceRoot, taskSessionId), "state.json");
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
  const state = readTaskState(workspaceRoot, taskSessionId);
  if (!state) {
    return { ok: false, error: `Unknown task session: ${taskSessionId}` };
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
      taskSessionId,
    };
  }
  return { ok: true, taskSessionId, state };
}

module.exports = {
  taskDir,
  readTaskState,
  validateMutationAuth,
};

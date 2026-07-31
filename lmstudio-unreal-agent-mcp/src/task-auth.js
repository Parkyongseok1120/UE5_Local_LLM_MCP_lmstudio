"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
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

function readTaskStateResult(_workspaceRoot, taskSessionId, stateRoot = null) {
  stateRoot = stateRoot || resolveAgentStateRoot(_workspaceRoot);
  let dir;
  try {
    dir = taskDir(_workspaceRoot, taskSessionId, stateRoot);
  } catch {
    return { state: null, errorCode: "TASK_STATE_UNAVAILABLE" };
  }
  const statePath = path.join(dir, "state.json");
  let selectedPath = statePath;
  if (!fs.existsSync(statePath)) {
    const legacyRoot = path.resolve(_workspaceRoot || process.cwd());
    const legacyPath = path.join(legacyRoot, ".agent", "tasks", taskSessionId, "state.json");
    if (fs.existsSync(legacyPath)) {
      selectedPath = legacyPath;
    } else {
      return { state: null, errorCode: "TASK_STATE_MISSING" };
    }
  }
  try {
    const state = JSON.parse(fs.readFileSync(selectedPath, "utf8"));
    if (!state || typeof state !== "object" || Array.isArray(state)) {
      return { state: null, errorCode: "TASK_STATE_CORRUPT" };
    }
    return { state, errorCode: "" };
  } catch {
    return { state: null, errorCode: "TASK_STATE_CORRUPT" };
  }
}

function readTaskState(_workspaceRoot, taskSessionId, stateRoot = null) {
  return readTaskStateResult(_workspaceRoot, taskSessionId, stateRoot).state;
}

function requiredFields(args = {}) {
  const nested = args.taskAuthorization && typeof args.taskAuthorization === "object"
    ? args.taskAuthorization
    : (args.task_authorization && typeof args.task_authorization === "object" ? args.task_authorization : {});
  return {
    taskSessionId: String(args.taskSessionId || args.task_session_id || nested.taskSessionId || nested.task_session_id || "").trim(),
    authToken: String(args.authToken || args.auth_token || args.token || nested.authToken || nested.auth_token || nested.token || "").trim(),
    planId: String(args.planId || args.plan_id || nested.planId || nested.plan_id || "").trim(),
    planRevision: String(args.planRevision || args.plan_revision || nested.planRevision || nested.plan_revision || "").trim(),
    activeSliceId: String(args.activeSliceId || args.active_slice_id || nested.activeSliceId || nested.active_slice_id || "").trim(),
  };
}

function requestedMutationPaths(args = {}, state = {}) {
  const raw = [];
  if (args.path) raw.push(args.path);
  for (const item of Array.isArray(args.files) ? args.files : []) {
    if (item && item.path) raw.push(item.path);
  }
  for (const item of Array.isArray(args.patches) ? args.patches : []) {
    if (item && item.path) raw.push(item.path);
  }
  const projectFile = String(state.projectFile || "").trim();
  const projectRoot = projectFile.toLowerCase().endsWith(".uproject")
    ? path.dirname(projectFile)
    : projectFile;
  return [...new Set(raw.map((value) => {
    const text = String(value || "").trim();
    return path.resolve(path.isAbsolute(text) ? text : path.join(projectRoot || process.cwd(), text));
  }))];
}

function sha1File(filePath) {
  return crypto.createHash("sha1").update(fs.readFileSync(filePath)).digest("hex");
}

function validateCompletedGates(state, args = {}) {
  const writeGate = state.writeGate && typeof state.writeGate === "object" ? state.writeGate : {};
  const required = Array.isArray(state.requiredBeforeWrite)
    ? state.requiredBeforeWrite.map(String)
    : (Array.isArray(writeGate.requiredBeforeWrite) ? writeGate.requiredBeforeWrite.map(String) : []);
  if (!required.length) return { ok: true };
  const completed = state.completedGates && typeof state.completedGates === "object"
    ? state.completedGates
    : {};
  const missing = required.filter((gate) => {
    const record = completed[gate];
    return !record || record.status !== "completed";
  });
  if (missing.length) {
    return {
      ok: false,
      error: `Required pre-write gates are incomplete: ${missing.join(", ")}`,
      errorCode: "REQUIRED_GATE_INCOMPLETE",
      pendingGates: missing,
    };
  }
  const expectedGateSetHash = String(state.requiredGateSetHash || "");
  const now = Date.now();
  for (const gate of required) {
    const record = completed[gate];
    if (!record || String(record.gateSetHash || "") !== expectedGateSetHash) {
      return {
        ok: false,
        error: `Required pre-write gate is stale for the active plan: ${gate}`,
        errorCode: "REQUIRED_GATE_STALE",
      };
    }
    const expiresAt = Date.parse(String(record.expiresAt || ""));
    if (!Number.isFinite(expiresAt) || expiresAt <= now) {
      return {
        ok: false,
        error: `Required pre-write gate has expired: ${gate}`,
        errorCode: "REQUIRED_GATE_EXPIRED",
      };
    }
  }

  const requestedPaths = requestedMutationPaths(args, state);
  const snapshotGates = required
    .map((gate) => ({ gate, snapshots: completed[gate]?.targetSnapshots }))
    .filter((item) => Array.isArray(item.snapshots) && item.snapshots.length);
  if (requestedPaths.length && snapshotGates.length) {
    const caseFold = (value) => process.platform === "win32" ? value.toLowerCase() : value;
    for (const snapshotGate of snapshotGates) {
      const byPath = new Map(
        snapshotGate.snapshots
          .filter((item) => item && item.absolutePath)
          .map((item) => [caseFold(path.resolve(String(item.absolutePath))), item])
      );
      for (const requestedPath of requestedPaths) {
        const snapshot = byPath.get(caseFold(requestedPath));
        if (!snapshot) {
          return {
            ok: false,
            error: `Mutation target was not covered by ${snapshotGate.gate} evidence: ${requestedPath}`,
            errorCode: "GATE_TARGET_MISMATCH",
          };
        }
        const existsNow = fs.existsSync(requestedPath);
        if (Boolean(snapshot.exists) !== existsNow) {
          return {
            ok: false,
            error: `Mutation target changed since ${snapshotGate.gate} validation: ${requestedPath}`,
            errorCode: "GATE_TARGET_STALE",
          };
        }
        if (existsNow && snapshot.fileHash) {
          let currentHash;
          try {
            currentHash = sha1File(requestedPath);
          } catch {
            return {
              ok: false,
              error: `Mutation target could not be re-read: ${requestedPath}`,
              errorCode: "GATE_TARGET_STALE",
            };
          }
          if (currentHash !== String(snapshot.fileHash)) {
            return {
              ok: false,
              error: `Mutation target content changed since ${snapshotGate.gate} validation: ${requestedPath}`,
              errorCode: "GATE_TARGET_STALE",
            };
          }
        }
      }
    }
  }
  return { ok: true };
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
  const stateResult = readTaskStateResult(workspaceRoot, sanitized.taskSessionId);
  const state = stateResult.state;
  if (!state) {
    if (stateResult.errorCode === "TASK_STATE_CORRUPT") {
      return {
        ok: false,
        error: `Task state is unreadable or malformed: ${sanitized.taskSessionId}`,
        errorCode: "TASK_STATE_CORRUPT",
        taskSessionId: sanitized.taskSessionId,
      };
    }
    return { ok: false, error: `Unknown task session: ${sanitized.taskSessionId}` };
  }
  if (String(state.taskSessionId || "").trim() !== sanitized.taskSessionId) {
    return {
      ok: false,
      error: `Task state identity mismatch: ${sanitized.taskSessionId}`,
      errorCode: "TASK_STATE_ID_MISMATCH",
      taskSessionId: sanitized.taskSessionId,
    };
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
  const continuity = state.continuity && typeof state.continuity === "object"
    ? state.continuity
    : null;
  if (continuity) {
    const lease = continuity.lease && typeof continuity.lease === "object"
      ? continuity.lease
      : null;
    if (lease) {
      const expiresAt = Date.parse(String(lease.expiresAt || ""));
      if (
        String(lease.status || "") !== "active"
        || !Number.isFinite(expiresAt)
        || expiresAt <= Date.now()
      ) {
        return {
          ok: false,
          error: "Task continuity lease is inactive or expired; heartbeat/recovery is required.",
          errorCode: "TASK_LEASE_EXPIRED",
          taskSessionId: sanitized.taskSessionId,
        };
      }
    }
    const recovery = continuity.recovery && typeof continuity.recovery === "object"
      ? continuity.recovery
      : {};
    const conflicts = Array.isArray(recovery.conflicts) ? recovery.conflicts : [];
    if (
      String(recovery.status || "") === "blocked_by_checkpoint_conflict"
      || conflicts.length > 0
    ) {
      return {
        ok: false,
        error: "Task checkpoint conflicts with current files; recover or explicitly rebase first.",
        errorCode: "TASK_CHECKPOINT_CONFLICT",
        taskSessionId: sanitized.taskSessionId,
        conflicts,
      };
    }
  }
  const activeJobId = String(state.activeJobId || "").trim();
  if (activeJobId) {
    return {
      ok: false,
      error: `Task has an active background job: ${activeJobId}`,
      errorCode: "TASK_JOB_IN_PROGRESS",
      taskSessionId: sanitized.taskSessionId,
      activeJobId,
    };
  }
  const writeGate = state.writeGate;
  const writesAllowed = writeGate && typeof writeGate === "object"
    ? writeGate.writesAllowed
    : (writeGate !== undefined ? writeGate : state.writesAllowed);
  if (writesAllowed !== true && writesAllowed !== "true") {
    return { ok: false, error: "Task writeGate denies writes", errorCode: "WRITE_GATE_DENIED" };
  }
  const gateValidation = validateCompletedGates(state, args);
  if (!gateValidation.ok) {
    return {
      ...gateValidation,
      taskSessionId: sanitized.taskSessionId,
    };
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
  validateCompletedGates,
  requestedMutationPaths,
  requiredFields,
};

"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { atomicWriteJson } = require("./atomic-io");
const { absolutePathIdentity } = require("./filesystem-path-identity");
const { resolveAgentStateRoot, taskStateDir } = require("./state-root");
const {
  tryAcquireCrossProcessLock,
  releaseCrossProcessLock,
} = require("./write-locks");

const STORE_SCHEMA_VERSION = 1;
const MAX_PERSISTED_BYTES = 256 * 1024;

function taskSessionIdFrom(value = {}) {
  const source = value && typeof value === "object" ? value : {};
  const authorization = source.taskAuthorization && typeof source.taskAuthorization === "object"
    ? source.taskAuthorization
    : source.task_authorization && typeof source.task_authorization === "object"
      ? source.task_authorization
      : {};
  return String(
    source.taskSessionId
    || source.task_session_id
    || authorization.taskSessionId
    || authorization.task_session_id
    || ""
  ).trim();
}

function normalizeGuardScope(value = {}) {
  const source = value && typeof value === "object" ? value : {};
  const taskSessionId = taskSessionIdFrom(source);
  const projectRoot = String(source.projectRoot || source.project_root || "").trim();
  const hostPlatform = String(source.hostPlatform || source.host_platform || process.platform);
  const parsedGeneration = Number(
    source.mutationGeneration ?? source.mutation_generation ?? Number.NaN
  );
  if (!taskSessionId || !projectRoot || !Number.isFinite(parsedGeneration)) return null;
  const mutationGeneration = Math.max(0, Math.floor(parsedGeneration));
  return {
    taskSessionId,
    projectRoot: absolutePathIdentity(projectRoot, hostPlatform),
    mutationGeneration,
    hostPlatform,
  };
}

function scopeIdentity(scope) {
  if (!scope) return "legacy";
  return crypto.createHash("sha256").update(JSON.stringify([
    scope.taskSessionId,
    scope.projectRoot,
    scope.mutationGeneration,
  ])).digest("hex");
}

function guardStatePath(component, scope, stateRoot = resolveAgentStateRoot()) {
  if (!scope) return "";
  const taskDir = taskStateDir(scope.taskSessionId, stateRoot);
  const safeComponent = String(component || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-");
  if (!safeComponent) throw new Error("durable guard component is required");
  const projectGenerationHash = crypto.createHash("sha256").update(JSON.stringify([
    scope.projectRoot,
    scope.mutationGeneration,
  ])).digest("hex").slice(0, 24);
  return path.join(taskDir, "guards", `${safeComponent}-${projectGenerationHash}.json`);
}

function loadGuardState(component, scopeValue, options = {}) {
  const scope = normalizeGuardScope(scopeValue);
  if (!scope || options.durable === false) return null;
  const target = guardStatePath(component, scope, options.stateRoot);
  try {
    const stat = fs.statSync(target);
    if (!stat.isFile() || stat.size > MAX_PERSISTED_BYTES) return null;
    const payload = JSON.parse(fs.readFileSync(target, "utf8"));
    if (
      Number(payload?.schemaVersion || 0) !== STORE_SCHEMA_VERSION
      || String(payload?.component || "") !== String(component || "")
      || scopeIdentity(payload?.scope) !== scopeIdentity(scope)
      || !payload.state
      || typeof payload.state !== "object"
    ) {
      return null;
    }
    return payload.state;
  } catch {
    return null;
  }
}

function saveGuardState(component, scopeValue, state, options = {}) {
  const scope = normalizeGuardScope(scopeValue);
  if (!scope || options.durable === false) return { persisted: false, reason: "scope_incomplete" };
  const stateRoot = path.resolve(options.stateRoot || resolveAgentStateRoot());
  const target = guardStatePath(component, scope, stateRoot);
  const payload = {
    schemaVersion: STORE_SCHEMA_VERSION,
    component: String(component || ""),
    scope: {
      taskSessionId: scope.taskSessionId,
      projectRoot: scope.projectRoot,
      mutationGeneration: scope.mutationGeneration,
      hostPlatform: scope.hostPlatform,
    },
    state,
  };
  const serialized = `${JSON.stringify(payload, null, 2)}\n`;
  if (Buffer.byteLength(serialized, "utf8") > MAX_PERSISTED_BYTES) {
    return { persisted: false, reason: "state_too_large" };
  }
  const acquired = tryAcquireCrossProcessLock(target, "durable-guard", stateRoot);
  if (!acquired.ok) return { persisted: false, reason: "state_locked" };
  try {
    atomicWriteJson(target, payload);
    return { persisted: true, path: target };
  } finally {
    releaseCrossProcessLock(target);
  }
}

function deleteGuardState(component, scopeValue, options = {}) {
  const scope = normalizeGuardScope(scopeValue);
  if (!scope) return false;
  const target = guardStatePath(component, scope, options.stateRoot);
  try {
    fs.unlinkSync(target);
    return true;
  } catch {
    return false;
  }
}

module.exports = {
  STORE_SCHEMA_VERSION,
  MAX_PERSISTED_BYTES,
  taskSessionIdFrom,
  normalizeGuardScope,
  scopeIdentity,
  guardStatePath,
  loadGuardState,
  saveGuardState,
  deleteGuardState,
};

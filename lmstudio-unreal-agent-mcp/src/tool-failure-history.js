"use strict";

// Duplicate tool-failure loop breaker for read/search tools.
//
// Guards against models retrying the same tool call after consecutive identical
// internal failures (e.g. read_file_range crashing with ReferenceError).

const crypto = require("crypto");
const path = require("path");
const {
  deleteGuardState,
  loadGuardState,
  normalizeGuardScope,
  saveGuardState,
  scopeIdentity,
  taskSessionIdFrom,
} = require("./durable-guard-store");
const { resolveAgentStateRoot } = require("./state-root");

const DEFAULT_MAX_ENTRIES = 50;
const DEFAULT_TTL_MS = 15 * 60 * 1000;

// scoped callKey -> { count, at, lastSeq, tool, errorCode, scopeKey }
const entries = new Map();
const sequences = new Map();
const loadedScopes = new Set();
const COMPONENT = "tool-failure-history";

function operationScope(args = {}, options = {}) {
  const combined = {
    ...(args && typeof args === "object" ? args : {}),
    ...(options && typeof options === "object" ? options : {}),
  };
  const taskSessionId = taskSessionIdFrom(options) || taskSessionIdFrom(args);
  if (taskSessionId) combined.taskSessionId = taskSessionId;
  const scope = normalizeGuardScope(combined);
  if (!scope) return { scope: null, key: "legacy", storageKey: "legacy", options };
  const stateRoot = path.resolve(options.stateRoot || resolveAgentStateRoot());
  const key = scopeIdentity(scope);
  return { scope, key, storageKey: `${stateRoot}:${key}`, options: { ...options, stateRoot } };
}

function ensureScopeLoaded(context) {
  if (!context.scope || loadedScopes.has(context.storageKey)) return;
  loadedScopes.add(context.storageKey);
  const saved = loadGuardState(COMPONENT, context.scope, context.options);
  if (!saved || !Array.isArray(saved.entries)) return;
  sequences.set(context.storageKey, Math.max(0, Number(saved.sequence || 0)));
  for (const row of saved.entries.slice(-DEFAULT_MAX_ENTRIES)) {
    if (!row || typeof row.key !== "string" || !row.value || typeof row.value !== "object") continue;
    entries.set(`${context.storageKey}:${row.key}`, {
      count: Math.max(1, Number(row.value.count || 1)),
      at: Math.max(0, Number(row.value.at || 0)),
      lastSeq: Math.max(0, Number(row.value.lastSeq || 0)),
      tool: String(row.value.tool || ""),
      errorCode: String(row.value.errorCode || ""),
      scopeKey: context.storageKey,
    });
  }
}

function persistScope(context) {
  if (!context.scope) return { persisted: false, reason: "scope_incomplete" };
  const rows = [];
  for (const [key, value] of entries) {
    if (value.scopeKey !== context.storageKey) continue;
    rows.push({
      key: key.slice(context.storageKey.length + 1),
      value: {
        count: value.count,
        at: value.at,
        lastSeq: value.lastSeq,
        tool: value.tool,
        errorCode: value.errorCode,
      },
    });
  }
  rows.sort((left, right) => Number(left.value.at || 0) - Number(right.value.at || 0));
  return saveGuardState(COMPONENT, context.scope, {
    sequence: Number(sequences.get(context.storageKey) || 0),
    entries: rows.slice(-DEFAULT_MAX_ENTRIES),
  }, context.options);
}

function stableStringify(value) {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableStringify(item)).join(",")}]`;
  }
  const keys = Object.keys(value).sort();
  return `{${keys.map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
}

const READ_FAILURE_NORMALIZE_TOOLS = new Set([
  "read_file",
  "read_file_range",
  "read_symbol",
  "search_files",
]);

function readFailureOwnerKey(args = {}) {
  const source = args && typeof args === "object" ? args : {};
  const authorization = source.taskAuthorization && typeof source.taskAuthorization === "object"
    ? source.taskAuthorization
    : {};
  const taskSessionId = String(
    authorization.taskSessionId
    || authorization.task_session_id
    || source.taskSessionId
    || source.task_session_id
    || ""
  ).trim();
  if (taskSessionId) return `task:${taskSessionId}`;
  const evidenceSessionId = String(
    source.evidenceSessionId
    || source.evidence_session_id
    || source.sessionId
    || source.session_id
    || ""
  ).trim();
  return `evidence:${evidenceSessionId}`;
}

function callKey(tool, args) {
  const hash = crypto.createHash("sha256");
  const name = String(tool || "");
  hash.update(name);
  hash.update("\u0000");
  if (READ_FAILURE_NORMALIZE_TOOLS.has(name)) {
    hash.update(readFailureOwnerKey(args));
    hash.update("\u0000");
  }
  hash.update(stableStringify(normalizeArgsForFailureKey(tool, args)));
  return hash.digest("hex");
}

/**
 * Align failure-repeat keys with read-history normalization for evidence tools.
 */
function normalizeArgsForFailureKey(tool, args) {
  const name = String(tool || "");
  if (!READ_FAILURE_NORMALIZE_TOOLS.has(name)) {
    return args || {};
  }
  // Lazy require avoids circular init with tool-read-history (which imports stableStringify).
  const { normalizeReadToolArgs } = require("./tool-read-history");
  try {
    return normalizeReadToolArgs(name, args || {});
  } catch {
    return args || {};
  }
}

function prune(now, maxEntries, ttlMs, scopeKey = "legacy") {
  for (const [key, value] of entries) {
    if (value.scopeKey === scopeKey && now - value.at > ttlMs) entries.delete(key);
  }
  while ([...entries.values()].filter((value) => value.scopeKey === scopeKey).length > maxEntries) {
    let oldestKey = null;
    let oldestAt = Infinity;
    for (const [key, value] of entries) {
      if (value.scopeKey !== scopeKey) continue;
      if (value.at < oldestAt) {
        oldestAt = value.at;
        oldestKey = key;
      }
    }
    if (oldestKey === null) break;
    entries.delete(oldestKey);
  }
}

/**
 * Advance the global call sequence and return the prior value.
 */
function beginToolCall(options = {}) {
  const context = operationScope({}, options);
  ensureScopeLoaded(context);
  const priorSeq = Number(sequences.get(context.storageKey) || 0);
  sequences.set(context.storageKey, priorSeq + 1);
  persistScope(context);
  return priorSeq;
}

/**
 * Check whether this tool call should be blocked before running the handler.
 */
function checkToolRepeatBlocked(tool, args, priorSeq, options = {}) {
  const context = operationScope(args, options);
  ensureScopeLoaded(context);
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const maxEntries = Number.isFinite(options.maxEntries) ? options.maxEntries : DEFAULT_MAX_ENTRIES;
  const ttlMs = Number.isFinite(options.ttlMs) ? options.ttlMs : DEFAULT_TTL_MS;
  prune(now, maxEntries, ttlMs, context.storageKey);

  const key = `${context.storageKey}:${callKey(tool, args)}`;
  const prior = entries.get(key);
  if (!prior) return { blocked: false, consecutive: false, attempts: 0 };

  const consecutive = prior.lastSeq === priorSeq;
  if (consecutive) {
    return { blocked: true, consecutive: true, attempts: prior.count + 1, tool: prior.tool };
  }
  if (prior.count >= 2) {
    return { blocked: true, consecutive: false, attempts: prior.count + 1, tool: prior.tool };
  }
  return { blocked: false, consecutive: false, attempts: prior.count };
}

/**
 * Record an internal tool failure after handler execution.
 */
function recordToolFailure(tool, args, errorCode, options = {}) {
  const context = operationScope(args, options);
  ensureScopeLoaded(context);
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const maxEntries = Number.isFinite(options.maxEntries) ? options.maxEntries : DEFAULT_MAX_ENTRIES;
  const ttlMs = Number.isFinite(options.ttlMs) ? options.ttlMs : DEFAULT_TTL_MS;
  prune(now, maxEntries, ttlMs, context.storageKey);

  const key = `${context.storageKey}:${callKey(tool, args)}`;
  const prior = entries.get(key);
  if (!prior) {
    entries.set(key, {
      count: 1,
      at: now,
      lastSeq: Number(sequences.get(context.storageKey) || 0),
      tool: String(tool || ""),
      errorCode: String(errorCode || ""),
      scopeKey: context.storageKey,
    });
    const persistence = persistScope(context);
    return { recorded: true, attempts: 1, persistence };
  }
  prior.count += 1;
  prior.at = now;
  prior.lastSeq = Number(sequences.get(context.storageKey) || 0);
  prior.errorCode = String(errorCode || "");
  const persistence = persistScope(context);
  return { recorded: true, attempts: prior.count, persistence };
}

function toolRepeatBlockedMessage(tool, status) {
  const attempt = status && status.attempts ? ` (attempt ${status.attempts})` : "";
  return (
    `identical ${tool} call failed internally${attempt}. `
    + "Do NOT retry this call with the same arguments. "
    + "Stop the current workflow and report the MCP internal error to the user."
  );
}

function clearToolFailureHistory(options = {}) {
  const context = operationScope({}, options);
  if (context.scope) {
    for (const [key, value] of entries) {
      if (value.scopeKey === context.storageKey) entries.delete(key);
    }
    sequences.delete(context.storageKey);
    loadedScopes.delete(context.storageKey);
    if (options.preserveDurable !== true) {
      deleteGuardState(COMPONENT, context.scope, context.options);
    }
  } else {
    entries.clear();
    sequences.clear();
    loadedScopes.clear();
  }
}

function toolFailureHistorySize() {
  return entries.size;
}

function exportToolFailureHistory(options = {}) {
  const context = operationScope({}, options);
  ensureScopeLoaded(context);
  return {
    sequence: Number(sequences.get(context.storageKey) || 0),
    entries: [...entries.entries()]
      .filter(([, value]) => value.scopeKey === context.storageKey)
      .map(([key, value]) => ({
        key: key.slice(context.storageKey.length + 1),
        value: {
          count: value.count,
          at: value.at,
          lastSeq: value.lastSeq,
          tool: value.tool,
          errorCode: value.errorCode,
        },
      })),
  };
}

function importToolFailureHistory(snapshot, options = {}) {
  const context = operationScope({}, options);
  if (!context.scope || !snapshot || typeof snapshot !== "object") return false;
  for (const [key, value] of entries) {
    if (value.scopeKey === context.storageKey) entries.delete(key);
  }
  sequences.set(context.storageKey, Math.max(0, Number(snapshot.sequence || 0)));
  for (const row of (Array.isArray(snapshot.entries) ? snapshot.entries : []).slice(-DEFAULT_MAX_ENTRIES)) {
    if (!row || typeof row.key !== "string" || !row.value || typeof row.value !== "object") continue;
    entries.set(`${context.storageKey}:${row.key}`, {
      count: Math.max(1, Number(row.value.count || 1)),
      at: Math.max(0, Number(row.value.at || 0)),
      lastSeq: Math.max(0, Number(row.value.lastSeq || 0)),
      tool: String(row.value.tool || ""),
      errorCode: String(row.value.errorCode || ""),
      scopeKey: context.storageKey,
    });
  }
  loadedScopes.add(context.storageKey);
  persistScope(context);
  return true;
}

module.exports = {
  beginToolCall,
  checkToolRepeatBlocked,
  recordToolFailure,
  toolRepeatBlockedMessage,
  clearToolFailureHistory,
  toolFailureHistorySize,
  exportToolFailureHistory,
  importToolFailureHistory,
  stableStringify,
  DEFAULT_MAX_ENTRIES,
  DEFAULT_TTL_MS,
};

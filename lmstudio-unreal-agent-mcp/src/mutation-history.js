"use strict";

// Duplicate-mutation loop breaker.
//
// Failure signature this guards against (observed in real sessions): a build or
// validation error makes the model retry the exact same write_file /
// replace_in_file call over and over without re-reading the file. Each repeat
// adds a failed tool result to context; past ~50-60K tokens tool-call generation
// itself degrades and the session dies.
//
// Policy (balances loop-breaking against legitimate retries):
// - A byte-identical call repeated CONSECUTIVELY (no other mutation in between)
//   is rejected on the 2nd attempt -- this is the classic stuck loop.
// - A byte-identical call repeated NON-consecutively is allowed once (e.g.
//   "write Foo.cpp -> validation fails on missing Foo.h -> write Foo.h ->
//   retry identical Foo.cpp" is a valid flow) and rejected from the 3rd
//   attempt within the TTL window.
// - Prefer checkMutationDuplicate + recordMutation(after success) so failed
//   oldText/occurrence mismatches do not poison the duplicate counter.

const crypto = require("crypto");
const path = require("path");
const {
  deleteGuardState,
  loadGuardState,
  normalizeGuardScope,
  saveGuardState,
  scopeIdentity,
} = require("./durable-guard-store");
const { resolveAgentStateRoot } = require("./state-root");

const DEFAULT_MAX_ENTRIES = 30;
const DEFAULT_TTL_MS = 15 * 60 * 1000;

// scoped hash -> { count, at, lastSeq, tool, scopeKey }
const entries = new Map();
const sequences = new Map();
const loadedScopes = new Set();
const COMPONENT = "mutation-history";

function operationScope(options = {}) {
  const scope = normalizeGuardScope(options);
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
      },
    });
  }
  rows.sort((left, right) => Number(left.value.at || 0) - Number(right.value.at || 0));
  return saveGuardState(COMPONENT, context.scope, {
    sequence: Number(sequences.get(context.storageKey) || 0),
    entries: rows.slice(-DEFAULT_MAX_ENTRIES),
  }, context.options);
}

function mutationHash(tool, absPath, payload) {
  const hash = crypto.createHash("sha256");
  hash.update(String(tool || ""));
  hash.update("\u0000");
  hash.update(String(absPath || ""));
  hash.update("\u0000");
  hash.update(String(payload || ""));
  return hash.digest("hex");
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
 * Peek whether this mutation would be treated as a pathological repeat.
 * Does not mutate history.
 */
function checkMutationDuplicate(tool, absPath, payload, options = {}) {
  const context = operationScope(options);
  ensureScopeLoaded(context);
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const maxEntries = Number.isFinite(options.maxEntries) ? options.maxEntries : DEFAULT_MAX_ENTRIES;
  const ttlMs = Number.isFinite(options.ttlMs) ? options.ttlMs : DEFAULT_TTL_MS;
  prune(now, maxEntries, ttlMs, context.storageKey);

  const mutationKey = mutationHash(tool, absPath, payload);
  const key = `${context.storageKey}:${mutationKey}`;
  const prior = entries.get(key);
  if (!prior) {
    return { duplicate: false, consecutive: false, attempts: 0, key: mutationKey };
  }
  const consecutive = prior.lastSeq === Number(sequences.get(context.storageKey) || 0);
  const nextCount = prior.count + 1;
  if (consecutive) {
    return { duplicate: true, consecutive: true, attempts: nextCount, key: mutationKey };
  }
  if (nextCount >= 3) {
    return { duplicate: true, consecutive: false, attempts: nextCount, key: mutationKey };
  }
  return { duplicate: false, consecutive: false, attempts: prior.count, key: mutationKey };
}

/**
 * Record a successful mutation after the write landed.
 */
function recordMutation(tool, absPath, payload, options = {}) {
  const context = operationScope(options);
  ensureScopeLoaded(context);
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const maxEntries = Number.isFinite(options.maxEntries) ? options.maxEntries : DEFAULT_MAX_ENTRIES;
  const ttlMs = Number.isFinite(options.ttlMs) ? options.ttlMs : DEFAULT_TTL_MS;
  prune(now, maxEntries, ttlMs, context.storageKey);

  const key = `${context.storageKey}:${mutationHash(tool, absPath, payload)}`;
  const prior = entries.get(key);
  const nextSequence = Number(sequences.get(context.storageKey) || 0) + 1;
  sequences.set(context.storageKey, nextSequence);
  if (!prior) {
    entries.set(key, {
      count: 1,
      at: now,
      lastSeq: nextSequence,
      tool: String(tool || ""),
      scopeKey: context.storageKey,
    });
    const persistence = persistScope(context);
    return { recorded: true, attempts: 1, persistence };
  }
  prior.count += 1;
  prior.at = now;
  prior.lastSeq = nextSequence;
  const persistence = persistScope(context);
  return { recorded: true, attempts: prior.count, persistence };
}

/**
 * Check + record in one step (legacy). Prefer check then record-after-success.
 */
function checkAndRecordMutation(tool, absPath, payload, options = {}) {
  const peek = checkMutationDuplicate(tool, absPath, payload, options);
  if (peek.duplicate) {
    return peek;
  }
  const recorded = recordMutation(tool, absPath, payload, options);
  return {
    duplicate: false,
    consecutive: false,
    attempts: recorded.attempts,
  };
}

function duplicateMutationMessage(tool, relPath, status) {
  const attempt = status && status.attempts ? ` (attempt ${status.attempts})` : "";
  return (
    `identical ${tool} call already attempted on ${relPath}${attempt}. `
    + "The file has not changed the way you expect. Do NOT repeat this call. "
    + "Use read_file to verify the current file state first. "
    + "If you are looping on a failing edit, stop and summarize the situation for the user instead of retrying."
  );
}

function clearMutationHistory(options = {}) {
  const context = operationScope(options);
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

function mutationHistorySize() {
  return entries.size;
}

function exportMutationHistory(options = {}) {
  const context = operationScope(options);
  ensureScopeLoaded(context);
  return {
    sequence: Number(sequences.get(context.storageKey) || 0),
    entries: [...entries.entries()]
      .filter(([, value]) => value.scopeKey === context.storageKey)
      .map(([key, value]) => ({
        key: key.slice(context.storageKey.length + 1),
        value: { count: value.count, at: value.at, lastSeq: value.lastSeq, tool: value.tool },
      })),
  };
}

function importMutationHistory(snapshot, options = {}) {
  const context = operationScope(options);
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
      scopeKey: context.storageKey,
    });
  }
  loadedScopes.add(context.storageKey);
  persistScope(context);
  return true;
}

module.exports = {
  checkAndRecordMutation,
  checkMutationDuplicate,
  recordMutation,
  duplicateMutationMessage,
  clearMutationHistory,
  mutationHistorySize,
  exportMutationHistory,
  importMutationHistory,
  DEFAULT_MAX_ENTRIES,
  DEFAULT_TTL_MS,
};

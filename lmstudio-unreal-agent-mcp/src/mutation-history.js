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

const DEFAULT_MAX_ENTRIES = 30;
const DEFAULT_TTL_MS = 15 * 60 * 1000;

// hash -> { count, at, lastSeq, tool, relPath }
const entries = new Map();
let globalSeq = 0;

function mutationHash(tool, absPath, payload) {
  const hash = crypto.createHash("sha256");
  hash.update(String(tool || ""));
  hash.update("\u0000");
  hash.update(String(absPath || ""));
  hash.update("\u0000");
  hash.update(String(payload || ""));
  return hash.digest("hex");
}

function prune(now, maxEntries, ttlMs) {
  for (const [key, value] of entries) {
    if (now - value.at > ttlMs) entries.delete(key);
  }
  while (entries.size > maxEntries) {
    let oldestKey = null;
    let oldestAt = Infinity;
    for (const [key, value] of entries) {
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
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const maxEntries = Number.isFinite(options.maxEntries) ? options.maxEntries : DEFAULT_MAX_ENTRIES;
  const ttlMs = Number.isFinite(options.ttlMs) ? options.ttlMs : DEFAULT_TTL_MS;
  prune(now, maxEntries, ttlMs);

  const key = mutationHash(tool, absPath, payload);
  const prior = entries.get(key);
  if (!prior) {
    return { duplicate: false, consecutive: false, attempts: 0, key };
  }
  const consecutive = prior.lastSeq === globalSeq;
  const nextCount = prior.count + 1;
  if (consecutive) {
    return { duplicate: true, consecutive: true, attempts: nextCount, key };
  }
  if (nextCount >= 3) {
    return { duplicate: true, consecutive: false, attempts: nextCount, key };
  }
  return { duplicate: false, consecutive: false, attempts: prior.count, key };
}

/**
 * Record a successful mutation after the write landed.
 */
function recordMutation(tool, absPath, payload, options = {}) {
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const maxEntries = Number.isFinite(options.maxEntries) ? options.maxEntries : DEFAULT_MAX_ENTRIES;
  const ttlMs = Number.isFinite(options.ttlMs) ? options.ttlMs : DEFAULT_TTL_MS;
  prune(now, maxEntries, ttlMs);

  const key = mutationHash(tool, absPath, payload);
  const prior = entries.get(key);
  globalSeq += 1;
  if (!prior) {
    entries.set(key, { count: 1, at: now, lastSeq: globalSeq, tool: String(tool || "") });
    return { recorded: true, attempts: 1 };
  }
  prior.count += 1;
  prior.at = now;
  prior.lastSeq = globalSeq;
  return { recorded: true, attempts: prior.count };
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

function clearMutationHistory() {
  entries.clear();
  globalSeq = 0;
}

function mutationHistorySize() {
  return entries.size;
}

module.exports = {
  checkAndRecordMutation,
  checkMutationDuplicate,
  recordMutation,
  duplicateMutationMessage,
  clearMutationHistory,
  mutationHistorySize,
  DEFAULT_MAX_ENTRIES,
  DEFAULT_TTL_MS,
};

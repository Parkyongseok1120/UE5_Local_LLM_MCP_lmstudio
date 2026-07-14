"use strict";

// Duplicate tool-failure loop breaker for read/search tools.
//
// Guards against models retrying the same tool call after consecutive identical
// internal failures (e.g. read_file_range crashing with ReferenceError).

const crypto = require("crypto");

const DEFAULT_MAX_ENTRIES = 50;
const DEFAULT_TTL_MS = 15 * 60 * 1000;

// callKey -> { count, at, lastSeq, tool, errorCode }
const entries = new Map();
let globalSeq = 0;

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

function callKey(tool, args) {
  const hash = crypto.createHash("sha256");
  hash.update(String(tool || ""));
  hash.update("\u0000");
  hash.update(stableStringify(args || {}));
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
 * Advance the global call sequence and return the prior value.
 */
function beginToolCall() {
  const priorSeq = globalSeq;
  globalSeq += 1;
  return priorSeq;
}

/**
 * Check whether this tool call should be blocked before running the handler.
 */
function checkToolRepeatBlocked(tool, args, priorSeq, options = {}) {
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const maxEntries = Number.isFinite(options.maxEntries) ? options.maxEntries : DEFAULT_MAX_ENTRIES;
  const ttlMs = Number.isFinite(options.ttlMs) ? options.ttlMs : DEFAULT_TTL_MS;
  prune(now, maxEntries, ttlMs);

  const key = callKey(tool, args);
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
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const maxEntries = Number.isFinite(options.maxEntries) ? options.maxEntries : DEFAULT_MAX_ENTRIES;
  const ttlMs = Number.isFinite(options.ttlMs) ? options.ttlMs : DEFAULT_TTL_MS;
  prune(now, maxEntries, ttlMs);

  const key = callKey(tool, args);
  const prior = entries.get(key);
  if (!prior) {
    entries.set(key, {
      count: 1,
      at: now,
      lastSeq: globalSeq,
      tool: String(tool || ""),
      errorCode: String(errorCode || "")
    });
    return { recorded: true, attempts: 1 };
  }
  prior.count += 1;
  prior.at = now;
  prior.lastSeq = globalSeq;
  prior.errorCode = String(errorCode || "");
  return { recorded: true, attempts: prior.count };
}

function toolRepeatBlockedMessage(tool, status) {
  const attempt = status && status.attempts ? ` (attempt ${status.attempts})` : "";
  return (
    `identical ${tool} call failed internally${attempt}. `
    + "Do NOT retry this call with the same arguments. "
    + "Stop the current workflow and report the MCP internal error to the user."
  );
}

function clearToolFailureHistory() {
  entries.clear();
  globalSeq = 0;
}

function toolFailureHistorySize() {
  return entries.size;
}

module.exports = {
  beginToolCall,
  checkToolRepeatBlocked,
  recordToolFailure,
  toolRepeatBlockedMessage,
  clearToolFailureHistory,
  toolFailureHistorySize,
  stableStringify,
  DEFAULT_MAX_ENTRIES,
  DEFAULT_TTL_MS,
};

"use strict";

// Success read/search repeat guard.
//
// Records successful read evidence tool calls so identical unchanged reads return
// cached content instead of letting the model loop on the same file range.

const crypto = require("crypto");
const { stableStringify } = require("./tool-failure-history");

const READ_EVIDENCE_TOOLS = new Set([
  "read_file",
  "read_file_range",
  "read_symbol",
  "search_files",
]);

const DEFAULT_MAX_ENTRIES = 120;
const DEFAULT_TTL_MS = 30 * 60 * 1000;
const DEFAULT_FILE_READ_BUDGET = 4;
const RECENT_KEY_WINDOW = 12;

// evidenceKey -> { content, at, tool, attempts, evidenceHash, fileAbsPath }
const successCache = new Map();
// fileVersionKey -> { count, lastContent, lastKey }
const fileReadBudget = new Map();
const recentKeys = [];

function evidenceContextKey(context = {}) {
  const hash = crypto.createHash("sha256");
  hash.update(String(context.fileSignature || context.scopeSignature || ""));
  hash.update("\u0000");
  hash.update(String(context.mutationGeneration ?? 0));
  return hash.digest("hex").slice(0, 24);
}

function buildEvidenceKey(tool, args, context = {}) {
  const hash = crypto.createHash("sha256");
  hash.update(String(tool || ""));
  hash.update("\u0000");
  hash.update(stableStringify(args || {}));
  hash.update("\u0000");
  hash.update(evidenceContextKey(context));
  return hash.digest("hex");
}

function fileVersionKey(context = {}) {
  if (!context.fileAbsPath || !context.fileSignature) return null;
  return `${context.fileAbsPath}\u0000${context.fileSignature}\u0000${context.mutationGeneration ?? 0}`;
}

function prune(now, maxEntries, ttlMs) {
  for (const [key, value] of successCache) {
    if (now - value.at > ttlMs) successCache.delete(key);
  }
  while (successCache.size > maxEntries) {
    let oldestKey = null;
    let oldestAt = Infinity;
    for (const [key, value] of successCache) {
      if (value.at < oldestAt) {
        oldestAt = value.at;
        oldestKey = key;
      }
    }
    if (oldestKey === null) break;
    successCache.delete(oldestKey);
  }
}

function detectPingPong(key) {
  if (recentKeys.length < 3) return false;
  const tail = recentKeys.slice(-4);
  if (tail.length >= 3 && tail[0] === tail[2] && tail[0] !== tail[1]) {
    return tail.includes(key);
  }
  if (tail.length === 4 && tail[0] === tail[2] && tail[1] === tail[3] && tail[0] !== tail[1]) {
    return tail.includes(key);
  }
  return false;
}

function findLastCachedForFile(context = {}) {
  const versionKey = fileVersionKey(context);
  if (!versionKey) return null;
  const budget = fileReadBudget.get(versionKey);
  return budget && budget.lastContent ? budget.lastContent : null;
}

/**
 * Check whether this read should return cached evidence instead of re-reading disk.
 */
function checkReadRepeat(tool, args, context = {}, options = {}) {
  if (!READ_EVIDENCE_TOOLS.has(tool)) return { repeat: false };
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const maxEntries = Number.isFinite(options.maxEntries) ? options.maxEntries : DEFAULT_MAX_ENTRIES;
  const ttlMs = Number.isFinite(options.ttlMs) ? options.ttlMs : DEFAULT_TTL_MS;
  prune(now, maxEntries, ttlMs);

  const key = buildEvidenceKey(tool, args, context);
  const prior = successCache.get(key);
  if (prior) {
    return {
      repeat: true,
      reason: "READ_REPEAT_DETECTED",
      key,
      cachedContent: prior.content,
      attempts: prior.attempts + 1,
      firstReadAt: prior.at,
    };
  }

  if (detectPingPong(key)) {
    const cachedContent = findLastCachedForFile(context);
    if (cachedContent) {
      return {
        repeat: true,
        reason: "EVIDENCE_STAGNATION",
        key,
        cachedContent,
        attempts: 2,
        pingPong: true,
      };
    }
  }

  const versionKey = fileVersionKey(context);
  if (versionKey) {
    const budget = fileReadBudget.get(versionKey);
    const fileReadBudgetLimit = Number.isFinite(options.fileReadBudget)
      ? options.fileReadBudget
      : DEFAULT_FILE_READ_BUDGET;
    if (budget && budget.count >= fileReadBudgetLimit) {
      const cachedContent = budget.lastContent || findLastCachedForFile(context);
      if (cachedContent) {
        return {
          repeat: true,
          reason: "EVIDENCE_STAGNATION",
          key,
          cachedContent,
          attempts: budget.count + 1,
          fileReadBudgetExceeded: true,
          readCount: budget.count,
        };
      }
    }
  }

  return { repeat: false, key };
}

/**
 * Record a successful read/search response for repeat detection.
 */
function recordReadSuccess(tool, args, context = {}, content, options = {}) {
  if (!READ_EVIDENCE_TOOLS.has(tool)) return { recorded: false };
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const maxEntries = Number.isFinite(options.maxEntries) ? options.maxEntries : DEFAULT_MAX_ENTRIES;
  const ttlMs = Number.isFinite(options.ttlMs) ? options.ttlMs : DEFAULT_TTL_MS;
  prune(now, maxEntries, ttlMs);

  const key = buildEvidenceKey(tool, args, context);
  const prior = successCache.get(key);
  const entry = {
    content: String(content ?? ""),
    at: prior ? prior.at : now,
    tool: String(tool || ""),
    attempts: prior ? prior.attempts + 1 : 1,
    evidenceHash: context.evidenceHash || null,
    fileAbsPath: context.fileAbsPath || null,
  };
  successCache.set(key, entry);

  const versionKey = fileVersionKey(context);
  if (versionKey) {
    const budget = fileReadBudget.get(versionKey) || { count: 0, lastContent: null, lastKey: null };
    budget.count += 1;
    budget.lastContent = entry.content;
    budget.lastKey = key;
    fileReadBudget.set(versionKey, budget);
  }

  recentKeys.push(key);
  while (recentKeys.length > RECENT_KEY_WINDOW) recentKeys.shift();

  return { recorded: true, key, attempts: entry.attempts };
}

function normalizeReadToolArgs(tool, args = {}) {
  const normalized = {};
  if (tool === "read_file") {
    normalized.path = String(args.path || "");
    if (args.detailLevel != null) normalized.detailLevel = String(args.detailLevel);
    if (args.maxBytes != null) normalized.maxBytes = Number(args.maxBytes);
    return normalized;
  }
  if (tool === "read_file_range") {
    const startLine = Math.max(1, Number(args.startLine || 1));
    const endLine = Math.max(startLine, Number(args.endLine || startLine));
    normalized.path = String(args.path || "");
    normalized.startLine = startLine;
    normalized.endLine = endLine;
    if (args.detailLevel != null) normalized.detailLevel = String(args.detailLevel);
    return normalized;
  }
  if (tool === "read_symbol") {
    normalized.path = String(args.path || "");
    normalized.symbol = String(args.symbol || "").trim();
    if (args.contextLines != null) normalized.contextLines = Number(args.contextLines);
    return normalized;
  }
  if (tool === "search_files") {
    normalized.query = String(args.query || "");
    if (args.path != null) normalized.path = String(args.path);
    if (args.regex != null) normalized.regex = Boolean(args.regex);
    if (args.maxResults != null) normalized.maxResults = Number(args.maxResults);
    return normalized;
  }
  return { ...args };
}

function cachedReadInstruction(reason) {
  if (reason === "EVIDENCE_STAGNATION") {
    return (
      "Enough evidence was already collected from this unchanged file. "
      + "Stop reading and produce your final analysis from existing evidence."
    );
  }
  return (
    "The same unchanged evidence was already returned. Do not read it again. "
    + "Finish the analysis from existing evidence."
  );
}

function clearReadSuccessHistory() {
  successCache.clear();
  fileReadBudget.clear();
  recentKeys.length = 0;
}

function readSuccessHistorySize() {
  return successCache.size;
}

module.exports = {
  READ_EVIDENCE_TOOLS,
  buildEvidenceKey,
  checkReadRepeat,
  recordReadSuccess,
  normalizeReadToolArgs,
  cachedReadInstruction,
  clearReadSuccessHistory,
  readSuccessHistorySize,
  findLastCachedForFile,
  DEFAULT_MAX_ENTRIES,
  DEFAULT_TTL_MS,
  DEFAULT_FILE_READ_BUDGET,
};

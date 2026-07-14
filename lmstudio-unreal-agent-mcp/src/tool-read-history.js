"use strict";

// Success read/search repeat guard.
//
// READ_REPEAT_DETECTED: identical (or fully covered) evidence — return cached content.
// EVIDENCE_STAGNATION: no new line coverage / ping-pong — hard error, no code body.
// New line ranges are always allowed regardless of call count.

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
const RECENT_KEY_WINDOW = 12;
/** Soft cap for non-range tools (search_files / read_file / read_symbol) per file version. */
const DEFAULT_NON_RANGE_BUDGET = 8;

// evidenceKey -> { content, at, tool, attempts, lineRange }
const successCache = new Map();
// fileVersionKey -> { ranges: [{start,end}], nonRangeCount, stagnationCount, lastKey }
const fileCoverage = new Map();
const recentKeys = [];
// stagnationKey -> { count, at }
const stagnationEntries = new Map();

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
  for (const [key, value] of stagnationEntries) {
    if (now - value.at > ttlMs) stagnationEntries.delete(key);
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

function mergeRanges(ranges) {
  if (!ranges.length) return [];
  const sorted = ranges
    .map((r) => ({ start: Number(r.start), end: Number(r.end) }))
    .filter((r) => Number.isFinite(r.start) && Number.isFinite(r.end) && r.end >= r.start)
    .sort((a, b) => a.start - b.start || a.end - b.end);
  if (!sorted.length) return [];
  const merged = [{ ...sorted[0] }];
  for (let i = 1; i < sorted.length; i += 1) {
    const cur = sorted[i];
    const last = merged[merged.length - 1];
    if (cur.start <= last.end + 1) {
      last.end = Math.max(last.end, cur.end);
    } else {
      merged.push({ ...cur });
    }
  }
  return merged;
}

function lineRangeFromArgs(tool, args) {
  if (tool !== "read_file_range") return null;
  const start = Math.max(1, Number(args.startLine || 1));
  const end = Math.max(start, Number(args.endLine || start));
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  return { start, end };
}

/**
 * Count lines in `requested` that are not covered by any merged prior range.
 */
function novelLineCount(requested, priorRanges) {
  if (!requested) return Infinity;
  const merged = mergeRanges(priorRanges || []);
  let novel = 0;
  for (let line = requested.start; line <= requested.end; line += 1) {
    const covered = merged.some((r) => line >= r.start && line <= r.end);
    if (!covered) novel += 1;
  }
  return novel;
}

function isFullyCovered(requested, priorRanges) {
  return novelLineCount(requested, priorRanges) === 0;
}

function detectPingPong(key) {
  if (recentKeys.length < 3) return false;
  const tail = recentKeys.slice(-4);
  if (tail.length >= 3 && tail[0] === tail[2] && tail[0] !== tail[1]) {
    return key === tail[1] || key === tail[0];
  }
  if (tail.length === 4 && tail[0] === tail[2] && tail[1] === tail[3] && tail[0] !== tail[1]) {
    return key === tail[0] || key === tail[1];
  }
  return false;
}

function findCoveringCachedContent(requested, versionKey) {
  if (!requested || !versionKey) return null;
  for (const entry of successCache.values()) {
    if (!entry.lineRange) continue;
    if (entry.fileVersionKey !== versionKey) continue;
    const r = entry.lineRange;
    if (requested.start >= r.start && requested.end <= r.end) {
      return entry.content;
    }
  }
  return null;
}

/**
 * Check whether this read should return cached evidence or hard-stop.
 * @returns {{ action: 'allow'|'cache'|'stagnation', ... }}
 */
function checkReadRepeat(tool, args, context = {}, options = {}) {
  if (!READ_EVIDENCE_TOOLS.has(tool)) return { action: "allow", repeat: false };
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const maxEntries = Number.isFinite(options.maxEntries) ? options.maxEntries : DEFAULT_MAX_ENTRIES;
  const ttlMs = Number.isFinite(options.ttlMs) ? options.ttlMs : DEFAULT_TTL_MS;
  prune(now, maxEntries, ttlMs);

  const key = buildEvidenceKey(tool, args, context);
  const versionKey = fileVersionKey(context);
  const coverage = versionKey ? fileCoverage.get(versionKey) : null;
  const requested = lineRangeFromArgs(tool, args);

  // Escalating hard-stop: after a stagnation response was recorded, identical
  // retries escalate to EVIDENCE_STAGNATION_REPEAT (never return a prior code body).
  const stagnant = stagnationEntries.get(key);
  if (stagnant && stagnant.count >= 1) {
    return {
      action: "stagnation",
      repeat: true,
      reason: "EVIDENCE_STAGNATION_REPEAT",
      key,
      attempts: stagnant.count + 1,
      escalated: true,
    };
  }

  const prior = successCache.get(key);
  if (prior) {
    return {
      action: "cache",
      repeat: true,
      reason: "READ_REPEAT_DETECTED",
      key,
      cachedContent: prior.content,
      attempts: prior.attempts + 1,
      firstReadAt: prior.at,
    };
  }

  // New line coverage always allowed for read_file_range.
  if (tool === "read_file_range" && requested) {
    const priorRanges = coverage ? coverage.ranges : [];
    if (isFullyCovered(requested, priorRanges)) {
      const covering = findCoveringCachedContent(requested, versionKey);
      return {
        action: "cache",
        repeat: true,
        reason: "READ_REPEAT_DETECTED",
        key,
        cachedContent: covering || null,
        attempts: 2,
        fullyCovered: true,
        coveredBy: priorRanges,
      };
    }
    // Novel lines exist — never block on call count.
    return { action: "allow", repeat: false, key, novelLines: novelLineCount(requested, priorRanges) };
  }

  // Ping-pong between already-seen keys with no new evidence.
  if (detectPingPong(key) && coverage && coverage.ranges && coverage.ranges.length > 0) {
    return {
      action: "stagnation",
      repeat: true,
      reason: "EVIDENCE_STAGNATION",
      key,
      attempts: 2,
      pingPong: true,
    };
  }

  // Soft budget only for non-range tools on the same file version.
  if (tool !== "read_file_range" && versionKey && coverage) {
    const nonRangeBudget = Number.isFinite(options.nonRangeBudget)
      ? options.nonRangeBudget
      : DEFAULT_NON_RANGE_BUDGET;
    if (coverage.nonRangeCount >= nonRangeBudget) {
      return {
        action: "stagnation",
        repeat: true,
        reason: "EVIDENCE_STAGNATION",
        key,
        attempts: coverage.nonRangeCount + 1,
        readCount: coverage.nonRangeCount,
      };
    }
  }

  return { action: "allow", repeat: false, key };
}

/**
 * Record that a stagnation / hard-stop response was returned (updates state so
 * the next identical call can escalate).
 */
function recordReadStagnation(tool, args, context = {}, options = {}) {
  if (!READ_EVIDENCE_TOOLS.has(tool)) return { recorded: false };
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const key = buildEvidenceKey(tool, args, context);
  const prior = stagnationEntries.get(key);
  const count = prior ? prior.count + 1 : 1;
  stagnationEntries.set(key, { count, at: now, tool: String(tool || "") });

  const versionKey = fileVersionKey(context);
  if (versionKey) {
    const coverage = fileCoverage.get(versionKey) || {
      ranges: [],
      nonRangeCount: 0,
      stagnationCount: 0,
      lastKey: null,
    };
    coverage.stagnationCount = (coverage.stagnationCount || 0) + 1;
    coverage.lastKey = key;
    fileCoverage.set(versionKey, coverage);
  }

  recentKeys.push(key);
  while (recentKeys.length > RECENT_KEY_WINDOW) recentKeys.shift();

  return { recorded: true, key, attempts: count };
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
  const lineRange = lineRangeFromArgs(tool, args) || options.lineRange || null;
  const versionKey = fileVersionKey(context);
  const entry = {
    content: String(content ?? ""),
    at: prior ? prior.at : now,
    tool: String(tool || ""),
    attempts: prior ? prior.attempts + 1 : 1,
    evidenceHash: context.evidenceHash || null,
    fileAbsPath: context.fileAbsPath || null,
    fileVersionKey: versionKey,
    lineRange,
  };
  successCache.set(key, entry);
  stagnationEntries.delete(key);

  if (versionKey) {
    const coverage = fileCoverage.get(versionKey) || {
      ranges: [],
      nonRangeCount: 0,
      stagnationCount: 0,
      lastKey: null,
    };
    if (lineRange) {
      coverage.ranges = mergeRanges([...coverage.ranges, lineRange]);
    } else {
      coverage.nonRangeCount += 1;
    }
    coverage.lastKey = key;
    fileCoverage.set(versionKey, coverage);
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
  if (
    reason === "EVIDENCE_STAGNATION"
    || reason === "EVIDENCE_STAGNATION_REPEAT"
    || reason === "TOOL_REPEAT_BLOCKED"
  ) {
    return (
      "Evidence read budget exhausted or stagnating. "
      + "Do not call another evidence tool. Produce the final analysis now."
    );
  }
  return (
    "The same unchanged evidence was already returned. Do not read it again. "
    + "Finish the analysis from existing evidence."
  );
}

function clearReadSuccessHistory() {
  successCache.clear();
  fileCoverage.clear();
  stagnationEntries.clear();
  recentKeys.length = 0;
}

function readSuccessHistorySize() {
  return successCache.size;
}

function getFileCoverage(context) {
  const versionKey = fileVersionKey(context);
  if (!versionKey) return null;
  return fileCoverage.get(versionKey) || null;
}

module.exports = {
  READ_EVIDENCE_TOOLS,
  buildEvidenceKey,
  checkReadRepeat,
  recordReadSuccess,
  recordReadStagnation,
  normalizeReadToolArgs,
  cachedReadInstruction,
  clearReadSuccessHistory,
  readSuccessHistorySize,
  mergeRanges,
  novelLineCount,
  isFullyCovered,
  getFileCoverage,
  DEFAULT_MAX_ENTRIES,
  DEFAULT_TTL_MS,
  DEFAULT_NON_RANGE_BUDGET,
  /** @deprecated use DEFAULT_NON_RANGE_BUDGET; kept for callers */
  DEFAULT_FILE_READ_BUDGET: DEFAULT_NON_RANGE_BUDGET,
};

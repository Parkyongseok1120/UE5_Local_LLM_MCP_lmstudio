"use strict";

// Read/search evidence coverage and delivery guard.
//
// A semantic evidence version is owned by canonical path + content/version
// identity + task/evidence scope. Presentation choices such as detailLevel,
// maxBytes, contextLines, and the exact materialized range are delivery
// metadata; they must not create a second semantic file version.

const crypto = require("crypto");
const {
  absolutePathIdentity,
  filesystemPathIdentity,
} = require("./filesystem-path-identity");
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
/**
 * A whole-file read proves line coverage, but compacted chats may no longer retain
 * the exact edit text. Permit a bounded number of exact range materializations
 * instead of returning an unrelated whole-file cache body.
 */
const DEFAULT_COVERED_RANGE_MATERIALIZATION_BUDGET = 8;

// evidenceKey -> { content, at, tool, attempts, lineRange }
const successCache = new Map();
// fileVersionKey -> { ranges, nonRangeCount, stagnationCount, coveredRepeatCount, lastKey }
const fileCoverage = new Map();
const recentKeys = [];
// stagnationKey -> { count, at }
const stagnationEntries = new Map();

function evidenceOwnerKey(context = {}) {
  const taskSessionId = String(context.taskSessionId || "").trim();
  if (taskSessionId) return `task:${taskSessionId}`;
  const evidenceSessionId = String(context.evidenceSessionId || "").trim();
  return `evidence:${evidenceSessionId}`;
}

function evidenceContextKey(context = {}) {
  const hash = crypto.createHash("sha256");
  // A routed task owns one evidence history even if a model-facing sessionId
  // changes between providers or compaction turns. Unbound observations still
  // use the conversation session so a fresh chat cannot inherit old evidence.
  hash.update(evidenceOwnerKey(context));
  hash.update("\u0000");
  hash.update(String(context.fileSignature || context.scopeSignature || ""));
  hash.update("\u0000");
  // A stable file signature can survive replacement of the file contents.
  // Content identity is therefore part of the semantic evidence version, not
  // merely a delivery/materialization detail.
  hash.update(String(context.contentHash || context.fileContentHash || ""));
  hash.update("\u0000");
  hash.update(String(context.mutationGeneration ?? 0));
  return hash.digest("hex").slice(0, 24);
}

function buildEvidenceKey(tool, args, context = {}) {
  const hash = crypto.createHash("sha256");
  hash.update(String(tool || ""));
  hash.update("\u0000");
  hash.update(stableStringify(normalizeReadToolArgs(
    tool,
    args || {},
    context.hostPlatform || process.platform
  )));
  hash.update("\u0000");
  hash.update(evidenceContextKey(context));
  return hash.digest("hex");
}

function fileVersionKey(context = {}) {
  const versionIdentity = String(
    context.contentHash
    || context.fileContentHash
    || context.fileSignature
    || ""
  ).trim();
  if (!context.fileAbsPath || !versionIdentity) return null;
  const fileIdentity = absolutePathIdentity(
    context.fileAbsPath,
    context.hostPlatform || process.platform
  );
  return `${evidenceOwnerKey(context)}\u0000${fileIdentity}\u0000${versionIdentity}\u0000${context.mutationGeneration ?? 0}`;
}

function canonicalCoverageIdentity(context = {}) {
  if (!context.fileAbsPath) return null;
  const versionIdentity = String(
    context.contentHash
    || context.fileContentHash
    || context.fileSignature
    || ""
  ).trim();
  if (!versionIdentity) return null;
  return {
    canonicalPath: absolutePathIdentity(
      context.fileAbsPath,
      context.hostPlatform || process.platform
    ),
    contentHash: String(context.contentHash || context.fileContentHash || "").trim().toLowerCase() || null,
    versionIdentity,
    mutationGeneration: Math.max(0, Number(context.mutationGeneration || 0)),
    evidenceScope: evidenceOwnerKey(context),
    key: fileVersionKey(context),
  };
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
  if (!Array.isArray(ranges) || !ranges.length) return [];
  const sorted = ranges
    .map((r) => ({
      start: Number(Array.isArray(r) ? r[0] : r?.start),
      end: Number(Array.isArray(r) ? r[1] : r?.end),
    }))
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

function hydrateDurableCoverage(context = {}, versionKey = null) {
  if (!versionKey || !context.durableCoverage || typeof context.durableCoverage !== "object") {
    return null;
  }
  const contextHash = String(context.contentHash || context.fileContentHash || "")
    .trim()
    .toLowerCase();
  const durable = context.durableCoverage;
  const durableHash = String(durable.contentHash || durable.evidenceHash || "")
    .trim()
    .toLowerCase();
  if (!contextHash || !durableHash || contextHash !== durableHash) return null;

  const mutationGeneration = Math.max(0, Number(context.mutationGeneration || 0));
  const durableMutationGeneration = Math.max(0, Number(durable.mutationGeneration || 0));
  if (mutationGeneration !== durableMutationGeneration) return null;

  const identity = canonicalCoverageIdentity(context);
  if (!identity) return null;
  const ranges = mergeRanges(durable.coveredRanges || durable.ranges || []);
  const lineCount = Math.max(0, Number(durable.lineCount || 0));
  const wholeFileComplete = durable.wholeFileComplete === true
    || (lineCount > 0 && ranges.some((range) => range.start <= 1 && range.end >= lineCount));
  const coverage = {
    canonicalPath: identity.canonicalPath,
    contentHash: identity.contentHash,
    mutationGeneration,
    ranges,
    nonRangeCount: Math.max(0, Number(
      durable.nonRangeCount || (wholeFileComplete ? 1 : 0),
    )),
    stagnationCount: Math.max(0, Number(durable.stagnationCount || 0)),
    coveredRepeatCount: Math.max(0, Number(durable.coveredRepeatCount || 0)),
    materializedCoveredRangeCount: Math.max(0, Number(durable.materializedCoveredRangeCount || 0)),
    lastKey: null,
    lineCount,
    wholeFileComplete,
    truncated: durable.truncated === true && !wholeFileComplete,
    nextUnreadLine: wholeFileComplete
      ? null
      : Math.max(1, Number(durable.nextUnreadLine || 1)),
    largestMaterialization: durable.largestMaterialization
      && typeof durable.largestMaterialization === "object"
      ? { ...durable.largestMaterialization }
      : null,
    acceptedEvidenceId: String(durable.acceptedEvidenceId || durable.evidenceId || "").slice(0, 80),
    semanticAnchors: Array.isArray(durable.semanticAnchors)
      ? durable.semanticAnchors.map(String).filter(Boolean).slice(0, 16)
      : [],
    lastEvidenceProgressed: true,
  };
  fileCoverage.set(versionKey, coverage);
  return coverage;
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

/**
 * Check whether this read is novel evidence, a materialization/cache hit, or
 * a prohibited semantic duplicate.
 * @returns {{ action: 'allow'|'materialize'|'cache'|'blocked'|'stagnation', ... }}
 */
function checkReadRepeat(tool, args, context = {}, options = {}) {
  if (!READ_EVIDENCE_TOOLS.has(tool)) return { action: "allow", repeat: false };
  const now = Number.isFinite(options.now) ? options.now : Date.now();
  const maxEntries = Number.isFinite(options.maxEntries) ? options.maxEntries : DEFAULT_MAX_ENTRIES;
  const ttlMs = Number.isFinite(options.ttlMs) ? options.ttlMs : DEFAULT_TTL_MS;
  prune(now, maxEntries, ttlMs);

  const key = buildEvidenceKey(tool, args, context);
  const versionKey = fileVersionKey(context);
  const coverage = versionKey
    ? fileCoverage.get(versionKey) || hydrateDurableCoverage(context, versionKey)
    : null;
  const coverageIdentity = canonicalCoverageIdentity(context);
  const requested = lineRangeFromArgs(tool, args);

  // A complete whole-file read owns the unchanged semantic version. A later
  // detailLevel/default presentation is a cache hit and cannot read the file
  // again. A truncated whole-file read must continue at the exact frontier;
  // restarting read_file from line 1 is prohibited.
  if (tool === "read_file" && coverage) {
    if (coverage.wholeFileComplete === true) {
      return {
        action: "cache",
        repeat: true,
        reason: "READ_CACHE_HIT",
        resultKind: "cache_hit",
        cached: true,
        evidenceProgressed: false,
        workflowProgressed: false,
        key,
        coverageIdentity,
        coverage: { ...coverage },
        cachedContent: null,
      };
    }
    return {
      action: "blocked",
      repeat: true,
      reason: "READ_REPEAT_BLOCKED",
      resultKind: "repeat_blocked",
      errorCode: "READ_REPEAT_BLOCKED",
      evidenceProgressed: false,
      workflowProgressed: false,
      key,
      coverageIdentity,
      coverage: { ...coverage },
      ...(context.coverageContinuation && typeof context.coverageContinuation === "object"
        ? { continuation: { ...context.coverageContinuation } }
        : {}),
    };
  }

  // A previously blocked semantic repeat remains blocked. This deliberately
  // does not call recordReadStagnation and therefore cannot reset or consume a
  // recovery allowance.
  const stagnant = stagnationEntries.get(key);
  if (stagnant && stagnant.count >= 1) {
    return {
      action: "blocked",
      repeat: true,
      reason: "READ_REPEAT_BLOCKED",
      resultKind: "repeat_blocked",
      errorCode: "READ_REPEAT_BLOCKED",
      evidenceProgressed: false,
      workflowProgressed: false,
      key,
      attempts: stagnant.count + 1,
      escalated: true,
    };
  }

  const prior = successCache.get(key);
  if (prior) {
    prior.attempts += 1;
    if (prior.attempts >= 3) {
      return {
        action: "blocked",
        repeat: true,
        reason: "READ_REPEAT_BLOCKED",
        resultKind: "repeat_blocked",
        errorCode: "READ_REPEAT_BLOCKED",
        evidenceProgressed: false,
        workflowProgressed: false,
        key,
        attempts: prior.attempts,
        escalated: true,
        exactRepeat: true,
      };
    }
    return {
      action: "cache",
      repeat: true,
      reason: "READ_CACHE_HIT",
      resultKind: "cache_hit",
      cached: true,
      evidenceProgressed: false,
      workflowProgressed: false,
      key,
      cachedContent: prior.content,
      attempts: prior.attempts,
      firstReadAt: prior.at,
      coverageIdentity,
    };
  }

  // New line coverage is always allowed. A fully covered range may still be
  // materialized once for exact edit text, but it is not new evidence.
  if (tool === "read_file_range" && requested) {
    const priorRanges = coverage ? coverage.ranges : [];
    const novelLines = novelLineCount(requested, priorRanges);
    if (novelLines === 0) {
      const materializationBudget = Number.isFinite(options.coveredRangeMaterializationBudget)
        ? options.coveredRangeMaterializationBudget
        : DEFAULT_COVERED_RANGE_MATERIALIZATION_BUDGET;
      const materialized = Number(coverage?.materializedCoveredRangeCount || 0);
      if (materialized >= materializationBudget) {
        return {
          action: "stagnation",
          repeat: true,
          reason: "EVIDENCE_STAGNATION",
          key,
          attempts: materialized + 1,
          fullyCovered: true,
          coveredBy: priorRanges,
          evidenceProgressed: false,
          workflowProgressed: false,
        };
      }
      return {
        action: "cache",
        repeat: true,
        resultKind: "cache_hit",
        cached: true,
        evidenceProgressed: false,
        workflowProgressed: false,
        key,
        materializeCoveredRange: true,
        materializationCount: materialized + 1,
        fullyCovered: true,
        coveredBy: priorRanges,
        coverageIdentity,
      };
    }
    return {
      action: "allow",
      repeat: false,
      key,
      coverageIdentity,
      novelLines,
      evidenceProgressed: true,
      workflowProgressed: true,
    };
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
      evidenceProgressed: false,
      workflowProgressed: false,
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
        evidenceProgressed: false,
        workflowProgressed: false,
      };
    }
  }

  return {
    action: "allow",
    repeat: false,
    key,
    coverageIdentity,
    evidenceProgressed: true,
    workflowProgressed: true,
  };
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
      coveredRepeatCount: 0,
      materializedCoveredRangeCount: 0,
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
  const lineRange = options.lineRange || lineRangeFromArgs(tool, args) || null;
  const versionKey = fileVersionKey(context);
  const priorCoverage = versionKey ? fileCoverage.get(versionKey) : null;
  const priorRanges = priorCoverage?.ranges || [];
  const novelLines = lineRange ? novelLineCount(lineRange, priorRanges) : null;
  const wholeFileComplete = options.wholeFileComplete === true
    || (tool === "read_file" && options.truncated !== true);
  const evidenceProgressed = options.materializationOnly === true
    || Boolean(prior)
    ? false
    : options.evidenceProgressed === false
      ? false
      : wholeFileComplete
        ? priorCoverage?.wholeFileComplete !== true
        : lineRange
          ? novelLines > 0
          : tool === "read_file"
            ? !priorCoverage
            : true;
  const entry = {
    content: String(content ?? ""),
    at: prior ? prior.at : now,
    tool: String(tool || ""),
    attempts: prior ? prior.attempts + 1 : 1,
    evidenceHash: context.evidenceHash || null,
    fileAbsPath: context.fileAbsPath || null,
    fileVersionKey: versionKey,
    lineRange,
    resultKind: String(options.resultKind || (evidenceProgressed ? "source_read" : "cache_hit")),
    evidenceProgressed,
  };
  successCache.set(key, entry);
  if (evidenceProgressed) stagnationEntries.delete(key);

  if (versionKey) {
    const coverage = fileCoverage.get(versionKey) || {
      ranges: [],
      nonRangeCount: 0,
      stagnationCount: 0,
      coveredRepeatCount: 0,
      materializedCoveredRangeCount: 0,
      lastKey: null,
    };
    if (lineRange) {
      const alreadyCovered = isFullyCovered(lineRange, coverage.ranges);
      coverage.ranges = mergeRanges([...coverage.ranges, lineRange]);
      if (evidenceProgressed) coverage.coveredRepeatCount = 0;
      if (tool === "read_file_range" && alreadyCovered && !evidenceProgressed) {
        coverage.materializedCoveredRangeCount = Number(coverage.materializedCoveredRangeCount || 0) + 1;
      }
    } else if (evidenceProgressed) {
      coverage.nonRangeCount += 1;
    }
    const identity = canonicalCoverageIdentity(context);
    coverage.canonicalPath = identity?.canonicalPath || coverage.canonicalPath || null;
    coverage.contentHash = String(context.contentHash || context.fileContentHash || coverage.contentHash || "")
      .trim().toLowerCase() || null;
    coverage.mutationGeneration = Math.max(0, Number(context.mutationGeneration || coverage.mutationGeneration || 0));
    coverage.lineCount = Math.max(0, Number(options.lineCount || coverage.lineCount || 0));
    coverage.wholeFileComplete = Boolean(coverage.wholeFileComplete === true || wholeFileComplete);
    if (
      coverage.lineCount > 0
      && coverage.ranges.some((range) => range.start <= 1 && range.end >= coverage.lineCount)
    ) {
      coverage.wholeFileComplete = true;
    }
    coverage.largestMaterialization = {
      ...(coverage.largestMaterialization && typeof coverage.largestMaterialization === "object"
        ? coverage.largestMaterialization
        : {}),
      ...(options.detailLevel != null ? { detailLevel: String(options.detailLevel) } : {}),
      ...(options.bytesReturned != null ? { bytesReturned: Math.max(0, Number(options.bytesReturned || 0)) } : {}),
      ...(options.lineCount != null ? { lineCount: Math.max(0, Number(options.lineCount || 0)) } : {}),
    };
    coverage.truncated = options.truncated === true && coverage.wholeFileComplete !== true;
    coverage.nextUnreadLine = coverage.wholeFileComplete === true
      ? null
      : Math.max(1, Number(options.nextUnreadLine || coverage.nextUnreadLine || (lineRange ? lineRange["end"] + 1 : 1)));
    if (Array.isArray(options.semanticAnchors) && options.semanticAnchors.length) {
      coverage.semanticAnchors = [...new Set(options.semanticAnchors.map(String).filter(Boolean))].slice(0, 16);
    }
    if (options.acceptedEvidenceId) coverage.acceptedEvidenceId = String(options.acceptedEvidenceId).slice(0, 80);
    coverage.lastEvidenceProgressed = evidenceProgressed;
    coverage.lastKey = key;
    fileCoverage.set(versionKey, coverage);
  }

  if (evidenceProgressed) {
    recentKeys.push(key);
    while (recentKeys.length > RECENT_KEY_WINDOW) recentKeys.shift();
  }

  return {
    recorded: true,
    key,
    attempts: entry.attempts,
    resultKind: evidenceProgressed ? "source_read" : "cache_hit",
    cached: !evidenceProgressed,
    evidenceProgressed,
    workflowProgressed: evidenceProgressed,
    coverage: versionKey ? { ...(fileCoverage.get(versionKey) || {}) } : null,
  };
}

function normalizeReadToolArgs(tool, args = {}, hostPlatform = process.platform) {
  const normalized = {};
  const normalizePath = (value) => filesystemPathIdentity(
    value,
    hostPlatform,
    { trimOuterSlashes: true }
  );
  if (tool === "read_file") {
    normalized.path = normalizePath(args.path);
    // detailLevel and maxBytes are materialization options, not semantic
    // evidence identity.
    return normalized;
  }
  if (tool === "read_file_range") {
    const startLine = Math.max(1, Number(args.startLine || 1));
    const endLine = Math.max(startLine, Number(args.endLine || startLine));
    normalized.path = normalizePath(args.path);
    normalized.startLine = startLine;
    normalized.endLine = endLine;
    return normalized;
  }
  if (tool === "read_symbol") {
    normalized.path = normalizePath(args.path);
    normalized.symbol = String(args.symbol || "").trim();
    // contextLines changes delivery only; the file/version remains the
    // semantic evidence owner.
    return normalized;
  }
  if (tool === "search_files") {
    normalized.query = String(args.query || "");
    if (args.path != null) normalized.path = normalizePath(args.path);
    if (args.regex != null) normalized.regex = Boolean(args.regex);
    if (args.matchFileNames != null) normalized.matchFileNames = Boolean(args.matchFileNames);
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
      + "The evidence phase is complete. Do not call another evidence tool. "
      + "Continue with an evidence-supported write/validation step when the user requested implementation; "
      + "otherwise produce the final analysis."
    );
  }
  if (reason === "READ_REPEAT_BLOCKED") {
    return (
      "This unchanged semantic evidence was already accepted and the repeated read is blocked. "
      + "Follow the authoritative requiredNextTool/requiredNextToolArgs continuation exactly; "
      + "do not restart the same file from line 1."
    );
  }
  return (
    "The same unchanged evidence was already returned. Do not re-read this same path. "
    + "Use the authoritative continuation if one is present; otherwise continue with other unread "
    + "files or retained evidence without re-reading it."
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
  canonicalCoverageIdentity,
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
  DEFAULT_COVERED_RANGE_MATERIALIZATION_BUDGET,
  /** @deprecated use DEFAULT_NON_RANGE_BUDGET; kept for callers */
  DEFAULT_FILE_READ_BUDGET: DEFAULT_NON_RANGE_BUDGET,
};

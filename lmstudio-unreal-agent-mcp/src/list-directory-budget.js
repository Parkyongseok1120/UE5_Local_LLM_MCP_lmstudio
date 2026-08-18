"use strict";

/**
 * Server-side list_directory exploration budget.
 * Caps call volume and duplicate paths per sliding window — not path depth.
 */

const DEFAULTS = Object.freeze({
  windowMs: 5 * 60 * 1000,
  maxCallsPerWindow: 24,
  maxCallsPerPath: 2,
});

const GLOBAL_BUDGET_CONTRACT = Object.freeze({
  budgetOwner: "agent_process",
  budgetKind: "global_abuse_guard",
  persistence: "process_local",
  resetRule: "sliding_window_or_process_restart",
  resumeAction: "search_files_or_known_file_read",
});

function normalizeListPath(input) {
  return String(input || ".")
    .replace(/\\/g, "/")
    .replace(/^\.\/+/, "")
    .replace(/\/+$/, "") || ".";
}

function createListDirectoryBudget(options = {}) {
  const windowMs = Math.max(10_000, Number(options.windowMs || DEFAULTS.windowMs));
  const maxCallsPerWindow = Math.max(1, Number(options.maxCallsPerWindow || DEFAULTS.maxCallsPerWindow));
  const maxCallsPerPath = Math.max(1, Number(options.maxCallsPerPath || DEFAULTS.maxCallsPerPath));
  /** @type {Map<string, { windowStart: number, calls: number, paths: Map<string, number> }>} */
  const buckets = new Map();

  function bucketKey(scope) {
    return String(scope || "default");
  }

  function ensureBucket(scope, now = Date.now()) {
    const key = bucketKey(scope);
    let bucket = buckets.get(key);
    if (!bucket || now - bucket.windowStart >= windowMs) {
      bucket = { windowStart: now, calls: 0, paths: new Map() };
      buckets.set(key, bucket);
    }
    return bucket;
  }

  function check(scope, listPath) {
    const now = Date.now();
    const bucket = ensureBucket(scope, now);
    const pathKey = normalizeListPath(listPath);
    const pathCount = Number(bucket.paths.get(pathKey) || 0);
    if (bucket.calls >= maxCallsPerWindow) {
      return {
        ...GLOBAL_BUDGET_CONTRACT,
        ok: false,
        errorCode: "LIST_DIRECTORY_BUDGET_EXCEEDED",
        path: pathKey,
        calls: bucket.calls,
        maxCallsPerWindow,
        pathCount,
        maxCallsPerPath,
        agentInstruction:
          "list_directory budget exhausted for this window. Prefer search_files or read_file, then answer.",
      };
    }
    if (pathCount >= maxCallsPerPath) {
      return {
        ...GLOBAL_BUDGET_CONTRACT,
        ok: false,
        errorCode: "LIST_DIRECTORY_DUPLICATE",
        path: pathKey,
        calls: bucket.calls,
        maxCallsPerWindow,
        pathCount,
        maxCallsPerPath,
        agentInstruction:
          "list_directory was already used for this path. Use search_files/read_file instead of re-listing.",
      };
    }
    return {
      ...GLOBAL_BUDGET_CONTRACT,
      ok: true,
      path: pathKey,
      calls: bucket.calls,
      maxCallsPerWindow,
      pathCount,
      maxCallsPerPath,
    };
  }

  function commit(scope, listPath) {
    const now = Date.now();
    const bucket = ensureBucket(scope, now);
    const pathKey = normalizeListPath(listPath);
    bucket.calls += 1;
    bucket.paths.set(pathKey, Number(bucket.paths.get(pathKey) || 0) + 1);
    return {
      ...GLOBAL_BUDGET_CONTRACT,
      ok: true,
      path: pathKey,
      calls: bucket.calls,
      maxCallsPerWindow,
      pathCount: Number(bucket.paths.get(pathKey) || 0),
      maxCallsPerPath,
    };
  }

  function reset(scope) {
    if (scope == null) buckets.clear();
    else buckets.delete(bucketKey(scope));
  }

  return {
    windowMs,
    maxCallsPerWindow,
    maxCallsPerPath,
    check,
    commit,
    reset,
    normalizeListPath,
  };
}

module.exports = {
  DEFAULTS,
  GLOBAL_BUDGET_CONTRACT,
  normalizeListPath,
  createListDirectoryBudget,
};

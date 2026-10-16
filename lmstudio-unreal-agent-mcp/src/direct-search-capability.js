"use strict";

// search_files: cancellable, deadline-bounded content and basename search.

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const { assertReadChildContained, displayPath, pathMetadata } = require("./read-path-resolver.js");
const { success, failure, renderModelJson } = require("./direct-response.js");
const { clamp, isBinary, relativeSlash, statOrNull, statSignature } = require("./direct-runtime-shared.js");

const fsp = fs.promises;
const DEFAULT_IGNORED_DIRS = new Set([
  ".git", ".vs", ".idea", "Binaries", "DerivedDataCache", "Intermediate",
  "node_modules", ".gradle", ".cache", ".pytest_cache",
]);
const SEARCH_READ_CONCURRENCY = 16;
const MAX_REGEX_QUERY_CHARS = 512;
// A group that contains an unescaped quantifier and is itself quantified.
const NESTED_QUANTIFIER = /\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)(?:[+*]|\{\d+,\d*\})/;
const TEXT_EXTENSIONS = new Set([
  ".h", ".hpp", ".inl", ".cpp", ".c", ".cc", ".cxx", ".cs",
  ".ini", ".json", ".uproject", ".uplugin", ".md", ".txt", ".py",
  ".js", ".ts", ".tsx", ".jsx", ".ps1", ".sh", ".bat", ".cmd",
  ".xml", ".yaml", ".yml", ".toml", ".cmake", ".build", ".target",
]);

function createSearchCapability(context) {
  const { dedupe, limits, payloadFits, resolveToolPath } = context;

  // Search stops cooperatively: request cancellation fails the call, while the
  // overall deadline returns the facts gathered so far as a partial result.
  function searchGuard(signal, deadlineMs) {
    const deadline = Date.now() + deadlineMs;
    return {
      reason() {
        if (signal?.aborted) return "cancelled";
        return Date.now() >= deadline ? "deadline" : "";
      },
    };
  }

  async function collectSearchFiles(rootResolution, maxFiles, guard) {
    const files = [];
    let discoveryErrors = 0, excludedEntries = 0;
    async function walk(target) {
      if (files.length >= maxFiles || guard.reason()) return;
      let stat;
      try { stat = await statOrNull(target); } catch { discoveryErrors++; return; }
      if (!stat) { discoveryErrors++; return; }
      if (stat.isFile()) {
        files.push({ target, stat });
        return;
      }
      if (!stat.isDirectory()) return;
      let entries;
      try { entries = await fsp.readdir(target, { withFileTypes: true }); } catch { discoveryErrors++; return; }
      for (const entry of entries) {
        if (files.length >= maxFiles || guard.reason()) break;
        if (entry.isDirectory() && DEFAULT_IGNORED_DIRS.has(entry.name)) { excludedEntries++; continue; }
        const child = path.join(target, entry.name);
        await assertReadChildContained(child, rootResolution);
        if (entry.isDirectory() || entry.isFile()) await walk(child);
        else excludedEntries++;
      }
    }
    await walk(rootResolution.absolutePath);
    return { files, discoveryErrors, excludedEntries };
  }

  async function searchFiles(args, requestContext = {}) {
    const query = String(args.query || "");
    if (!query) return failure("INVALID_ARGUMENT", "query is required", { retryAllowed: true });
    const resolution = await resolveToolPath(args.path || "workspace://", args.project);
    const rootStat = await statOrNull(resolution.absolutePath);
    if (!rootStat) return failure("NOT_FOUND", `Search path not found: ${args.path || "."}`);
    const maxResults = clamp(args.maxResults, 100, 1, 1000);
    const maxFiles = clamp(args.maxFiles, 5000, 1, 50000);
    const caseSensitive = args.caseSensitive === true;
    let matcher;
    if (args.regex === true) {
      // JavaScript regexes cannot be interrupted; bound the pattern instead.
      if (query.length > MAX_REGEX_QUERY_CHARS) {
        return failure("INVALID_REGEX", `regex query must be at most ${MAX_REGEX_QUERY_CHARS} characters.`, { retryAllowed: true, retryMode: "different_arguments" });
      }
      if (NESTED_QUANTIFIER.test(query)) {
        return failure("INVALID_REGEX", "Nested quantifiers such as (a+)+ can stall the server; simplify the pattern.", { retryAllowed: true, retryMode: "different_arguments" });
      }
      try {
        matcher = new RegExp(query, caseSensitive ? "" : "i");
      } catch (error) {
        return failure("INVALID_REGEX", String(error.message || error), { retryAllowed: true, retryMode: "different_arguments" });
      }
    }
    const inferredFileName = /\\\.[A-Za-z0-9]+\$$/.test(query) || /^\*\./.test(query);
    const matchFileNames = args.matchFileNames !== undefined ? args.matchFileNames === true : inferredFileName;
    const needle = caseSensitive ? query : query.toLowerCase();
    const matchesText = (value) => matcher ? matcher.test(value) : (caseSensitive ? value : value.toLowerCase()).includes(needle);
    const rootUri = displayPath(resolution);
    const resultUri = (rel) => rel === "." ? rootUri : `${rootUri}${rootUri.endsWith("/") ? "" : "/"}${rel}`;
    const guard = searchGuard(requestContext.signal, limits.searchDeadlineMs);
    const cancelled = () => failure("REQUEST_CANCELLED", "search_files was cancelled.", {
      details: { cancelled: true, executionState: "stopped" },
    });
    const { files, discoveryErrors, excludedEntries } = await collectSearchFiles(resolution, maxFiles, guard);
    if (guard.reason() === "cancelled") return cancelled();
    const skippedByReason = { size: 0, unsupported_extension: 0, binary: 0, read_error: 0 };
    let filesRead = 0;
    let filesVisited = 0;
    let stopReason = guard.reason();
    const results = [];

    // Reads run SEARCH_READ_CONCURRENCY at a time; matches are appended in
    // file order, so results stay deterministic regardless of I/O timing.
    async function scanFile(item) {
      const rel = relativeSlash(resolution.absolutePath, item.target);
      const found = [];
      if (matchFileNames && matchesText(path.basename(item.target))) {
        found.push({ path: rel, uri: resultUri(rel), kind: "file_name" });
      }
      if (item.stat.size > 2 * 1024 * 1024) return { found, skipped: "size" };
      const ext = path.extname(item.target).toLowerCase();
      if (ext && !TEXT_EXTENSIONS.has(ext)) return { found, skipped: "unsupported_extension" };
      let buffer;
      try {
        buffer = await fsp.readFile(item.target, { signal: requestContext.signal });
      } catch {
        return { found, skipped: "read_error" };
      }
      if (isBinary(buffer)) return { found, skipped: "binary" };
      const lines = buffer.toString("utf8").split(/\r?\n/);
      for (let index = 0; index < lines.length && found.length < maxResults; index += 1) {
        if (index % 1024 === 1023 && guard.reason()) return { found, read: true, interrupted: true };
        if (!matchesText(lines[index])) continue;
        found.push({ path: rel, uri: resultUri(rel), line: index + 1, text: lines[index].slice(0, 1200) });
      }
      return { found, read: true };
    }

    for (let start = 0; start < files.length && !stopReason && results.length < maxResults; start += SEARCH_READ_CONCURRENCY) {
      const batch = await Promise.all(files.slice(start, start + SEARCH_READ_CONCURRENCY).map(scanFile));
      if (guard.reason() === "cancelled") return cancelled();
      for (const scanned of batch) {
        filesVisited++;
        if (scanned.skipped) skippedByReason[scanned.skipped]++;
        if (scanned.read) filesRead++;
        for (const result of scanned.found) {
          if (results.length >= maxResults) break;
          results.push(result);
        }
        if (scanned.interrupted) stopReason = "deadline";
        if (results.length >= maxResults || stopReason) break;
      }
      stopReason = stopReason || guard.reason();
    }
    const treeState = crypto.createHash("sha256")
      .update(files.map(({ target, stat }) => `${relativeSlash(resolution.absolutePath, target)}:${statSignature(stat)}`).join("\n"))
      .digest("hex");
    const complete = filesRead === files.length && !discoveryErrors && !excludedEntries && files.length < maxFiles
      && results.length < maxResults && !stopReason;
    const makePayload = (list, omitted) => success({
      path: rootUri,
      ...pathMetadata(resolution),
      query,
      results: list,
      filesScanned: files.length, // Legacy candidate count; filesRead is actual content coverage.
      filesRead, filesSkipped: Object.values(skippedByReason).reduce((a, b) => a + b, 0),
      readErrorCount: skippedByReason.read_error, discoveryErrorCount: discoveryErrors, excludedEntries, skippedByReason,
      coverage: complete && !omitted ? "complete" : "partial",
      maxFilesReached: files.length >= maxFiles,
      truncated: results.length >= maxResults || omitted,
      ...(stopReason ? { stopReason, filesUnvisited: files.length - filesVisited, deadlineMs: limits.searchDeadlineMs } : {}),
      ...(omitted ? { omissionReason: "response_budget" } : {}),
    });

    // Measure the envelope once, then add each result's own compact size
    // instead of re-serializing the whole payload per result.
    const reserve = 256;
    const maxChars = limits.maxResponseChars - reserve;
    const maxBytes = args.byteBudget === undefined ? Infinity : args.byteBudget - reserve;
    const envelope = renderModelJson(makePayload([], true));
    let chars = envelope.length;
    let bytes = Buffer.byteLength(envelope, "utf8");
    let fitted = 0;
    for (const result of results) {
      const text = renderModelJson(result);
      const separator = fitted ? 1 : 0;
      if (chars + text.length + separator > maxChars || bytes + Buffer.byteLength(text, "utf8") + separator > maxBytes) break;
      chars += text.length + separator;
      bytes += Buffer.byteLength(text, "utf8") + separator;
      fitted++;
    }
    let payload = makePayload(results.slice(0, fitted), fitted < results.length);
    while (fitted > 0 && !payloadFits(payload, reserve, args.byteBudget)) {
      fitted--;
      payload = makePayload(results.slice(0, fitted), true);
    }
    if (!payloadFits(payload, reserve, args.byteBudget) || !payload.results.length && results.length)
      return failure("OUTPUT_LIMIT_EXCEEDED", "Search metadata or first match exceeds the response budget; increase byteBudget or narrow the search.", { retryAllowed: true });
    return dedupe("search_files", args, `${resolution.activeProject || ""}|${treeState}|${payload.coverage}|${filesRead}|${JSON.stringify(skippedByReason)}|${discoveryErrors}|${excludedEntries}|${stopReason}`, payload);
  }

  return { search_files: searchFiles };
}

module.exports = { MAX_REGEX_QUERY_CHARS, NESTED_QUANTIFIER, SEARCH_READ_CONCURRENCY, createSearchCapability };

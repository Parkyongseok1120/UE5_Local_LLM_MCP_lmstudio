"use strict";

// search_files: cancellable, deadline-bounded content and basename search.

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const { assertReadChildContained, displayPath, pathMetadata } = require("./read-path-resolver.js");
const { success, failure, renderModelJson } = require("./direct-response.js");
const { clamp, isBinary, relativeSlash, statOrNull, statSignature } = require("./direct-runtime-shared.js");

const { numberLines } = require("../../shared-tool-core/line-numbering.js");

const fsp = fs.promises;
const MAX_CONTEXT_LINES = 8;
const MAX_CONTEXT_LINE_CHARS = 400;
const MAX_INCLUDE_CHARS = 200;

// A glob over the path relative to the search root: "*" and "?" stay inside one
// segment, "**/" spans directories. Without a "/" it matches the basename.
function includeMatcher(pattern) {
  const glob = String(pattern || "").trim();
  if (!glob) return null;
  const DEEP = "\u0000", ANY = "\u0001";
  const source = glob.replace(/[.+^${}()|[\]\\]/gu, "\\$&")
    .replace(/\*\*\//gu, DEEP).replace(/\*\*/gu, ANY)
    .replace(/\*/gu, "[^/]*").replace(/\?/gu, "[^/]")
    .split(DEEP).join("(?:.*/)?").split(ANY).join(".*");
  const regex = new RegExp(`^${source}$`, "iu");
  return glob.includes("/") ? (rel) => regex.test(rel) : (rel) => regex.test(rel.split("/").pop());
}
const DEFAULT_IGNORED_DIRS = new Set([
  ".git", ".vs", ".idea", "Binaries", "DerivedDataCache", "Intermediate",
  "node_modules", ".gradle", ".cache", ".pytest_cache",
]);
const SEARCH_READ_CONCURRENCY = 16;
const MAX_REGEX_QUERY_CHARS = 512;
// A cursor re-runs the same deterministic search and skips matches already
// returned; this bounds how far a continuation may reach.
const MAX_CURSOR_MATCHES = 20000;
// A group that contains an unescaped quantifier and is itself quantified.
const NESTED_QUANTIFIER = /\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)(?:[+*]|\{\d+,\d*\})/;
const TEXT_EXTENSIONS = new Set([
  ".h", ".hpp", ".inl", ".cpp", ".c", ".cc", ".cxx", ".cs",
  ".ini", ".json", ".uproject", ".uplugin", ".md", ".txt", ".py",
  ".js", ".ts", ".tsx", ".jsx", ".ps1", ".sh", ".bat", ".cmd",
  ".xml", ".yaml", ".yml", ".toml", ".cmake", ".build", ".target",
]);

// The search a cursor belongs to: every argument that changes which matches
// exist or their order. Paging and budget fields are excluded.
function searchIdentity(args) {
  return crypto.createHash("sha256").update(JSON.stringify([
    String(args.query || ""), String(args.path || "workspace://"), String(args.project || ""),
    args.regex === true, args.matchFileNames ?? null, args.caseSensitive === true,
    args.maxFiles ?? null, String(args.include || ""),
  ])).digest("hex").slice(0, 16);
}

function encodeCursor(identity, treeState, offset) {
  return Buffer.from(JSON.stringify({ v: 1, s: identity, t: treeState.slice(0, 16), n: offset }), "utf8")
    .toString("base64url");
}

function decodeCursor(cursor) {
  try {
    const value = JSON.parse(Buffer.from(String(cursor), "base64url").toString("utf8"));
    if (value?.v !== 1 || typeof value.s !== "string" || typeof value.t !== "string"
      || !Number.isInteger(value.n) || value.n < 0 || value.n > MAX_CURSOR_MATCHES) return null;
    return value;
  } catch { return null; }
}

function createSearchCapability(context, helpers = {}) {
  const { dedupe, limits, payloadFits, projectScopedSuggestionArgs, resolveToolPath } = context;

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
        try { await assertReadChildContained(child, rootResolution); }
        catch (error) {
          if (error.code !== "ENGINE_SOURCE_PATH_DENIED") throw error;
          excludedEntries++;
          continue;
        }
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
    const cursor = args.cursor === undefined ? null : decodeCursor(args.cursor);
    if (args.cursor !== undefined && !cursor) {
      return failure("INVALID_ARGUMENT", "cursor is not a search_files nextCursor.", { retryAllowed: true, retryMode: "different_arguments" });
    }
    const identity = searchIdentity(args);
    if (cursor && cursor.s !== identity) {
      return failure("INVALID_ARGUMENT", "cursor belongs to a different search; pass it with the same query, path and filters.",
        { retryAllowed: true, retryMode: "different_arguments" });
    }
    const resolution = await resolveToolPath(args.path || "workspace://", args.project);
    const rootStat = await statOrNull(resolution.absolutePath);
    if (!rootStat) {
      return failure("NOT_FOUND", `Search path not found: ${args.path || "."}`,
        { retryAllowed: true, retryMode: "different_arguments", suggestion: await helpers.notFoundSuggestion?.(args, resolution) });
    }
    const maxResults = clamp(args.maxResults, 100, 1, 1000);
    const offset = cursor ? cursor.n : 0;
    // Collect through the requested page; the matches before it are skipped.
    const collectLimit = Math.min(offset + maxResults, MAX_CURSOR_MATCHES);
    const maxFiles = clamp(args.maxFiles, 5000, 1, 50000);
    const contextLines = clamp(args.contextLines, 0, 0, MAX_CONTEXT_LINES);
    if (args.include !== undefined && (typeof args.include !== "string" || args.include.length > MAX_INCLUDE_CHARS)) {
      return failure("INVALID_ARGUMENT", `include must be a glob string of at most ${MAX_INCLUDE_CHARS} characters.`, { retryAllowed: true, retryMode: "different_arguments" });
    }
    const included = includeMatcher(args.include);
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
    const discovered = await collectSearchFiles(resolution, maxFiles, guard);
    const { discoveryErrors, excludedEntries } = discovered;
    // include narrows the scope itself; coverage then speaks for the included files only.
    const files = included ? discovered.files.filter(({ target }) => included(
      rootStat.isFile() ? path.basename(target) : relativeSlash(resolution.absolutePath, target),
    )) : discovered.files;
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
      for (let index = 0; index < lines.length && found.length < collectLimit; index += 1) {
        if (index % 1024 === 1023 && guard.reason()) return { found, read: true, interrupted: true };
        if (!matchesText(lines[index])) continue;
        const first = Math.max(0, index - contextLines), last = Math.min(lines.length, index + contextLines + 1);
        found.push({ path: rel, uri: resultUri(rel), line: index + 1, text: lines[index].slice(0, 1200),
          ...(contextLines ? { contextStartLine: first + 1, contextEndLine: last,
            context: numberLines(lines.slice(first, last).map((line) => line.slice(0, MAX_CONTEXT_LINE_CHARS)), first + 1).join("\n") } : {}) });
      }
      return { found, read: true };
    }

    for (let start = 0; start < files.length && !stopReason && results.length < collectLimit; start += SEARCH_READ_CONCURRENCY) {
      const batch = await Promise.all(files.slice(start, start + SEARCH_READ_CONCURRENCY).map(scanFile));
      if (guard.reason() === "cancelled") return cancelled();
      for (const scanned of batch) {
        filesVisited++;
        if (scanned.skipped) skippedByReason[scanned.skipped]++;
        if (scanned.read) filesRead++;
        for (const result of scanned.found) {
          if (results.length >= collectLimit) break;
          results.push(result);
        }
        if (scanned.interrupted) stopReason = "deadline";
        if (results.length >= collectLimit || stopReason) break;
      }
      stopReason = stopReason || guard.reason();
    }
    const treeState = crypto.createHash("sha256")
      .update(files.map(({ target, stat }) => `${relativeSlash(resolution.absolutePath, target)}:${statSignature(stat)}`).join("\n"))
      .digest("hex");
    if (cursor && cursor.t !== treeState.slice(0, 16)) {
      const { cursor: _stale, repeatReceipt: _receipt, ...fresh } = args;
      return failure("CURSOR_STALE", "Files under the search path changed since this cursor was issued; search again from the start.", {
        retryAllowed: true, retryMode: "different_arguments", suggestion: { tool: "search_files", args: fresh },
      });
    }
    // The page after the matches a cursor already returned.
    const page = results.slice(offset);
    const limitReached = results.length >= collectLimit;
    const maxFilesReached = discovered.files.length >= maxFiles;
    const complete = filesRead === files.length && !discoveryErrors && !excludedEntries && !maxFilesReached
      && !limitReached && !stopReason;
    const makePayload = (list, omitted) => {
      const more = omitted || limitReached;
      // A deadline stop rescans differently each time, so it is not resumable.
      const cursorLimitReached = more && offset + list.length >= MAX_CURSOR_MATCHES;
      const nextCursor = more && !stopReason && !cursorLimitReached ? encodeCursor(identity, treeState, offset + list.length) : null;
      const { repeatReceipt: _receipt, cursor: _cursor, ...sameSearch } = args;
      return success({
        path: rootUri,
        ...pathMetadata(resolution),
        query,
        results: list,
        filesScanned: files.length, // Legacy candidate count; filesRead is actual content coverage.
        ...(included ? { include: args.include, filesFilteredByInclude: discovered.files.length - files.length } : {}),
        filesRead, filesSkipped: Object.values(skippedByReason).reduce((a, b) => a + b, 0),
        readErrorCount: skippedByReason.read_error, discoveryErrorCount: discoveryErrors, excludedEntries, skippedByReason,
        coverage: complete && !omitted ? "complete" : "partial",
        maxFilesReached,
        truncated: more,
        ...(cursor ? { cursorOffset: offset } : {}),
        ...(cursorLimitReached ? { stopReason: "cursor_limit", cursorMatchLimit: MAX_CURSOR_MATCHES,
          nextStep: "Narrow the path or query and search again; the continuation match limit was reached." } : {}),
        ...(stopReason ? { stopReason, filesUnvisited: files.length - filesVisited, deadlineMs: limits.searchDeadlineMs } : {}),
        ...(omitted ? { omissionReason: "response_budget" } : {}),
        // Matches that were found but not returned are counted, so a partial
        // page is never read as the absence of further matches.
        ...(more ? { matchesReturned: list.length, matchesOmitted: page.length - list.length,
          moreMatchesPossible: limitReached || Boolean(stopReason) } : {}),
        ...(complete && !omitted ? {} : { absenceVerified: false }),
        ...(nextCursor ? { nextCursor,
          suggestion: { tool: "search_files", args: projectScopedSuggestionArgs(args, { ...sameSearch, cursor: nextCursor }) } } : {}),
      });
    };

    // Measure the envelope once, then add each result's own compact size
    // instead of re-serializing the whole payload per result.
    const reserve = 256;
    const maxChars = limits.maxResponseChars - reserve;
    const maxBytes = args.byteBudget === undefined ? Infinity : args.byteBudget - reserve;
    const envelope = renderModelJson(makePayload([], true));
    let chars = envelope.length;
    let bytes = Buffer.byteLength(envelope, "utf8");
    let fitted = 0;
    for (const result of page) {
      const text = renderModelJson(result);
      const separator = fitted ? 1 : 0;
      if (chars + text.length + separator > maxChars || bytes + Buffer.byteLength(text, "utf8") + separator > maxBytes) break;
      chars += text.length + separator;
      bytes += Buffer.byteLength(text, "utf8") + separator;
      fitted++;
    }
    let payload = makePayload(page.slice(0, fitted), fitted < page.length);
    while (fitted > 0 && !payloadFits(payload, reserve, args.byteBudget)) {
      fitted--;
      payload = makePayload(page.slice(0, fitted), true);
    }
    if (!payloadFits(payload, reserve, args.byteBudget) || !payload.results.length && page.length)
      return failure("OUTPUT_LIMIT_EXCEEDED", "Search metadata or first match exceeds the response budget; increase byteBudget or narrow the search.", { retryAllowed: true });
    return dedupe("search_files", args, `${resolution.activeProject || ""}|${treeState}|${payload.coverage}|${filesRead}|${JSON.stringify(skippedByReason)}|${discoveryErrors}|${excludedEntries}|${stopReason}`, payload);
  }

  return { search_files: searchFiles };
}

module.exports = { MAX_CURSOR_MATCHES, MAX_REGEX_QUERY_CHARS, NESTED_QUANTIFIER, SEARCH_READ_CONCURRENCY, createSearchCapability };

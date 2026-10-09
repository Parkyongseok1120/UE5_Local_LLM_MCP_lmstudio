"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const { assertReadChildContained, displayPath, pathMetadata } = require("./read-path-resolver.js");
const { readStableFileWindow, readStableTextFile } = require("./direct-file-snapshot.js");
const { success, failure } = require("./direct-response.js");
const { registerReadSnapshot, unavailableReadSnapshot: unavailable } = require("./direct-read-snapshot.js");
const { clamp, isBinary, relativeSlash, statOrNull, statSignature } = require("./direct-runtime-shared.js");

const { lexicalSymbolWindow } = require("./direct-symbol-extraction.js");

const fsp = fs.promises;
const DEFAULT_IGNORED_DIRS = new Set([
  ".git", ".vs", ".idea", "Binaries", "DerivedDataCache", "Intermediate",
  "node_modules", ".gradle", ".cache", ".pytest_cache",
]);
const TEXT_EXTENSIONS = new Set([
  ".h", ".hpp", ".inl", ".cpp", ".c", ".cc", ".cxx", ".cs",
  ".ini", ".json", ".uproject", ".uplugin", ".md", ".txt", ".py",
  ".js", ".ts", ".tsx", ".jsx", ".ps1", ".sh", ".bat", ".cmd",
  ".xml", ".yaml", ".yml", ".toml", ".cmake", ".build", ".target",
]);

function createReadCapabilities(context) {
  const {
    dedupe,
    fileSnapshots,
    fitUtf8Prefix,
    limits,
    payloadFits,
    projectScopedSuggestionArgs,
    resolveToolPath,
  } = context;

  async function listDirectory(args) {
    const resolution = await resolveToolPath(args.path, args.project);
    const stat = await statOrNull(resolution.absolutePath);
    if (!stat) {
      return failure("NOT_FOUND", `Directory not found: ${args.path}`, {
        suggestion: {
          tool: "search_files",
          args: projectScopedSuggestionArgs(args, { query: path.basename(String(args.path)), path: ".", matchFileNames: true }),
        },
      });
    }
    if (!stat.isDirectory()) return failure("NOT_A_DIRECTORY", `Not a directory: ${args.path}`);
    const maxEntries = clamp(args.maxEntries, 200, 1, 2000);
    const rawEntries = await fsp.readdir(resolution.absolutePath, { withFileTypes: true });
    const entries = [];
    for (const entry of rawEntries.sort((a, b) => a.name.localeCompare(b.name))) {
      const candidate = path.join(resolution.absolutePath, entry.name);
      await assertReadChildContained(candidate, resolution);
      const childStat = await statOrNull(candidate);
      entries.push({
        name: entry.name,
        type: entry.isDirectory() ? "directory" : entry.isFile() ? "file" : "other",
        size: childStat?.isFile() ? childStat.size : undefined,
        modifiedAt: childStat ? childStat.mtime.toISOString() : undefined,
      });
      if (entries.length >= maxEntries) break;
    }
    const payload = success({
      path: displayPath(resolution),
      ...pathMetadata(resolution),
      entries,
      totalEntries: rawEntries.length,
      truncated: entries.length < rawEntries.length,
    });
    return dedupe("list_directory", args, `${resolution.activeProject || ""}|${statSignature(stat)}`, payload);
  }

  async function collectSearchFiles(rootResolution, maxFiles) {
    const files = [];
    let discoveryErrors = 0, excludedEntries = 0;
    async function walk(target) {
      if (files.length >= maxFiles) return;
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
        if (files.length >= maxFiles) break;
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

  async function searchFiles(args) {
    const query = String(args.query || "");
    if (!query) return failure("INVALID_ARGUMENT", "query is required", { retryAllowed: true });
    const resolution = await resolveToolPath(args.path || "workspace://", args.project);
    const rootStat = await statOrNull(resolution.absolutePath);
    if (!rootStat) return failure("NOT_FOUND", `Search path not found: ${args.path || "."}`);
    const maxResults = clamp(args.maxResults, 100, 1, 1000);
    const maxFiles = clamp(args.maxFiles, 5000, 1, 50000);
    const caseSensitive = args.caseSensitive === true;
    let matcher;
    try {
      matcher = args.regex === true ? new RegExp(query, caseSensitive ? "" : "i") : null;
    } catch (error) {
      return failure("INVALID_REGEX", String(error.message || error), { retryAllowed: true, retryMode: "different_arguments" });
    }
    const inferredFileName = /\\\.[A-Za-z0-9]+\$$/.test(query) || /^\*\./.test(query);
    const matchFileNames = args.matchFileNames !== undefined ? args.matchFileNames === true : inferredFileName;
    const needle = caseSensitive ? query : query.toLowerCase();
    const matchesText = (value) => matcher ? matcher.test(value) : (caseSensitive ? value : value.toLowerCase()).includes(needle);
    const rootUri = displayPath(resolution);
    const resultUri = (rel) => rel === "." ? rootUri : `${rootUri}${rootUri.endsWith("/") ? "" : "/"}${rel}`;
    const { files, discoveryErrors, excludedEntries } = await collectSearchFiles(resolution, maxFiles);
    const skippedByReason = { size: 0, unsupported_extension: 0, binary: 0, read_error: 0 };
    let filesRead = 0;
    const results = [];
    for (const item of files) {
      const rel = relativeSlash(resolution.absolutePath, item.target);
      if (matchFileNames && matchesText(path.basename(item.target))) {
        results.push({ path: rel, uri: resultUri(rel), kind: "file_name" });
        if (results.length >= maxResults) break;
      }
      if (item.stat.size > 2 * 1024 * 1024) { skippedByReason.size++; continue; }
      const ext = path.extname(item.target).toLowerCase();
      if (ext && !TEXT_EXTENSIONS.has(ext)) { skippedByReason.unsupported_extension++; continue; }
      let buffer;
      try {
        buffer = await fsp.readFile(item.target);
      } catch {
        skippedByReason.read_error++; continue;
      }
      if (isBinary(buffer)) { skippedByReason.binary++; continue; }
      filesRead++;
      const lines = buffer.toString("utf8").split(/\r?\n/);
      for (let index = 0; index < lines.length; index += 1) {
        if (!matchesText(lines[index])) continue;
        results.push({ path: rel, uri: resultUri(rel), line: index + 1, text: lines[index].slice(0, 1200) });
        if (results.length >= maxResults) break;
      }
      if (results.length >= maxResults) break;
    }
    const treeState = crypto.createHash("sha256")
      .update(files.map(({ target, stat }) => `${relativeSlash(resolution.absolutePath, target)}:${statSignature(stat)}`).join("\n"))
      .digest("hex");
    const payload = success({
      path: rootUri,
      ...pathMetadata(resolution),
      query,
      results,
      filesScanned: files.length, // Legacy candidate count; filesRead is actual content coverage.
      filesRead, filesSkipped: Object.values(skippedByReason).reduce((a, b) => a + b, 0),
      readErrorCount: skippedByReason.read_error, discoveryErrorCount: discoveryErrors, excludedEntries, skippedByReason,
      coverage: filesRead === files.length && !discoveryErrors && !excludedEntries && files.length < maxFiles && results.length < maxResults ? "complete" : "partial",
      maxFilesReached: files.length >= maxFiles,
      truncated: results.length >= maxResults,
    });
    const foundCount = results.length;
    while (payload.results.length && !payloadFits(payload, 256, args.byteBudget)) {
      payload.results.pop(); payload.truncated = true; payload.coverage = "partial";
      payload.omissionReason = "response_budget";
    }
    if (!payloadFits(payload, 256, args.byteBudget) || !payload.results.length && foundCount && payload.omissionReason)
      return failure("OUTPUT_LIMIT_EXCEEDED", "Search metadata or first match exceeds the response budget; increase byteBudget or narrow the search.", { retryAllowed: true });
    return dedupe("search_files", args, `${resolution.activeProject || ""}|${treeState}|${payload.coverage}|${filesRead}|${JSON.stringify(skippedByReason)}|${discoveryErrors}|${excludedEntries}`, payload);
  }

  async function readFile(args, requestContext = {}) {
    const resolution = await resolveToolPath(args.path, args.project);
    try {
    const maxBytes = clamp(args.maxBytes, limits.maxReadBytes, 1024, 2 * 1024 * 1024);
    const offset = clamp(args.offsetBytes, 0, 0, Number.MAX_SAFE_INTEGER);
    const initialStat = await statOrNull(resolution.absolutePath);
    const initialState = `${resolution.activeProject || ""}|${statSignature(initialStat)}`;
    if (!initialStat) {
      return dedupe("read_file", args, initialState, unavailable(resolution, "NOT_FOUND", `File not found: ${args.path}`, {
        suggestion: {
          tool: "search_files",
          args: projectScopedSuggestionArgs(args, { query: path.basename(String(args.path)), path: "project://", matchFileNames: true }),
        },
      }));
    }
    if (!initialStat.isFile()) return unavailable(resolution, "NOT_A_FILE", `Not a file: ${args.path}`);
    const stableRead = await readStableFileWindow(resolution.absolutePath, offset, maxBytes);
    if (!stableRead.ok) {
      return unavailable(resolution, stableRead.errorCode, stableRead.message, { retryAllowed: true, retryMode: "after_state_change" });
    }
    if (isBinary(stableRead.buffer)) return unavailable(resolution, "BINARY_FILE", `File appears binary: ${args.path}`);
    const version = registerReadSnapshot(fileSnapshots, resolution, stableRead, requestContext);
    const makePayload = (slice, byteLength, decoded) => {
      const nextOffsetBytes = offset + byteLength;
      return success({
        path: displayPath(resolution),
        ...pathMetadata(resolution),
        content: decoded === undefined ? slice.toString("utf8") : decoded,
        sha256: stableRead.hash,
        size: stableRead.stat.size,
        offsetBytes: offset,
        nextOffsetBytes,
        hasMore: nextOffsetBytes < stableRead.stat.size,
        truncated: nextOffsetBytes < stableRead.stat.size,
        ...version,
      });
    };
    const fitted = fitUtf8Prefix(stableRead.buffer, makePayload, args.byteBudget);
    if (!fitted || (stableRead.buffer.length > 0 && fitted.bytes === 0)) {
      return failure("OUTPUT_LIMIT_EXCEEDED", "The next UTF-8 unit cannot fit in the response limit. Reduce surrounding metadata or use a larger configured response limit.", { retryAllowed: true, retryMode: "different_arguments" });
    }
    return dedupe(
      "read_file",
      args,
      `${resolution.activeProject || ""}|${statSignature(stableRead.stat)}`,
      fitted.payload,
    );
    } catch (error) {
      return unavailable(resolution, ["EACCES", "EPERM"].includes(error.code) ? "ACCESS_DENIED" : "READ_FAILED", String(error.message || error));
    }
  }

  async function readFileRange(args, requestContext = {}) {
    const resolution = await resolveToolPath(args.path, args.project);
    const read = await readStableTextFile(resolution.absolutePath, limits.maxSourceBytes);
    if (!read.ok) {
      return unavailable(resolution, read.errorCode, read.message, {
        suggestion: read.errorCode === "NOT_FOUND" ? {
          tool: "search_files",
          args: projectScopedSuggestionArgs(args, { query: path.basename(String(args.path)), path: "project://", matchFileNames: true }),
        } : undefined,
      });
    }
    const lines = read.content.split(/\r?\n/);
    const start = clamp(args.startLine, 1, 1, Math.max(1, lines.length));
    const requestedEnd = clamp(args.endLine, start, start, Math.max(start, lines.length));
    const maximumEnd = Math.min(requestedEnd, start + 3999, lines.length);
    const version = registerReadSnapshot(fileSnapshots, resolution, read, requestContext);
    const makePayload = (end) => success({
      path: displayPath(resolution),
      ...pathMetadata(resolution),
      content: lines.slice(start - 1, end).join("\n"),
      startLine: start,
      endLine: end,
      totalLines: lines.length,
      sha256: read.hash,
      truncated: end < requestedEnd || end < lines.length,
      nextStartLine: end < lines.length ? end + 1 : null,
      ...version,
    });
    let low = start;
    let high = maximumEnd;
    let payload = null;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2);
      const candidate = makePayload(middle);
      if (payloadFits(candidate, 256, args.byteBudget)) {
        payload = candidate;
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }
    if (!payload) {
      // Count original newline bytes, including CRLF, for the byte reader.
      const prefix = read.content.match(/[^\n]*\n/g)?.slice(0, start - 1).join("") || "";
      const byteOffset = Buffer.byteLength(prefix, "utf8");
      return failure("LINE_TOO_LARGE", `Line ${start} cannot fit in one response. Read it with read_file byte windows instead.`, {
        retryAllowed: true,
        retryMode: "different_arguments",
        suggestion: {
          tool: "read_file",
          args: projectScopedSuggestionArgs(args, {
            path: args.path,
            offsetBytes: byteOffset,
            maxBytes: Math.max(1024, Math.floor(limits.maxResponseChars / 4)),
            ...(args.byteBudget === undefined ? {} : { byteBudget: args.byteBudget }),
          }),
        },
      });
    }
    return dedupe("read_file_range", args, `${resolution.activeProject || ""}|${statSignature(read.stat)}`, payload);
  }

  async function readSymbol(args, requestContext = {}) {
    const resolution = await resolveToolPath(args.path, args.project);
    const read = await readStableTextFile(resolution.absolutePath, limits.maxSourceBytes);
    if (!read.ok) return unavailable(resolution, read.errorCode, read.message);
    const window = lexicalSymbolWindow(read.content, args.symbol, args.contextLines);
    if (!window.ok) return window;
    const { symbol, allLines, start, end, candidateCount } = window;
    const version = registerReadSnapshot(fileSnapshots, resolution, read, requestContext);
    const makePayload = returnedEnd => success({
      path: displayPath(resolution),
      symbol,
      ...pathMetadata(resolution),
      content: allLines.slice(start - 1, returnedEnd).join("\n"),
      startLine: start,
      endLine: returnedEnd,
      requestedEndLine: end,
      nextStartLine: returnedEnd < end ? returnedEnd + 1 : null,
      hasMore: returnedEnd < end,
      truncated: returnedEnd < end,
      representation: "lexical_body_candidate",
      candidateCount,
      candidateLines: window.candidateLines,
      selection: window.selection,
      ambiguity: candidateCount > 1 ? "multiple_lexical_matches" : "semantic_resolution_unverified",
      signatureVerification: "lexical_only; overloads_inheritance_macros_and_callability_unverified",
      totalLines: allLines.length,
      sha256: read.hash,
      ...version,
    });
    let low = start, high = end, payload = null;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2), candidate = makePayload(middle);
      if (payloadFits(candidate, 256, args.byteBudget)) { payload = candidate; low = middle + 1; }
      else high = middle - 1;
    }
    if (!payload) return failure("LINE_TOO_LARGE", "The first candidate line or metadata exceeds the response budget; use read_file byte windows or increase byteBudget.", { retryAllowed: true });
    return dedupe("read_symbol", args, `${resolution.activeProject || ""}|${statSignature(read.stat)}`, payload);
  }

  return {
    list_directory: listDirectory,
    read_file: readFile,
    read_file_range: readFileRange,
    read_symbol: readSymbol,
    search_files: searchFiles,
  };
}

module.exports = { createReadCapabilities };

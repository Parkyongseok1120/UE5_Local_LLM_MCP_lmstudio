"use strict";

const fs = require("node:fs");
const path = require("node:path");

const { assertReadChildContained, displayPath, isWithin, pathMetadata } = require("./read-path-resolver.js");
const { ENGINE_URI_PREFIX, isAllowedEngineRelative } = require("./engine-source-access.js");
const { createSearchCapability } = require("./direct-search-capability.js");
const { readStableFileWindow, readStableTextFile, checkReadCancellation } = require("./direct-file-snapshot.js");
const { success, failure } = require("./direct-response.js");
const { prepareReadSnapshot, readMessage, sourceReadState, unavailableReadSnapshot: unavailable } = require("./direct-read-snapshot.js");
const { clamp, isBinary, statOrNull, statSignature } = require("./direct-runtime-shared.js");
const { isSourceLikeExt } = require("./write-guards.js");
const { lineRangeSha256 } = require("./line-range-hash.js");
const { lexicalDeclarationWindow, lexicalSymbolWindow } = require("./direct-symbol-extraction.js");
const { LINE_NUMBER_FORMAT, numberLines } = require("../../shared-tool-core/line-numbering.js");

const fsp = fs.promises;

function createReadCapabilities(context) {
  const {
    dedupe,
    fileSnapshots,
    fitCount,
    fitUtf8Prefix,
    limits,
    payloadFits,
    responseBudgetFailure,
    projectScopedSuggestionArgs,
    resolveToolPath,
  } = context;

  // Locate-first reads: one list for the whole-read refusal and completed source ranges.
  function locateAlternatives(args) {
    return [
      { tool: "search_files", argsTemplate: projectScopedSuggestionArgs(args, { path: args.path, query: "<symbol>", contextLines: 3 }) },
      { tool: "read_symbol", argsTemplate: projectScopedSuggestionArgs(args, { path: args.path, symbol: "<symbol>", view: "declaration" }) },
    ];
  }

  // The one advisory search for a missing path. For engine:// it searches the
  // owning module (or the nearest existing readable ancestor) inside the
  // engine, never the project, and names only directories that exist.
  async function notFoundSuggestion(args, resolution) {
    const query = path.basename(String(args.path || ""));
    if (resolution.resolvedRootType !== "installed_engine") {
      return { tool: "search_files", args: projectScopedSuggestionArgs(args, { query, path: "project://", matchFileNames: true }) };
    }
    const root = resolution.engineLexicalRoot;
    const segments = path.relative(root, resolution.absolutePath).split(path.sep).join("/").split("/");
    const sourceIndex = segments.lastIndexOf("Source");
    const moduleDepth = segments[0] === "Engine" && segments[1] === "Source" ? sourceIndex + 3 : sourceIndex + 2;
    const candidates = [];
    if (sourceIndex >= 0 && segments.length > moduleDepth) candidates.push(segments.slice(0, moduleDepth).join("/"));
    for (let depth = segments.length - 1; depth > 0; depth--) candidates.push(segments.slice(0, depth).join("/"));
    for (const relative of candidates) {
      const directory = path.join(root, relative);
      const stat = await statOrNull(directory);
      if (stat?.isDirectory() && isWithin(directory, root) && isAllowedEngineRelative(relative, { isFile: false })) {
        return { tool: "search_files",
          args: projectScopedSuggestionArgs(args, { query, path: `${ENGINE_URI_PREFIX}${relative}`, matchFileNames: true }) };
      }
    }
    return undefined;
  }

  // byteBudget bounds the whole serialized response (metadata included), not
  // the body; say so whenever it is what stopped the body early.
  function budgetFields(args, cutByBudget) {
    if (args.byteBudget === undefined) return cutByBudget ? { omissionReason: "response_budget" } : {};
    return { effectiveByteBudget: args.byteBudget, budgetScope: "whole_response",
      ...(cutByBudget ? { omissionReason: "byte_budget" } : {}) };
  }

  // The same read, continued: identity and budget kept, receipt dropped.
  function continuationArgs(args, position) {
    const { repeatReceipt: _receipt, project: _project, ...same } = args;
    return { ...same, ...position };
  }

  async function listDirectory(args) {
    const resolution = await resolveToolPath(args.path, args.project);
    const stat = await statOrNull(resolution.absolutePath);
    if (!stat) {
      return failure("NOT_FOUND", `Directory not found: ${args.path}`, {
        suggestion: resolution.resolvedRootType === "installed_engine"
          ? await notFoundSuggestion(args, resolution)
          : { tool: "search_files",
            args: projectScopedSuggestionArgs(args, { query: path.basename(String(args.path)), path: ".", matchFileNames: true }) },
      });
    }
    if (!stat.isDirectory()) return failure("NOT_A_DIRECTORY", `Not a directory: ${args.path}`);
    const maxEntries = clamp(args.maxEntries, 200, 1, 2000);
    const rawEntries = await fsp.readdir(resolution.absolutePath, { withFileTypes: true });
    let omittedByPathPolicy = 0;
    const readableEntries = [];
    for (const entry of rawEntries.sort((a, b) => a.name.localeCompare(b.name))) {
      try {
        if (resolution.resolvedRootType === "installed_engine") {
          await assertReadChildContained(path.join(resolution.absolutePath, entry.name), resolution);
        }
      }
      catch (error) {
        if (error.code !== "ENGINE_SOURCE_PATH_DENIED") throw error;
        omittedByPathPolicy++;
        continue;
      }
      readableEntries.push(entry);
    }
    const offset = clamp(args.offset, 0, 0, readableEntries.length);
    const entries = [];
    for (const entry of readableEntries.slice(offset, offset + maxEntries)) {
      const candidate = path.join(resolution.absolutePath, entry.name);
      await assertReadChildContained(candidate, resolution);
      const childStat = await statOrNull(candidate);
      entries.push({
        name: entry.name,
        type: entry.isDirectory() ? "directory" : entry.isFile() ? "file" : "other",
        size: childStat?.isFile() ? childStat.size : undefined,
        modifiedAt: childStat ? childStat.mtime.toISOString() : undefined,
      });
    }
    const makePayload = (count) => {
      const nextOffset = offset + count < readableEntries.length ? offset + count : null;
      return success({
        path: displayPath(resolution),
        ...pathMetadata(resolution),
        entries: entries.slice(0, count),
        offset,
        nextOffset,
        totalEntries: readableEntries.length,
        ...(resolution.resolvedRootType === "installed_engine" ? { omittedByPathPolicy } : {}),
        truncated: nextOffset !== null,
        ...(count < entries.length ? { omissionReason: "response_budget" } : {}),
      });
    };
    const fitted = fitCount(entries.length, makePayload, args.byteBudget);
    if (!fitted || (entries.length && fitted.count === 0)) {
      return failure("OUTPUT_LIMIT_EXCEEDED", "Directory metadata or the first entry exceeds the response budget; increase byteBudget.", { retryAllowed: true, retryMode: "different_arguments" });
    }
    return dedupe("list_directory", args, `${resolution.activeProject || ""}|${statSignature(stat)}`, fitted.payload);
  }

  async function readFile(args, requestContext = {}) {
    const resolution = await resolveToolPath(args.path, args.project);
    try {
    checkReadCancellation(requestContext.signal);
    const maxBytes = clamp(args.maxBytes, limits.maxReadBytes, 1024, 2 * 1024 * 1024);
    const offset = clamp(args.offsetBytes, 0, 0, Number.MAX_SAFE_INTEGER);
    const initialStat = await statOrNull(resolution.absolutePath);
    const initialState = `${resolution.activeProject || ""}|${statSignature(initialStat)}`;
    if (!initialStat) {
      return dedupe("read_file", args, initialState, unavailable(resolution, "NOT_FOUND", `File not found: ${args.path}`, {
        suggestion: await notFoundSuggestion(args, resolution),
      }));
    }
    if (!initialStat.isFile()) return unavailable(resolution, "NOT_A_FILE", `Not a file: ${args.path}`);
    const explicitWindow = args.offsetBytes !== undefined || args.maxBytes !== undefined;
    const engineFile = resolution.resolvedRootType === "installed_engine", sourceFile = isSourceLikeExt(path.extname(resolution.absolutePath));
    // Small project source reads whole as the numbered read_file_range 1..N payload (2026-10-20 audit: refused
    // headers and 100-line pages split the code the model compared); engine and larger source are located first.
    if (!explicitWindow && !engineFile && sourceFile && initialStat.size <= limits.wholeSourceReadMaxBytes) return readFileRange({ ...args, startLine: 1, endLine: Number.MAX_SAFE_INTEGER, lineNumbers: true }, requestContext);
    const wholeReadRefusal = explicitWindow ? null
      : engineFile ? "engine source requires a targeted read"
        : sourceFile ? `source files are located first, not read whole, above ${limits.wholeSourceReadMaxBytes} bytes`
          : initialStat.size > limits.wholeReadMaxBytes ? `files over ${limits.wholeReadMaxBytes} bytes require a targeted read` : null;
    if (wholeReadRefusal) {
      const counted = await readStableTextFile(resolution.absolutePath, limits.maxSourceBytes, requestContext.signal), totalLines = counted.ok ? counted.content.split(/\r?\n/).length : undefined;
      checkReadCancellation(requestContext.signal);
      return unavailable(resolution, "WHOLE_READ_TOO_LARGE",
        `${args.path} is ${initialStat.size} bytes${totalLines ? ` (${totalLines} lines)` : ""}; ${wholeReadRefusal}. `
        + "Search the symbol with context lines, read its declaration or body, or read the exact line range.", {
          retryAllowed: true, retryMode: "different_arguments", suggestion: locateAlternatives(args)[0],
          details: { size: initialStat.size, ...(totalLines ? { totalLines } : {}), alternatives: [...locateAlternatives(args),
            { tool: "read_file_range", argsTemplate: projectScopedSuggestionArgs(args, { path: args.path, startLine: "<first>", endLine: "<last>" }) }] } });
    }
    const stableRead = await readStableFileWindow(resolution.absolutePath, offset, maxBytes, requestContext.signal);
    if (!stableRead.ok) {
      return unavailable(resolution, stableRead.errorCode, readMessage(stableRead, args, resolution),
        stableRead.errorCode === "REQUEST_CANCELLED" ? { retryAllowed: false, retryMode: "none" }
          : { retryAllowed: true, retryMode: "after_state_change" });
    }
    if (isBinary(stableRead.buffer)) return unavailable(resolution, "BINARY_FILE", `File appears binary: ${args.path}`);
    const snapshot = prepareReadSnapshot(fileSnapshots, resolution, stableRead, requestContext);
    const version = snapshot.fields;
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
        ...budgetFields(args, byteLength < stableRead.buffer.length),
        ...version,
        ...(nextOffsetBytes < stableRead.stat.size ? { suggestion: { tool: "read_file",
          args: projectScopedSuggestionArgs(args, continuationArgs(args, { offsetBytes: nextOffsetBytes })) } } : {}),
      });
    };
    const fitted = fitUtf8Prefix(stableRead.buffer, makePayload, args.byteBudget);
    if (!fitted || (stableRead.buffer.length > 0 && fitted.bytes === 0)) {
      return failure("OUTPUT_LIMIT_EXCEEDED", "The next UTF-8 unit cannot fit in the response limit. byteBudget bounds the whole JSON response including metadata; raise byteBudget (max 65536).", { retryAllowed: true, retryMode: "different_arguments" });
    }
    return dedupe(
      "read_file",
      args,
      sourceReadState(resolution, stableRead),
      fitted.payload,
      snapshot.commit,
    );
    } catch (error) {
      return unavailable(resolution, error.code === "REQUEST_CANCELLED" ? error.code : ["EACCES", "EPERM"].includes(error.code) ? "ACCESS_DENIED" : "READ_FAILED", String(error.message || error));
    }
  }

  async function readFileRange(args, requestContext = {}) {
    const resolution = await resolveToolPath(args.path, args.project);
    const read = await readStableTextFile(resolution.absolutePath, limits.maxSourceBytes, requestContext.signal);
    if (!read.ok) {
      return unavailable(resolution, read.errorCode, readMessage(read, args, resolution), {
        suggestion: read.errorCode === "NOT_FOUND" ? await notFoundSuggestion(args, resolution) : undefined,
      });
    }
    const lines = read.content.split(/\r?\n/);
    const start = clamp(args.startLine, 1, 1, Math.max(1, lines.length));
    const requestedEnd = clamp(args.endLine, start, start, Math.max(start, lines.length));
    const maximumEnd = Math.min(requestedEnd, start + 3999, lines.length);
    const snapshot = prepareReadSnapshot(fileSnapshots, resolution, read, requestContext);
    const version = snapshot.fields;
    const numbered = args.lineNumbers !== false;
    const sourceFile = isSourceLikeExt(path.extname(resolution.absolutePath));
    const makePayload = (end) => success({
      path: displayPath(resolution),
      ...pathMetadata(resolution),
      content: (numbered ? numberLines(lines.slice(start - 1, end), start) : lines.slice(start - 1, end)).join("\n"),
      ...(numbered ? { lineNumbers: true, lineNumberFormat: LINE_NUMBER_FORMAT, lineNumbersAreDisplayOnly: true } : { lineNumbers: false }),
      startLine: start,
      endLine: end, rangeSha256: lineRangeSha256(lines, start, end),
      totalLines: lines.length,
      sha256: read.hash,
      // truncated: the requested range was not returned whole. A file that
      // merely continues after endLine is moreLinesInFile, not truncation.
      truncated: end < requestedEnd,
      requestedEndLine: requestedEnd,
      requestedRangeComplete: end >= requestedEnd,
      rangeComplete: end >= requestedEnd,
      moreLinesInFile: end < lines.length,
      // Only a cut request continues; lines past a completed range are not owed.
      ...(end < requestedEnd ? { nextStartLine: end + 1 } : {}),
      ...(end < requestedEnd ? { omissionReason: end >= maximumEnd ? "line_limit"
        : args.byteBudget === undefined ? "response_budget" : "byte_budget" } : {}),
      ...budgetFields(args, end < maximumEnd),
      ...version,
      ...(end < requestedEnd ? { suggestion: { tool: "read_file_range",
        args: projectScopedSuggestionArgs(args, continuationArgs(args, { startLine: end + 1, endLine: requestedEnd })) } } : {}),
      // A completed source range does not owe the rest of the file: locate, don't sweep to EOF.
      ...(end >= requestedEnd && end < lines.length && sourceFile
        ? { afterRange: { owed: false, locateNext: locateAlternatives(args) } } : {}),
    });
    const full = makePayload(maximumEnd);
    // Completing a range drops continuation metadata, so check it before the
    // monotone partial-page search rather than assuming completion is larger.
    const payload = payloadFits(full, 256, args.byteBudget) ? full
      : fitCount(maximumEnd - start + 1, count => makePayload(start + Math.max(1, count) - 1), args.byteBudget)?.payload;
    if (!payload) {
      const budgetError = responseBudgetFailure(makePayload(start), args, { tool: "read_file_range",
        args: projectScopedSuggestionArgs(args, continuationArgs(args, { startLine: start, endLine: requestedEnd })) });
      if (budgetError) return budgetError;
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
    return dedupe("read_file_range", args, sourceReadState(resolution, read), payload, snapshot.commit);
  }

  async function readSymbol(args, requestContext = {}) {
    const resolution = await resolveToolPath(args.path, args.project);
    const read = await readStableTextFile(resolution.absolutePath, limits.maxSourceBytes, requestContext.signal);
    if (!read.ok) {
      return unavailable(resolution, read.errorCode, readMessage(read, args, resolution),
        read.errorCode === "NOT_FOUND" ? { suggestion: await notFoundSuggestion(args, resolution) } : {});
    }
    if (args.view !== undefined && !["body", "declaration", "member"].includes(args.view)) {
      return failure("INVALID_ARGUMENT", "view must be body, declaration or member.", { retryAllowed: true, retryMode: "different_arguments" });
    }
    if (args.view === "member" && !/^[A-Za-z_]\w*::[A-Za-z_]\w*$/.test(String(args.symbol || ""))) {
      return failure("INVALID_SYMBOL", "member view requires Owner::Name to select the class/struct scope.", { retryAllowed: true });
    }
    const declaration = args.view === "declaration" || args.view === "member";
    const window = (declaration ? lexicalDeclarationWindow : lexicalSymbolWindow)(read.content, args.symbol, args.contextLines);
    if (!window.ok) return window;
    const { symbol, allLines, start, end, candidateCount } = window;
    const snapshot = prepareReadSnapshot(fileSnapshots, resolution, read, requestContext);
    const version = snapshot.fields;
    const numbered = args.lineNumbers !== false;
    const makePayload = returnedEnd => success({
      path: displayPath(resolution),
      symbol,
      ...pathMetadata(resolution),
      content: (numbered ? numberLines(allLines.slice(start - 1, returnedEnd), start) : allLines.slice(start - 1, returnedEnd)).join("\n"),
      ...(numbered ? { lineNumbers: true, lineNumberFormat: LINE_NUMBER_FORMAT, lineNumbersAreDisplayOnly: true } : { lineNumbers: false }),
      startLine: start,
      endLine: returnedEnd, rangeSha256: lineRangeSha256(allLines, start, returnedEnd),
      requestedEndLine: end,
      nextStartLine: returnedEnd < end ? returnedEnd + 1 : null,
      hasMore: returnedEnd < end,
      truncated: returnedEnd < end,
      representation: declaration ? "lexical_declaration_candidate" : "lexical_body_candidate",
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
    return dedupe("read_symbol", args, sourceReadState(resolution, read), payload, snapshot.commit);
  }

  return {
    list_directory: listDirectory,
    read_file: readFile,
    read_file_range: readFileRange,
    read_symbol: readSymbol,
    ...createSearchCapability(context, { notFoundSuggestion }),
  };
}

module.exports = { createReadCapabilities };

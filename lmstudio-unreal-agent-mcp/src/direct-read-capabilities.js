"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const { assertReadChildContained, displayPath, pathMetadata } = require("./read-path-resolver.js");
const { readStableFileWindow, readStableTextFile } = require("./direct-file-snapshot.js");
const { success, failure } = require("./direct-response.js");
const {
  clamp,
  isBinary,
  relativeSlash,
  statOrNull,
  statSignature,
} = require("./direct-runtime-shared.js");

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
    async function walk(target) {
      if (files.length >= maxFiles) return;
      const stat = await statOrNull(target);
      if (!stat) return;
      if (stat.isFile()) {
        files.push({ target, stat });
        return;
      }
      if (!stat.isDirectory()) return;
      const entries = await fsp.readdir(target, { withFileTypes: true });
      for (const entry of entries) {
        if (files.length >= maxFiles) break;
        if (entry.isDirectory() && DEFAULT_IGNORED_DIRS.has(entry.name)) continue;
        const child = path.join(target, entry.name);
        await assertReadChildContained(child, rootResolution);
        if (entry.isDirectory() || entry.isFile()) await walk(child);
      }
    }
    await walk(rootResolution.absolutePath);
    return files;
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
    const files = await collectSearchFiles(resolution, maxFiles);
    const results = [];
    for (const item of files) {
      const rel = relativeSlash(resolution.absolutePath, item.target);
      if (matchFileNames && matchesText(path.basename(item.target))) {
        results.push({ path: rel, kind: "file_name" });
        if (results.length >= maxResults) break;
      }
      if (item.stat.size > 2 * 1024 * 1024) continue;
      const ext = path.extname(item.target).toLowerCase();
      if (ext && !TEXT_EXTENSIONS.has(ext)) continue;
      let buffer;
      try {
        buffer = await fsp.readFile(item.target);
      } catch {
        continue;
      }
      if (isBinary(buffer)) continue;
      const lines = buffer.toString("utf8").split(/\r?\n/);
      for (let index = 0; index < lines.length; index += 1) {
        if (!matchesText(lines[index])) continue;
        results.push({ path: rel, line: index + 1, text: lines[index].slice(0, 1200) });
        if (results.length >= maxResults) break;
      }
      if (results.length >= maxResults) break;
    }
    const treeState = crypto.createHash("sha256")
      .update(files.map(({ target, stat }) => `${relativeSlash(resolution.absolutePath, target)}:${statSignature(stat)}`).join("\n"))
      .digest("hex");
    const payload = success({
      path: displayPath(resolution),
      query,
      results,
      filesScanned: files.length,
      maxFilesReached: files.length >= maxFiles,
      truncated: results.length >= maxResults,
    });
    return dedupe("search_files", args, `${resolution.activeProject || ""}|${treeState}`, payload);
  }

  async function readFile(args) {
    const resolution = await resolveToolPath(args.path, args.project);
    const maxBytes = clamp(args.maxBytes, limits.maxReadBytes, 1024, 2 * 1024 * 1024);
    const offset = clamp(args.offsetBytes, 0, 0, Number.MAX_SAFE_INTEGER);
    const initialStat = await statOrNull(resolution.absolutePath);
    const initialState = `${resolution.activeProject || ""}|${statSignature(initialStat)}`;
    if (!initialStat) {
      return dedupe("read_file", args, initialState, failure("NOT_FOUND", `File not found: ${args.path}`, {
        suggestion: {
          tool: "search_files",
          args: projectScopedSuggestionArgs(args, { query: path.basename(String(args.path)), path: "project://", matchFileNames: true }),
        },
      }));
    }
    if (!initialStat.isFile()) return failure("NOT_A_FILE", `Not a file: ${args.path}`);
    const stableRead = await readStableFileWindow(resolution.absolutePath, offset, maxBytes);
    if (!stableRead.ok) {
      return failure(stableRead.errorCode, stableRead.message, { retryAllowed: true, retryMode: "after_state_change" });
    }
    if (isBinary(stableRead.buffer)) return failure("BINARY_FILE", `File appears binary: ${args.path}`);
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
      });
    };
    const fitted = fitUtf8Prefix(stableRead.buffer, makePayload);
    if (!fitted || (stableRead.buffer.length > 0 && fitted.bytes === 0)) {
      return failure("OUTPUT_LIMIT_EXCEEDED", "The next UTF-8 unit cannot fit in the response limit. Reduce surrounding metadata or use a larger configured response limit.", { retryAllowed: true, retryMode: "different_arguments" });
    }
    return dedupe(
      "read_file",
      args,
      `${resolution.activeProject || ""}|${statSignature(stableRead.stat)}`,
      fitted.payload,
    );
  }

  async function readFileRange(args) {
    const resolution = await resolveToolPath(args.path, args.project);
    const read = await readStableTextFile(resolution.absolutePath, limits.maxSourceBytes);
    if (!read.ok) {
      return failure(read.errorCode, read.message, {
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
    });
    let low = start;
    let high = maximumEnd;
    let payload = null;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2);
      const candidate = makePayload(middle);
      if (payloadFits(candidate, 256)) {
        payload = candidate;
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }
    if (!payload) {
      const byteOffset = Buffer.byteLength(lines.slice(0, start - 1).join("\n") + (start > 1 ? "\n" : ""), "utf8");
      return failure("LINE_TOO_LARGE", `Line ${start} cannot fit in one response. Read it with read_file byte windows instead.`, {
        retryAllowed: true,
        retryMode: "different_arguments",
        suggestion: {
          tool: "read_file",
          args: projectScopedSuggestionArgs(args, {
            path: args.path,
            offsetBytes: byteOffset,
            maxBytes: Math.max(1024, Math.floor(limits.maxResponseChars / 4)),
          }),
        },
      });
    }
    return dedupe("read_file_range", args, `${resolution.activeProject || ""}|${statSignature(read.stat)}`, payload);
  }

  async function readSymbol(args) {
    const resolution = await resolveToolPath(args.path, args.project);
    const read = await readStableTextFile(resolution.absolutePath, limits.maxSourceBytes);
    if (!read.ok) return failure(read.errorCode, read.message);
    const symbol = String(args.symbol || "").trim();
    const parts = symbol.split("::");
    const leaf = parts.at(-1) || "";
    if (!/^[A-Za-z_~][A-Za-z0-9_~]*$/.test(leaf)) return failure("INVALID_SYMBOL", "symbol must be a C/C++ identifier", { retryAllowed: true });
    const escape = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const qualified = new RegExp(`\\b${parts.map(escape).join("\\s*::\\s*")}\\s*\\(`, "m");
    const fallback = new RegExp(`\\b${escape(leaf)}\\s*\\(`, "m");
    const match = qualified.exec(read.content) || fallback.exec(read.content);
    if (!match) {
      return failure("SYMBOL_NOT_FOUND", `Symbol not found: ${symbol}`, {
        suggestion: { tool: "search_files", args: projectScopedSuggestionArgs(args, { query: leaf, path: "project://Source" }) },
      });
    }
    const braceStart = read.content.indexOf("{", match.index + match[0].length);
    const semicolon = read.content.indexOf(";", match.index + match[0].length);
    if (braceStart < 0 || (semicolon >= 0 && semicolon < braceStart)) {
      return failure("SYMBOL_BODY_NOT_FOUND", `Definition body not found: ${symbol}`, {
        suggestion: { tool: "search_files", args: projectScopedSuggestionArgs(args, { query: `${leaf}(`, path: "project://Source" }) },
      });
    }
    let depth = 0;
    let endOffset = -1;
    let quote = "";
    let escaped = false;
    for (let index = braceStart; index < read.content.length; index += 1) {
      const char = read.content[index];
      if (quote) {
        if (escaped) escaped = false;
        else if (char === "\\") escaped = true;
        else if (char === quote) quote = "";
        continue;
      }
      if (char === "\"" || char === "'") {
        quote = char;
        continue;
      }
      if (char === "{") depth += 1;
      if (char === "}" && --depth === 0) {
        endOffset = index;
        break;
      }
    }
    if (endOffset < 0) return failure("UNBALANCED_SYMBOL_BODY", `Unbalanced function body: ${symbol}`);
    const allLines = read.content.split(/\r?\n/);
    const lineAt = (offset) => read.content.slice(0, offset).split(/\r?\n/).length;
    const contextLines = clamp(args.contextLines, 3, 0, 30);
    const start = Math.max(1, lineAt(match.index) - contextLines);
    const end = Math.min(allLines.length, lineAt(endOffset) + contextLines);
    const payload = success({
      path: displayPath(resolution),
      symbol,
      content: allLines.slice(start - 1, end).join("\n"),
      startLine: start,
      endLine: end,
      totalLines: allLines.length,
      sha256: read.hash,
    });
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

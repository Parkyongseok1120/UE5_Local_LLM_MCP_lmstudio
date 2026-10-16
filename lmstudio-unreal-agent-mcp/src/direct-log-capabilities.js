"use strict";

const fs = require("node:fs");
const path = require("node:path");

const { readUtf8Tail, readUtf8Window } = require("./bounded-read.js");
const { success, failure } = require("./direct-response.js");
const {
  clamp,
  stableStatIdentity,
  statOrNull,
  statSignature,
} = require("./direct-runtime-shared.js");

const fsp = fs.promises;

function createLogCapabilities(context) {
  const { dedupe, limits, payloadFits, resolveCallProject, workspaceRoot } = context;

  async function logCandidates(args) {
    const active = await resolveCallProject(args.project);
    const roots = [path.join(workspaceRoot, ".agent", "logs")];
    if (active) roots.push(path.join(path.dirname(path.resolve(active)), "Saved", "Logs"));
    const exact = String(args.fileName || "").trim();
    if (exact && path.basename(exact) !== exact) {
      throw new Error("fileName must be an exact basename without directory components");
    }
    const candidates = [];
    for (const root of roots) {
      let entries;
      try {
        entries = await fsp.readdir(root, { withFileTypes: true });
      } catch {
        continue;
      }
      for (const entry of entries) {
        if (!entry.isFile()) continue;
        if (exact && entry.name !== exact) continue;
        if (!exact && !/\.(?:log|txt)$/i.test(entry.name)) continue;
        const target = path.join(root, entry.name);
        const stat = await statOrNull(target);
        if (stat?.isFile()) candidates.push({ target, stat });
      }
    }
    return candidates.sort((a, b) => b.stat.mtimeMs - a.stat.mtimeMs);
  }

  async function readLogWindows(args, mode, candidates, perFileBytes) {
    const maxFiles = clamp(args.maxFiles, 1, 1, 3);
    const maxLines = clamp(args.maxLines, 80, 1, 500);
    const filter = String(args.filter || "").toLowerCase();
    const logs = [];
    const stableStats = [];
    for (const item of candidates.slice(0, maxFiles)) {
      const before = await statOrNull(item.target);
      if (!before?.isFile()) continue;
      let start = 0;
      let nextCursorByte = before.size;
      let hasMore = false;
      let sourceContent = "";
      if (mode === "tail") {
        const tail = await readUtf8Tail(item.target, perFileBytes);
        start = Math.max(0, before.size - tail.bytesRead);
        sourceContent = tail.content;
      } else {
        start = mode === "range" ? clamp(args.cursorByte, 0, 0, before.size) : 0;
        const window = await readUtf8Window(item.target, {
          startByte: start,
          maxBytes: perFileBytes,
          maxLines,
        });
        sourceContent = window.content;
        nextCursorByte = window.nextCursorByte;
        hasMore = window.hasMore;
      }
      const after = await statOrNull(item.target);
      if (stableStatIdentity(before) !== stableStatIdentity(after)) {
        return { failure: failure("READ_CHANGED_DURING_CALL", `Log changed while it was being read: ${path.basename(item.target)}`, {
          retryAllowed: true,
          retryMode: "after_state_change",
        }) };
      }
      stableStats.push({ target: item.target, stat: after });
      let lines = sourceContent.split(/\r?\n/);
      if (mode === "first_error") {
        const index = lines.findIndex((line) => /\b(?:fatal\s+)?error\b|ensure condition failed|assertion failed|Unhandled Exception|LNK\d+|error C\d+/i.test(line));
        lines = index >= 0 ? lines.slice(Math.max(0, index - 3), index + 21) : lines.slice(0, maxLines);
      } else if (mode === "tail") {
        lines = lines.slice(-maxLines);
      }
      if (filter) lines = lines.filter((line) => line.toLowerCase().includes(filter));
      const content = lines.join("\n");
      logs.push({
        fileName: path.basename(item.target),
        fullPath: item.target,
        modifiedAt: after.mtime.toISOString(),
        size: after.size,
        cursorByte: start,
        nextCursorByte,
        hasMore,
        content,
        truncated: hasMore || Buffer.byteLength(content, "utf8") < Buffer.byteLength(sourceContent, "utf8"),
      });
    }
    return { logs, stableStats };
  }

  async function readUnrealLogs(args) {
    const mode = String(args.mode || "tail");
    const candidates = await logCandidates(args);
    const maxFiles = clamp(args.maxFiles, 1, 1, 3);
    const maxBytes = clamp(args.maxBytes, 256 * 1024, 1024, 4 * 1024 * 1024);
    // Shrink the source window (never the returned text) until the payload
    // fits, so every cursor still points at bytes the model actually received.
    const responseBytes = Math.min(limits.maxResponseChars, args.byteBudget ?? Number.MAX_SAFE_INTEGER);
    let perFileBytes = Math.min(maxBytes, Math.max(256, Math.floor((responseBytes - 1024) / maxFiles)));
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const read = await readLogWindows(args, mode, candidates, perFileBytes);
      if (read.failure) return read.failure;
      if (!read.logs.length) {
        return failure("LOG_NOT_FOUND", args.fileName ? `Log not found: ${args.fileName}` : "No Unreal or MCP build logs were found.");
      }
      const payload = success({ mode, logs: read.logs });
      if (payloadFits(payload, 256, args.byteBudget)) {
        const state = read.stableStats.map((item) => statSignature(item.stat, item.target)).join("|");
        return dedupe("read_unreal_logs", args, state, payload);
      }
      if (perFileBytes <= 256) break;
      perFileBytes = Math.max(256, Math.floor(perFileBytes / 2));
    }
    return failure("OUTPUT_LIMIT_EXCEEDED", "Log metadata exceeds the response budget; increase byteBudget or lower maxFiles.", {
      retryAllowed: true,
      retryMode: "different_arguments",
    });
  }

  return { read_unreal_logs: readUnrealLogs };
}

module.exports = { createLogCapabilities };

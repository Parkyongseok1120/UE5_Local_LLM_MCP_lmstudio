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
  const { dedupe, limits, resolveCallProject, workspaceRoot } = context;

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

  async function readUnrealLogs(args) {
    const mode = String(args.mode || "tail");
    const candidates = await logCandidates(args);
    const maxFiles = clamp(args.maxFiles, 1, 1, 3);
    const maxBytes = clamp(args.maxBytes, 256 * 1024, 1024, 4 * 1024 * 1024);
    const maxLines = clamp(args.maxLines, 80, 1, 500);
    const filter = String(args.filter || "").toLowerCase();
    const logs = [];
    const stableStats = [];
    const perFileBytes = Math.min(
      maxBytes,
      Math.max(1024, Math.floor((limits.maxResponseChars - 8192) / Math.max(1, maxFiles) / 8)),
    );
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
        return failure("READ_CHANGED_DURING_CALL", `Log changed while it was being read: ${path.basename(item.target)}`, {
          retryAllowed: true,
          retryMode: "after_state_change",
        });
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
    if (!logs.length) {
      return failure("LOG_NOT_FOUND", args.fileName ? `Log not found: ${args.fileName}` : "No Unreal or MCP build logs were found.");
    }
    const state = stableStats.map((item) => statSignature(item.stat, item.target)).join("|");
    return dedupe("read_unreal_logs", args, state, success({ mode, logs }));
  }

  return { read_unreal_logs: readUnrealLogs };
}

module.exports = { createLogCapabilities };

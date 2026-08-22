"use strict";

const fs = require("node:fs");
const crypto = require("node:crypto");

const { sha256Buffer } = require("./safe-write.js");
const { isBinary, stableStatIdentity, statOrNull } = require("./direct-runtime-shared.js");

const fsp = fs.promises;

async function openOrResult(target) {
  try {
    return { handle: await fsp.open(target, "r") };
  } catch (error) {
    if (error.code === "ENOENT") {
      return { result: { ok: false, errorCode: "NOT_FOUND", message: `File not found: ${target}`, stat: null } };
    }
    throw error;
  }
}

async function readAt(handle, position, length) {
  const buffer = Buffer.alloc(Math.max(0, length));
  let bytesRead = 0;
  while (bytesRead < length) {
    const part = await handle.read(buffer, bytesRead, length - bytesRead, position + bytesRead);
    if (!part.bytesRead) break;
    bytesRead += part.bytesRead;
  }
  return buffer.subarray(0, bytesRead);
}

async function snapshotStillCurrent(target, before, handle) {
  const after = await handle.stat();
  const currentPathStat = await statOrNull(target);
  return stableStatIdentity(before) === stableStatIdentity(after)
    && stableStatIdentity(after) === stableStatIdentity(currentPathStat);
}

function changedResult() {
  return {
    ok: false,
    errorCode: "READ_CHANGED_DURING_CALL",
    message: "The file changed while it was being read. Retry the read before using its hash or cursor.",
  };
}

async function readStableTextFile(target, maxBytes) {
  const opened = await openOrResult(target);
  if (opened.result) return opened.result;
  const { handle } = opened;
  try {
    const before = await handle.stat();
    if (!before.isFile()) return { ok: false, errorCode: "NOT_A_FILE", message: `Not a file: ${target}`, stat: before };
    if (before.size > maxBytes) {
      return { ok: false, errorCode: "FILE_TOO_LARGE", message: `File is ${before.size} bytes; limit is ${maxBytes}.`, stat: before };
    }
    const buffer = await readAt(handle, 0, before.size);
    if (buffer.length !== before.size || !(await snapshotStillCurrent(target, before, handle))) return changedResult();
    if (isBinary(buffer)) return { ok: false, errorCode: "BINARY_FILE", message: `File appears binary: ${target}`, stat: before };
    return { ok: true, stat: before, buffer, content: buffer.toString("utf8"), hash: sha256Buffer(buffer) };
  } finally {
    await handle.close();
  }
}

async function readStableFileWindow(target, offset, maxBytes) {
  const opened = await openOrResult(target);
  if (opened.result) return opened.result;
  const { handle } = opened;
  try {
    const before = await handle.stat();
    if (!before.isFile()) return { ok: false, errorCode: "NOT_A_FILE", message: `Not a file: ${target}`, stat: before };
    if (offset > before.size) {
      return { ok: false, errorCode: "OFFSET_OUT_OF_RANGE", message: `offsetBytes ${offset} exceeds file size ${before.size}.` };
    }
    const length = Math.max(0, Math.min(maxBytes, before.size - offset));
    const window = await readAt(handle, offset, length);
    const digest = crypto.createHash("sha256");
    let position = 0;
    while (position < before.size) {
      const part = await readAt(handle, position, Math.min(64 * 1024, before.size - position));
      if (!part.length) break;
      digest.update(part);
      position += part.length;
    }
    if (position !== before.size || !(await snapshotStillCurrent(target, before, handle))) return changedResult();
    return { ok: true, stat: before, buffer: window, hash: digest.digest("hex") };
  } finally {
    await handle.close();
  }
}

module.exports = {
  readStableFileWindow,
  readStableTextFile,
  snapshotStillCurrent,
};

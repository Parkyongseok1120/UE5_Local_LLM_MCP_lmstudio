"use strict";

const fs = require("node:fs");
const crypto = require("node:crypto");



const fsp = fs.promises;

function sha256Buffer(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

function sha256Text(text) {
  return sha256Buffer(Buffer.from(String(text ?? ""), "utf8"));
}

function stableStatIdentity(stat) {
  return stat
    ? `${stat.dev || 0}:${stat.ino || 0}:${stat.size}:${Math.trunc(stat.mtimeMs)}:${Math.trunc(stat.ctimeMs)}`
    : "missing";
}

async function statOrNull(target) {
  try {
    return await fsp.stat(target);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

function isBinary(buffer) {
  const sample = buffer.subarray(0, Math.min(buffer.length, 8192));
  return sample.includes(0);
}

function readFailure(error) {
  return { ok: false, errorCode: error?.code === "REQUEST_CANCELLED" ? "REQUEST_CANCELLED" : ["EACCES", "EPERM"].includes(error?.code) ? "ACCESS_DENIED" : error?.code === "ENOENT" ? "NOT_FOUND" : "READ_FAILED",
    message: String(error?.message || error) };
}

function checkReadCancellation(signal) {
  if (!signal?.aborted) return;
  const error = new Error("Source observation canceled before completion.");
  error.code = error.directErrorCode = "REQUEST_CANCELLED";
  error.details = { cancelled: true, observationProduced: false };
  throw error;
}

async function openOrResult(target, signal) {
  try {
    checkReadCancellation(signal);
    return { handle: await fsp.open(target, "r") };
  } catch (error) {
    if (error.code === "ENOENT") {
      return { result: { ok: false, errorCode: "NOT_FOUND", message: `File not found: ${target}`, stat: null } };
    }
    return { result: readFailure(error) };
  }
}

async function readAt(handle, position, length, signal) {
  checkReadCancellation(signal);
  const buffer = Buffer.alloc(Math.max(0, length));
  let bytesRead = 0;
  while (bytesRead < length) {
    const part = await handle.read(buffer, bytesRead, Math.min(64 * 1024, length - bytesRead), position + bytesRead);
    checkReadCancellation(signal);
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

async function readStableTextFile(target, maxBytes, signal) {
  const opened = await openOrResult(target, signal);
  if (opened.result) return opened.result;
  const { handle } = opened;
  try {
    const before = await handle.stat();
    checkReadCancellation(signal);
    if (!before.isFile()) return { ok: false, errorCode: "NOT_A_FILE", message: `Not a file: ${target}`, stat: before };
    if (before.size > maxBytes) {
      return { ok: false, errorCode: "FILE_TOO_LARGE", message: `File is ${before.size} bytes; limit is ${maxBytes}.`, stat: before };
    }
    const buffer = await readAt(handle, 0, before.size, signal);
    if (buffer.length !== before.size || !(await snapshotStillCurrent(target, before, handle))) return changedResult();
    checkReadCancellation(signal);
    if (isBinary(buffer)) return { ok: false, errorCode: "BINARY_FILE", message: `File appears binary: ${target}`, stat: before };
    return { ok: true, stat: before, buffer, content: buffer.toString("utf8"), hash: sha256Buffer(buffer) };
  } catch (error) {
    return readFailure(error);
  } finally {
    await handle.close();
  }
}

async function readStableFileWindow(target, offset, maxBytes, signal) {
  const opened = await openOrResult(target, signal);
  if (opened.result) return opened.result;
  const { handle } = opened;
  try {
    const before = await handle.stat();
    checkReadCancellation(signal);
    if (!before.isFile()) return { ok: false, errorCode: "NOT_A_FILE", message: `Not a file: ${target}`, stat: before };
    if (offset > before.size) {
      return { ok: false, errorCode: "OFFSET_OUT_OF_RANGE", message: `offsetBytes ${offset} exceeds file size ${before.size}.` };
    }
    const length = Math.max(0, Math.min(maxBytes, before.size - offset));
    const window = await readAt(handle, offset, length, signal);
    const digest = crypto.createHash("sha256");
    let position = 0;
    while (position < before.size) {
      const part = await readAt(handle, position, Math.min(64 * 1024, before.size - position), signal);
      if (!part.length) break;
      digest.update(part);
      position += part.length;
    }
    if (position !== before.size || !(await snapshotStillCurrent(target, before, handle))) return changedResult();
    checkReadCancellation(signal);
    return { ok: true, stat: before, buffer: window, hash: digest.digest("hex") };
  } catch (error) {
    return readFailure(error);
  } finally {
    await handle.close();
  }
}

module.exports = {
  checkReadCancellation,
  sha256Buffer, sha256Text, stableStatIdentity, statOrNull, isBinary,
  readStableFileWindow,
  readStableTextFile,
  snapshotStillCurrent,
};

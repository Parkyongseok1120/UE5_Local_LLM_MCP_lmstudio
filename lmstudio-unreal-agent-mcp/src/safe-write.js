"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const crypto = require("crypto");
const { atomicWriteText, atomicWriteTextExclusive } = require("./atomic-io");

function sha256Buffer(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

function sha256Text(text) {
  return sha256Buffer(Buffer.from(String(text ?? ""), "utf8"));
}

async function sha256File(absPath) {
  return sha256Buffer(await fsp.readFile(absPath));
}

function verifyReadHash(currentHash, expectedHash, label = "file") {
  if (!expectedHash) {
    return { ok: true };
  }
  const expected = String(expectedHash).toLowerCase();
  const actual = String(currentHash || "").toLowerCase();
  if (actual !== expected) {
    return {
      ok: false,
      error: `CAS mismatch for ${label}: file changed since read evidence (expected ${expected.slice(0, 12)}..., got ${actual.slice(0, 12)}...)`,
      errorCode: "READ_HASH_CAS_MISMATCH",
    };
  }
  return { ok: true };
}

async function assertPreCommitHash(absPath, expectedHash) {
  if (!expectedHash || !(await fileExists(absPath))) {
    return { ok: true, hash: expectedHash || "" };
  }
  const current = await sha256File(absPath);
  const check = verifyReadHash(current, expectedHash, absPath);
  if (!check.ok) {
    return { ...check, hash: current };
  }
  return { ok: true, hash: current };
}

async function fileExists(absPath) {
  try {
    const st = await fsp.stat(absPath);
    return st.isFile();
  } catch {
    return false;
  }
}

async function replaceWithCAS({
  targetPath,
  priorContent,
  oldText,
  newText,
  expectedOccurrences,
  readHash,
  normalizeLineEndings = true,
}) {
  const raw = Buffer.isBuffer(priorContent)
    ? priorContent
    : Buffer.from(String(priorContent ?? ""), "utf8");
  const preHash = sha256Buffer(raw);
  const cas = verifyReadHash(preHash, readHash, targetPath);
  if (!cas.ok) {
    return cas;
  }

  const hasCRLF = raw.includes(Buffer.from("\r\n"));
  const content = raw.toString("utf8");
  const contentNorm = normalizeLineEndings ? content.replace(/\r\n/g, "\n") : content;
  const oldTextNorm = normalizeLineEndings ? String(oldText).replace(/\r\n/g, "\n") : String(oldText);
  const occurrences = oldTextNorm ? contentNorm.split(oldTextNorm).length - 1 : 0;
  if (expectedOccurrences !== undefined && occurrences !== expectedOccurrences) {
    return {
      ok: false,
      error: `occurrence mismatch: expected ${expectedOccurrences}, found ${occurrences}`,
    };
  }
  if (occurrences === 0) {
    return { ok: false, error: "oldText not found in target file" };
  }

  const replacement = normalizeLineEndings ? String(newText).replace(/\r\n/g, "\n") : String(newText);
  const updatedNorm = expectedOccurrences === 1
    ? contentNorm.replace(oldTextNorm, replacement)
    : contentNorm.split(oldTextNorm).join(replacement);
  const updated = hasCRLF && normalizeLineEndings ? updatedNorm.replace(/\n/g, "\r\n") : updatedNorm;

  atomicWriteText(targetPath, updated);
  return { ok: true, updated, occurrences, priorContent: content, preHash };
}

async function createExclusive(targetPath, content) {
  atomicWriteTextExclusive(targetPath, String(content ?? ""));
  return { ok: true };
}

module.exports = {
  sha256Buffer,
  sha256Text,
  sha256File,
  verifyReadHash,
  assertPreCommitHash,
  replaceWithCAS,
  createExclusive,
};

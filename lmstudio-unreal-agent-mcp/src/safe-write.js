"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const { sha256Buffer, sha256Text } = require("../../shared-tool-core/direct-file-snapshot");
const { atomicWriteText, atomicCreateText } = require("./atomic-io");
const {
  LINE_NUMBER_PATCH_ERROR,
  LINE_NUMBER_PATCH_MESSAGE,
  isLineNumberPrefixedPatch,
} = require("../../shared-tool-core/line-numbering.js");
const { lineRangeSha256 } = require("./line-range-hash");

function assertMutationActive(signal) {
  if (signal?.aborted) {
    const error = new Error("Request cancelled before file publication.");
    error.code = "REQUEST_CANCELLED";
    throw error;
  }
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

function calculateReplacement({
  priorContent,
  oldText,
  newText,
  expectedOccurrences,
  normalizeLineEndings = true,
}) {
  const raw = Buffer.isBuffer(priorContent)
    ? priorContent
    : Buffer.from(String(priorContent ?? ""), "utf8");
  const hasCRLF = raw.includes(Buffer.from("\r\n"));
  const content = raw.toString("utf8");
  const contentNorm = normalizeLineEndings ? content.replace(/\r\n/g, "\n") : content;
  const oldTextNorm = normalizeLineEndings ? String(oldText).replace(/\r\n/g, "\n") : String(oldText);
  const occurrences = oldTextNorm ? contentNorm.split(oldTextNorm).length - 1 : 0;
  // Text copied from a numbered read ("<n>\t<text>") is a display artifact, not file content.
  if (occurrences === 0 && isLineNumberPrefixedPatch(oldTextNorm, contentNorm)) {
    return { ok: false, errorCode: LINE_NUMBER_PATCH_ERROR, error: LINE_NUMBER_PATCH_MESSAGE };
  }
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
  return { ok: true, updated, occurrences, priorContent: content, preHash: sha256Buffer(raw) };
}

// Several exact, non-overlapping regions of one file, each found exactly once in
// the same prior content, applied together. Returns each region's committed
// lines (start lines in the prior and the updated text) as change evidence.
function calculateReplacements({ priorContent, edits, normalizeLineEndings = true }) {
  const raw = Buffer.isBuffer(priorContent) ? priorContent : Buffer.from(String(priorContent ?? ""), "utf8");
  const hasCRLF = raw.includes(Buffer.from("\r\n"));
  const content = raw.toString("utf8");
  const norm = (value) => (normalizeLineEndings ? String(value).replace(/\r\n/g, "\n") : String(value));
  const contentNorm = norm(content);
  const spans = [];
  for (const [editIndex, edit] of edits.entries()) {
    const oldTextNorm = norm(edit.oldText);
    // split() misses overlapping matches ("aaa" occurs twice in "aaaa").
    let occurrences = 0;
    for (let at = oldTextNorm ? contentNorm.indexOf(oldTextNorm) : -1; at >= 0;
      at = contentNorm.indexOf(oldTextNorm, at + 1)) occurrences++;
    if (occurrences === 0 && isLineNumberPrefixedPatch(oldTextNorm, contentNorm)) {
      return { ok: false, errorCode: LINE_NUMBER_PATCH_ERROR, error: LINE_NUMBER_PATCH_MESSAGE, editIndex };
    }
    if (occurrences !== 1) {
      return { ok: false, errorCode: "OCCURRENCE_MISMATCH", editIndex,
        error: `edits[${editIndex}] occurrence mismatch: expected 1, found ${occurrences}` };
    }
    const start = contentNorm.indexOf(oldTextNorm);
    spans.push({ editIndex, start, end: start + oldTextNorm.length, replacement: norm(edit.newText) });
  }
  const ordered = [...spans].sort((a, b) => a.start - b.start);
  for (let index = 1; index < ordered.length; index++) {
    if (ordered[index].start < ordered[index - 1].end) {
      const later = Math.max(ordered[index].editIndex, ordered[index - 1].editIndex);
      return { ok: false, errorCode: "EDITS_OVERLAP", editIndex: later,
        error: `edits[${later}] overlaps another edit; merge them into one region.` };
    }
  }
  let updatedNorm = "", cursor = 0, lineDelta = 0;
  const regions = [];
  for (const span of ordered) {
    const beforeStartLine = contentNorm.slice(0, span.start).split("\n").length;
    const before = contentNorm.slice(span.start, span.end);
    regions.push({ beforeStartLine, afterStartLine: beforeStartLine + lineDelta, before, after: span.replacement });
    lineDelta += span.replacement.split("\n").length - before.split("\n").length;
    updatedNorm += contentNorm.slice(cursor, span.start) + span.replacement;
    cursor = span.end;
  }
  updatedNorm += contentNorm.slice(cursor);
  const updated = hasCRLF && normalizeLineEndings ? updatedNorm.replace(/\n/g, "\r\n") : updatedNorm;
  return { ok: true, updated, occurrences: edits.length, regions, priorContent: content, preHash: sha256Buffer(raw) };
}

// Lines startLine..endLine replaced as a whole when they still hash to
// expectedRangeSha256 (see line-range-hash.js). Other lines keep their own
// terminators; new internal breaks use the file's CRLF/LF style. Empty
// newText deletes the lines. A mismatch returns the current range text.
function calculateLineRangeReplacement({
  priorContent,
  startLine,
  endLine,
  expectedRangeSha256,
  newText,
  maxRangeTextChars = Infinity,
  maxPatchChars = Infinity,
}) {
  const raw = Buffer.isBuffer(priorContent) ? priorContent : Buffer.from(String(priorContent ?? ""), "utf8");
  const content = raw.toString("utf8");
  const parts = content.split(/(\r?\n)/);
  const lines = parts.filter((_, index) => index % 2 === 0);
  const breaks = parts.filter((_, index) => index % 2 === 1);
  if (!Number.isInteger(startLine) || !Number.isInteger(endLine) || startLine < 1 || endLine < startLine
    || endLine > lines.length) {
    return { ok: false, errorCode: "RANGE_OUT_OF_BOUNDS", totalLines: lines.length,
      error: `lines ${startLine}-${endLine} are outside the file's ${lines.length} lines` };
  }
  const before = lines.slice(startLine - 1, endLine).join("\n");
  const rangeSha256 = lineRangeSha256(lines, startLine, endLine);
  const currentRange = { startLine, endLine, rangeSha256, totalLines: lines.length,
    text: before.slice(0, maxRangeTextChars), ...(before.length > maxRangeTextChars ? { textTruncated: true } : {}) };
  if (rangeSha256 !== String(expectedRangeSha256 || "").toLowerCase()) {
    return { ok: false, errorCode: "RANGE_HASH_MISMATCH", currentRange,
      error: `lines ${startLine}-${endLine} no longer hash to expectedRangeSha256; currentRange holds their text now` };
  }
  if (before.length > maxRangeTextChars || before.length + String(newText).length > maxPatchChars) {
    return { ok: false, errorCode: "PATCH_TOO_LARGE", currentRange,
      error: `lines ${startLine}-${endLine} hold ${before.length} characters; a range replaces at most ${maxRangeTextChars}` };
  }
  const eol = breaks.includes("\r\n") ? "\r\n" : "\n";
  const replacement = String(newText).replace(/\r\n/g, "\n");
  const head = parts.slice(0, (startLine - 1) * 2).join("");
  const tail = parts.slice(endLine * 2 - 1).join("");
  let updated;
  if (replacement) updated = head + replacement.split("\n").join(eol) + tail;
  // Deleting the last lines drops the break before them instead of after.
  else updated = tail ? head + tail.slice(breaks[endLine - 1].length) : head.slice(0, head.length - (breaks[startLine - 2]?.length || 0));
  return { ok: true, updated, occurrences: 1, priorContent: content, preHash: sha256Buffer(raw),
    regions: [{ beforeStartLine: startLine, afterStartLine: startLine, before, after: replacement }] };
}

function isLineRangePatch(patch) {
  return Boolean(patch) && ["startLine", "endLine", "expectedRangeSha256"].some((field) => patch[field] !== undefined);
}

// One of the three patch forms: edits[], a line range, or one oldText.
function calculatePatch(priorContent, patch, { maxRangeTextChars, maxPatchChars } = {}) {
  if (patch.edits) return calculateReplacements({ priorContent, edits: patch.edits });
  if (isLineRangePatch(patch)) {
    return calculateLineRangeReplacement({ priorContent, startLine: patch.startLine, endLine: patch.endLine,
      expectedRangeSha256: patch.expectedRangeSha256, newText: patch.newText, maxRangeTextChars, maxPatchChars });
  }
  return calculateReplacement({ priorContent, oldText: patch.oldText, newText: patch.newText,
    expectedOccurrences: patch.expectedOccurrences });
}

async function replaceWithCAS({
  targetPath,
  priorContent,
  oldText,
  newText,
  edits,
  range,
  expectedOccurrences,
  readHash,
  normalizeLineEndings = true,
  signal,
  beforeCommit,
}) {
  const resolved = path.resolve(String(targetPath));
  let raw;
  if (await fileExists(resolved)) {
    raw = await fsp.readFile(resolved);
    const diskHash = sha256Buffer(raw);
    if (readHash) {
      const diskCas = verifyReadHash(diskHash, readHash, resolved);
      if (!diskCas.ok) {
        return diskCas;
      }
    }
  } else {
    raw = Buffer.isBuffer(priorContent)
      ? priorContent
      : Buffer.from(String(priorContent ?? ""), "utf8");
    if (readHash) {
      const cas = verifyReadHash(sha256Buffer(raw), readHash, resolved);
      if (!cas.ok) {
        return cas;
      }
    }
  }

  const replacement = range
    ? calculateLineRangeReplacement({ ...range, priorContent: raw, newText })
    : edits
    ? calculateReplacements({ priorContent: raw, edits, normalizeLineEndings })
    : calculateReplacement({
      priorContent: raw,
      oldText,
      newText,
      expectedOccurrences,
      normalizeLineEndings,
    });
  if (!replacement.ok) return replacement;

  assertMutationActive(signal);
  if (beforeCommit) beforeCommit();
  atomicWriteText(resolved, replacement.updated);
  return replacement;
}

async function createExclusive(targetPath, content, signal) {
  assertMutationActive(signal);
  atomicCreateText(targetPath, String(content ?? ""));
  return { ok: true };
}

module.exports = {
  assertMutationActive,
  sha256Buffer,
  sha256Text,
  sha256File,
  verifyReadHash,
  assertPreCommitHash,
  calculateReplacement,
  calculateReplacements,
  calculateLineRangeReplacement,
  calculatePatch,
  isLineRangePatch,
  replaceWithCAS,
  createExclusive,
};

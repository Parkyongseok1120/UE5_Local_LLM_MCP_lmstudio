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
const { lineRangeSha256, locateRangeSha256 } = require("./line-range-hash");

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

// Matching runs on LF-normalized text; writing splices the original text, so
// lines outside a match keep their own terminators (a mixed LF/CRLF file is
// not rewritten to CRLF) and newText is inserted literally ("$&", "$$" and
// "$`" are not replacement patterns: C# raw strings use "$$\"\"\"").
function crlfNewlinePositions(content) {
  const positions = [];
  for (let at = content.indexOf("\r\n"), removed = 0; at >= 0; at = content.indexOf("\r\n", at + 2), removed++) {
    positions.push(at - removed);
  }
  return positions;
}

function originalOffset(crlfPositions, normalizedIndex) {
  let low = 0, high = crlfPositions.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (crlfPositions[middle] < normalizedIndex) low = middle + 1;
    else high = middle;
  }
  return normalizedIndex + low;
}

// spans: sorted, non-overlapping { start, end, replacement } in normalized
// coordinates. A span ending in a "\n" that was "\r\n" takes its "\r" too.
function spliceNormalizedSpans(content, spans, { normalized, eol }) {
  const crlfs = normalized ? crlfNewlinePositions(content) : [];
  const at = (index) => (normalized ? originalOffset(crlfs, index) : index);
  let updated = "", cursor = 0;
  for (const span of spans) {
    updated += content.slice(cursor, at(span.start)) + (normalized ? span.replacement.split("\n").join(eol) : span.replacement);
    cursor = at(span.end);
  }
  return updated + content.slice(cursor);
}

// "};\n \n// next" sent for "};\n\n// next" (conversation 1794402335258): a model
// that strips "28" from the numbered blank line "28\t" keeps the tab as a space.
// Diagnosis only; the candidate lines are reported, never edited.
function squashLineWhitespace(text) {
  return text.replace(/[ \t]+\n/g, "\n").replace(/\n[ \t]+/g, "\n").replace(/[ \t]+/g, " ");
}

function whitespaceOnlyCandidate(contentNorm, oldTextNorm, maxRangeTextChars = 1200) {
  const squashedOld = squashLineWhitespace(oldTextNorm);
  const needle = squashedOld.trim();
  if (needle.length < 8) return null;
  const haystack = squashLineWhitespace(contentNorm);
  const first = haystack.indexOf(needle);
  if (first < 0) return null;
  if (haystack.indexOf(needle, first + 1) >= 0) return { kind: "whitespace_only", candidates: "multiple" };
  const leadingLines = (squashedOld.slice(0, squashedOld.length - squashedOld.trimStart().length).match(/\n/g) || []).length;
  const startLine = haystack.slice(0, first).split("\n").length;
  const endLine = startLine + needle.split("\n").length - 1;
  const fileLines = contentNorm.split("\n");
  const oldLines = oldTextNorm.split("\n").slice(leadingLines);
  let difference = null;
  for (let index = 0; index < oldLines.length && startLine + index <= fileLines.length; index++) {
    const fileText = fileLines[startLine + index - 1], oldLine = oldLines[index];
    // The first line may start mid-line and the last may end mid-line.
    const first = index === 0, last = index === oldLines.length - 1;
    const same = first && last ? fileText.includes(oldLine) : first ? fileText.endsWith(oldLine)
      : last ? fileText.startsWith(oldLine) : fileText === oldLine;
    if (!same) { difference = { oldTextLine: leadingLines + index + 1, fileLine: startLine + index, oldText: oldLine, fileText }; break; }
  }
  const text = fileLines.slice(startLine - 1, endLine).join("\n");
  return {
    kind: "whitespace_only", candidates: "one", startLine, endLine,
    ...(difference ? { difference } : {}),
    currentRange: { startLine, endLine, rangeSha256: lineRangeSha256(fileLines, startLine, endLine), totalLines: fileLines.length,
      text: text.slice(0, maxRangeTextChars), ...(text.length > maxRangeTextChars ? { textTruncated: true } : {}) },
  };
}

function notFoundError(candidate, label = "oldText") {
  if (candidate?.candidates === "multiple") {
    return `${label} not found in target file; it matches several places only when whitespace is ignored. Copy tabs, spaces and empty lines exactly.`;
  }
  if (!candidate) return `${label} not found in target file`;
  const where = candidate.difference
    ? ` (oldText line ${candidate.difference.oldTextLine} is ${JSON.stringify(candidate.difference.oldText)}; file line ${candidate.difference.fileLine} is ${JSON.stringify(candidate.difference.fileText)})`
    : "";
  return `${label} matches lines ${candidate.startLine}-${candidate.endLine} only when whitespace is ignored${where}. Copy tabs, spaces and empty lines exactly, or replace lines ${candidate.startLine}-${candidate.endLine} with currentRange.rangeSha256 after checking currentRange.text.`;
}

function calculateReplacement({
  priorContent,
  oldText,
  newText,
  expectedOccurrences,
  // Count enforced when expectedOccurrences is omitted (replace_in_file uses 1).
  defaultOccurrences,
  normalizeLineEndings = true,
}) {
  const raw = Buffer.isBuffer(priorContent)
    ? priorContent
    : Buffer.from(String(priorContent ?? ""), "utf8");
  const hasCRLF = raw.includes(Buffer.from("\r\n"));
  const content = raw.toString("utf8");
  const contentNorm = normalizeLineEndings ? content.replace(/\r\n/g, "\n") : content;
  const oldTextNorm = normalizeLineEndings ? String(oldText).replace(/\r\n/g, "\n") : String(oldText);
  // Non-overlapping, left to right: the matches split() counted before.
  const starts = [];
  for (let at = oldTextNorm ? contentNorm.indexOf(oldTextNorm) : -1; at >= 0;
    at = contentNorm.indexOf(oldTextNorm, at + oldTextNorm.length)) starts.push(at);
  const occurrences = starts.length;
  // Text copied from a numbered read ("<n>\t<text>") is a display artifact, not file content.
  if (occurrences === 0 && isLineNumberPrefixedPatch(oldTextNorm, contentNorm)) {
    return { ok: false, errorCode: LINE_NUMBER_PATCH_ERROR, error: LINE_NUMBER_PATCH_MESSAGE };
  }
  // Existing contract: zero matches are OLD_TEXT_NOT_FOUND when no count was
  // given and an occurrence mismatch ("found 0") when one was.
  if (occurrences === 0) {
    const whitespaceCandidate = oldTextNorm ? whitespaceOnlyCandidate(contentNorm, oldTextNorm) : null;
    const diagnosis = whitespaceCandidate ? notFoundError(whitespaceCandidate) : "";
    return expectedOccurrences === undefined
      ? { ok: false, errorCode: "OLD_TEXT_NOT_FOUND", error: notFoundError(whitespaceCandidate),
        ...(whitespaceCandidate ? { whitespaceCandidate } : {}) }
      : { ok: false, errorCode: "OCCURRENCE_MISMATCH",
        error: `occurrence mismatch: expected ${expectedOccurrences}, found 0${diagnosis ? `; ${diagnosis}` : ""}`,
        ...(whitespaceCandidate ? { whitespaceCandidate } : {}) };
  }
  const expected = expectedOccurrences !== undefined ? expectedOccurrences : defaultOccurrences;
  if (expected !== undefined && occurrences !== expected) {
    return {
      ok: false,
      errorCode: "OCCURRENCE_MISMATCH",
      error: `occurrence mismatch: expected ${expected}, found ${occurrences}`,
    };
  }

  const replacement = normalizeLineEndings ? String(newText).replace(/\r\n/g, "\n") : String(newText);
  const updated = spliceNormalizedSpans(content,
    starts.map(start => ({ start, end: start + oldTextNorm.length, replacement })),
    { normalized: normalizeLineEndings, eol: hasCRLF ? "\r\n" : "\n" });
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
    if (occurrences === 0) {
      const whitespaceCandidate = oldTextNorm ? whitespaceOnlyCandidate(contentNorm, oldTextNorm) : null;
      return { ok: false, errorCode: "OCCURRENCE_MISMATCH", editIndex,
        error: whitespaceCandidate ? notFoundError(whitespaceCandidate, `edits[${editIndex}].oldText`)
          : `edits[${editIndex}] occurrence mismatch: expected 1, found 0`,
        ...(whitespaceCandidate ? { whitespaceCandidate } : {}) };
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
  let lineDelta = 0;
  const regions = [];
  for (const span of ordered) {
    const beforeStartLine = contentNorm.slice(0, span.start).split("\n").length;
    const before = contentNorm.slice(span.start, span.end);
    regions.push({ beforeStartLine, afterStartLine: beforeStartLine + lineDelta, before, after: span.replacement });
    lineDelta += span.replacement.split("\n").length - before.split("\n").length;
  }
  const updated = spliceNormalizedSpans(content, ordered, { normalized: normalizeLineEndings, eol: hasCRLF ? "\r\n" : "\n" });
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
  const expected = String(expectedRangeSha256 || "").trim().toLowerCase();
  let rangeProof = null;
  if (rangeSha256 !== expected) {
    // The rangeSha256 of a read that contains these lines proves them too: the
    // same proof already allows editing that whole read range. Conversations
    // 1794393906420 and 1794402335258 sent such a hash 15 times and lost a
    // round each time. An overlapping or foreign hash is still refused.
    const fileHash = expected === sha256Buffer(raw);
    const other = fileHash ? null : locateRangeSha256(lines, expected, { startLine, endLine });
    if (other && other.startLine <= startLine && other.endLine >= endLine) {
      rangeProof = { startLine: other.startLine, endLine: other.endLine };
    } else {
      const expectedRangeSource = fileHash ? { kind: "file_sha256" }
        : other ? { kind: "other_range", startLine: other.startLine, endLine: other.endLine } : { kind: "not_in_current_file" };
      const error = fileHash
        ? `expectedRangeSha256 is the whole-file sha256, not a range hash. Pass the rangeSha256 of a read that covers lines ${startLine}-${endLine}; currentRange holds the one for exactly these lines with their text.`
        : other
          ? `expectedRangeSha256 is the rangeSha256 of lines ${other.startLine}-${other.endLine}, which does not cover lines ${startLine}-${endLine}. Check currentRange.text and use currentRange.rangeSha256 for lines ${startLine}-${endLine}, or read the lines first.`
          : `lines ${startLine}-${endLine} do not hash to expectedRangeSha256; currentRange holds their text now`;
      return { ok: false, errorCode: "RANGE_HASH_MISMATCH", currentRange, expectedRangeSource, error };
    }
  }
  const combined = before.length + String(newText).length;
  if (before.length > maxRangeTextChars || combined > maxPatchChars) {
    return { ok: false, errorCode: "PATCH_TOO_LARGE", currentRange,
      error: before.length > maxRangeTextChars
        ? `lines ${startLine}-${endLine} hold ${before.length} characters; a range replaces at most ${maxRangeTextChars}`
        : `lines ${startLine}-${endLine} (${before.length} characters) plus newText (${String(newText).length}) total ${combined} characters; a region allows at most ${maxPatchChars} combined` };
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
    ...(rangeProof ? { rangeProof } : {}),
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
  // An omitted count means 1 here too, as at commit: otherwise a duplicated
  // oldText passed this check and failed under the lock as PATCH_FAILED.
  return calculateReplacement({ priorContent, oldText: patch.oldText, newText: patch.newText,
    expectedOccurrences: patch.expectedOccurrences, defaultOccurrences: 1 });
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

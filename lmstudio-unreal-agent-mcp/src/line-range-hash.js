"use strict";

const crypto = require("node:crypto");
const { sha256Text } = require("../../shared-tool-core/direct-file-snapshot");

// The hash a line-range edit proves: lines startLine..endLine (1-based, as
// read_file_range splits them) joined by "\n", without the last terminator.
// Terminator style does not change it, so a range read on CRLF and LF copies
// of a file proves the same text.
function lineRangeSha256(lines, startLine, endLine) {
  return sha256Text(lines.slice(startLine - 1, endLine).join("\n"));
}

// Diagnosis only, never proof: which nearby range of these same lines does a
// rejected expectedRangeSha256 belong to? Conversation 1794393906420 sent the
// hash of a wider read (83-115) with an edit of 85-88 thirteen times. Bounded
// to ranges overlapping startLine..endLine within `window` lines.
function locateRangeSha256(lines, expectedRangeSha256, { startLine, endLine, window = 64, maxDigests = 20000 } = {}) {
  const expected = String(expectedRangeSha256 || "").trim().toLowerCase();
  if (!/^[a-f0-9]{64}$/u.test(expected) || !Number.isInteger(startLine) || !Number.isInteger(endLine)) return null;
  const lastEnd = Math.min(lines.length, endLine + window);
  let digests = 0;
  for (let first = Math.max(1, startLine - window); first <= Math.min(endLine, lines.length); first += 1) {
    const hash = crypto.createHash("sha256");
    for (let last = first; last <= lastEnd; last += 1) {
      hash.update(last === first ? lines[last - 1] : `\n${lines[last - 1]}`, "utf8");
      if (last < startLine) continue;
      if (++digests > maxDigests) return null;
      if (hash.copy().digest("hex") === expected) return { startLine: first, endLine: last };
    }
  }
  return null;
}

module.exports = { lineRangeSha256, locateRangeSha256 };

"use strict";

const { sha256Text } = require("./safe-write");

// Optional evidence over actual committed contents. Linear prefix/suffix work
// is bounded independently of the source file limit; no IO or semantic diff.
function committedChange(before, after, previousHash, nextHash) {
  try {
    const left = Buffer.isBuffer(before) ? before.toString("utf8") : String(before);
    const right = String(after);
    const base = { schemaVersion: 1, previousSha256: previousHash || null, sha256: nextHash,
      status: "omitted", coverage: "omitted", hunks: [] };
    if (left.length + right.length > 2 * 1024 * 1024) return base;
    if ((previousHash && sha256Text(left) !== previousHash) || sha256Text(right) !== nextHash) return base;
    if (previousHash === nextHash && left === right) return { ...base, status: "no_change", coverage: "complete" };
    let start = 0;
    while (start < left.length && start < right.length && left[start] === right[start]) start++;
    let leftEnd = left.length, rightEnd = right.length;
    while (leftEnd > start && rightEnd > start && left[leftEnd - 1] === right[rightEnd - 1]) { leftEnd--; rightEnd--; }
    // Expand to whole lines so displayed snippets never cut a contract midway.
    const lineStart = start === 0 ? 0 : left.lastIndexOf("\n", start - 1) + 1;
    const ending = (value, end) => { const newline = value.indexOf("\n", end); return newline < 0 ? value.length : newline + 1; };
    leftEnd = ending(left, leftEnd); rightEnd = ending(right, rightEnd);
    const oldText = left.slice(lineStart, leftEnd), newText = right.slice(lineStart, rightEnd);
    const beforeStartLine = left.slice(0, lineStart).split("\n").length;
    const afterStartLine = right.slice(0, lineStart).split("\n").length;
    if (oldText.length + newText.length > 768) return { ...base, omittedHunks: 1 };
    return { ...base, status: "changed", coverage: "complete_changed_region",
      hunks: [{ beforeStartLine, afterStartLine, before: oldText, after: newText }] };
  } catch { return undefined; }
}

function withChangeEvidence(payload, evidence, payloadFits) {
  try {
    if (!evidence) return payload;
    const candidate = { ...payload, changeEvidence: evidence };
    if (payloadFits(candidate)) return candidate;
    candidate.changeEvidence = { ...evidence, hunks: [], status: "omitted", coverage: "omitted" };
    return payloadFits(candidate) ? candidate : payload;
  } catch { return payload; }
}

function withBundleChangeEvidence(payload, evidenceByPath, payloadFits) {
  try {
    const files = payload.files.map(file => ({ ...file,
      ...(evidenceByPath?.[file.path] ? { changeEvidence: evidenceByPath[file.path] } : {}) }));
    const candidate = { ...payload, files };
    if (payloadFits(candidate)) return candidate;
    for (const file of files) if (file.changeEvidence) file.changeEvidence = {
      ...file.changeEvidence, hunks: [], status: "omitted", coverage: "omitted" };
    return payloadFits(candidate) ? candidate : payload;
  } catch { return payload; }
}

module.exports = { committedChange, withChangeEvidence, withBundleChangeEvidence };

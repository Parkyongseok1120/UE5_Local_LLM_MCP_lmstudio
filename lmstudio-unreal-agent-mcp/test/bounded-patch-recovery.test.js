"use strict";

/**
 * Ensures BOUNDED_PATCH recovery prefers split-from-evidence when prior reads exist,
 * instead of directing the model into read_file_range → EVIDENCE_STAGNATION deadlock.
 */

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  checkReadRepeat,
  recordReadSuccess,
  clearReadSuccessHistory,
  getFileCoverage,
} = require("../src/tool-read-history");

test("prior full-file read marks coverage so recovery can skip re-read", () => {
  clearReadSuccessHistory();
  const context = {
    fileAbsPath: "C:/proj/Source/Mod/File.cpp",
    fileSignature: "sig-1",
    mutationGeneration: 0,
  };
  recordReadSuccess("read_file", { path: "Source/Mod/File.cpp" }, context, "file body");
  const cov = getFileCoverage(context);
  assert.ok(cov);
  assert.ok((cov.nonRangeCount || 0) > 0);
  const hasPriorEvidence = Boolean(
    cov
    && (
      (cov.nonRangeCount || 0) > 0
      || ((cov.ranges || []).length > 0)
      || (cov.coveredRepeatCount || 0) > 0
      || (cov.stagnationCount || 0) > 0
    )
  );
  assert.equal(hasPriorEvidence, true);
});

test("covered range repeat escalates to stagnation (deadlock trigger)", () => {
  clearReadSuccessHistory();
  const context = {
    fileAbsPath: "C:/proj/Source/Mod/File.cpp",
    fileSignature: "sig-2",
    mutationGeneration: 0,
  };
  recordReadSuccess(
    "read_file_range",
    { path: "Source/Mod/File.cpp", startLine: 1, endLine: 200 },
    context,
    "lines 1-200"
  );
  const first = checkReadRepeat(
    "read_file_range",
    { path: "Source/Mod/File.cpp", startLine: 1, endLine: 120 },
    context
  );
  assert.equal(first.action, "cache");
  const second = checkReadRepeat(
    "read_file_range",
    { path: "Source/Mod/File.cpp", startLine: 1, endLine: 120 },
    context
  );
  assert.equal(second.action, "stagnation");
  assert.equal(second.reason, "EVIDENCE_STAGNATION");
});

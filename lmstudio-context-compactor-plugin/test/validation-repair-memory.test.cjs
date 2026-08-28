"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const core = require("../src/direct-compaction-core.js");
const toolMemory = require("../src/compaction-tool-memory.js");

test("failed evidence validation retains bounded actionable repair facts", () => {
  const errors = Array.from(
    { length: 20 },
    (_, index) => `claims[${index}].evidence[0].location is required`,
  );
  const parsed = core.parseToolResult(JSON.stringify({
    ok: false,
    mode: "architecture",
    claimCount: 20,
    errorCount: 20,
    warningCount: 1,
    errorShapeCount: 1,
    omittedErrorShapeCount: 0,
    schemaVersion: "1.1",
    errors,
    warnings: ["proposed is empty; this is valid for an as-built report"],
  }));

  assert.equal(parsed.ok, false);
  assert.equal(parsed.mode, "architecture");
  assert.equal(parsed.claimCount, 20);
  assert.equal(parsed.errorCount, 20);
  assert.equal(parsed.errorShapeCount, 1);
  assert.equal(parsed.schemaVersion, "1.1");
  assert.equal(parsed.errors.length, 8);
  assert.equal(parsed.errors[0], "claims[0].evidence[0].location is required");
  assert.equal(parsed.errors[7], "claims[7].evidence[0].location is required");
  assert.deepEqual(parsed.warnings, [
    "proposed is empty; this is valid for an as-built report",
  ]);
});

test("validation repair diagnostics remain capability-free and individually bounded", () => {
  const parsed = core.parseToolResult(JSON.stringify({
    ok: false,
    errorCount: 2,
    errors: [
      `claims[0].observation contains fvr1_${"A".repeat(80)}`,
      "x".repeat(500),
    ],
  }));

  assert.equal(parsed.errors.length, 2);
  assert.doesNotMatch(parsed.errors[0], /fvr1_/i);
  assert.ok(parsed.errors[1].length <= 320);
});

test("serialized validation repair keeps counts and as many diagnostics as fit", () => {
  const record = core.parseToolResult(JSON.stringify({
    ok: false,
    mode: "architecture",
    claimCount: 7,
    errorCount: 82,
    errorShapeCount: 15,
    omittedErrorShapeCount: 0,
    errors: Array.from(
      { length: 8 },
      (_, index) => `claims[${index}].behaviorPath[0].stageStatus is required`,
    ),
  }));
  const serialized = toolMemory.serializeToolOutcomeRecords([record], {
    maxToolResultChars: 500,
  })[0];
  const retained = JSON.parse(serialized);

  assert.ok(serialized.length <= 500);
  assert.equal(retained.ok, false);
  assert.equal(retained.errorCount, 82);
  assert.equal(retained.errorShapeCount, 15);
  assert.ok(retained.errors.length >= 1);
  assert.match(retained.errors[0], /stageStatus is required/);
});

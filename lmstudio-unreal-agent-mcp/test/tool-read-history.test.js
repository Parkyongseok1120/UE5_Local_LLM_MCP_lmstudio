"use strict";

const assert = require("assert");
const test = require("node:test");
const {
  checkReadRepeat,
  recordReadSuccess,
  clearReadSuccessHistory,
  normalizeReadToolArgs,
} = require("../src/tool-read-history");

const CONTEXT = {
  fileAbsPath: "C:/proj/Source/Foo.cpp",
  fileSignature: "1200:1700000000000",
  mutationGeneration: 3,
};

test("normalizeReadToolArgs canonicalizes read_file_range bounds", () => {
  const normalized = normalizeReadToolArgs("read_file_range", {
    path: "Source/Foo.cpp",
    startLine: "10",
    endLine: "20",
  });
  assert.strictEqual(normalized.startLine, 10);
  assert.strictEqual(normalized.endLine, 20);
});

test("identical successful read returns cached repeat on second call", () => {
  clearReadSuccessHistory();
  const tool = "read_file_range";
  const args = normalizeReadToolArgs(tool, {
    path: "Source/Foo.cpp",
    startLine: 10,
    endLine: 20,
  });
  const content = "File: project://Source/Foo.cpp\nLines: 10-20\n\n10|void Foo() {}";

  const first = checkReadRepeat(tool, args, CONTEXT);
  assert.strictEqual(first.repeat, false);
  recordReadSuccess(tool, args, CONTEXT, content);

  const second = checkReadRepeat(tool, args, CONTEXT);
  assert.strictEqual(second.repeat, true);
  assert.strictEqual(second.reason, "READ_REPEAT_DETECTED");
  assert.strictEqual(second.cachedContent, content);
  assert.strictEqual(second.attempts, 2);
});

test("changed file version allows the same range to be read again", () => {
  clearReadSuccessHistory();
  const tool = "read_file_range";
  const args = normalizeReadToolArgs(tool, {
    path: "Source/Foo.cpp",
    startLine: 10,
    endLine: 20,
  });
  recordReadSuccess(tool, args, CONTEXT, "old content");

  const changedContext = { ...CONTEXT, fileSignature: "1300:1700000000001" };
  const retry = checkReadRepeat(tool, args, changedContext);
  assert.strictEqual(retry.repeat, false);
});

test("file read budget triggers evidence stagnation", () => {
  clearReadSuccessHistory();
  const tool = "read_file_range";
  const baseArgs = { path: "Source/Foo.cpp" };
  for (let i = 0; i < 4; i += 1) {
    const args = normalizeReadToolArgs(tool, { ...baseArgs, startLine: 1 + i, endLine: 5 + i });
    recordReadSuccess(tool, args, CONTEXT, `content-${i}`);
  }

  const nextArgs = normalizeReadToolArgs(tool, { ...baseArgs, startLine: 99, endLine: 120 });
  const blocked = checkReadRepeat(tool, nextArgs, CONTEXT);
  assert.strictEqual(blocked.repeat, true);
  assert.strictEqual(blocked.reason, "EVIDENCE_STAGNATION");
  assert.ok(blocked.cachedContent);
});

test("alternating range reads exhaust file budget", () => {
  clearReadSuccessHistory();
  const tool = "read_file_range";
  const argsA = normalizeReadToolArgs(tool, { path: "Source/Foo.cpp", startLine: 1, endLine: 20 });
  const argsB = normalizeReadToolArgs(tool, { path: "Source/Foo.cpp", startLine: 21, endLine: 40 });
  recordReadSuccess(tool, argsA, CONTEXT, "range-a");
  recordReadSuccess(tool, argsB, CONTEXT, "range-b");
  recordReadSuccess(tool, argsA, CONTEXT, "range-a");
  recordReadSuccess(tool, argsB, CONTEXT, "range-b");

  const argsC = normalizeReadToolArgs(tool, { path: "Source/Foo.cpp", startLine: 41, endLine: 60 });
  const blocked = checkReadRepeat(tool, argsC, CONTEXT);
  assert.strictEqual(blocked.repeat, true);
  assert.strictEqual(blocked.reason, "EVIDENCE_STAGNATION");
  assert.ok(blocked.cachedContent);
});

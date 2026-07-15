"use strict";

const assert = require("assert");
const test = require("node:test");
const {
  checkReadRepeat,
  recordReadSuccess,
  recordReadStagnation,
  clearReadSuccessHistory,
  normalizeReadToolArgs,
  novelLineCount,
  mergeRanges,
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
  assert.strictEqual(first.action, "allow");
  recordReadSuccess(tool, args, CONTEXT, content);

  const second = checkReadRepeat(tool, args, CONTEXT);
  assert.strictEqual(second.action, "cache");
  assert.strictEqual(second.reason, "READ_REPEAT_DETECTED");
  assert.strictEqual(second.cachedContent, content);

  const third = checkReadRepeat(tool, args, CONTEXT);
  assert.strictEqual(third.action, "stagnation");
  assert.strictEqual(third.reason, "EVIDENCE_STAGNATION");
  assert.strictEqual(third.attempts, 3);
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
  assert.strictEqual(retry.action, "allow");
});

test("novel line ranges are allowed after many prior reads", () => {
  clearReadSuccessHistory();
  const tool = "read_file_range";
  const baseArgs = { path: "Source/Foo.cpp" };
  // 100–200, 200–300, 300–400 only — leave 400–500 unread.
  for (let i = 0; i < 3; i += 1) {
    const args = normalizeReadToolArgs(tool, {
      ...baseArgs,
      startLine: 100 + i * 100,
      endLine: 200 + i * 100,
    });
    recordReadSuccess(tool, args, CONTEXT, `content-${i}`);
  }

  const nextArgs = normalizeReadToolArgs(tool, { ...baseArgs, startLine: 400, endLine: 500 });
  const decision = checkReadRepeat(tool, nextArgs, CONTEXT);
  assert.strictEqual(decision.action, "allow");
  assert.ok(decision.novelLines > 0);
});

test("fully covered sub-range is cache/covered, not stagnation with wrong body", () => {
  clearReadSuccessHistory();
  const tool = "read_file_range";
  const wide = normalizeReadToolArgs(tool, { path: "Source/Foo.cpp", startLine: 100, endLine: 200 });
  recordReadSuccess(tool, wide, CONTEXT, "wide-100-200");

  const nested = normalizeReadToolArgs(tool, { path: "Source/Foo.cpp", startLine: 120, endLine: 150 });
  const decision = checkReadRepeat(tool, nested, CONTEXT);
  assert.strictEqual(decision.action, "cache");
  assert.strictEqual(decision.reason, "READ_REPEAT_DETECTED");
  assert.strictEqual(decision.fullyCovered, true);
  assert.strictEqual(decision.cachedContent, "wide-100-200");
});

test("different fully covered sub-ranges share one file stagnation budget", () => {
  clearReadSuccessHistory();
  const tool = "read_file_range";
  const wide = normalizeReadToolArgs(tool, { path: "Source/Foo.cpp", startLine: 1, endLine: 200 });
  recordReadSuccess(tool, wide, CONTEXT, "wide-1-200");

  const firstNested = normalizeReadToolArgs(
    tool,
    { path: "Source/Foo.cpp", startLine: 20, endLine: 80 }
  );
  const secondNested = normalizeReadToolArgs(
    tool,
    { path: "Source/Foo.cpp", startLine: 90, endLine: 140 }
  );

  assert.strictEqual(checkReadRepeat(tool, firstNested, CONTEXT).action, "cache");
  const blocked = checkReadRepeat(tool, secondNested, CONTEXT);
  assert.strictEqual(blocked.action, "stagnation");
  assert.strictEqual(blocked.reason, "EVIDENCE_STAGNATION");
  assert.strictEqual(blocked.fullyCovered, true);
});

test("novelLineCount and mergeRanges cover unions", () => {
  const merged = mergeRanges([
    { start: 1, end: 100 },
    { start: 100, end: 200 },
    { start: 250, end: 300 },
  ]);
  assert.deepStrictEqual(merged, [
    { start: 1, end: 200 },
    { start: 250, end: 300 },
  ]);
  assert.strictEqual(novelLineCount({ start: 180, end: 220 }, merged), 20);
  assert.strictEqual(novelLineCount({ start: 50, end: 80 }, merged), 0);
});

test("stagnation records escalate on second identical blocked call", () => {
  clearReadSuccessHistory();
  const tool = "search_files";
  const args = normalizeReadToolArgs(tool, { query: "Foo", path: "Source" });
  // Exhaust non-range soft budget.
  for (let i = 0; i < 8; i += 1) {
    const a = normalizeReadToolArgs(tool, { query: `Q${i}`, path: "Source" });
    recordReadSuccess(tool, a, CONTEXT, `search-${i}`);
  }
  const first = checkReadRepeat(tool, args, CONTEXT);
  assert.strictEqual(first.action, "stagnation");
  assert.strictEqual(first.reason, "EVIDENCE_STAGNATION");
  assert.strictEqual(first.cachedContent, undefined);
  recordReadStagnation(tool, args, CONTEXT);

  const second = checkReadRepeat(tool, args, CONTEXT);
  assert.strictEqual(second.action, "stagnation");
  assert.strictEqual(second.reason, "EVIDENCE_STAGNATION_REPEAT");
});

test("covering cache does not leak content across files", () => {
  clearReadSuccessHistory();
  const tool = "read_file_range";
  const ctxA = { ...CONTEXT, fileAbsPath: "C:/proj/Source/A.cpp" };
  const ctxB = { ...CONTEXT, fileAbsPath: "C:/proj/Source/B.cpp" };
  const wideA = normalizeReadToolArgs(tool, { path: "Source/A.cpp", startLine: 1, endLine: 200 });
  recordReadSuccess(tool, wideA, ctxA, "FILE-A-BODY");

  const nestedB = normalizeReadToolArgs(tool, { path: "Source/B.cpp", startLine: 50, endLine: 80 });
  // No prior coverage on B — allow read.
  const firstB = checkReadRepeat(tool, nestedB, ctxB);
  assert.strictEqual(firstB.action, "allow");

  // After covering a range on B that is fully covered by A's numbers only, still no A body.
  const wideB = normalizeReadToolArgs(tool, { path: "Source/B.cpp", startLine: 1, endLine: 100 });
  recordReadSuccess(tool, wideB, ctxB, "FILE-B-BODY");
  const nestedOnB = normalizeReadToolArgs(tool, { path: "Source/B.cpp", startLine: 20, endLine: 40 });
  const covered = checkReadRepeat(tool, nestedOnB, ctxB);
  assert.strictEqual(covered.action, "cache");
  assert.strictEqual(covered.cachedContent, "FILE-B-BODY");
  assert.notStrictEqual(covered.cachedContent, "FILE-A-BODY");
});

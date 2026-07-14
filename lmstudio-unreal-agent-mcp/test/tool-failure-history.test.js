"use strict";

const assert = require("assert");
const test = require("node:test");
const {
  beginToolCall,
  checkToolRepeatBlocked,
  recordToolFailure,
  clearToolFailureHistory,
  stableStringify,
} = require("../src/tool-failure-history");

test("stableStringify sorts object keys deterministically", () => {
  assert.strictEqual(
    stableStringify({ b: 2, a: 1 }),
    stableStringify({ a: 1, b: 2 })
  );
});

test("consecutive identical internal failures block on second call", () => {
  clearToolFailureHistory();
  const tool = "read_file_range";
  const args = { path: "Source/Foo.cpp", startLine: 10, endLine: 20 };

  const firstSeq = beginToolCall();
  assert.strictEqual(checkToolRepeatBlocked(tool, args, firstSeq).blocked, false);
  recordToolFailure(tool, args, "INTERNAL_ERROR");

  const secondSeq = beginToolCall();
  const blocked = checkToolRepeatBlocked(tool, args, secondSeq);
  assert.strictEqual(blocked.blocked, true);
  assert.strictEqual(blocked.consecutive, true);
  assert.strictEqual(blocked.attempts, 2);
});

test("non-consecutive identical failures allow one retry then block", () => {
  clearToolFailureHistory();
  const tool = "read_symbol";
  const args = { path: "Source/Foo.cpp", symbol: "UFoo::Bar" };

  let seq = beginToolCall();
  recordToolFailure(tool, args, "INTERNAL_ERROR");

  seq = beginToolCall();
  assert.strictEqual(checkToolRepeatBlocked("search_files", { query: "Bar" }, seq).blocked, false);

  seq = beginToolCall();
  assert.strictEqual(checkToolRepeatBlocked(tool, args, seq).blocked, false);
  recordToolFailure(tool, args, "INTERNAL_ERROR");

  seq = beginToolCall();
  const blocked = checkToolRepeatBlocked(tool, args, seq);
  assert.strictEqual(blocked.blocked, true);
  assert.strictEqual(blocked.attempts, 3);
});

test("read-tool failure keys normalize equivalent args", () => {
  clearToolFailureHistory();
  const tool = "read_file_range";
  const first = { path: "Source/Foo.cpp", startLine: 10, endLine: 20 };
  const equivalent = { endLine: 20, startLine: 10, path: "Source/Foo.cpp" };

  const firstSeq = beginToolCall();
  assert.strictEqual(checkToolRepeatBlocked(tool, first, firstSeq).blocked, false);
  recordToolFailure(tool, first, "INTERNAL_ERROR");

  const secondSeq = beginToolCall();
  const blocked = checkToolRepeatBlocked(tool, equivalent, secondSeq);
  assert.strictEqual(blocked.blocked, true);
  assert.strictEqual(blocked.consecutive, true);
});

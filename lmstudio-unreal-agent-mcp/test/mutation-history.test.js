"use strict";

const assert = require("assert");
const test = require("node:test");
const {
  checkMutationDuplicate,
  recordMutation,
  clearMutationHistory,
  checkAndRecordMutation,
} = require("../src/mutation-history");

test("failed replace attempts do not count toward duplicate until recorded", () => {
  clearMutationHistory();
  const tool = "replace_in_file";
  const path = "C:/proj/Source/Foo.cpp";
  const payload = "old\u0000new\u0000";

  const first = checkMutationDuplicate(tool, path, payload);
  assert.strictEqual(first.duplicate, false);
  // Simulate oldText not found — do not record.
  const second = checkMutationDuplicate(tool, path, payload);
  assert.strictEqual(second.duplicate, false);

  recordMutation(tool, path, payload);
  const afterSuccess = checkMutationDuplicate(tool, path, payload);
  assert.strictEqual(afterSuccess.duplicate, true);
  assert.strictEqual(afterSuccess.consecutive, true);
});

test("legacy checkAndRecordMutation still blocks consecutive repeats", () => {
  clearMutationHistory();
  const first = checkAndRecordMutation("write_file", "/tmp/A.cpp", "body");
  assert.strictEqual(first.duplicate, false);
  const second = checkAndRecordMutation("write_file", "/tmp/A.cpp", "body");
  assert.strictEqual(second.duplicate, true);
});

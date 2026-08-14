"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { missingReadTargetRecovery } = require("../src/context-ux");

test("missing project read forces one exact basename search without closing the workflow", () => {
  const recovery = missingReadTargetRecovery(
    "read_file_range",
    "Source/O_Mock/Tests/GomokuLocalPlayTest.cpp",
    "active_project"
  );

  assert.equal(recovery.errorCode, "READ_TARGET_NOT_FOUND");
  assert.equal(recovery.stopCurrentWorkflow, false);
  assert.deepEqual(recovery.doNotRetryTools, ["read_file_range"]);
  assert.equal(recovery.requiredNextTool, "search_files");
  assert.deepEqual(recovery.requiredNextToolArgs, {
    query: "GomokuLocalPlayTest.cpp",
    path: "project://Source",
    matchFileNames: true,
  });
  assert.deepEqual(recovery.nextActionArgs, recovery.requiredNextToolArgs);
  assert.deepEqual(recovery.suggestedToolCalls, [{
    tool: "search_files",
    args: recovery.requiredNextToolArgs,
  }]);
});

test("missing workspace read keeps recovery inside the workspace root", () => {
  const recovery = missingReadTargetRecovery(
    "read_file",
    "docs/Missing.md",
    "workspace"
  );

  assert.equal(recovery.requiredNextToolArgs.query, "Missing.md");
  assert.equal(recovery.requiredNextToolArgs.path, "workspace://");
  assert.deepEqual(recovery.doNotRetryTools, ["read_file"]);
});

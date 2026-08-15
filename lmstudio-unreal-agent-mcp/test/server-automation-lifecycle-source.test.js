"use strict";

const fs = require("fs");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert");

test("successful automation batch advance is not routed into completion failure", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "src", "server.js"), "utf8");
  const start = source.indexOf("const completion = completeTaskAfterBuildViaPython(");
  const end = source.indexOf('if (name === "build_unreal_project")', start);
  assert.ok(start >= 0 && end > start);
  const branch = source.slice(start, end);
  const failureGuard = branch.indexOf("if (completion?.ok !== true)");
  const batchFinalizeGuard = branch.indexOf(
    "if (completion?.ok === true && completion?.automationBatchAdvanced !== true)"
  );
  assert.ok(failureGuard >= 0);
  assert.ok(batchFinalizeGuard > failureGuard);
  assert.doesNotMatch(
    branch.slice(batchFinalizeGuard),
    /TASK_AUTOMATION_COMPLETION_FAILED/u
  );
});

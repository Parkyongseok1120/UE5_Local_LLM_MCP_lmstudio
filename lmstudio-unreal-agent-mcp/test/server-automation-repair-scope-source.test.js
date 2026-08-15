"use strict";

const fs = require("fs");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert");

test("automation discovery expands an active temporary repair slice to causal files", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "src", "server.js"), "utf8");
  const helperStart = source.indexOf("function automationScopeForTask(");
  const helperEnd = source.indexOf("function recordValidationContinuityCheckpoint(", helperStart);
  assert.ok(helperStart >= 0 && helperEnd > helperStart);
  const helper = source.slice(helperStart, helperEnd);
  assert.match(helper, /repairScope\.status/u);
  assert.match(helper, /repairScope\.temporarySliceId/u);
  assert.match(helper, /repairScope\.causalSliceFiles/u);
  assert.match(helper, /repairScope\.targetFile/u);
  assert.match(helper, /temporarySliceId === String\(taskState\?\.activeSliceId/u);
  assert.strictEqual(
    (source.match(/const automationScope = automationScopeForTask\(/gu) || []).length,
    2
  );
});

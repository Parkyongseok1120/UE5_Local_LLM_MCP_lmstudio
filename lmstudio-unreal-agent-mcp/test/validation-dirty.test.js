"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const {
  markUnvalidated,
  getDirtyState,
  clearValidated,
} = require("../src/validation-dirty");

test("validation dirty persists to project state file", () => {
  const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), "validation-dirty-"));
  markUnvalidated(projectRoot, "Source/Demo/Demo.cpp", "timeout");
  const statePath = path.join(projectRoot, ".agent", "state", "validation.json");
  assert.ok(fs.existsSync(statePath));
  clearValidated(projectRoot);
  const reloaded = getDirtyState(projectRoot);
  assert.equal(reloaded.validationRequired, false);
});

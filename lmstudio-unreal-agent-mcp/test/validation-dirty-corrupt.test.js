"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { getDirtyState, requireCleanOrFail } = require("../src/validation-dirty");

test("corrupt validation state blocks build fail-closed", () => {
  const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), "validation-corrupt-"));
  const statePath = path.join(projectRoot, ".agent", "state", "validation.json");
  fs.mkdirSync(path.dirname(statePath), { recursive: true });
  fs.writeFileSync(statePath, "{not-json", "utf8");
  const state = getDirtyState(projectRoot);
  assert.equal(state.validationRequired, true);
  const gate = requireCleanOrFail(projectRoot);
  assert.equal(gate.ok, false);
});

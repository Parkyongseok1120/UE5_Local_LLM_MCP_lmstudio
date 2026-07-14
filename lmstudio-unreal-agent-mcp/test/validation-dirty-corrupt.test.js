"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const {
  getDirtyState,
  requireCleanOrFail,
  requireValidationProofOrOverride,
} = require("../src/validation-dirty");

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

test("stale validation proof blocks without override", () => {
  const gate = requireValidationProofOrOverride({ mutationGeneration: 3, validatedGeneration: 0 });
  assert.equal(gate.ok, false);
  assert.equal(gate.errorCode, "VALIDATION_PROOF_STALE");
  assert.equal(gate.stopCurrentWorkflow, true);
});

test("explicit override permits one audited build with stale proof", () => {
  const gate = requireValidationProofOrOverride(
    { mutationGeneration: 3, validatedGeneration: 0 },
    { override: true, auditNote: "Reviewed pre-existing validation findings." }
  );
  assert.equal(gate.ok, true);
  assert.equal(gate.overridden, true);
  assert.equal(gate.mutationGeneration, 3);
  assert.equal(gate.validatedGeneration, 0);
  assert.equal(gate.auditNote, "Reviewed pre-existing validation findings.");
});

test("fresh validation proof does not report override", () => {
  const gate = requireValidationProofOrOverride(
    { mutationGeneration: 3, validatedGeneration: 3 },
    { override: true, auditNote: "unused" }
  );
  assert.equal(gate.ok, true);
  assert.equal(gate.overridden, false);
  assert.equal(gate.auditNote, "");
});

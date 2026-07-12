"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const {
  beginValidation,
  finishValidation,
  readMutationState,
  recordDeletion,
} = require("../src/mutation-generation");

function tempProject() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "mutation-gen-"));
  return dir;
}

test("finishValidation persists validatedGeneration when generation is stable", async () => {
  const projectRoot = tempProject();
  const start = await beginValidation(projectRoot);
  const finish = await finishValidation(projectRoot, start.startGeneration);
  assert.strictEqual(finish.validationStale, false);
  assert.strictEqual(finish.validatedGeneration, 0);
  const state = await readMutationState(projectRoot);
  assert.strictEqual(state.validatedGeneration, 0);
});

test("recordDeletion increments mutation generation and removes path entry", async () => {
  const projectRoot = tempProject();
  const first = await recordDeletion(projectRoot, "Source/Old.cpp");
  assert.strictEqual(first.mutationGeneration, 1);
  const state = await readMutationState(projectRoot);
  assert.strictEqual(state.mutationGeneration, 1);
  assert.strictEqual(state.paths["Source/Old.cpp"], undefined);
});

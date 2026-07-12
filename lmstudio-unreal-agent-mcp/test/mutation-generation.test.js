"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const {
  finishValidationAndClear,
  readMutationState,
} = require("../src/mutation-generation");
const { getDirtyState, markUnvalidated, stateFilePath } = require("../src/validation-dirty");

test("finishValidationAndClear persists validatedGeneration and clears validation.json", async () => {
  const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), "mut-val-"));
  markUnvalidated(projectRoot, "Source/A.cpp");
  assert.strictEqual(getDirtyState(projectRoot).validationRequired, true);
  const finish = await finishValidationAndClear(projectRoot, 0);
  assert.strictEqual(finish.validationStale, false);
  assert.strictEqual(finish.validatedGeneration, 0);
  const state = await readMutationState(projectRoot);
  assert.strictEqual(state.validatedGeneration, 0);
  assert.strictEqual(fs.existsSync(stateFilePath(projectRoot)), false);
  assert.strictEqual(getDirtyState(projectRoot).validationRequired, false);
});

test("readMutationState fails closed on corrupt mutation.json", async () => {
  const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), "mut-corrupt-"));
  const file = path.join(projectRoot, ".agent", "state", "mutation.json");
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, "{not json", "utf8");
  await assert.rejects(
    () => readMutationState(projectRoot),
    (err) => err && err.errorCode === "MUTATION_STATE_CORRUPT",
  );
});

test("getDirtyState reloads validation.json written by another process", () => {
  const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), "dirty-reload-"));
  assert.strictEqual(getDirtyState(projectRoot).validationRequired, false);
  markUnvalidated(projectRoot, "Source/B.cpp");
  assert.strictEqual(getDirtyState(projectRoot).validationRequired, true);
});

"use strict";

// Historical mutation-generation behavior; excluded from the product test suite.

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const {
  compensateMutationBatch,
  finishValidationAndClear,
  recordMutation,
  recordMutationBatch,
  readMutationState,
} = require("../src/mutation-generation");
const { sha256Text } = require("../src/safe-write");
const { getDirtyState, markUnvalidated, stateFilePath } = require("../src/validation-dirty");

test("recordMutationBatch persists all path hashes in one revision with per-file generations", async () => {
  const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), "mut-batch-"));
  const recorded = await recordMutationBatch(projectRoot, [
    { relPath: "Source\\A.cpp", content: "alpha" },
    { relPath: "Source/B.cpp", content: "beta" },
  ]);

  assert.strictEqual(recorded.recordedMutationCount, 2);
  assert.strictEqual(recorded.mutationGeneration, 2);
  assert.strictEqual(recorded.mutationRevision, 1);
  assert.deepStrictEqual(recorded.pathHashes, {
    "Source/A.cpp": sha256Text("alpha"),
    "Source/B.cpp": sha256Text("beta"),
  });
  const state = await readMutationState(projectRoot);
  assert.strictEqual(state.mutationGeneration, 2);
  assert.strictEqual(state.mutationRevision, 1);
  assert.strictEqual(state.paths["Source/A.cpp"], sha256Text("alpha"));
  assert.strictEqual(state.paths["Source/B.cpp"], sha256Text("beta"));
  assert.strictEqual(state.validationPassed, false);
  assert.strictEqual(state.validationStatus, "pending");
});

test("compensateMutationBatch CAS-restores the state before a recorded bundle", async () => {
  const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), "mut-compensate-"));
  await recordMutation(projectRoot, "Source/Keep.cpp", "before");
  const recorded = await recordMutationBatch(projectRoot, [
    { relPath: "Source/Keep.cpp", content: "after" },
    { relPath: "Source/New.cpp", content: "new" },
  ]);

  const compensation = await compensateMutationBatch(projectRoot, recorded.compensationReceipt);
  assert.deepStrictEqual(compensation, {
    compensated: true,
    conflict: false,
    mutationGeneration: 1,
    mutationRevision: 3,
    revertedMutationCount: 2,
  });
  const state = await readMutationState(projectRoot);
  assert.strictEqual(state.mutationGeneration, 1);
  assert.strictEqual(state.mutationRevision, 3);
  assert.deepStrictEqual(state.paths, {
    "Source/Keep.cpp": sha256Text("before"),
  });
});

test("compensateMutationBatch refuses to overwrite an intervening mutation", async () => {
  const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), "mut-compensate-cas-"));
  const recorded = await recordMutationBatch(projectRoot, [
    { relPath: "Source/A.cpp", content: "alpha" },
    { relPath: "Source/B.cpp", content: "beta" },
  ]);
  await recordMutation(projectRoot, "Source/C.cpp", "gamma");

  const compensation = await compensateMutationBatch(projectRoot, recorded.compensationReceipt);
  assert.strictEqual(compensation.compensated, false);
  assert.strictEqual(compensation.conflict, true);
  assert.strictEqual(compensation.errorCode, "MUTATION_COMPENSATION_CONFLICT");
  assert.strictEqual(compensation.reason, "mutation_revision_changed");
  const state = await readMutationState(projectRoot);
  assert.strictEqual(state.mutationGeneration, 3);
  assert.strictEqual(state.mutationRevision, 2);
  assert.strictEqual(state.paths["Source/C.cpp"], sha256Text("gamma"));
});

test("write-ahead compensation failure leaves mutation state untouched", async () => {
  const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), "mut-write-ahead-"));
  await assert.rejects(
    () => recordMutation(projectRoot, "Source/A.cpp", "alpha", {
      prepareCompensation: async (receipt) => {
        assert.ok(receipt.receiptId);
        assert.strictEqual(receipt.expectedMutationGeneration, 1);
        throw new Error("injected journal persistence failure");
      },
    }),
    /injected journal persistence failure/,
  );
  const state = await readMutationState(projectRoot);
  assert.strictEqual(state.mutationGeneration, 0);
  assert.strictEqual(state.mutationRevision, 0);
  assert.deepStrictEqual(state.paths, {});
});

test("mutation compensation is idempotent after the receipt is applied", async () => {
  const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), "mut-compensate-idempotent-"));
  const recorded = await recordMutation(projectRoot, "Source/A.cpp", "alpha");
  const first = await compensateMutationBatch(projectRoot, recorded.compensationReceipt);
  const second = await compensateMutationBatch(projectRoot, recorded.compensationReceipt);
  assert.strictEqual(first.compensated, true);
  assert.strictEqual(second.compensated, true);
  assert.strictEqual(second.alreadyCompensated, true);
  assert.strictEqual(second.mutationRevision, first.mutationRevision);
});

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

test("finishValidationAndClear fails closed when dirty-state cleanup fails", async () => {
  const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), "mut-val-cleanup-"));
  await recordMutation(projectRoot, "Source/A.cpp", "changed");
  markUnvalidated(projectRoot, "Source/A.cpp");
  const validationFile = stateFilePath(projectRoot);
  const originalUnlinkSync = fs.unlinkSync;
  fs.unlinkSync = function injectedUnlinkFailure(targetPath) {
    if (path.resolve(String(targetPath)) === path.resolve(validationFile)) {
      const err = new Error("injected cleanup failure");
      err.code = "EPERM";
      throw err;
    }
    return originalUnlinkSync.apply(this, arguments);
  };
  try {
    await assert.rejects(
      () => finishValidationAndClear(projectRoot, 1, {
        passed: true,
        proofLevel: "StaticVerified",
      }),
      (err) => err
        && err.errorCode === "VALIDATION_STATE_CLEANUP_FAILED"
        && err.validationStatePath === validationFile,
    );
  } finally {
    fs.unlinkSync = originalUnlinkSync;
  }

  const state = await readMutationState(projectRoot);
  assert.strictEqual(state.validationPassed, false);
  assert.strictEqual(state.validationStatus, "pending");
  assert.strictEqual(state.validatedGeneration, 0);
  assert.strictEqual(getDirtyState(projectRoot).validationRequired, true);
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

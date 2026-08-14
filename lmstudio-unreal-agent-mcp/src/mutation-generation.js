"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const crypto = require("crypto");
const { atomicWriteText } = require("./atomic-io");
const { sha256Text } = require("./safe-write");
const { tryAcquirePathLock, releasePathLock } = require("./write-locks");

const LOCK_ATTEMPTS = 40;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function mutationStatePath(projectRoot) {
  return path.join(path.resolve(projectRoot), ".agent", "state", "mutation.json");
}

function validationStatePath(projectRoot) {
  return path.join(path.resolve(projectRoot), ".agent", "state", "validation.json");
}

function defaultState() {
  return {
    mutationGeneration: 0,
    mutationRevision: 0,
    paths: {},
    validatedGeneration: 0,
    validationPassed: true,
    validationStatus: "baseline",
    validationBlockingErrorCount: 0,
    validationProofLevel: "Baseline",
    updatedAt: new Date().toISOString(),
  };
}

function mutationStateCorruptError(cause) {
  const err = new Error("MUTATION_STATE_CORRUPT");
  err.errorCode = "MUTATION_STATE_CORRUPT";
  err.cause = cause;
  return err;
}

async function readMutationState(projectRoot) {
  const file = mutationStatePath(projectRoot);
  if (!fs.existsSync(file)) {
    return defaultState();
  }
  try {
    const parsed = JSON.parse(await fsp.readFile(file, "utf8"));
    const state = { ...defaultState(), ...parsed };
    // Older mutation files had no pass/fail proof field. A non-zero legacy
    // generation must be revalidated instead of inheriting the baseline true.
    if (!Object.prototype.hasOwnProperty.call(parsed, "validationPassed") && int(state.mutationGeneration) > 0) {
      state.validationPassed = false;
      state.validationStatus = "legacy_unverified";
      state.validationProofLevel = "NeedsStaticValidation";
    }
    return state;
  } catch (err) {
    throw mutationStateCorruptError(err);
  }
}

async function writeMutationState(projectRoot, state) {
  const file = mutationStatePath(projectRoot);
  await fsp.mkdir(path.dirname(file), { recursive: true });
  state.updatedAt = new Date().toISOString();
  atomicWriteText(file, JSON.stringify(state, null, 2));
}

async function withMutationLock(projectRoot, fn) {
  const stateFile = mutationStatePath(projectRoot);
  for (let attempt = 0; attempt < LOCK_ATTEMPTS; attempt += 1) {
    const lock = tryAcquirePathLock(stateFile, "mutation_generation", { heartbeat: true });
    if (lock.ok) {
      try {
        return await fn();
      } finally {
        releasePathLock(stateFile);
      }
    }
    await sleep(Math.min(50 * (attempt + 1), 500));
  }
  throw new Error("mutation generation lock busy");
}

async function recordMutation(projectRoot, relPath, content, options = {}) {
  return recordMutationBatch(projectRoot, [{ relPath, content }], options);
}

function int(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.trunc(parsed) : 0;
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

function normalizedRelPath(relPath) {
  return String(relPath || "").replace(/\\/g, "/");
}

function stableJson(value) {
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableJson(item)).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => (
      `${JSON.stringify(key)}:${stableJson(value[key])}`
    )).join(",")}}`;
  }
  return JSON.stringify(value);
}

function pathsDigest(paths) {
  return sha256Text(stableJson(paths && typeof paths === "object" ? paths : {}));
}

function semanticStateDigest(state) {
  const normalized = cloneJson(state && typeof state === "object" ? state : {});
  delete normalized.updatedAt;
  delete normalized.mutationRevision;
  delete normalized.lastCompensatedReceiptId;
  return sha256Text(stableJson(normalized));
}

function normalizeBatchMutations(mutations) {
  if (!Array.isArray(mutations) || mutations.length === 0) {
    throw new TypeError("mutations must be a non-empty array");
  }
  return mutations.map((mutation, index) => {
    if (!mutation || typeof mutation !== "object") {
      throw new TypeError(`mutations[${index}] must be an object`);
    }
    const relPath = normalizedRelPath(mutation.relPath);
    if (!relPath) {
      throw new TypeError(`mutations[${index}].relPath must not be empty`);
    }
    return {
      relPath,
      deleted: mutation.deleted === true,
      content: String(mutation.content ?? ""),
    };
  });
}

function markValidationPending(state) {
  state.validationPassed = false;
  state.validationStatus = "pending";
  state.validationBlockingErrorCount = 0;
  state.validationProofLevel = "NeedsStaticValidation";
}

async function recordMutationBatch(projectRoot, mutations, options = {}) {
  const normalized = normalizeBatchMutations(mutations);
  return withMutationLock(projectRoot, async () => {
    const state = await readMutationState(projectRoot);
    const previousState = cloneJson(state);
    if (!state.paths || typeof state.paths !== "object" || Array.isArray(state.paths)) {
      state.paths = {};
    }

    const pathHashes = {};
    for (const mutation of normalized) {
      state.mutationGeneration = int(state.mutationGeneration) + 1;
      if (mutation.deleted) {
        delete state.paths[mutation.relPath];
        pathHashes[mutation.relPath] = null;
      } else {
        const digest = sha256Text(mutation.content);
        state.paths[mutation.relPath] = digest;
        pathHashes[mutation.relPath] = digest;
      }
    }
    state.mutationRevision = int(state.mutationRevision) + 1;
    markValidationPending(state);

    const compensationReceipt = {
      receiptVersion: 2,
      receiptId: crypto.randomUUID(),
      previousState,
      previousSemanticStateDigest: semanticStateDigest(previousState),
      expectedMutationGeneration: int(state.mutationGeneration),
      expectedMutationRevision: int(state.mutationRevision),
      expectedPathsDigest: pathsDigest(state.paths),
      expectedSemanticStateDigest: semanticStateDigest(state),
      mutationCount: normalized.length,
    };
    if (typeof options.prepareCompensation === "function") {
      // The receipt is a write-ahead record. If this callback does not durably
      // persist it, mutation.json must remain untouched.
      await options.prepareCompensation(cloneJson(compensationReceipt), {
        mutationGeneration: int(state.mutationGeneration),
        mutationRevision: int(state.mutationRevision),
        pathHashes: cloneJson(pathHashes),
      });
    }
    await writeMutationState(projectRoot, state);
    return {
      mutationGeneration: int(state.mutationGeneration),
      mutationRevision: int(state.mutationRevision),
      recordedMutationCount: normalized.length,
      pathHashes,
      compensationReceipt,
    };
  });
}

function invalidCompensationReceipt(reason) {
  const err = new Error(`invalid mutation compensation receipt: ${reason}`);
  err.errorCode = "MUTATION_COMPENSATION_RECEIPT_INVALID";
  return err;
}

async function compensateMutationBatch(projectRoot, receipt, options = {}) {
  if (
    !receipt
    || typeof receipt !== "object"
    || ![1, 2].includes(Number(receipt.receiptVersion))
  ) {
    throw invalidCompensationReceipt("unsupported or missing receiptVersion");
  }
  if (!receipt.previousState || typeof receipt.previousState !== "object") {
    throw invalidCompensationReceipt("previousState is missing");
  }

  return withMutationLock(projectRoot, async () => {
    const state = await readMutationState(projectRoot);
    const currentGeneration = int(state.mutationGeneration);
    const currentRevision = int(state.mutationRevision);
    if (
      receipt.receiptId
      && String(state.lastCompensatedReceiptId || "") === String(receipt.receiptId)
    ) {
      return {
        compensated: true,
        alreadyCompensated: true,
        conflict: false,
        mutationGeneration: currentGeneration,
        mutationRevision: currentRevision,
        revertedMutationCount: Math.max(0, int(receipt.mutationCount)),
      };
    }
    const previousDigest = String(
      receipt.previousSemanticStateDigest || semanticStateDigest(receipt.previousState)
    );
    if (semanticStateDigest(state) === previousDigest) {
      return {
        compensated: true,
        alreadyCompensated: true,
        conflict: false,
        mutationGeneration: currentGeneration,
        mutationRevision: currentRevision,
        revertedMutationCount: Math.max(0, int(receipt.mutationCount)),
      };
    }
    let conflictReason = "";
    const descendantReceipt = options?.descendantCompensationReceipt;
    const lineageRebase = Boolean(
      Number(receipt.receiptVersion) === 2
      && descendantReceipt
      && typeof descendantReceipt === "object"
      && descendantReceipt.receiptId
      && String(state.lastCompensatedReceiptId || "") === String(descendantReceipt.receiptId)
      && String(descendantReceipt.previousSemanticStateDigest || "")
        === String(receipt.expectedSemanticStateDigest || "")
      && int(descendantReceipt.previousState?.mutationRevision)
        === int(receipt.expectedMutationRevision)
      && int(descendantReceipt.previousState?.mutationGeneration)
        === int(receipt.expectedMutationGeneration)
      && currentGeneration === int(receipt.expectedMutationGeneration)
      && pathsDigest(state.paths) === String(receipt.expectedPathsDigest || "")
      && semanticStateDigest(state) === String(receipt.expectedSemanticStateDigest || "")
    );
    if (currentRevision !== int(receipt.expectedMutationRevision) && !lineageRebase) {
      conflictReason = "mutation_revision_changed";
    } else if (currentGeneration !== int(receipt.expectedMutationGeneration)) {
      conflictReason = "mutation_generation_changed";
    } else if (pathsDigest(state.paths) !== String(receipt.expectedPathsDigest || "")) {
      conflictReason = "path_hashes_changed";
    }
    if (conflictReason) {
      return {
        compensated: false,
        conflict: true,
        errorCode: "MUTATION_COMPENSATION_CONFLICT",
        reason: conflictReason,
        mutationGeneration: currentGeneration,
        mutationRevision: currentRevision,
      };
    }

    const restored = cloneJson(receipt.previousState);
    // Keep the CAS revision monotonic so an old receipt cannot match again after
    // compensation followed by a logically identical mutation (the ABA case).
    restored.mutationRevision = currentRevision + 1;
    if (receipt.receiptId) {
      restored.lastCompensatedReceiptId = String(receipt.receiptId);
    }
    await writeMutationState(projectRoot, restored);
    return {
      compensated: true,
      conflict: false,
      mutationGeneration: int(restored.mutationGeneration),
      mutationRevision: int(restored.mutationRevision),
      revertedMutationCount: Math.max(0, int(receipt.mutationCount)),
    };
  });
}

async function inspectMutationCompensation(projectRoot, receipt) {
  if (!receipt || typeof receipt !== "object" || !receipt.previousState) {
    return { status: "unknown", reason: "missing_receipt" };
  }
  return withMutationLock(projectRoot, async () => {
    const state = await readMutationState(projectRoot);
    if (
      receipt.receiptId
      && String(state.lastCompensatedReceiptId || "") === String(receipt.receiptId)
    ) {
      return { status: "previous", state, alreadyCompensated: true };
    }
    const previousDigest = String(
      receipt.previousSemanticStateDigest || semanticStateDigest(receipt.previousState)
    );
    if (semanticStateDigest(state) === previousDigest) {
      return { status: "previous", state, alreadyCompensated: true };
    }
    const expected = (
      int(state.mutationGeneration) === int(receipt.expectedMutationGeneration)
      && int(state.mutationRevision) === int(receipt.expectedMutationRevision)
      && pathsDigest(state.paths) === String(receipt.expectedPathsDigest || "")
    );
    if (expected) return { status: "expected_post", state };
    return {
      status: "conflict",
      state,
      reason: "mutation_state_changed",
    };
  });
}

function safeProjectRelativePath(projectRoot, relPath) {
  const root = path.resolve(projectRoot);
  const normalized = normalizedRelPath(relPath);
  if (!normalized || path.isAbsolute(normalized)) {
    throw new Error(`invalid mutation reconciliation path: ${relPath}`);
  }
  const absolutePath = path.resolve(root, normalized);
  const relative = path.relative(root, absolutePath);
  if (
    !relative
    || relative === ".."
    || relative.startsWith(`..${path.sep}`)
    || path.isAbsolute(relative)
  ) {
    throw new Error(`mutation reconciliation path outside project: ${relPath}`);
  }
  return {
    relPath: relative.replace(/\\/g, "/"),
    absolutePath,
  };
}

async function reconcileMutationPathsFromDisk(projectRoot, relativePaths) {
  const unique = [...new Set((relativePaths || []).map(normalizedRelPath).filter(Boolean))]
    .map((relPath) => safeProjectRelativePath(projectRoot, relPath));
  if (!unique.length) {
    const state = await readMutationState(projectRoot);
    return {
      reconciled: true,
      mutationGeneration: int(state.mutationGeneration),
      mutationRevision: int(state.mutationRevision),
      paths: [],
    };
  }
  return withMutationLock(projectRoot, async () => {
    const state = await readMutationState(projectRoot);
    if (!state.paths || typeof state.paths !== "object" || Array.isArray(state.paths)) {
      state.paths = {};
    }
    const pathHashes = {};
    for (const target of unique) {
      let content = null;
      try {
        content = await fsp.readFile(target.absolutePath, "utf8");
      } catch (error) {
        if (!error || error.code !== "ENOENT") throw error;
      }
      state.mutationGeneration = int(state.mutationGeneration) + 1;
      if (content == null) {
        delete state.paths[target.relPath];
        pathHashes[target.relPath] = null;
      } else {
        const digest = sha256Text(content);
        state.paths[target.relPath] = digest;
        pathHashes[target.relPath] = digest;
      }
    }
    state.mutationRevision = int(state.mutationRevision) + 1;
    delete state.lastCompensatedReceiptId;
    markValidationPending(state);
    await writeMutationState(projectRoot, state);
    return {
      reconciled: true,
      mutationGeneration: int(state.mutationGeneration),
      mutationRevision: int(state.mutationRevision),
      paths: unique.map((item) => item.relPath),
      pathHashes,
    };
  });
}

async function beginValidation(projectRoot) {
  const state = await readMutationState(projectRoot);
  return { startGeneration: int(state.mutationGeneration), state };
}

function validationProofMetadata(options = {}) {
  const passed = options.passed === true;
  return {
    validationPassed: passed,
    validationStatus: passed ? "passed" : "failed",
    validationBlockingErrorCount: Math.max(0, int(options.blockingErrorCount || 0)),
    validationProofLevel: String(options.proofLevel || (passed ? "StaticVerified" : "StaticFailed")),
  };
}

async function finishValidation(projectRoot, startGeneration, options = {}) {
  return withMutationLock(projectRoot, async () => {
    const state = await readMutationState(projectRoot);
    const current = int(state.mutationGeneration);
    if (current !== int(startGeneration)) {
      return { validationStale: true, validatedGeneration: null, mutationGeneration: current };
    }
    state.validatedGeneration = current;
    Object.assign(state, validationProofMetadata(options));
    await writeMutationState(projectRoot, state);
    return {
      validationStale: false,
      validatedGeneration: current,
      mutationGeneration: current,
      ...validationProofMetadata(options),
    };
  });
}

async function finishValidationAndClear(projectRoot, startGeneration, options = {}) {
  return withMutationLock(projectRoot, async () => {
    const state = await readMutationState(projectRoot);
    const current = int(state.mutationGeneration);
    if (current !== int(startGeneration)) {
      return { validationStale: true, validatedGeneration: null, mutationGeneration: current };
    }
    const validationFile = validationStatePath(projectRoot);
    try {
      if (fs.existsSync(validationFile)) {
        fs.unlinkSync(validationFile);
      }
    } catch (cause) {
      const err = new Error(`failed to clear dirty validation state: ${validationFile}`);
      err.errorCode = "VALIDATION_STATE_CLEANUP_FAILED";
      err.validationStatePath = validationFile;
      err.cause = cause;
      throw err;
    }
    if (fs.existsSync(validationFile)) {
      const err = new Error(`dirty validation state remains after cleanup: ${validationFile}`);
      err.errorCode = "VALIDATION_STATE_CLEANUP_FAILED";
      err.validationStatePath = validationFile;
      throw err;
    }
    state.validatedGeneration = current;
    Object.assign(state, validationProofMetadata(options));
    await writeMutationState(projectRoot, state);
    return {
      validationStale: false,
      validatedGeneration: current,
      mutationGeneration: current,
      ...validationProofMetadata(options),
    };
  });
}

async function recordDeletion(projectRoot, relPath, options = {}) {
  return recordMutationBatch(projectRoot, [{ relPath, deleted: true }], options);
}

async function beginBuild(projectRoot) {
  const state = await readMutationState(projectRoot);
  return {
    buildStartGeneration: int(state.mutationGeneration),
    validatedGeneration: int(state.validatedGeneration),
    mutationGeneration: int(state.mutationGeneration),
  };
}

async function finishBuild(projectRoot, buildStartGeneration) {
  const state = await readMutationState(projectRoot);
  const endGeneration = int(state.mutationGeneration);
  const stale = endGeneration !== int(buildStartGeneration);
  return {
    buildEndGeneration: endGeneration,
    buildStale: stale,
    mutationGeneration: endGeneration,
  };
}

module.exports = {
  mutationStatePath,
  readMutationState,
  recordMutation,
  recordMutationBatch,
  compensateMutationBatch,
  inspectMutationCompensation,
  reconcileMutationPathsFromDisk,
  recordDeletion,
  withMutationLock,
  beginValidation,
  finishValidation,
  finishValidationAndClear,
  beginBuild,
  finishBuild,
};

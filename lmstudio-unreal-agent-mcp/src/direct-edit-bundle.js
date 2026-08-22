"use strict";

const {
  canonicalizeBundleTargets,
  validateBundleLimits,
} = require("./direct-edit-bundle-plan");
const { constrainMutationLimits } = require("./direct-mutation-limits");
const { captureBundleBaseline, commitBundle } = require("./direct-edit-bundle-commit");
const { validateBundleBaselineHashes } = require("./direct-edit-bundle-preflight.js");
const { rollbackRuntimeTransaction } = require("./direct-transaction-recovery");
const {
  archiveRuntimeTransaction,
  createRuntimeTransaction,
  normalizeRuntimeOwner,
  saveRuntimeTransaction,
} = require("./direct-transaction-store");
const { releasePathLock, tryAcquirePathLock } = require("./write-locks");

const EDIT_BUNDLE_OPTION_FIELDS = new Set([
  "maxFilesPerEdit",
  "mutationLimits",
  "projectPath",
  "projectRoot",
  "runtimeOwner",
  "stateRoot",
  "transactionHooks",
]);

function emptyRollback() {
  return {
    rolledBack: true,
    rollbackIncomplete: false,
    restoredPaths: [],
    untouchedPaths: [],
    externalChangeDetected: [],
    rollbackErrors: [],
  };
}

async function applyDirectEditBundle(bundle, resolvePath, options = {}) {
  for (const key of Object.keys(options)) {
    if (!EDIT_BUNDLE_OPTION_FIELDS.has(key)) {
      throw new Error(`Direct edit bundle contains unsupported option: ${key}`);
    }
  }
  const runtimeOwner = normalizeRuntimeOwner(options.runtimeOwner);
  const stateRoot = options.stateRoot;
  const mutationLimits = constrainMutationLimits({
    ...(options.mutationLimits || {}),
    ...(options.maxFilesPerEdit === undefined
      ? {}
      : { maxFilesPerEdit: options.maxFilesPerEdit }),
  });
  const { relativePaths, targets } = await canonicalizeBundleTargets(
    bundle,
    resolvePath,
    mutationLimits,
  );
  const journal = createRuntimeTransaction({
    runtimeOwner,
    stateRoot,
    projectRoot: options.projectRoot,
    projectPath: options.projectPath,
  });
  const acquired = [];

  try {
    const lockOrder = [...relativePaths].sort((left, right) => (
      targets.get(left).absolutePath.localeCompare(targets.get(right).absolutePath)
    ));
    for (const relativePath of lockOrder) {
      const target = targets.get(relativePath);
      const lock = tryAcquirePathLock(target.absolutePath, `${runtimeOwner}_edit_bundle`, {
        stateRoot,
      });
      if (!lock.ok) {
        journal.status = "aborted";
        journal.error = `Another write is in progress on ${relativePath}`;
        saveRuntimeTransaction(journal, stateRoot);
        await archiveRuntimeTransaction(journal, stateRoot);
        return {
          ok: false,
          error: journal.error,
          transactionId: journal.transactionId,
          lockFailure: true,
          rollback: emptyRollback(),
          rolledBack: false,
          rollbackIncomplete: false,
        };
      }
      acquired.push(lock);
    }

    try {
      const baseline = await captureBundleBaseline(targets, journal, stateRoot);
      validateBundleBaselineHashes(bundle, baseline);
      const committed = await commitBundle(
        bundle,
        targets,
        baseline,
        journal,
        stateRoot,
        options.transactionHooks || {},
      );
      journal.status = "completed";
      journal.error = "";
      saveRuntimeTransaction(journal, stateRoot);
      let archiveWarning = "";
      try {
        await archiveRuntimeTransaction(journal, stateRoot);
      } catch (error) {
        // A durable completed journal is safe to archive on the next startup;
        // never roll back a commit solely because archival failed.
        archiveWarning = String(error.message || error);
      }
      return {
        ok: true,
        transactionId: journal.transactionId,
        journal,
        targets,
        baseline,
        writtenAbsolutePaths: committed.writtenAbsolutePaths,
        postWriteHashes: committed.postWriteHashes,
        preChangeHashes: Object.fromEntries(
          [...baseline].map(([relativePath, snapshot]) => [relativePath, snapshot.preHash]),
        ),
        archiveWarning,
      };
    } catch (error) {
      const rollback = journal.entries.some((entry) => entry.writeStarted)
        ? await rollbackRuntimeTransaction(journal, stateRoot, { locksHeld: true })
        : emptyRollback();
      if (!journal.entries.some((entry) => entry.writeStarted)) {
        journal.status = "aborted";
        journal.error = String(error.message || error);
        saveRuntimeTransaction(journal, stateRoot);
        await archiveRuntimeTransaction(journal, stateRoot);
      }
      return {
        ok: false,
        error: String(error.message || error),
        ...(error.mutationFailure ? { mutationFailure: { ...error.mutationFailure } } : {}),
        transactionId: journal.transactionId,
        rollback,
        rolledBack: rollback.rolledBack,
        rollbackIncomplete: rollback.rollbackIncomplete,
        restoredPaths: rollback.restoredPaths,
        externalChangeDetected: rollback.externalChangeDetected,
      };
    }
  } finally {
    for (const lock of acquired.reverse()) releasePathLock(lock);
  }
}

module.exports = {
  applyDirectEditBundle,
  validateBundleLimits,
};

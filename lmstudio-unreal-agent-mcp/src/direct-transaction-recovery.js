"use strict";

const fs = require("node:fs");
const fsp = fs.promises;
const path = require("node:path");
const { atomicWriteText } = require("./atomic-io");
const { absolutePathIsWithin } = require("./filesystem-path-identity");
const { sha256Text } = require("./safe-write");
const { withPathLock } = require("./write-locks");
const {
  archiveRuntimeTransaction,
  listRuntimeTransactionFiles,
  normalizeRuntimeOwner,
  readRuntimeTransaction,
  runtimeTransactionPaths,
  saveRuntimeTransaction,
  updateRuntimeTransactionEntry,
} = require("./direct-transaction-store");

function entryOwnedPostImage(entry, existsNow, currentHash) {
  if (!existsNow) return false;
  const hashes = new Set([
    entry.postHash,
    ...(Array.isArray(entry.intendedPostHashes) ? entry.intendedPostHashes : []),
  ].filter(Boolean));
  return hashes.has(currentHash);
}

function assertRecoveryPaths(journal, entry, stateRoot) {
  const target = path.resolve(entry.canonicalAbsolutePath);
  if (!absolutePathIsWithin(target, journal.projectRoot)) {
    throw new Error(`Recovery target escapes project root: ${entry.relativePath}`);
  }
  if (entry.existedBefore && entry.preContentBackupPath) {
    const backupRoot = runtimeTransactionPaths(stateRoot, journal.runtimeOwner).backups;
    if (!absolutePathIsWithin(path.resolve(entry.preContentBackupPath), backupRoot)) {
      throw new Error(`Recovery backup escapes owner backup root: ${entry.relativePath}`);
    }
  }
}

async function restoreEntry(journal, entry, stateRoot) {
  assertRecoveryPaths(journal, entry, stateRoot);
  if (entry.writeStarted !== true) return { ok: true, restored: false, untouched: true };
  const target = path.resolve(entry.canonicalAbsolutePath);
  const recover = async () => {
    const existsNow = fs.existsSync(target);
    const currentHash = existsNow ? sha256Text(await fsp.readFile(target, "utf8")) : "";
    const preImageIntact = entry.existedBefore
      ? existsNow && Boolean(entry.preHash && currentHash === entry.preHash)
      : !existsNow;
    if (preImageIntact) return { ok: true, restored: false, alreadyRestored: true };

    if (!entryOwnedPostImage(entry, existsNow, currentHash)) {
      return { ok: false, externalChange: true, currentHash, existsNow };
    }
    if (entry.existedBefore) {
      const backup = String(entry.preContentBackupPath || "");
      if (!backup || !fs.existsSync(backup)) {
        return { ok: false, error: `Missing pre-image backup: ${entry.relativePath}` };
      }
      const preContent = await fsp.readFile(backup, "utf8");
      if (sha256Text(preContent) !== entry.preHash) {
        return { ok: false, error: `Pre-image backup hash mismatch: ${entry.relativePath}` };
      }
      atomicWriteText(target, preContent, "utf8");
    } else {
      await fsp.unlink(target);
    }
    return { ok: true, restored: true };
  };
  const locked = await withPathLock(target, `${journal.runtimeOwner}_transaction_recovery`, recover, {
    stateRoot,
  });
  return locked.locked
    ? { ok: false, error: `Recovery target is locked: ${entry.relativePath}` }
    : locked.result;
}

async function rollbackRuntimeTransaction(journal, stateRoot, options = {}) {
  const restoredPaths = [];
  const untouchedPaths = [];
  const externalChangeDetected = [];
  const rollbackErrors = [];
  const entries = [...journal.entries].reverse();

  for (const entry of entries) {
    let result;
    try {
      if (options.locksHeld === true) {
        assertRecoveryPaths(journal, entry, stateRoot);
        if (entry.writeStarted !== true) {
          result = { ok: true, untouched: true };
        } else {
          const target = path.resolve(entry.canonicalAbsolutePath);
          const existsNow = fs.existsSync(target);
          const currentHash = existsNow ? sha256Text(await fsp.readFile(target, "utf8")) : "";
          const preImageIntact = entry.existedBefore
            ? existsNow && Boolean(entry.preHash && currentHash === entry.preHash)
            : !existsNow;
          if (preImageIntact) {
            result = { ok: true, alreadyRestored: true };
          } else if (!entryOwnedPostImage(entry, existsNow, currentHash)) {
            result = { ok: false, externalChange: true };
          } else if (entry.existedBefore) {
            const backup = String(entry.preContentBackupPath || "");
            if (!backup || !fs.existsSync(backup)) {
              result = { ok: false, error: `Missing pre-image backup: ${entry.relativePath}` };
            } else {
              const preContent = await fsp.readFile(backup, "utf8");
              if (sha256Text(preContent) !== entry.preHash) {
                result = { ok: false, error: `Pre-image backup hash mismatch: ${entry.relativePath}` };
              } else {
                atomicWriteText(target, preContent, "utf8");
                result = { ok: true, restored: true };
              }
            }
          } else {
            await fsp.unlink(target);
            result = { ok: true, restored: true };
          }
        }
      } else {
        result = await restoreEntry(journal, entry, stateRoot);
      }
    } catch (error) {
      result = { ok: false, error: String(error.message || error) };
    }

    if (result.ok) {
      if (result.restored) restoredPaths.push(entry.relativePath);
      else untouchedPaths.push(entry.relativePath);
      updateRuntimeTransactionEntry(journal, {
        relativePath: entry.relativePath,
        restored: true,
        rollbackSkippedReason: "",
      }, stateRoot);
    } else {
      if (result.externalChange) externalChangeDetected.push(entry.relativePath);
      rollbackErrors.push({
        path: entry.relativePath,
        error: result.error || "external_change_detected",
      });
      updateRuntimeTransactionEntry(journal, {
        relativePath: entry.relativePath,
        restored: false,
        rollbackSkippedReason: result.externalChange ? "external_change_detected" : "recovery_failed",
      }, stateRoot);
    }
  }

  const rolledBack = rollbackErrors.length === 0;
  journal.status = rolledBack ? "rolled_back" : "rollback_incomplete";
  journal.error = rolledBack ? "" : "One or more paths could not be restored safely.";
  saveRuntimeTransaction(journal, stateRoot);
  if (rolledBack) await archiveRuntimeTransaction(journal, stateRoot);
  return {
    rolledBack,
    rollbackIncomplete: !rolledBack,
    restoredPaths,
    untouchedPaths,
    externalChangeDetected,
    rollbackErrors,
  };
}

async function recoverRuntimeTransactions(stateRoot, owner) {
  const runtimeOwner = normalizeRuntimeOwner(owner);
  const report = {
    runtimeOwner,
    scanned: 0,
    recovered: [],
    archived: [],
    recoveryRequired: [],
  };
  const files = await listRuntimeTransactionFiles(stateRoot, runtimeOwner);
  for (const filePath of files) {
    report.scanned += 1;
    let journal;
    try {
      journal = await readRuntimeTransaction(filePath, runtimeOwner);
    } catch (error) {
      report.recoveryRequired.push({
        filePath,
        error: `Invalid ${runtimeOwner} transaction journal: ${error.message || error}`,
      });
      continue;
    }

    try {
      if (["completed", "aborted", "rolled_back"].includes(journal.status)) {
        await archiveRuntimeTransaction(journal, stateRoot);
        report.archived.push(journal.transactionId);
        continue;
      }
      if (journal.status === "rollback_incomplete") {
        // Once an external image has been observed, hash equality on a later
        // startup cannot prove ownership (the file may have gone through ABA).
        // Keep the journal byte-stable for explicit inspection and never retry
        // an automatic rollback that could overwrite newer work.
        report.recoveryRequired.push({
          transactionId: journal.transactionId,
          externalChangeDetected: journal.entries
            .filter((entry) => entry.rollbackSkippedReason === "external_change_detected")
            .map((entry) => entry.relativePath),
          error: journal.error || "Transaction rollback requires explicit inspection.",
        });
        continue;
      }
      const rollback = await rollbackRuntimeTransaction(journal, stateRoot);
      if (rollback.rolledBack) {
        report.recovered.push({ transactionId: journal.transactionId, ...rollback });
      } else {
        report.recoveryRequired.push({ transactionId: journal.transactionId, ...rollback });
      }
    } catch (error) {
      report.recoveryRequired.push({
        transactionId: journal.transactionId,
        filePath,
        error: String(error.message || error),
      });
    }
  }
  return report;
}

module.exports = {
  entryOwnedPostImage,
  recoverRuntimeTransactions,
  rollbackRuntimeTransaction,
};

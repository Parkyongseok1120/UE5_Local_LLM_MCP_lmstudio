"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const crypto = require("crypto");
const { atomicWriteText } = require("./atomic-io");
const {
  sha256Text,
  calculateReplacement,
  replaceWithCAS,
  createExclusive,
} = require("./safe-write");
const { tryAcquirePathLock, releasePathLock, canonicalLockKey } = require("./write-locks");
const {
  createJournal,
  upsertEntry,
  completedEntries,
  archiveJournal,
  saveJournal,
  pathIdentity,
  requiresBuildValidation,
  entryMatchesOwnedPostImage,
} = require("./transaction-journal");
const { ensureStateRootLayout, resolveAgentStateRoot } = require("./state-root");

const MAX_BUNDLE_FILES = 32;
const MAX_BUNDLE_BYTES = 2 * 1024 * 1024;
const MAX_BUNDLE_OPERATIONS = 128;
const DEFAULT_MAX_FILES_PER_EDIT = 2;

const PROTECTED_OVERWRITE_EXT = new Set([".h", ".hpp", ".cpp", ".c", ".cc", ".cxx", ".cs", ".json", ".ini", ".uplugin", ".uproject"]);

function bundlePaths(bundle) {
  const paths = [];
  for (const item of bundle?.patches || []) {
    if (item?.path) paths.push(String(item.path).replace(/\\/g, "/"));
  }
  for (const item of bundle?.files || []) {
    if (item?.path) paths.push(String(item.path).replace(/\\/g, "/"));
  }
  return paths;
}

function entryByteSize(item) {
  return Buffer.byteLength(String(item.content || ""), "utf8")
    + Buffer.byteLength(String(item.newText || ""), "utf8")
    + Buffer.byteLength(String(item.oldText || ""), "utf8");
}

function validateBundleLimits(bundle, maxFilesPerEdit = DEFAULT_MAX_FILES_PER_EDIT) {
  const operations = [...(bundle?.patches || []), ...(bundle?.files || [])];
  if (operations.length > MAX_BUNDLE_OPERATIONS) {
    throw new Error(`apply_edit_bundle: too many operations (max ${MAX_BUNDLE_OPERATIONS})`);
  }
  const relPaths = bundlePaths(bundle);
  const unique = new Set(relPaths);
  const filePaths = (bundle?.files || [])
    .filter((item) => item?.path)
    .map((item) => String(item.path).replace(/\\/g, "/"));
  if (new Set(filePaths).size !== filePaths.length) {
    throw new Error("apply_edit_bundle: duplicate files[] paths are not allowed");
  }
  const patchPaths = new Set(
    (bundle?.patches || [])
      .filter((item) => item?.path)
      .map((item) => String(item.path).replace(/\\/g, "/"))
  );
  const mixedPath = filePaths.find((rel) => patchPaths.has(rel));
  if (mixedPath) {
    throw new Error(`apply_edit_bundle: ${mixedPath} cannot appear in both files[] and patches[]`);
  }
  if (unique.size > maxFilesPerEdit) {
    throw new Error(`apply_edit_bundle: too many files (max ${maxFilesPerEdit})`);
  }
  if (unique.size > MAX_BUNDLE_FILES) {
    throw new Error(`apply_edit_bundle: too many files (max ${MAX_BUNDLE_FILES})`);
  }
  let bytes = 0;
  for (const item of operations) {
    bytes += entryByteSize(item);
  }
  if (bytes > MAX_BUNDLE_BYTES) {
    throw new Error(`apply_edit_bundle: bundle payload too large (max ${MAX_BUNDLE_BYTES} bytes)`);
  }
}

async function canonicalizeTargets(bundle, resolvePathFn) {
  validateBundleLimits(bundle);
  const relPaths = [...new Set(bundlePaths(bundle))];
  const targets = {};
  const canonicalKeys = new Map();
  for (const rel of relPaths) {
    const resolution = await resolvePathFn(rel);
    if (!resolution?.ok) {
      throw new Error(resolution?.error || `Invalid bundle path: ${rel}`);
    }
    const abs = path.resolve(resolution.absolutePath);
    let canonicalAbs = abs;
    try {
      canonicalAbs = fs.realpathSync.native ? fs.realpathSync.native(abs) : fs.realpathSync(abs);
    } catch {
      canonicalAbs = abs;
    }
    const key = canonicalLockKey(canonicalAbs);
    if (canonicalKeys.has(key) && canonicalKeys.get(key).rel !== rel) {
      throw new Error(`apply_edit_bundle: alias paths resolve to same file: ${rel} and ${canonicalKeys.get(key).rel}`);
    }
    canonicalKeys.set(key, { rel, abs: canonicalAbs });
    targets[rel] = {
      rel,
      abs: canonicalAbs,
      canonicalKey: key,
    };
  }
  return { relPaths, targets };
}

function backupPath(stateRoot, transactionId, rel) {
  const digest = crypto.createHash("sha256").update(rel).digest("hex").slice(0, 16);
  return path.join(stateRoot, "backups", `${transactionId}-${digest}.bak`);
}

async function captureBaseline(targets, journal, stateRoot) {
  const baseline = {};
  for (const rel of Object.keys(targets)) {
    const { abs } = targets[rel];
    let existedBefore = false;
    let preHash = "";
    let preContent = null;
    let statError = null;
    try {
      const st = await fsp.stat(abs);
      if (st.isFile()) {
        existedBefore = true;
        preContent = await fsp.readFile(abs, "utf8");
        preHash = sha256Text(preContent);
      } else if (st.isDirectory()) {
        throw new Error(`Path is a directory: ${rel}`);
      }
    } catch (err) {
      if (err && err.code === "ENOENT") {
        existedBefore = false;
      } else {
        statError = String(err.message || err);
        throw new Error(`Cannot stat ${rel}: ${statError}`);
      }
    }
    let preContentBackupPath = null;
    if (existedBefore && preContent != null) {
      preContentBackupPath = backupPath(stateRoot, journal.transactionId, rel);
      atomicWriteText(preContentBackupPath, preContent);
    }
    baseline[rel] = { existedBefore, preHash, preContent, preContentBackupPath };
    upsertEntry(journal, {
      relativePath: rel,
      canonicalAbsolutePath: abs,
      operation: "baseline",
      existedBefore,
      preHash,
      preContentBackupPath,
      writeStarted: false,
      writeCompleted: false,
    }, stateRoot);
  }
  journal.status = "locked";
  saveJournal(journal, stateRoot);
  return baseline;
}

async function commitFromTargets(
  bundle,
  targets,
  baseline,
  journal,
  stateRoot,
  hooks = {},
  atomicMetadata = null,
) {
  const writtenAbs = [];
  const writtenAbsSet = new Set();
  const postWriteHashes = {};
  const patchedPaths = new Set();
  let stageIndex = 0;

  journal.status = "committing";
  saveJournal(journal, stateRoot);
  if (atomicMetadata && typeof atomicMetadata === "object") {
    journal.requiresAtomicCheckpoint = true;
    journal.checkpointRequired = atomicMetadata.checkpointRequired !== false;
    journal.checkpointCommitted = false;
    journal.mutationStateRecorded = false;
    journal.mutationStateRequired = atomicMetadata.mutationStateRequired !== false;
    journal.rollbackStateReconciled = false;
    journal.projectRoot = pathIdentity(atomicMetadata.projectRoot || process.cwd());
    journal.projectPath = String(atomicMetadata.projectPath || "");
    journal.taskSessionId = String(atomicMetadata.taskSessionId || "").trim();
  }

  for (const item of bundle?.patches || []) {
    const rel = String(item.path).replace(/\\/g, "/");
    const target = targets[rel];
    if (!target) throw new Error(`Unknown patch path: ${rel}`);
    const abs = target.abs;
    const base = baseline[rel];
    const priorContent = base.existedBefore ? await fsp.readFile(abs, "utf8") : "";
    const planned = calculateReplacement({
      priorContent,
      oldText: String(item.oldText || ""),
      newText: String(item.newText || ""),
      expectedOccurrences: Number(item.expectedOccurrences ?? 1),
    });
    if (!planned.ok) {
      throw new Error(planned.error || `Patch failed for ${rel}`);
    }
    const postHash = sha256Text(planned.updated);
    const previousEntry = (journal.entries || []).find((entry) => entry.relativePath === rel);
    const intendedPostHashes = [...new Set([
      ...(Array.isArray(previousEntry?.intendedPostHashes) ? previousEntry.intendedPostHashes : []),
      previousEntry?.postHash,
      postHash,
    ].filter(Boolean))];
    upsertEntry(journal, {
      relativePath: rel,
      canonicalAbsolutePath: abs,
      operation: "patch",
      existedBefore: base.existedBefore,
      preHash: base.preHash,
      preContentBackupPath: base.preContentBackupPath,
      postHash,
      intendedPostHashes,
      writeStarted: true,
      writeCompleted: false,
      restored: false,
    }, stateRoot);
    stageIndex += 1;
    if (typeof hooks.afterWriteAhead === "function") {
      await hooks.afterWriteAhead({
        operation: "patch",
        relativePath: rel,
        stageIndex,
        priorHash: sha256Text(priorContent),
        postHash,
        journal,
      });
    }
    // Repeated patches for one file are applied in request order. The first
    // edit is guarded by the caller's/baseline hash; later edits compare their
    // exact oldText against the content produced by the preceding edit.
    const readHash = patchedPaths.has(rel)
      ? sha256Text(priorContent)
      : (item.readHash || base.preHash || null);
    const result = await replaceWithCAS({
      targetPath: abs,
      priorContent,
      oldText: String(item.oldText || ""),
      newText: String(item.newText || ""),
      expectedOccurrences: Number(item.expectedOccurrences ?? 1),
      readHash,
    });
    if (!result.ok) {
      throw new Error(result.error || `Patch failed for ${rel}`);
    }
    if (sha256Text(result.updated) !== postHash) {
      throw new Error(`Patch plan changed during commit for ${rel}`);
    }
    if (typeof hooks.afterDiskWrite === "function") {
      await hooks.afterDiskWrite({
        operation: "patch",
        relativePath: rel,
        stageIndex,
        priorHash: sha256Text(priorContent),
        postHash,
        journal,
      });
    }
    postWriteHashes[rel] = postHash;
    upsertEntry(journal, {
      relativePath: rel,
      postHash,
      writeCompleted: true,
      restored: false,
    }, stateRoot);
    patchedPaths.add(rel);
    if (!writtenAbsSet.has(abs)) {
      writtenAbsSet.add(abs);
      writtenAbs.push(abs);
    }
  }

  for (const item of bundle?.files || []) {
    const rel = String(item.path).replace(/\\/g, "/");
    const target = targets[rel];
    if (!target) throw new Error(`Unknown file path: ${rel}`);
    const abs = target.abs;
    const base = baseline[rel];
    if (base.existedBefore) {
      const ext = path.extname(rel).toLowerCase();
      if (PROTECTED_OVERWRITE_EXT.has(ext) || base.existedBefore) {
        throw new Error(`files[] cannot overwrite existing file ${rel}; use patches`);
      }
    }
    const content = String(item.content || "");
    const postHash = sha256Text(content);
    const previousEntry = (journal.entries || []).find((entry) => entry.relativePath === rel);
    const intendedPostHashes = [...new Set([
      ...(Array.isArray(previousEntry?.intendedPostHashes) ? previousEntry.intendedPostHashes : []),
      previousEntry?.postHash,
      postHash,
    ].filter(Boolean))];
    upsertEntry(journal, {
      relativePath: rel,
      canonicalAbsolutePath: abs,
      operation: "create",
      existedBefore: base.existedBefore,
      preHash: base.preHash,
      postHash,
      intendedPostHashes,
      writeStarted: true,
      writeCompleted: false,
      restored: false,
    }, stateRoot);
    stageIndex += 1;
    if (typeof hooks.afterWriteAhead === "function") {
      await hooks.afterWriteAhead({
        operation: "create",
        relativePath: rel,
        stageIndex,
        priorHash: "",
        postHash,
        journal,
      });
    }
    if (base.existedBefore) {
      throw new Error(`files[] create-only violation for existing path: ${rel}`);
    }
    await createExclusive(abs, content);
    if (typeof hooks.afterDiskWrite === "function") {
      await hooks.afterDiskWrite({
        operation: "create",
        relativePath: rel,
        stageIndex,
        priorHash: "",
        postHash,
        journal,
      });
    }
    postWriteHashes[rel] = postHash;
    upsertEntry(journal, {
      relativePath: rel,
      postHash,
      writeCompleted: true,
      restored: false,
    }, stateRoot);
    if (!writtenAbsSet.has(abs)) {
      writtenAbsSet.add(abs);
      writtenAbs.push(abs);
    }
  }

  journal.status = "committed";
  saveJournal(journal, stateRoot);
  return { writtenAbs, postWriteHashes };
}

async function rollbackJournal(journal, stateRoot = resolveAgentStateRoot()) {
  const restored = [];
  const unrestored = [];
  const errors = [];
  const externalChangeDetected = [];

  for (const entry of completedEntries(journal)) {
    const abs = entry.canonicalAbsolutePath;
    const rel = entry.relativePath;
    try {
      const existsNow = fs.existsSync(abs);
      let currentHash = "";
      if (existsNow) {
        currentHash = sha256Text(await fsp.readFile(abs, "utf8"));
      }
      const preImageIntact = entry.existedBefore
        ? existsNow && Boolean(entry.preHash && currentHash === entry.preHash)
        : !existsNow;
      if (preImageIntact) {
        restored.push(rel);
        upsertEntry(journal, { relativePath: rel, restored: true }, stateRoot);
        continue;
      }
      if (entry.existedBefore) {
        if (entry.deletedAfter === true) {
          if (existsNow) {
            externalChangeDetected.push(rel);
            unrestored.push(rel);
            upsertEntry(journal, { relativePath: rel, rollbackSkippedReason: "external_change_detected" }, stateRoot);
            continue;
          }
          const deletedBackup = entry.preContentBackupPath;
          if (deletedBackup && fs.existsSync(deletedBackup)) {
            atomicWriteText(abs, fs.readFileSync(deletedBackup, "utf8"));
            restored.push(rel);
            upsertEntry(journal, { relativePath: rel, restored: true }, stateRoot);
            continue;
          }
          throw new Error(`missing backup for ${rel}`);
        }
        if (!existsNow) {
          externalChangeDetected.push(rel);
          unrestored.push(rel);
          upsertEntry(journal, { relativePath: rel, rollbackSkippedReason: "external_change_detected" }, stateRoot);
          continue;
        } else if (!entryMatchesOwnedPostImage(entry, existsNow, currentHash)) {
          externalChangeDetected.push(rel);
          unrestored.push(rel);
          upsertEntry(journal, { relativePath: rel, rollbackSkippedReason: "external_change_detected" }, stateRoot);
          continue;
        } else {
          const backup = entry.preContentBackupPath;
          if (backup && fs.existsSync(backup)) {
            atomicWriteText(abs, fs.readFileSync(backup, "utf8"));
          } else {
            throw new Error(`missing backup for ${rel}`);
          }
        }
      } else if (!existsNow) {
        restored.push(rel);
        upsertEntry(journal, { relativePath: rel, restored: true }, stateRoot);
        continue;
      } else if (entryMatchesOwnedPostImage(entry, existsNow, currentHash)) {
        await fsp.unlink(abs);
      } else {
        externalChangeDetected.push(rel);
        unrestored.push(rel);
        upsertEntry(journal, { relativePath: rel, rollbackSkippedReason: "external_change_detected" }, stateRoot);
        continue;
      }
      restored.push(rel);
      upsertEntry(journal, { relativePath: rel, restored: true }, stateRoot);
    } catch (err) {
      errors.push({ path: rel, error: String(err.message || err) });
      unrestored.push(rel);
    }
  }

  const rolledBack = unrestored.length === 0 && errors.length === 0;
  journal.status = rolledBack ? "rolled_back" : "rollback_incomplete";
  saveJournal(journal, stateRoot);
  return {
    rolledBack,
    rollbackIncomplete: !rolledBack,
    restoredPaths: restored,
    unrestoredPaths: unrestored,
    rollbackErrors: errors,
    externalChangeDetected,
  };
}

async function applyBundleTransaction(bundle, resolvePathFn, options = {}) {
  const maxFilesPerEdit = Number(options.maxFilesPerEdit || DEFAULT_MAX_FILES_PER_EDIT);
  validateBundleLimits(bundle, maxFilesPerEdit);
  const stateRoot = ensureStateRootLayout(resolveAgentStateRoot());
  const journal = createJournal({ operation: "apply_edit_bundle" }, stateRoot);
  const acquired = [];
  let wroteAny = false;

  try {
    const { relPaths, targets } = await canonicalizeTargets(bundle, resolvePathFn);
    const lockOrder = [...relPaths].sort((a, b) => targets[a].abs.localeCompare(targets[b].abs));
    for (const rel of lockOrder) {
      const lock = tryAcquirePathLock(targets[rel].abs, "apply_edit_bundle");
      if (!lock.ok) {
        return {
          ok: false,
          error: `previous write still in progress on ${rel}`,
          transactionId: journal.transactionId,
          rolledBack: false,
          rollbackIncomplete: false,
          lockFailure: true,
        };
      }
      acquired.push(targets[rel].abs);
    }

    const baseline = await captureBaseline(targets, journal, stateRoot);
    const commitResult = await commitFromTargets(
      bundle,
      targets,
      baseline,
      journal,
      stateRoot,
      options.transactionHooks || {},
      options.deferFinalization === true ? {
        projectRoot: options.projectRoot,
        projectPath: options.transactionMetadata?.projectPath,
        taskSessionId: options.taskSessionId,
        checkpointRequired: options.checkpointRequired !== undefined
          ? options.checkpointRequired
          : Boolean(String(options.taskSessionId || "").trim()),
        mutationStateRequired: true,
      } : null,
    );
    wroteAny = commitResult.writtenAbs.length > 0;

    let validationResult = { ok: true };
    if (typeof options.onCommitted === "function") {
      validationResult = await options.onCommitted({
        writtenAbs: commitResult.writtenAbs,
        postWriteHashes: commitResult.postWriteHashes,
        journal,
        targets,
        baseline,
        preChangeHashes: Object.fromEntries(Object.entries(baseline).map(([rel, b]) => [rel, b.preHash])),
      }) || { ok: true };
    }

    if (!validationResult.ok) {
      const rollback = await rollbackJournal(journal);
      return {
        ok: false,
        error: validationResult.error || "validation failed",
        transactionId: journal.transactionId,
        rollback,
        rolledBack: rollback.rolledBack,
        rollbackIncomplete: rollback.rollbackIncomplete,
        restoredPaths: rollback.restoredPaths,
        unrestoredPaths: rollback.unrestoredPaths,
        externalChangeDetected: rollback.externalChangeDetected,
        validation: validationResult.validation,
      };
    }

    const buildValidationRequired = (journal.entries || []).some(
      (entry) => requiresBuildValidation(entry.relativePath)
    );
    if (options.deferFinalization === true && buildValidationRequired) {
      Object.assign(journal, options.transactionMetadata || {});
      journal.status = "awaiting_build";
      journal.projectRoot = pathIdentity(options.projectRoot || process.cwd());
      journal.taskSessionId = String(options.taskSessionId || "").trim();
      journal.mutationGeneration = Number(options.mutationGeneration || 0);
      saveJournal(journal);
    } else {
      journal.status = "completed";
      saveJournal(journal);
      await archiveJournal(journal.transactionId, stateRoot);
    }

    return {
      ok: true,
      transactionId: journal.transactionId,
      journal,
      targets,
      baseline,
      writtenAbs: commitResult.writtenAbs,
      postWriteHashes: commitResult.postWriteHashes,
      preChangeHashes: Object.fromEntries(Object.entries(baseline).map(([rel, b]) => [rel, b.preHash])),
      validation: validationResult.validation,
    };
  } catch (error) {
    let rollback = {
      rolledBack: true,
      rollbackIncomplete: false,
      restoredPaths: [],
      unrestoredPaths: [],
      rollbackErrors: [],
      externalChangeDetected: [],
    };
    // A process-local exception can arrive after the filesystem write but
    // before the matching writeCompleted journal update (for example from a
    // post-write hook or the journal persistence itself).  The durable
    // write-ahead entry is therefore the rollback authority; relying only on
    // writeCompleted/wroteAny can falsely report a rollback and archive the
    // sole recovery record while the post-image is still on disk.
    const hasDurableWriteIntent = completedEntries(journal).length > 0;
    if (hasDurableWriteIntent || wroteAny) {
      rollback = await rollbackJournal(journal);
    } else {
      journal.status = "aborted";
      saveJournal(journal);
    }
    return {
      ok: false,
      error: String(error.message || error),
      transactionId: journal.transactionId,
      rollback,
      rolledBack: rollback.rolledBack,
      rollbackIncomplete: rollback.rollbackIncomplete,
      restoredPaths: rollback.restoredPaths,
      unrestoredPaths: rollback.unrestoredPaths,
      externalChangeDetected: rollback.externalChangeDetected,
    };
  } finally {
    for (const abs of acquired.reverse()) {
      releasePathLock(abs);
    }
  }
}

async function finalizeTransaction(journal, validationOk) {
  if (validationOk) {
    journal.status = "completed";
    saveJournal(journal);
    await archiveJournal(journal.transactionId);
    return { ok: true };
  }
  const rollback = await rollbackJournal(journal);
  return { ok: false, rollback };
}

module.exports = {
  bundlePaths,
  validateBundleLimits,
  canonicalizeTargets,
  commitFromTargets,
  rollbackJournal,
  applyBundleTransaction,
  finalizeTransaction,
  MAX_BUNDLE_FILES,
  MAX_BUNDLE_BYTES,
  MAX_BUNDLE_OPERATIONS,
  DEFAULT_MAX_FILES_PER_EDIT,
};

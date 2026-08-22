"use strict";

const fs = require("node:fs");
const fsp = fs.promises;
const { atomicWriteText } = require("./atomic-io");
const {
  calculateReplacement,
  createExclusive,
  replaceWithCAS,
  sha256Text,
} = require("./safe-write");
const {
  saveRuntimeTransaction,
  transactionBackupPath,
  updateRuntimeTransactionEntry,
} = require("./direct-transaction-store");

async function captureBundleBaseline(targets, journal, stateRoot) {
  const baseline = new Map();
  for (const [relativePath, target] of targets) {
    let existedBefore = false;
    let preContent = "";
    let preHash = "";
    try {
      const stat = await fsp.stat(target.absolutePath);
      if (stat.isDirectory()) throw new Error(`Path is a directory: ${relativePath}`);
      if (!stat.isFile()) throw new Error(`Path is not a regular file: ${relativePath}`);
      existedBefore = true;
      preContent = await fsp.readFile(target.absolutePath, "utf8");
      preHash = sha256Text(preContent);
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }

    let preContentBackupPath = "";
    if (existedBefore) {
      preContentBackupPath = transactionBackupPath(
        stateRoot,
        journal.runtimeOwner,
        journal.transactionId,
        relativePath,
      );
      atomicWriteText(preContentBackupPath, preContent, "utf8");
    }
    const snapshot = { existedBefore, preContent, preHash, preContentBackupPath };
    baseline.set(relativePath, snapshot);
    updateRuntimeTransactionEntry(journal, {
      relativePath,
      canonicalAbsolutePath: target.absolutePath,
      operation: "baseline",
      existedBefore,
      preHash,
      preContentBackupPath,
      postHash: "",
      intendedPostHashes: [],
      writeStarted: false,
      writeCompleted: false,
      restored: false,
      rollbackSkippedReason: "",
    }, stateRoot);
  }
  journal.status = "locked";
  saveRuntimeTransaction(journal, stateRoot);
  return baseline;
}

function patchFailure(result, relativePath, item) {
  const error = new Error(result?.error || `Patch failed for ${relativePath}`);
  const message = String(result?.error || "");
  error.mutationFailure = {
    errorCode: String(result?.errorCode || "") || (
      /oldText not found|found 0\b/iu.test(message)
        ? "OLD_TEXT_NOT_FOUND"
        : /occurrence mismatch/iu.test(message)
          ? "OCCURRENCE_MISMATCH"
          : "PATCH_CAS_FAILED"
    ),
    relativePath,
    expectedOccurrences: Number(item?.expectedOccurrences ?? 1),
  };
  return error;
}

function intendedHashes(journal, relativePath, postHash) {
  const entry = journal.entries.find((item) => item.relativePath === relativePath);
  return [...new Set([
    ...(entry?.intendedPostHashes || []),
    entry?.postHash,
    postHash,
  ].filter(Boolean))];
}

async function callHook(hooks, name, payload) {
  if (typeof hooks?.[name] === "function") await hooks[name](payload);
}

async function commitBundle(bundle, targets, baseline, journal, stateRoot, hooks = {}) {
  const writtenAbsolutePaths = [];
  const writtenSet = new Set();
  const postWriteHashes = {};
  const patchedPaths = new Set();
  let stageIndex = 0;

  journal.status = "committing";
  saveRuntimeTransaction(journal, stateRoot);
  for (const item of bundle.patches || []) {
    const relativePath = String(item.path).replace(/\\/g, "/");
    const target = targets.get(relativePath);
    const original = baseline.get(relativePath);
    if (!target || !original) throw new Error(`Unknown patch path: ${relativePath}`);
    const priorContent = await fsp.readFile(target.absolutePath, "utf8");
    const planned = calculateReplacement({
      priorContent,
      oldText: String(item.oldText || ""),
      newText: String(item.newText || ""),
      expectedOccurrences: Number(item.expectedOccurrences ?? 1),
    });
    if (!planned.ok) throw patchFailure(planned, relativePath, item);
    const priorHash = sha256Text(priorContent);
    const postHash = sha256Text(planned.updated);
    updateRuntimeTransactionEntry(journal, {
      relativePath,
      operation: "patch",
      postHash,
      intendedPostHashes: intendedHashes(journal, relativePath, postHash),
      writeStarted: true,
      writeCompleted: false,
      restored: false,
    }, stateRoot);
    stageIndex += 1;
    await callHook(hooks, "afterWriteAhead", {
      operation: "patch",
      relativePath,
      stageIndex,
      priorHash,
      postHash,
      journal,
    });

    const result = await replaceWithCAS({
      targetPath: target.absolutePath,
      priorContent,
      oldText: String(item.oldText || ""),
      newText: String(item.newText || ""),
      expectedOccurrences: Number(item.expectedOccurrences ?? 1),
      readHash: patchedPaths.has(relativePath)
        ? priorHash
        : (item.expectedHash || original.preHash || null),
    });
    if (!result.ok) throw patchFailure(result, relativePath, item);
    if (sha256Text(result.updated) !== postHash) {
      throw new Error(`Patch plan changed during commit for ${relativePath}`);
    }
    await callHook(hooks, "afterDiskWrite", {
      operation: "patch",
      relativePath,
      stageIndex,
      priorHash,
      postHash,
      journal,
    });
    updateRuntimeTransactionEntry(journal, {
      relativePath,
      postHash,
      writeCompleted: true,
    }, stateRoot);
    postWriteHashes[relativePath] = postHash;
    patchedPaths.add(relativePath);
    if (!writtenSet.has(target.absolutePath)) {
      writtenSet.add(target.absolutePath);
      writtenAbsolutePaths.push(target.absolutePath);
    }
  }

  for (const item of bundle.files || []) {
    const relativePath = String(item.path).replace(/\\/g, "/");
    const target = targets.get(relativePath);
    const original = baseline.get(relativePath);
    if (!target || !original) throw new Error(`Unknown file path: ${relativePath}`);
    if (original.existedBefore) {
      throw new Error(`files[] cannot overwrite existing file ${relativePath}; use patches`);
    }
    const content = String(item.content || "");
    const postHash = sha256Text(content);
    updateRuntimeTransactionEntry(journal, {
      relativePath,
      operation: "create",
      postHash,
      intendedPostHashes: intendedHashes(journal, relativePath, postHash),
      writeStarted: true,
      writeCompleted: false,
      restored: false,
    }, stateRoot);
    stageIndex += 1;
    await callHook(hooks, "afterWriteAhead", {
      operation: "create",
      relativePath,
      stageIndex,
      priorHash: "",
      postHash,
      journal,
    });
    await createExclusive(target.absolutePath, content);
    await callHook(hooks, "afterDiskWrite", {
      operation: "create",
      relativePath,
      stageIndex,
      priorHash: "",
      postHash,
      journal,
    });
    updateRuntimeTransactionEntry(journal, {
      relativePath,
      postHash,
      writeCompleted: true,
    }, stateRoot);
    postWriteHashes[relativePath] = postHash;
    if (!writtenSet.has(target.absolutePath)) {
      writtenSet.add(target.absolutePath);
      writtenAbsolutePaths.push(target.absolutePath);
    }
  }

  journal.status = "committed";
  saveRuntimeTransaction(journal, stateRoot);
  return { postWriteHashes, writtenAbsolutePaths };
}

module.exports = { captureBundleBaseline, commitBundle };

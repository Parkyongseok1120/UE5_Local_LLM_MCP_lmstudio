"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const { atomicWriteText, atomicCreateText } = require("./atomic-io");
const { sha256File, sha256Text, replaceWithCAS, createExclusive } = require("./safe-write");
const { tryAcquirePathLock, releasePathLock } = require("./write-locks");

const MAX_BUNDLE_FILES = 32;
const MAX_BUNDLE_BYTES = 2 * 1024 * 1024;

function bundlePaths(bundle) {
  const paths = [];
  for (const item of bundle?.patches || []) {
    if (item?.path) {
      paths.push(String(item.path).replace(/\\/g, "/"));
    }
  }
  for (const item of bundle?.files || []) {
    if (item?.path) {
      paths.push(String(item.path).replace(/\\/g, "/"));
    }
  }
  return paths;
}

function validateBundleLimits(bundle) {
  const relPaths = bundlePaths(bundle);
  const unique = new Set(relPaths);
  if (unique.size !== relPaths.length) {
    throw new Error("apply_edit_bundle: duplicate paths in bundle are not allowed");
  }
  if (unique.size > MAX_BUNDLE_FILES) {
    throw new Error(`apply_edit_bundle: too many files (max ${MAX_BUNDLE_FILES})`);
  }
  let bytes = 0;
  for (const item of [...(bundle?.patches || []), ...(bundle?.files || [])]) {
    bytes += Buffer.byteLength(String(item.content ?? item.newText ?? item.oldText ?? ""), "utf8");
  }
  if (bytes > MAX_BUNDLE_BYTES) {
    throw new Error(`apply_edit_bundle: bundle payload too large (max ${MAX_BUNDLE_BYTES} bytes)`);
  }
}

async function stageBundle(bundle, resolvePathFn) {
  validateBundleLimits(bundle);
  const relPaths = [...new Set(bundlePaths(bundle))];
  const validated = [];
  for (const rel of relPaths) {
    const resolution = await resolvePathFn(rel);
    if (!resolution?.ok) {
      throw new Error(resolution?.error || `Invalid bundle path: ${rel}`);
    }
    validated.push({ rel, abs: resolution.absolutePath });
  }

  const preHashes = {};
  const staged = {};
  const absByRel = {};
  for (const { rel, abs } of validated) {
    absByRel[rel] = abs;
    try {
      const st = await fsp.stat(abs);
      if (st.isFile()) {
        staged[rel] = await fsp.readFile(abs, "utf8");
        preHashes[rel] = await sha256File(abs);
      } else {
        staged[rel] = null;
      }
    } catch {
      staged[rel] = null;
    }
  }
  return { relPaths, preHashes, staged, absByRel };
}

async function commitBundleEntries(bundle, preHashes, absByRel, resolvePathFn) {
  const postWriteHashes = {};
  const writtenAbs = [];

  for (const item of bundle?.patches || []) {
    const rel = String(item.path).replace(/\\/g, "/");
    const resolution = await resolvePathFn(rel);
    if (!resolution?.ok) {
      throw new Error(resolution?.error || `Invalid patch path: ${rel}`);
    }
    const abs = resolution.absolutePath;
    const expectedHash = item.readHash || preHashes[rel] || "";
    const priorContent = fs.existsSync(abs) ? await fsp.readFile(abs, "utf8") : "";
    const result = await replaceWithCAS({
      targetPath: abs,
      priorContent,
      oldText: String(item.oldText || ""),
      newText: String(item.newText || ""),
      expectedOccurrences: Number(item.expectedOccurrences ?? 1),
      readHash: expectedHash || preHashes[rel] || null,
    });
    if (!result.ok) {
      throw new Error(result.error || `Patch failed for ${rel}`);
    }
    postWriteHashes[rel] = sha256Text(result.updated);
    writtenAbs.push(abs);
  }

  for (const item of bundle?.files || []) {
    const rel = String(item.path).replace(/\\/g, "/");
    const resolution = await resolvePathFn(rel);
    if (!resolution?.ok) {
      throw new Error(resolution?.error || `Invalid file path: ${rel}`);
    }
    const abs = resolution.absolutePath;
    const expectedHash = item.readHash || preHashes[rel] || "";
    const exists = fs.existsSync(abs);
    if (exists) {
      const priorContent = await fsp.readFile(abs, "utf8");
      const result = await replaceWithCAS({
        targetPath: abs,
        priorContent,
        oldText: priorContent,
        newText: String(item.content || ""),
        expectedOccurrences: 1,
        readHash: expectedHash || preHashes[rel] || null,
      });
      if (!result.ok) {
        throw new Error(result.error || `Overwrite failed for ${rel}`);
      }
      postWriteHashes[rel] = sha256Text(result.updated);
    } else {
      await createExclusive(abs, String(item.content || ""));
      postWriteHashes[rel] = sha256Text(String(item.content || ""));
    }
    writtenAbs.push(abs);
  }

  return { writtenAbs, postWriteHashes };
}

async function rollbackBundle(staged, absByRel, postWriteHashes = {}) {
  const restored = [];
  const skipped = [];
  const errors = [];

  for (const [rel, prior] of Object.entries(staged || {})) {
    const abs = absByRel[rel] || rel;
    const expectedPost = postWriteHashes[rel];
    try {
      if (prior === null) {
        if (!fs.existsSync(abs)) {
          restored.push(rel);
          continue;
        }
        const current = await fsp.readFile(abs, "utf8");
        const currentHash = sha256Text(current);
        if (expectedPost && currentHash !== expectedPost) {
          skipped.push({ path: rel, reason: "external_change_detected" });
          continue;
        }
        await fsp.unlink(abs);
        restored.push(rel);
      } else {
        let currentHash = "";
        if (fs.existsSync(abs)) {
          currentHash = sha256Text(await fsp.readFile(abs, "utf8"));
        }
        if (expectedPost && currentHash && currentHash !== expectedPost) {
          skipped.push({ path: rel, reason: "external_change_detected" });
          continue;
        }
        await fsp.mkdir(path.dirname(abs), { recursive: true });
        atomicWriteText(abs, prior);
        restored.push(rel);
      }
    } catch (err) {
      errors.push({ path: rel, error: String(err.message || err) });
    }
  }

  const rolledBack = skipped.length === 0 && errors.length === 0;
  return {
    rolledBack,
    rollbackIncomplete: !rolledBack,
    restoredPaths: restored,
    unrestoredPaths: skipped.map((item) => item.path),
    rollbackErrors: errors,
    rollbackSkipped: skipped,
  };
}

async function applyBundleTransaction(bundle, resolvePathFn) {
  const staged = await stageBundle(bundle, resolvePathFn);
  const lockOrder = [...staged.relPaths].sort((a, b) => staged.absByRel[a].localeCompare(staged.absByRel[b]));
  const acquired = [];
  let postWriteHashes = {};
  try {
    for (const rel of lockOrder) {
      const lock = tryAcquirePathLock(staged.absByRel[rel], "apply_edit_bundle");
      if (!lock.ok) {
        throw new Error(`previous write still in progress on ${rel}`);
      }
      acquired.push(staged.absByRel[rel]);
    }
    const commitResult = await commitBundleEntries(
      bundle,
      staged.preHashes,
      staged.absByRel,
      resolvePathFn
    );
    postWriteHashes = commitResult.postWriteHashes;
    return {
      ok: true,
      staged,
      writtenAbs: commitResult.writtenAbs,
      postWriteHashes,
      preChangeHashes: staged.preHashes,
    };
  } catch (error) {
    const rollback = await rollbackBundle(staged.staged, staged.absByRel, postWriteHashes);
    return {
      ok: false,
      error: String(error.message || error),
      staged,
      postWriteHashes: {},
      preChangeHashes: staged.preHashes,
      rollback,
    };
  } finally {
    for (const abs of acquired.reverse()) {
      releasePathLock(abs);
    }
  }
}

module.exports = {
  bundlePaths,
  stageBundle,
  commitBundleEntries,
  rollbackBundle,
  applyBundleTransaction,
  MAX_BUNDLE_FILES,
  MAX_BUNDLE_BYTES,
};

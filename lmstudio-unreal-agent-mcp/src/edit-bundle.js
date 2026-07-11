"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const { atomicWriteText, atomicWriteTextExclusive } = require("./atomic-io");
const { sha256File, sha256Text, assertPreCommitHash, replaceWithCAS } = require("./safe-write");

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
  return [...new Set(paths)];
}

function capturePreHashes(projectRoot, paths) {
  const pre = {};
  for (const rel of paths) {
    const abs = path.join(projectRoot, rel);
    try {
      const st = await fsp.stat(abs);
      if (st.isFile()) {
        pre[rel] = await sha256File(abs);
      }
    } catch {
      // missing file
    }
  }
  return pre;
}

async function stageBundle(projectRoot, bundle, resolvePathFn) {
  const relPaths = bundlePaths(bundle);
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
  for (const { rel, abs } of validated) {
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
  return { relPaths, preHashes, staged, validated };
}

async function commitBundle(projectRoot, bundle, preHashes, resolvePathFn) {
  const written = [];
  for (const item of bundle?.patches || []) {
    const rel = String(item.path).replace(/\\/g, "/");
    const resolution = await resolvePathFn(rel);
    if (!resolution?.ok) {
      throw new Error(resolution?.error || `Invalid patch path: ${rel}`);
    }
    const abs = resolution.absolutePath;
    const expectedHash = item.readHash || preHashes[rel] || "";
    const check = await assertPreCommitHash(abs, expectedHash);
    if (!check.ok) {
      throw new Error(check.error || `Pre-commit CAS failed for ${rel}`);
    }
    const priorContent = fs.existsSync(abs) ? await fsp.readFile(abs, "utf8") : "";
    const result = await replaceWithCAS({
      targetPath: abs,
      priorContent,
      oldText: String(item.oldText || ""),
      newText: String(item.newText || ""),
      expectedOccurrences: Number(item.expectedOccurrences ?? 1),
      readHash: expectedHash,
    });
    if (!result.ok) {
      throw new Error(result.error || `Patch failed for ${rel}`);
    }
    written.push(abs);
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
      const check = await assertPreCommitHash(abs, expectedHash);
      if (!check.ok) {
        throw new Error(check.error || `Pre-commit CAS failed for ${rel}`);
      }
      const priorContent = await fsp.readFile(abs, "utf8");
      const result = await replaceWithCAS({
        targetPath: abs,
        priorContent,
        oldText: priorContent,
        newText: String(item.content || ""),
        expectedOccurrences: 1,
        readHash: expectedHash,
      });
      if (!result.ok) {
        throw new Error(result.error || `Overwrite failed for ${rel}`);
      }
    } else {
      atomicWriteTextExclusive(abs, String(item.content || ""));
    }
    written.push(abs);
  }
  return written;
}

async function rollbackBundle(projectRoot, staged, postWriteHashes = {}) {
  for (const [rel, prior] of Object.entries(staged || {})) {
    const abs = path.join(projectRoot, rel);
    if (prior === null) {
      try {
        const current = fs.existsSync(abs) ? await fsp.readFile(abs, "utf8") : null;
        const expected = postWriteHashes[rel];
        if (expected && current && sha256Text(current) !== expected) {
          continue;
        }
        await fsp.unlink(abs);
      } catch {
        /* ignore */
      }
    } else {
      const current = fs.existsSync(abs) ? await fsp.readFile(abs, "utf8") : null;
      const expected = postWriteHashes[rel];
      if (expected && current && sha256Text(current) !== expected) {
        continue;
      }
      await fsp.mkdir(path.dirname(abs), { recursive: true });
      atomicWriteText(abs, prior);
    }
  }
}

module.exports = {
  bundlePaths,
  capturePreHashes,
  stageBundle,
  commitBundle,
  rollbackBundle,
};

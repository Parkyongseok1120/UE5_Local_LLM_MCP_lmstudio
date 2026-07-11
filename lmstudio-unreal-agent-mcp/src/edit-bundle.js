"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const crypto = require("crypto");

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

function hashFile(absPath) {
  try {
    const data = fs.readFileSync(absPath);
    return crypto.createHash("sha256").update(data).digest("hex").slice(0, 16);
  } catch {
    return "";
  }
}

function capturePreHashes(projectRoot, paths) {
  const pre = {};
  for (const rel of paths) {
    const abs = path.join(projectRoot, rel);
    if (fs.existsSync(abs) && fs.statSync(abs).isFile()) {
      pre[rel] = hashFile(abs);
    }
  }
  return pre;
}

async function stageBundle(projectRoot, bundle) {
  const relPaths = bundlePaths(bundle);
  const preHashes = capturePreHashes(projectRoot, relPaths);
  const staged = {};
  for (const rel of relPaths) {
    const abs = path.join(projectRoot, rel);
    if (fs.existsSync(abs)) {
      staged[rel] = await fsp.readFile(abs, "utf8");
    } else {
      staged[rel] = null;
    }
  }
  return { relPaths, preHashes, staged };
}

async function commitBundle(projectRoot, bundle) {
  const written = [];
  for (const item of bundle?.patches || []) {
    const rel = String(item.path).replace(/\\/g, "/");
    const abs = path.join(projectRoot, rel);
    const oldText = String(item.oldText || "");
    const newText = String(item.newText || "");
    const expected = Number(item.expectedOccurrences || 1);
    let content = fs.existsSync(abs) ? await fsp.readFile(abs, "utf8") : "";
    const count = oldText ? content.split(oldText).length - 1 : 0;
    if (count !== expected) {
      throw new Error(`patch occurrence mismatch for ${rel}: expected ${expected}, found ${count}`);
    }
    content = content.split(oldText).join(newText);
    await fsp.mkdir(path.dirname(abs), { recursive: true });
    await fsp.writeFile(abs, content, "utf8");
    written.push(abs);
  }
  for (const item of bundle?.files || []) {
    const rel = String(item.path).replace(/\\/g, "/");
    const abs = path.join(projectRoot, rel);
    await fsp.mkdir(path.dirname(abs), { recursive: true });
    await fsp.writeFile(abs, String(item.content || ""), "utf8");
    written.push(abs);
  }
  return written;
}

async function rollbackBundle(projectRoot, staged) {
  for (const [rel, prior] of Object.entries(staged || {})) {
    const abs = path.join(projectRoot, rel);
    if (prior === null) {
      try {
        await fsp.unlink(abs);
      } catch {
        /* ignore */
      }
    } else {
      await fsp.mkdir(path.dirname(abs), { recursive: true });
      await fsp.writeFile(abs, prior, "utf8");
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

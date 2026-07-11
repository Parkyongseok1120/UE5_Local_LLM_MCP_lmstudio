"use strict";

const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { ensureStateRootLayout, resolveAgentStateRoot } = require("./state-root");

const pendingPaths = new Map();
const OWNER = `${process.pid}:${crypto.randomUUID()}`;

function canonicalLockKey(absPath) {
  try {
    const resolved = fs.realpathSync.native ? fs.realpathSync.native(absPath) : fs.realpathSync(absPath);
    return resolved.toLowerCase();
  } catch {
    return path.resolve(absPath).toLowerCase();
  }
}

function lockFilePath(absPath, stateRoot = resolveAgentStateRoot()) {
  ensureStateRootLayout(stateRoot);
  const digest = crypto.createHash("sha256").update(canonicalLockKey(absPath)).digest("hex");
  return path.join(stateRoot, "locks", `${digest}.lock`);
}

function readLockOwner(lockPath) {
  try {
    return fs.readFileSync(lockPath, "utf8").trim();
  } catch {
    return "";
  }
}

function isStaleLock(lockPath) {
  const owner = readLockOwner(lockPath);
  if (!owner) {
    return true;
  }
  const pidPart = owner.split(":")[0];
  const pid = Number(pidPart);
  if (!Number.isFinite(pid) || pid <= 0) {
    return true;
  }
  try {
    process.kill(pid, 0);
    return false;
  } catch {
    return true;
  }
}

function tryAcquireCrossProcessLock(absPath, label = "write", stateRoot = resolveAgentStateRoot()) {
  const key = canonicalLockKey(absPath);
  if (pendingPaths.has(key)) {
    return { ok: false, holder: pendingPaths.get(key), scope: "in_process" };
  }
  const lockPath = lockFilePath(absPath, stateRoot);
  try {
    const fd = fs.openSync(lockPath, "wx");
    fs.writeFileSync(fd, `${OWNER}\n${label}\n${new Date().toISOString()}\n`);
    fs.closeSync(fd);
    pendingPaths.set(key, { owner: OWNER, label, lockPath });
    return { ok: true, lockPath, key };
  } catch (err) {
    if (err && err.code === "EEXIST") {
      if (isStaleLock(lockPath)) {
        try {
          fs.unlinkSync(lockPath);
        } catch {
          return { ok: false, holder: readLockOwner(lockPath), scope: "cross_process" };
        }
        return tryAcquireCrossProcessLock(absPath, label, stateRoot);
      }
      return { ok: false, holder: readLockOwner(lockPath), scope: "cross_process" };
    }
    return { ok: false, error: String(err.message || err) };
  }
}

function releaseCrossProcessLock(absPath) {
  const key = canonicalLockKey(absPath);
  const meta = pendingPaths.get(key);
  if (!meta) {
    return;
  }
  pendingPaths.delete(key);
  try {
    const owner = readLockOwner(meta.lockPath);
    if (owner.startsWith(OWNER)) {
      fs.unlinkSync(meta.lockPath);
    }
  } catch {
    // Best effort.
  }
}

function tryAcquirePathLock(absPath, label = "write") {
  return tryAcquireCrossProcessLock(absPath, label);
}

function releasePathLock(absPath) {
  releaseCrossProcessLock(absPath);
}

function isPathLocked(absPath) {
  const key = canonicalLockKey(absPath);
  if (pendingPaths.has(key)) {
    return true;
  }
  const lockPath = lockFilePath(absPath);
  return fs.existsSync(lockPath) && !isStaleLock(lockPath);
}

async function withPathLock(absPath, label, fn) {
  const acquired = tryAcquirePathLock(absPath, label);
  if (!acquired.ok) {
    return { locked: true, holder: acquired.holder };
  }
  try {
    return { locked: false, result: await fn() };
  } finally {
    releasePathLock(absPath);
  }
}

module.exports = {
  canonicalLockKey,
  tryAcquirePathLock,
  releasePathLock,
  isPathLocked,
  withPathLock,
  tryAcquireCrossProcessLock,
  releaseCrossProcessLock,
};

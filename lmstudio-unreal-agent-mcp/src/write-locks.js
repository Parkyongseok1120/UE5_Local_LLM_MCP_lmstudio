"use strict";

// In-memory single-flight guard for file mutations. Node is single-threaded, so
// "concurrent" here means overlapping async operations (a timed-out request whose
// write/validation is still running when the model retries the same path).
const pendingPaths = new Map();

function lockKey(absPath) {
  return String(absPath || "");
}

function tryAcquirePathLock(absPath, label = "write") {
  const key = lockKey(absPath);
  if (pendingPaths.has(key)) {
    return { ok: false, holder: pendingPaths.get(key) };
  }
  pendingPaths.set(key, label);
  return { ok: true };
}

function releasePathLock(absPath) {
  pendingPaths.delete(lockKey(absPath));
}

function isPathLocked(absPath) {
  return pendingPaths.has(lockKey(absPath));
}

async function withPathLock(absPath, label, fn) {
  const acquired = tryAcquirePathLock(absPath, label);
  if (!acquired.ok) {
    return {
      locked: true,
      holder: acquired.holder,
    };
  }
  try {
    return { locked: false, result: await fn() };
  } finally {
    releasePathLock(absPath);
  }
}

module.exports = {
  tryAcquirePathLock,
  releasePathLock,
  isPathLocked,
  withPathLock,
};

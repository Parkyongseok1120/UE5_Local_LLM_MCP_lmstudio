"use strict";

const crypto = require("crypto");
const cp = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { canonicalAbsolutePathIdentity } = require("./filesystem-path-identity");
const { ensureStateRootLayout, resolveAgentStateRoot } = require("./state-root");
const { resolvePythonExe } = require("./validate-write");

const pendingPaths = new Map();
const OWNER = `${process.pid}:${crypto.randomUUID()}`;
const HEARTBEAT_INTERVAL_MS = 60_000;
const STALE_LOCK_AGE_MS = HEARTBEAT_INTERVAL_MS * 3;
let processIdentityCache;

function canonicalLockKey(absPath, hostPlatform = process.platform) {
  return canonicalAbsolutePathIdentity(absPath, hostPlatform);
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

function readLockProcessIdentity(lockPath) {
  try {
    const line = fs.readFileSync(lockPath, "utf8")
      .split(/\r?\n/)
      .find((item) => item.startsWith("processIdentity:"));
    return line ? line.slice("processIdentity:".length).trim() : "";
  } catch {
    return "";
  }
}

function probeProcessStartIdentity(pid) {
  if (!Number.isFinite(pid) || pid <= 0) return "";
  if (process.platform === "win32") {
    const script = [
      `$p = Get-Process -Id ${pid} -ErrorAction Stop`,
      "[Console]::Out.Write($p.StartTime.ToUniversalTime().ToFileTimeUtc())",
    ].join("; ");
    const result = cp.spawnSync(
      "powershell.exe",
      ["-NoProfile", "-NonInteractive", "-Command", script],
      { encoding: "utf8", windowsHide: true, timeout: 2500 }
    );
    const value = String(result.stdout || "").trim();
    return result.status === 0 && /^\d+$/.test(value) ? `filetime:${value}` : "";
  }
  try {
    const statPath = `/proc/${pid}/stat`;
    if (fs.existsSync(statPath)) {
      const stat = fs.readFileSync(statPath, "utf8");
      const close = stat.lastIndexOf(")");
      const fields = (close >= 0 ? stat.slice(close + 1) : stat).trim().split(/\s+/);
      const ticks = Number(fields[19]);
      if (Number.isInteger(ticks) && ticks >= 0) return `ticks:${ticks}`;
    }
  } catch {
    return "";
  }
  const result = cp.spawnSync("ps", ["-o", "lstart=", "-p", String(pid)], {
    encoding: "utf8",
    timeout: 5000,
  });
  const started = String(result.stdout || "").trim();
  return result.status === 0 && started ? `ps:${started}` : "";
}

function currentProcessIdentity() {
  if (processIdentityCache === undefined) {
    processIdentityCache = probeProcessStartIdentity(process.pid);
  }
  return processIdentityCache;
}

function lockPayload(label) {
  return `${OWNER}\n${label}\n${new Date().toISOString()}\nprocessIdentity:${currentProcessIdentity()}\n`;
}

function staleReclaimGuardPath(lockPath) {
  return path.join(path.dirname(lockPath), "stale-reclaim.sqlite3");
}

function runStaleReclaimBridge(action, lockPath) {
  const script = path.join(__dirname, "write-lock-reclaim-bridge.py");
  const result = cp.spawnSync(
    resolvePythonExe(),
    [script, action, staleReclaimGuardPath(lockPath), path.basename(lockPath), OWNER, String(process.pid)],
    {
      encoding: "utf8",
      windowsHide: true,
      timeout: 10_000,
      env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
    }
  );
  if (result.error) {
    return { ok: false, error: String(result.error.message || result.error) };
  }
  try {
    return JSON.parse(String(result.stdout || "").trim());
  } catch {
    return {
      ok: false,
      error: String(result.stderr || result.stdout || `reclaim bridge exited ${result.status}`),
    };
  }
}

function tryAcquireStaleReclaimGuard(lockPath) {
  const result = runStaleReclaimBridge("acquire", lockPath);
  return result.ok ? { ok: true, guardPath: lockPath } : result;
}

function releaseStaleReclaimGuard(lockPath) {
  runStaleReclaimBridge("release", lockPath);
}

function isProcessAlive(pid) {
  if (!Number.isFinite(pid) || pid <= 0) {
    return "dead";
  }
  try {
    process.kill(pid, 0);
    return "alive";
  } catch (err) {
    if (err && err.code === "EPERM") {
      return "unknown";
    }
    return "dead";
  }
}

function isStaleLock(lockPath) {
  if (!fs.existsSync(lockPath)) {
    return true;
  }
  const owner = readLockOwner(lockPath);
  if (!owner) {
    return true;
  }
  const pidPart = owner.split(":")[0];
  const pid = Number(pidPart);
  if (!Number.isFinite(pid) || pid <= 0) {
    return true;
  }
  const alive = isProcessAlive(pid);
  if (alive === "dead") {
    return true;
  }
  const recordedIdentity = readLockProcessIdentity(lockPath);
  const currentIdentity = recordedIdentity ? probeProcessStartIdentity(pid) : "";
  if (recordedIdentity && currentIdentity && recordedIdentity !== currentIdentity) {
    return true;
  }
  // Match the Python lock policy: a lock owned by a live process (or a
  // process whose liveness cannot be inspected) is never stolen due to age.
  // Heartbeat age is diagnostic only because long operations can legitimately
  // outlive several heartbeat intervals.
  return false;
}

function refreshLockHeartbeat(absPath, label = "write", stateRoot = resolveAgentStateRoot()) {
  const lockPath = lockFilePath(absPath, stateRoot);
  const owner = readLockOwner(lockPath);
  if (owner.startsWith(OWNER)) {
    fs.writeFileSync(lockPath, lockPayload(label));
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
    fs.writeFileSync(fd, lockPayload(label));
    fs.closeSync(fd);
    pendingPaths.set(key, { owner: OWNER, label, lockPath });
    return { ok: true, lockPath, key };
  } catch (err) {
    if (err && err.code === "EEXIST") {
      if (isStaleLock(lockPath)) {
        const reclaim = tryAcquireStaleReclaimGuard(lockPath);
        if (!reclaim.ok) {
          return {
            ok: false,
            holder: reclaim.holder || readLockOwner(lockPath),
            scope: "cross_process",
            ...(reclaim.error ? { error: reclaim.error } : {}),
          };
        }
        try {
          // Re-check only after exclusively claiming stale recovery. A second
          // reclaimer can no longer unlink a live lock created by the winner.
          if (!isStaleLock(lockPath)) {
            return { ok: false, holder: readLockOwner(lockPath), scope: "cross_process" };
          }
          fs.unlinkSync(lockPath);
          const reclaimedFd = fs.openSync(lockPath, "wx");
          fs.writeFileSync(reclaimedFd, lockPayload(label));
          fs.closeSync(reclaimedFd);
          pendingPaths.set(key, { owner: OWNER, label, lockPath });
          return { ok: true, lockPath, key, staleReclaimed: true };
        } catch (reclaimError) {
          return {
            ok: false,
            holder: readLockOwner(lockPath),
            scope: "cross_process",
            error: String(reclaimError.message || reclaimError),
          };
        } finally {
          releaseStaleReclaimGuard(reclaim.guardPath);
        }
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
  if (meta.heartbeatTimer) {
    clearInterval(meta.heartbeatTimer);
  }
  try {
    const owner = readLockOwner(meta.lockPath);
    if (owner.startsWith(OWNER)) {
      fs.unlinkSync(meta.lockPath);
    }
  } catch {
    // Best effort.
  }
}

function tryAcquirePathLock(absPath, label = "write", options = {}) {
  const acquired = tryAcquireCrossProcessLock(absPath, label, options.stateRoot);
  if (!acquired.ok) {
    return acquired;
  }
  if (options.heartbeat) {
    const key = acquired.key;
    const meta = pendingPaths.get(key);
    if (meta) {
      meta.heartbeatTimer = setInterval(() => {
        refreshLockHeartbeat(absPath, label, options.stateRoot);
      }, HEARTBEAT_INTERVAL_MS);
      if (typeof meta.heartbeatTimer.unref === "function") {
        meta.heartbeatTimer.unref();
      }
    }
  }
  return acquired;
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

async function withPathLock(absPath, label, fn, options = {}) {
  const acquired = tryAcquirePathLock(absPath, label, options);
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
  lockFilePath,
  tryAcquirePathLock,
  releasePathLock,
  isPathLocked,
  withPathLock,
  tryAcquireCrossProcessLock,
  releaseCrossProcessLock,
  refreshLockHeartbeat,
  isStaleLock,
};

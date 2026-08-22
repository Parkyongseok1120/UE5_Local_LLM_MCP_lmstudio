"use strict";

const assert = require("assert");
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");

const {
  canonicalLockKey,
  isStaleLock,
  lockFilePath,
  releaseCrossProcessLock,
  tryAcquireCrossProcessLock,
} = require("../src/write-locks");

test("lock identity is host-aware without Unicode case folding", () => {
  const dottedCapitalI = path.join(os.tmpdir(), "Source", "\u0130", "Thing.cpp");
  const decomposedDottedI = path.join(os.tmpdir(), "Source", "I\u0307", "Thing.cpp");
  for (const hostPlatform of ["linux", "darwin", "win32"]) {
    assert.notStrictEqual(
      canonicalLockKey(dottedCapitalI, hostPlatform),
      canonicalLockKey(decomposedDottedI, hostPlatform)
    );
  }
  assert.strictEqual(
    canonicalLockKey(path.join(os.tmpdir(), "Source", "FOO.cpp"), "win32"),
    canonicalLockKey(path.join(os.tmpdir(), "source", "foo.cpp"), "win32")
  );
  assert.notStrictEqual(
    canonicalLockKey(path.join(os.tmpdir(), "Source", "FOO.cpp"), "linux"),
    canonicalLockKey(path.join(os.tmpdir(), "source", "foo.cpp"), "linux")
  );
});

test("POSIX locks keep canonically similar I-dot files independent", { skip: process.platform === "win32" }, (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unicode-locks-"));
  const stateRoot = path.join(root, "state");
  const first = path.join(root, "Source", "\u0130", "Thing.cpp");
  const second = path.join(root, "Source", "I\u0307", "Thing.cpp");
  fs.mkdirSync(path.dirname(first), { recursive: true });
  fs.mkdirSync(path.dirname(second), { recursive: true });
  fs.writeFileSync(first, "first");
  fs.writeFileSync(second, "second");
  try {
    const firstStat = fs.statSync(first);
    const secondStat = fs.statSync(second);
    if (firstStat.dev === secondStat.dev && firstStat.ino === secondStat.ino) {
      t.skip("host filesystem aliases the two Unicode spellings");
      return;
    }
    assert.notStrictEqual(lockFilePath(first, stateRoot), lockFilePath(second, stateRoot));
    assert.strictEqual(tryAcquireCrossProcessLock(first, "first", stateRoot).ok, true);
    assert.strictEqual(tryAcquireCrossProcessLock(second, "second", stateRoot).ok, true);
  } finally {
    releaseCrossProcessLock(second, stateRoot);
    releaseCrossProcessLock(first, stateRoot);
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("old lock owned by the current live PID is never stolen", () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "live-lock-state-"));
  const target = path.join(stateRoot, "target.cpp");
  fs.writeFileSync(target, "content");
  const lockPath = lockFilePath(target, stateRoot);
  fs.writeFileSync(lockPath, `${process.pid}:python-owner\npython\nold\n`);
  const old = new Date(Date.now() - (24 * 60 * 60 * 1000));
  fs.utimesSync(lockPath, old, old);
  try {
    assert.strictEqual(isStaleLock(lockPath), false);
    const acquired = tryAcquireCrossProcessLock(target, "node", stateRoot);
    assert.strictEqual(acquired.ok, false);
    assert.strictEqual(acquired.scope, "cross_process");
  } finally {
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("two stale reclaimers cannot both acquire the replacement lock", async () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "stale-lock-race-state-"));
  const target = path.join(stateRoot, "target.cpp");
  fs.writeFileSync(target, "content");
  const lockPath = lockFilePath(target, stateRoot);
  fs.writeFileSync(lockPath, "999999:dead-owner\nwrite\nold\n");
  const modulePath = path.resolve(__dirname, "..", "src", "write-locks.js");
  const childScript = `
const locks = require(process.argv[1]);
const target = process.argv[2];
const stateRoot = process.argv[3];
const result = locks.tryAcquireCrossProcessLock(target, "race", stateRoot);
process.stdout.write(JSON.stringify(result));
if (result.ok) {
  setTimeout(() => locks.releaseCrossProcessLock(target, stateRoot), 1200);
}
`;
  const run = (root = stateRoot) => new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["-e", childScript, modulePath, target, root], {
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) reject(new Error(stderr || `child exited ${code}`));
      else resolve(JSON.parse(stdout));
    });
  });
  try {
    const aliasRoot = process.platform === "win32" ? stateRoot.toUpperCase() : stateRoot;
    const results = await Promise.all([run(), run(aliasRoot)]);
    assert.strictEqual(results.filter((result) => result.ok).length, 1);
    assert.strictEqual(results.filter((result) => !result.ok).length, 1);
  } finally {
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("fresh empty lock is protected during initialization and aged empty lock is reclaimed", () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "initializing-lock-state-"));
  const target = path.join(stateRoot, "target.cpp");
  fs.writeFileSync(target, "content");
  const lockPath = lockFilePath(target, stateRoot);
  fs.closeSync(fs.openSync(lockPath, "wx"));
  try {
    assert.strictEqual(isStaleLock(lockPath), false);
    const blocked = tryAcquireCrossProcessLock(target, "contender", stateRoot);
    assert.strictEqual(blocked.ok, false);
    assert.strictEqual(blocked.scope, "cross_process");

    const aged = new Date(Date.now() - 10_000);
    fs.utimesSync(lockPath, aged, aged);
    assert.strictEqual(isStaleLock(lockPath), true);
    const acquired = tryAcquireCrossProcessLock(target, "replacement", stateRoot);
    assert.strictEqual(acquired.ok, true, JSON.stringify(acquired));
    assert.strictEqual(acquired.staleReclaimed, true);
  } finally {
    releaseCrossProcessLock(target, stateRoot);
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("reused live PID lock is stale when its birth identity differs", () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "reused-live-pid-state-"));
  const target = path.join(stateRoot, "target.cpp");
  fs.writeFileSync(target, "content");
  const lockPath = lockFilePath(target, stateRoot);
  fs.writeFileSync(
    lockPath,
    `${process.pid}:old-owner\nwrite\nold\nprocessIdentity:reused:old\n`
  );
  try {
    assert.strictEqual(isStaleLock(lockPath), true);
  } finally {
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("dead stale-reclaim owner is transactionally replaced", () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "dead-reclaimer-state-"));
  const target = path.join(stateRoot, "target.cpp");
  fs.writeFileSync(target, "content");
  const lockPath = lockFilePath(target, stateRoot);
  fs.writeFileSync(lockPath, "999999:dead-lock-owner\nwrite\nold\n");
  const bridge = path.resolve(__dirname, "..", "src", "write-lock-reclaim-bridge.py");
  const database = path.join(path.dirname(lockPath), "stale-reclaim.sqlite3");
  const seeded = require("child_process").spawnSync(
    process.platform === "win32" ? "python" : "python3",
    [bridge, "acquire", database, path.basename(lockPath), "999999:dead-reclaimer", "999999"],
    { encoding: "utf8", windowsHide: true }
  );
  try {
    assert.strictEqual(seeded.status, 0, seeded.stderr || seeded.stdout);
    assert.strictEqual(JSON.parse(seeded.stdout).ok, true);
    const acquired = tryAcquireCrossProcessLock(target, "replacement", stateRoot);
    assert.strictEqual(acquired.ok, true, JSON.stringify(acquired));
    assert.strictEqual(acquired.staleReclaimed, true);
  } finally {
    releaseCrossProcessLock(target, stateRoot);
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("reused PID with a different birth identity cannot strand reclaim", () => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "reused-pid-state-"));
  const target = path.join(stateRoot, "target.cpp");
  fs.writeFileSync(target, "content");
  const lockPath = lockFilePath(target, stateRoot);
  fs.writeFileSync(lockPath, "999999:dead-lock-owner\nwrite\nold\n");
  const database = path.join(path.dirname(lockPath), "stale-reclaim.sqlite3");
  const seedScript = [
    "import sqlite3,sys",
    "c=sqlite3.connect(sys.argv[1])",
    "c.execute(\"CREATE TABLE reclaim_guards (lock_path TEXT PRIMARY KEY, owner TEXT NOT NULL, pid INTEGER NOT NULL, process_identity TEXT NOT NULL DEFAULT '', acquired_at TEXT NOT NULL)\")",
    "c.execute(\"INSERT INTO reclaim_guards VALUES (?, ?, ?, ?, ?)\", (sys.argv[2], 'old-process', int(sys.argv[3]), 'reused:old', 'old'))",
    "c.commit()",
  ].join(";");
  const seeded = require("child_process").spawnSync(
    process.platform === "win32" ? "python" : "python3",
    ["-c", seedScript, database, path.basename(lockPath), String(process.pid)],
    { encoding: "utf8", windowsHide: true }
  );
  try {
    assert.strictEqual(seeded.status, 0, seeded.stderr || seeded.stdout);
    const acquired = tryAcquireCrossProcessLock(target, "replacement", stateRoot);
    assert.strictEqual(acquired.ok, true, JSON.stringify(acquired));
    assert.strictEqual(acquired.staleReclaimed, true);
  } finally {
    releaseCrossProcessLock(target, stateRoot);
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

"use strict";

const assert = require("assert");
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

test("POSIX locks keep canonically similar I-dot files independent", { skip: process.platform === "win32" }, () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unicode-locks-"));
  const stateRoot = path.join(root, "state");
  const first = path.join(root, "Source", "\u0130", "Thing.cpp");
  const second = path.join(root, "Source", "I\u0307", "Thing.cpp");
  fs.mkdirSync(path.dirname(first), { recursive: true });
  fs.mkdirSync(path.dirname(second), { recursive: true });
  fs.writeFileSync(first, "first");
  fs.writeFileSync(second, "second");
  try {
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

"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");

const {
  isStaleLock,
  lockFilePath,
  tryAcquireCrossProcessLock,
} = require("../src/write-locks");

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

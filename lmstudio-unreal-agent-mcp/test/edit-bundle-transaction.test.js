"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const {
  applyBundleTransaction,
  rollbackJournal,
  DEFAULT_MAX_FILES_PER_EDIT,
} = require("../src/edit-bundle");
const { ensureStateRootLayout } = require("../src/state-root");

function tmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "bundle-tx-"));
}

test("lock failure before write returns without rollback", async () => {
  process.env.AGENT_STATE_ROOT = tmpDir();
  const dir = tmpDir();
  const target = path.join(dir, "A.cpp");
  fs.writeFileSync(target, "a\n", "utf8");
  const { tryAcquirePathLock } = require("../src/write-locks");
  tryAcquirePathLock(target, "blocker");
  const result = await applyBundleTransaction(
    { patches: [{ path: "A.cpp", oldText: "a", newText: "b", expectedOccurrences: 1 }] },
    async (rel) => ({ ok: true, absolutePath: path.join(dir, rel) })
  );
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.lockFailure, true);
  assert.strictEqual(fs.readFileSync(target, "utf8"), "a\n");
});

test("maxFilesPerEdit=2 enforced", async () => {
  process.env.AGENT_STATE_ROOT = tmpDir();
  await assert.rejects(
    () => applyBundleTransaction(
      {
        files: [
          { path: "a.txt", content: "1" },
          { path: "b.txt", content: "2" },
          { path: "c.txt", content: "3" },
        ],
      },
      async (rel) => ({ ok: true, absolutePath: path.join(tmpDir(), rel) }),
      { maxFilesPerEdit: 2 }
    ),
    /too many files/i
  );
});

test("partial write rolls back completed journal entry", async () => {
  process.env.AGENT_STATE_ROOT = ensureStateRootLayout(tmpDir());
  const dir = tmpDir();
  const first = path.join(dir, "One.cpp");
  const second = path.join(dir, "Two.cpp");
  fs.writeFileSync(first, "one\n", "utf8");
  fs.writeFileSync(second, "two\n", "utf8");
  const result = await applyBundleTransaction(
    {
      patches: [
        { path: "One.cpp", oldText: "one", newText: "ONE", expectedOccurrences: 1 },
        { path: "Two.cpp", oldText: "missing", newText: "TWO", expectedOccurrences: 1 },
      ],
    },
    async (rel) => ({ ok: true, absolutePath: path.join(dir, rel) })
  );
  assert.strictEqual(result.ok, false);
  assert.strictEqual(fs.readFileSync(first, "utf8"), "one\n");
  assert.strictEqual(fs.readFileSync(second, "utf8"), "two\n");
});

test("files[] cannot overwrite existing source file", async () => {
  process.env.AGENT_STATE_ROOT = tmpDir();
  const dir = tmpDir();
  const target = path.join(dir, "Existing.cpp");
  fs.writeFileSync(target, "keep\n", "utf8");
  const result = await applyBundleTransaction(
    { files: [{ path: "Existing.cpp", content: "new" }] },
    async (rel) => ({ ok: true, absolutePath: path.join(dir, rel) })
  );
  assert.strictEqual(result.ok, false);
  assert.strictEqual(fs.readFileSync(target, "utf8"), "keep\n");
});

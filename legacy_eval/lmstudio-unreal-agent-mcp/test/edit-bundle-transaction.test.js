"use strict";

// Historical task-owned transaction behavior; excluded from the product test suite.

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
const { sha256Text } = require("../src/safe-write");

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

test("bundle operation count bounds write-ahead hash growth", async () => {
  process.env.AGENT_STATE_ROOT = tmpDir();
  const dir = tmpDir();
  const target = path.join(dir, "A.cpp");
  fs.writeFileSync(target, "a\n", "utf8");
  const patches = Array.from({ length: 129 }, () => ({
    path: "A.cpp",
    oldText: "a",
    newText: "a",
    expectedOccurrences: 1,
  }));
  await assert.rejects(
    () => applyBundleTransaction(
      { patches },
      async (rel) => ({ ok: true, absolutePath: path.join(dir, rel) }),
    ),
    /too many operations \(max 128\)/i,
  );
  assert.strictEqual(fs.readFileSync(target, "utf8"), "a\n");
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

test("multiple exact patches for one file apply in listed order", async () => {
  process.env.AGENT_STATE_ROOT = ensureStateRootLayout(tmpDir());
  const dir = tmpDir();
  const target = path.join(dir, "One.cpp");
  fs.writeFileSync(target, "alpha\nbeta\ngamma\n", "utf8");

  const result = await applyBundleTransaction(
    {
      patches: [
        { path: "One.cpp", oldText: "alpha", newText: "ALPHA", expectedOccurrences: 1 },
        { path: "One.cpp", oldText: "gamma", newText: "GAMMA", expectedOccurrences: 1 },
      ],
    },
    async (rel) => ({ ok: true, absolutePath: path.join(dir, rel) })
  );

  assert.strictEqual(result.ok, true);
  assert.strictEqual(fs.readFileSync(target, "utf8"), "ALPHA\nbeta\nGAMMA\n");
  assert.strictEqual(result.writtenAbs.length, 1);
  assert.strictEqual(path.basename(result.writtenAbs[0]), "One.cpp");
  assert.strictEqual(fs.readFileSync(result.writtenAbs[0], "utf8"), "ALPHA\nbeta\nGAMMA\n");
});

test("later failed patch on one file rolls all earlier patches back", async () => {
  process.env.AGENT_STATE_ROOT = ensureStateRootLayout(tmpDir());
  const dir = tmpDir();
  const target = path.join(dir, "One.cpp");
  fs.writeFileSync(target, "alpha\nbeta\n", "utf8");

  const result = await applyBundleTransaction(
    {
      patches: [
        { path: "One.cpp", oldText: "alpha", newText: "ALPHA", expectedOccurrences: 1 },
        { path: "One.cpp", oldText: "missing", newText: "MISSING", expectedOccurrences: 1 },
      ],
    },
    async (rel) => ({ ok: true, absolutePath: path.join(dir, rel) })
  );

  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.rolledBack, true);
  assert.deepStrictEqual(result.mutationFailure, {
    errorCode: "OLD_TEXT_NOT_FOUND",
    relativePath: "One.cpp",
    oldText: "missing",
    expectedOccurrences: 1,
  });
  assert.strictEqual(fs.readFileSync(target, "utf8"), "alpha\nbeta\n");
});

test("expectedHash is rechecked under the bundle transaction lock", async () => {
  process.env.AGENT_STATE_ROOT = ensureStateRootLayout(tmpDir());
  const dir = tmpDir();
  const target = path.join(dir, "Concurrent.cpp");
  const originallyRead = "int Value = 1;\n";
  const externallyChanged = "// external change\nint Value = 1;\n";
  fs.writeFileSync(target, externallyChanged, "utf8");

  const result = await applyBundleTransaction(
    {
      patches: [{
        path: "Concurrent.cpp",
        oldText: "int Value = 1;",
        newText: "int Value = 2;",
        expectedOccurrences: 1,
        expectedHash: sha256Text(originallyRead),
      }],
    },
    async (rel) => ({ ok: true, absolutePath: path.join(dir, rel) }),
  );

  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.mutationFailure.errorCode, "READ_HASH_CAS_MISMATCH");
  assert.strictEqual(fs.readFileSync(target, "utf8"), externallyChanged);
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

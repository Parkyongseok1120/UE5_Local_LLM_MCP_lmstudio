"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const {
  stageBundle,
  commitBundleEntries,
  rollbackBundle,
  applyBundleTransaction,
} = require("../src/edit-bundle");

function tmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "bundle-tx-"));
}

test("rollback restores existing file when post-write hash matches", async () => {
  const dir = tmpDir();
  const target = path.join(dir, "Sample.cpp");
  fs.writeFileSync(target, "alpha\n", "utf8");
  const absByRel = { "Sample.cpp": target };
  const staged = { "Sample.cpp": "alpha\n" };
  const { postWriteHashes } = await commitBundleEntries(
    { patches: [{ path: "Sample.cpp", oldText: "alpha", newText: "beta", expectedOccurrences: 1 }] },
    { "Sample.cpp": require("../src/safe-write").sha256Text("alpha\n") },
    absByRel,
    async (rel) => ({ ok: true, absolutePath: path.join(dir, rel) })
  );
  const rollback = await rollbackBundle(staged, absByRel, postWriteHashes);
  assert.strictEqual(rollback.rolledBack, true);
  assert.strictEqual(fs.readFileSync(target, "utf8"), "alpha\n");
});

test("rollback skips existing file when post-write hash mismatches pre-hash guard", async () => {
  const dir = tmpDir();
  const target = path.join(dir, "Sample.cpp");
  fs.writeFileSync(target, "alpha\n", "utf8");
  const absByRel = { "Sample.cpp": target };
  const staged = { "Sample.cpp": "alpha\n" };
  const rollback = await rollbackBundle(staged, absByRel, {
    "Sample.cpp": require("../src/safe-write").sha256Text("beta\n"),
  });
  assert.strictEqual(rollback.rolledBack, false);
  assert.strictEqual(rollback.rollbackIncomplete, true);
  assert.deepStrictEqual(rollback.unrestoredPaths, ["Sample.cpp"]);
});

test("duplicate bundle paths rejected at stage", async () => {
  await assert.rejects(
    () => stageBundle(
      { files: [{ path: "a.txt", content: "x" }, { path: "a.txt", content: "y" }] },
      async () => ({ ok: true, absolutePath: "/tmp/a.txt" })
    ),
    /duplicate paths/i
  );
});

test("applyBundleTransaction reports failure without claiming full rollback on commit error", async () => {
  const dir = tmpDir();
  const target = path.join(dir, "Only.cpp");
  fs.writeFileSync(target, "keep\n", "utf8");
  const result = await applyBundleTransaction(
    {
      patches: [{
        path: "Only.cpp",
        oldText: "missing",
        newText: "beta",
        expectedOccurrences: 1,
      }],
    },
    async (rel) => ({ ok: true, absolutePath: path.join(dir, rel) })
  );
  assert.strictEqual(result.ok, false);
  assert.strictEqual(fs.readFileSync(target, "utf8"), "keep\n");
});

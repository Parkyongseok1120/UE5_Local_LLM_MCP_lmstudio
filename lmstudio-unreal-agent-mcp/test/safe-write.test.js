"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const { atomicWriteTextExclusive } = require("../src/atomic-io");
const { createExclusive, replaceWithCAS, sha256Text } = require("../src/safe-write");

test("atomicWriteTextExclusive rejects existing file", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "exclusive-io-"));
  const target = path.join(dir, "sample.txt");
  atomicWriteTextExclusive(target, "first\n");
  assert.throws(() => atomicWriteTextExclusive(target, "second\n"), (err) => err && err.code === "EEXIST");
});

test("createExclusive creates new file only", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "safe-write-"));
  const target = path.join(dir, "new.txt");
  await createExclusive(target, "hello\n");
  assert.strictEqual(fs.readFileSync(target, "utf8"), "hello\n");
  await assert.rejects(() => createExclusive(target, "again\n"), (err) => err && err.code === "EEXIST");
});

test("replaceWithCAS rejects hash mismatch", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "safe-write-cas-"));
  const target = path.join(dir, "file.txt");
  fs.writeFileSync(target, "alpha\n", "utf8");
  const prior = "alpha\n";
  const result = await replaceWithCAS({
    targetPath: target,
    priorContent: prior,
    oldText: "alpha",
    newText: "beta",
    expectedOccurrences: 1,
    readHash: sha256Text("stale\n"),
  });
  assert.strictEqual(result.ok, false);
  assert.match(result.error || "", /CAS mismatch/i);
});

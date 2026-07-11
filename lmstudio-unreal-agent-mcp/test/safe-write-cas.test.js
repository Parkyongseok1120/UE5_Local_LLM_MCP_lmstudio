"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const { replaceWithCAS, sha256Text } = require("../src/safe-write");

test("replaceWithCAS rejects when disk content changed after read evidence", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "cas-disk-"));
  const target = path.join(dir, "file.txt");
  fs.writeFileSync(target, "alpha\n", "utf8");
  const readHash = sha256Text("alpha\n");
  fs.writeFileSync(target, "external edit\n", "utf8");
  const result = await replaceWithCAS({
    targetPath: target,
    priorContent: "alpha\n",
    oldText: "alpha",
    newText: "beta",
    expectedOccurrences: 1,
    readHash,
  });
  assert.strictEqual(result.ok, false);
  assert.match(result.error || "", /CAS mismatch/i);
  assert.strictEqual(fs.readFileSync(target, "utf8"), "external edit\n");
});

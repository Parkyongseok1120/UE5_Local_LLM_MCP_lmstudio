"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const { rollbackJournal } = require("../src/edit-bundle");
const { createJournal, upsertEntry } = require("../src/transaction-journal");
const { atomicWriteText } = require("../src/atomic-io");
const { sha256Text } = require("../src/safe-write");

test("rollback flags external deletion of existing file", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "bundle-rb-"));
  const abs = path.join(root, "Source", "A.cpp");
  fs.mkdirSync(path.dirname(abs), { recursive: true });
  const original = "original content\n";
  atomicWriteText(abs, original);
  const backupDir = path.join(root, ".agent", "backups");
  fs.mkdirSync(backupDir, { recursive: true });
  const backupPath = path.join(backupDir, "A.cpp.bak");
  atomicWriteText(backupPath, original);

  const journal = createJournal({ operation: "apply_edit_bundle" });
  upsertEntry(journal, {
    relativePath: "Source/A.cpp",
    canonicalAbsolutePath: abs,
    operation: "patch",
    existedBefore: true,
    preHash: sha256Text(original),
    preContentBackupPath: backupPath,
    postHash: sha256Text("mutated content\n"),
    writeCompleted: true,
  });
  journal.status = "committed";

  fs.unlinkSync(abs);
  const result = await rollbackJournal(journal);
  assert.strictEqual(result.externalChangeDetected.includes("Source/A.cpp"), true);
  assert.strictEqual(fs.existsSync(abs), false);
});

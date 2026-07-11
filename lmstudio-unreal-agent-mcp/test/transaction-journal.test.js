"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const {
  createJournal,
  upsertEntry,
  recoverIncompleteJournals,
} = require("../src/transaction-journal");

function tempStateRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "tx-journal-"));
}

test("createJournal persists planned journal under state root", () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const journal = createJournal({ operation: "test_bundle" });
  assert.ok(journal.transactionId);
  assert.strictEqual(journal.status, "planned");
  const file = path.join(stateRoot, "transactions", `${journal.transactionId}.json`);
  assert.ok(fs.existsSync(file));
  delete process.env.AGENT_STATE_ROOT;
});

test("upsertEntry records writeCompleted and postHash", () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const journal = createJournal();
  upsertEntry(journal, {
    relativePath: "Source/Demo/A.cpp",
    preHash: "abc",
    writeCompleted: true,
    postHash: "def",
  });
  const entry = journal.entries.find((item) => item.relativePath === "Source/Demo/A.cpp");
  assert.strictEqual(entry.writeCompleted, true);
  assert.strictEqual(entry.postHash, "def");
  delete process.env.AGENT_STATE_ROOT;
});

test("recoverIncompleteJournals flags recoveryRequired for external change", async () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const journal = createJournal();
  upsertEntry(journal, {
    relativePath: "missing.cpp",
    canonicalAbsolutePath: path.join(stateRoot, "missing.cpp"),
    preHash: null,
    writeCompleted: true,
    postHash: "deadbeef",
  });
  journal.status = "committing";
  const { atomicWriteText } = require("../src/atomic-io");
  atomicWriteText(
    path.join(stateRoot, "transactions", `${journal.transactionId}.json`),
    JSON.stringify(journal, null, 2)
  );
  const report = await recoverIncompleteJournals(stateRoot);
  assert.ok(report.recoveryRequired.length > 0);
  delete process.env.AGENT_STATE_ROOT;
});

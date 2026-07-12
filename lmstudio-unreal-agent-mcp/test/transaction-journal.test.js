"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const {
  createJournal,
  upsertEntry,
  saveJournal,
  recoverIncompleteJournals,
} = require("../src/transaction-journal");

function tempStateRoot() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "tx-journal-"));
  for (const sub of ["locks", "transactions", "tasks", "jobs", "backups"]) {
    fs.mkdirSync(path.join(root, sub), { recursive: true });
  }
  return root;
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

test("recoverIncompleteJournals skips recovered journals on rescan", async () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const { sha256Text } = require("../src/safe-write");
  const { atomicWriteText } = require("../src/atomic-io");
  const journal = createJournal();
  const content = "done";
  upsertEntry(journal, {
    relativePath: "done.cpp",
    canonicalAbsolutePath: path.join(stateRoot, "done.cpp"),
    preHash: null,
    writeCompleted: true,
    postHash: sha256Text(content),
  });
  journal.status = "committing";
  atomicWriteText(path.join(stateRoot, "done.cpp"), content);
  atomicWriteText(
    path.join(stateRoot, "transactions", `${journal.transactionId}.json`),
    JSON.stringify(journal, null, 2)
  );
  const first = await recoverIncompleteJournals(stateRoot);
  assert.ok(first.recovered.includes("done.cpp"));
  const second = await recoverIncompleteJournals(stateRoot);
  assert.strictEqual(second.scanned, 0);
  delete process.env.AGENT_STATE_ROOT;
});

test("recoverIncompleteJournals isolates corrupt json", async () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  fs.writeFileSync(path.join(stateRoot, "transactions", "bad.json"), "{not-json");
  const journal = createJournal();
  journal.status = "committing";
  saveJournal(journal, stateRoot);
  const report = await recoverIncompleteJournals(stateRoot);
  assert.strictEqual(report.skippedCorrupt.length, 1);
  delete process.env.AGENT_STATE_ROOT;
});

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
  prepareSingleFileJournal,
  markJournalAwaitingBuild,
  listPendingJournals,
  finalizePendingJournals,
  projectPathIdentity,
  beginMutationJournal,
  commitMutationJournal,
  pendingBuildJournals,
  finalizePendingBuildJournals,
} = require("../src/transaction-journal");
const { rollbackJournal } = require("../src/edit-bundle");

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
  atomicWriteText(path.join(stateRoot, "missing.cpp"), "external-change");
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

test("successful mutation remains pending until build proof finalizes it", async () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const project = path.join(stateRoot, "Demo.uproject");
  const target = path.join(stateRoot, "Source", "Demo", "Thing.cpp");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, "before\n", "utf8");
  const journal = prepareSingleFileJournal({
    operation: "replace_in_file",
    absolutePath: target,
    relativePath: "Source/Demo/Thing.cpp",
    priorContent: "before\n",
    postContent: "after\n",
    taskSessionId: "task-one",
    projectPath: project,
  });
  fs.writeFileSync(target, "after\n", "utf8");
  markJournalAwaitingBuild(journal);

  assert.strictEqual(
    listPendingJournals({ taskSessionId: "task-one", projectPath: project }).length,
    1,
  );
  const finalized = await finalizePendingJournals({ taskSessionId: "task-one", projectPath: project });
  assert.deepStrictEqual(finalized.finalized, [journal.transactionId]);
  assert.strictEqual(listPendingJournals({ taskSessionId: "task-one", projectPath: project }).length, 0);
  delete process.env.AGENT_STATE_ROOT;
});

test("single-file journal honors an explicit state root without leaking a duplicate", () => {
  const stateRoot = tempStateRoot();
  const defaultRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = defaultRoot;
  const target = path.join(stateRoot, "Source", "Demo", "Thing.cpp");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, "before\n", "utf8");

  const journal = prepareSingleFileJournal({
    operation: "replace_in_file",
    absolutePath: target,
    relativePath: "Source/Demo/Thing.cpp",
    priorContent: "before\n",
    postContent: "after\n",
  }, stateRoot);

  assert.ok(fs.existsSync(path.join(
    stateRoot,
    "transactions",
    `${journal.transactionId}.json`,
  )));
  assert.ok(!fs.existsSync(path.join(
    defaultRoot,
    "transactions",
    `${journal.transactionId}.json`,
  )));
  delete process.env.AGENT_STATE_ROOT;
});

test("restart recovery restores an agent-deleted file from its backup", async () => {
  const stateRoot = tempStateRoot();
  const target = path.join(stateRoot, "Source", "Demo", "Deleted.cpp");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, "before deletion\n", "utf8");
  const journal = prepareSingleFileJournal({
    operation: "delete_file",
    absolutePath: target,
    relativePath: "Source/Demo/Deleted.cpp",
    priorContent: "before deletion\n",
    postContent: "",
    deletedAfter: true,
  }, stateRoot);
  fs.unlinkSync(target);
  markJournalAwaitingBuild(journal, {}, stateRoot);

  const report = await recoverIncompleteJournals(stateRoot);

  assert.ok(report.recovered.includes("Source/Demo/Deleted.cpp"));
  assert.strictEqual(fs.readFileSync(target, "utf8"), "before deletion\n");
});

test("project path identity folds case only for Windows", () => {
  const mixed = path.join("ProjectRoot", "Demo.uproject");
  assert.strictEqual(
    projectPathIdentity(mixed, "win32"),
    projectPathIdentity(mixed.toUpperCase(), "win32"),
  );
  assert.notStrictEqual(
    projectPathIdentity(mixed, "linux"),
    projectPathIdentity(mixed.toUpperCase(), "linux"),
  );
  assert.notStrictEqual(
    projectPathIdentity(mixed, "darwin"),
    projectPathIdentity(mixed.toUpperCase(), "darwin"),
  );
});

test("crash after filesystem write but before journal commit restores the pre-image", async () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const target = path.join(stateRoot, "Demo.cpp");
  fs.writeFileSync(target, "before", "utf8");
  beginMutationJournal({
    operation: "replace_in_file",
    projectRoot: stateRoot,
    relativePath: "Demo.cpp",
    canonicalAbsolutePath: target,
    existedBefore: true,
    preContent: "before",
    intendedPostContent: "after",
  });
  fs.writeFileSync(target, "after", "utf8");

  const report = await recoverIncompleteJournals(stateRoot);
  assert.ok(report.recovered.includes("Demo.cpp"));
  assert.strictEqual(fs.readFileSync(target, "utf8"), "before");
  delete process.env.AGENT_STATE_ROOT;
});

test("prepared journal with an untouched pre-image is archived without false recovery conflict", async () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const target = path.join(stateRoot, "Untouched.cpp");
  fs.writeFileSync(target, "before", "utf8");
  beginMutationJournal({
    operation: "replace_in_file",
    projectRoot: stateRoot,
    relativePath: "Untouched.cpp",
    canonicalAbsolutePath: target,
    existedBefore: true,
    preContent: "before",
    intendedPostContent: "after",
  });

  const report = await recoverIncompleteJournals(stateRoot);
  assert.deepStrictEqual(report.recoveryRequired, []);
  assert.ok(report.recovered.includes("Untouched.cpp"));
  assert.strictEqual(fs.readFileSync(target, "utf8"), "before");
  delete process.env.AGENT_STATE_ROOT;
});

test("RC2 replay H: terminal build recovery rolls a matching mutation post-image back", async () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const target = path.join(stateRoot, "Source", "Demo.cpp");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, "before", "utf8");
  const journal = beginMutationJournal({
    operation: "replace_in_file",
    projectRoot: stateRoot,
    taskSessionId: "task_demo",
    relativePath: "Source/Demo.cpp",
    canonicalAbsolutePath: target,
    existedBefore: true,
    preContent: "before",
    intendedPostContent: "after",
  });
  fs.writeFileSync(target, "after", "utf8");
  commitMutationJournal(journal, "after", { mutationGeneration: 7 });

  assert.strictEqual(pendingBuildJournals({ projectRoot: stateRoot }).length, 1);
  const rollback = await rollbackJournal(journal);
  assert.strictEqual(rollback.rolledBack, true);
  assert.strictEqual(fs.readFileSync(target, "utf8"), "before");
  delete process.env.AGENT_STATE_ROOT;
});

test("pending rollback never overwrites an external post-write change", async () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const target = path.join(stateRoot, "Source", "Demo.cpp");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, "before", "utf8");
  const journal = beginMutationJournal({
    operation: "replace_in_file",
    projectRoot: stateRoot,
    relativePath: "Source/Demo.cpp",
    canonicalAbsolutePath: target,
    existedBefore: true,
    preContent: "before",
    intendedPostContent: "after",
  });
  fs.writeFileSync(target, "after", "utf8");
  commitMutationJournal(journal, "after", { mutationGeneration: 8 });
  fs.writeFileSync(target, "external", "utf8");

  const rollback = await rollbackJournal(journal);
  assert.strictEqual(rollback.rollbackIncomplete, true);
  assert.deepStrictEqual(rollback.externalChangeDetected, ["Source/Demo.cpp"]);
  assert.strictEqual(fs.readFileSync(target, "utf8"), "external");
  delete process.env.AGENT_STATE_ROOT;
});

test("multiple failed generations roll back newest-first to the original pre-image", async () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const target = path.join(stateRoot, "Source", "Chain.cpp");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, "A", "utf8");
  const first = beginMutationJournal({
    operation: "replace_in_file",
    projectRoot: stateRoot,
    relativePath: "Source/Chain.cpp",
    canonicalAbsolutePath: target,
    existedBefore: true,
    preContent: "A",
    intendedPostContent: "B",
  });
  fs.writeFileSync(target, "B", "utf8");
  commitMutationJournal(first, "B", { mutationGeneration: 1 });
  first.status = "build_failed";
  saveJournal(first);

  const second = beginMutationJournal({
    operation: "replace_in_file",
    projectRoot: stateRoot,
    relativePath: "Source/Chain.cpp",
    canonicalAbsolutePath: target,
    existedBefore: true,
    preContent: "B",
    intendedPostContent: "C",
  });
  fs.writeFileSync(target, "C", "utf8");
  commitMutationJournal(second, "C", { mutationGeneration: 2 });
  second.status = "build_failed";
  saveJournal(second);

  const failed = pendingBuildJournals({
    projectRoot: stateRoot,
    mutationGeneration: 2,
    statuses: ["build_failed"],
  }).reverse();
  assert.deepStrictEqual(failed.map((item) => item.transactionId), [
    second.transactionId,
    first.transactionId,
  ]);
  for (const journal of failed) {
    assert.strictEqual((await rollbackJournal(journal)).rolledBack, true);
  }
  assert.strictEqual(fs.readFileSync(target, "utf8"), "A");
  delete process.env.AGENT_STATE_ROOT;
});

test("deleted source is restored when its pending build is rolled back", async () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const target = path.join(stateRoot, "Source", "Deleted.cpp");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, "before", "utf8");
  const journal = beginMutationJournal({
    operation: "delete_file",
    projectRoot: stateRoot,
    relativePath: "Source/Deleted.cpp",
    canonicalAbsolutePath: target,
    existedBefore: true,
    preContent: "before",
    deleteTarget: true,
  });
  fs.unlinkSync(target);
  commitMutationJournal(journal, null, { mutationGeneration: 9, deletedAfter: true });

  const rollback = await rollbackJournal(journal);
  assert.strictEqual(rollback.rolledBack, true);
  assert.strictEqual(fs.readFileSync(target, "utf8"), "before");
  delete process.env.AGENT_STATE_ROOT;
});

test("successful build finalization archives only matching pending journals", async () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const target = path.join(stateRoot, "Source", "Final.cpp");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const journal = beginMutationJournal({
    operation: "write_file",
    projectRoot: stateRoot,
    taskSessionId: "task_final",
    relativePath: "Source/Final.cpp",
    canonicalAbsolutePath: target,
    existedBefore: false,
    intendedPostContent: "final",
  });
  fs.writeFileSync(target, "final", "utf8");
  commitMutationJournal(journal, "final", {
    mutationGeneration: 10,
    taskSessionId: "task_final",
  });

  const finalized = await finalizePendingBuildJournals({
    projectRoot: stateRoot,
    taskSessionId: "task_final",
    mutationGeneration: 10,
  });
  assert.deepStrictEqual(finalized, [journal.transactionId]);
  assert.strictEqual(pendingBuildJournals({ projectRoot: stateRoot }).length, 0);
  assert.ok(fs.existsSync(path.join(stateRoot, "transactions", "archive", `${journal.transactionId}.json`)));
  delete process.env.AGENT_STATE_ROOT;
});

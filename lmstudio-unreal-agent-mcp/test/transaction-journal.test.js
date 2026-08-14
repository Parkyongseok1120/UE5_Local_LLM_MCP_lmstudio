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
  pathIdentity,
  projectPathIdentity,
  beginMutationJournal,
  commitMutationJournal,
  armAtomicMutationJournal,
  stageMutationCompensation,
  markMutationStateRecorded,
  completeMutationJournalCheckpoint,
  armMutationRollback,
  pendingBuildJournals,
  finalizePendingBuildJournals,
} = require("../src/transaction-journal");
const { applyBundleTransaction, rollbackJournal } = require("../src/edit-bundle");
const {
  recordMutation,
  recordMutationBatch,
  readMutationState,
  reconcileMutationPathsFromDisk,
} = require("../src/mutation-generation");
const { sha256Text } = require("../src/safe-write");
const {
  checkpointMutationViaPython,
  checkpointRollbackViaPython,
  canonicalProjectIdentity,
  taskAuthorizationForState,
} = require("../src/task-auth");

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

test("canonical project identity preserves POSIX case and Unicode spelling for missing paths", () => {
  const missingBase = path.join(os.tmpdir(), "missing-project-identity");
  const mixed = path.join(missingBase, "CaseSensitive", "Project");
  assert.strictEqual(fs.existsSync(mixed), false);
  assert.strictEqual(pathIdentity(mixed, "win32"), pathIdentity(mixed.toUpperCase(), "win32"));
  assert.notStrictEqual(pathIdentity(mixed, "linux"), pathIdentity(mixed.toUpperCase(), "linux"));
  assert.notStrictEqual(pathIdentity(mixed, "darwin"), pathIdentity(mixed.toUpperCase(), "darwin"));
  assert.notStrictEqual(
    pathIdentity(path.join(missingBase, "Cafe\u0301"), "linux"),
    pathIdentity(path.join(missingBase, "Caf\u00e9"), "linux"),
  );
  assert.notStrictEqual(
    pathIdentity(path.join(missingBase, "Cafe\u0301"), "darwin"),
    pathIdentity(path.join(missingBase, "Caf\u00e9"), "darwin"),
  );
  assert.notStrictEqual(
    pathIdentity(path.join(missingBase, "Cafe\u0301"), "win32"),
    pathIdentity(path.join(missingBase, "Caf\u00e9"), "win32"),
  );
  assert.notStrictEqual(
    pathIdentity(path.join(missingBase, "\u0130"), "win32"),
    pathIdentity(path.join(missingBase, "i\u0307"), "win32"),
  );
});

test("dual-existing POSIX Unicode projects keep pending build journals isolated", (t) => {
  if (process.platform !== "linux") {
    t.skip("dual NFC/NFD directory entries are verified on Linux filesystems");
    return;
  }
  const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), "tx-unicode-projects-"));
  const nfcProject = path.join(fixtureRoot, "Caf\u00e9");
  const nfdProject = path.join(fixtureRoot, "Cafe\u0301");
  const stateRoot = tempStateRoot();
  const previous = process.env.AGENT_STATE_ROOT;
  fs.mkdirSync(nfcProject, { recursive: true });
  fs.mkdirSync(nfdProject, { recursive: true });
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    assert.notStrictEqual(fs.realpathSync(nfcProject), fs.realpathSync(nfdProject));
    assert.notStrictEqual(pathIdentity(nfcProject), pathIdentity(nfdProject));
    const nfcJournal = beginMutationJournal({
      operation: "write_file",
      projectRoot: nfcProject,
      taskSessionId: "task_unicode_nfc",
      relativePath: "Source/Demo.cpp",
      canonicalAbsolutePath: path.join(nfcProject, "Source", "Demo.cpp"),
      existedBefore: false,
      intendedPostContent: "nfc",
    });
    const nfdJournal = beginMutationJournal({
      operation: "write_file",
      projectRoot: nfdProject,
      taskSessionId: "task_unicode_nfd",
      relativePath: "Source/Demo.cpp",
      canonicalAbsolutePath: path.join(nfdProject, "Source", "Demo.cpp"),
      existedBefore: false,
      intendedPostContent: "nfd",
    });

    assert.deepStrictEqual(
      pendingBuildJournals({ projectRoot: nfcProject }).map((item) => item.transactionId),
      [nfcJournal.transactionId],
    );
    assert.deepStrictEqual(
      pendingBuildJournals({ projectRoot: nfdProject }).map((item) => item.transactionId),
      [nfdJournal.transactionId],
    );
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(fixtureRoot, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("existing directory aliases select the same pending project journal", (t) => {
  const fixtureRoot = fs.mkdtempSync(path.join(os.tmpdir(), "tx-project-alias-"));
  const projectRoot = path.join(fixtureRoot, "CanonicalProject");
  const aliasRoot = path.join(fixtureRoot, "ProjectAlias");
  const stateRoot = tempStateRoot();
  const previous = process.env.AGENT_STATE_ROOT;
  fs.mkdirSync(projectRoot, { recursive: true });
  try {
    try {
      fs.symlinkSync(
        projectRoot,
        aliasRoot,
        process.platform === "win32" ? "junction" : "dir",
      );
    } catch (error) {
      if (!["EPERM", "EACCES", "ENOTSUP", "UNKNOWN"].includes(String(error.code || ""))) {
        throw error;
      }
      t.skip(`directory aliases are unavailable on this host: ${error.code || error.message}`);
      return;
    }

    process.env.AGENT_STATE_ROOT = stateRoot;
    const journal = createJournal({ operation: "canonical_project_alias" }, stateRoot);
    journal.status = "awaiting_build";
    // Simulate a journal persisted before canonical realpath identities were
    // introduced: startup selection must canonicalize the stored alias too.
    journal.projectRoot = aliasRoot;
    journal.taskSessionId = "";
    saveJournal(journal, stateRoot);

    assert.strictEqual(pathIdentity(aliasRoot), pathIdentity(projectRoot));
    assert.deepStrictEqual(
      pendingBuildJournals({ projectRoot }).map((item) => item.transactionId),
      [journal.transactionId],
    );
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(fixtureRoot, { recursive: true, force: true });
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("existing Windows 8.3 aliases select the same pending project journal", (t) => {
  if (process.platform !== "win32") {
    t.skip("Windows 8.3 aliases are host-specific");
    return;
  }
  const longPath = String(process.env.ProgramFiles || "C:\\Program Files");
  const shortPath = path.join(path.parse(longPath).root, "PROGRA~1");
  if (!fs.existsSync(longPath) || !fs.existsSync(shortPath)) {
    t.skip("8.3 Program Files alias is unavailable on this volume");
    return;
  }
  if (fs.realpathSync.native(longPath) !== fs.realpathSync.native(shortPath)) {
    t.skip("the available short path is not an alias of Program Files");
    return;
  }

  assert.strictEqual(pathIdentity(shortPath), pathIdentity(longPath));
  assert.strictEqual(
    canonicalProjectIdentity(shortPath),
    canonicalProjectIdentity(longPath),
  );

  const stateRoot = tempStateRoot();
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    const journal = createJournal({ operation: "windows_8dot3_identity" }, stateRoot);
    journal.status = "awaiting_build";
    // Preserve the pre-fix lexical 8.3 identity in the fixture so the query
    // proves backward-compatible journal selection, not only new writes.
    journal.projectRoot = shortPath.replace(/\\/g, "/").toLowerCase();
    journal.taskSessionId = "";
    saveJournal(journal, stateRoot);
    assert.deepStrictEqual(
      pendingBuildJournals({ projectRoot: longPath }).map((item) => item.transactionId),
      [journal.transactionId],
    );
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
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
  markMutationStateRecorded(first, { mutationGeneration: 1, mutationStateRequired: false });
  await completeMutationJournalCheckpoint(first, { skipped: true });
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
  markMutationStateRecorded(second, { mutationGeneration: 2, mutationStateRequired: false });
  await completeMutationJournalCheckpoint(second, { skipped: true });
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
  markMutationStateRecorded(journal, {
    mutationGeneration: 10,
    mutationStateRequired: false,
  });
  await completeMutationJournalCheckpoint(journal, { skipped: true });

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

async function atomicCrashFixture({ withMutationState = false, withCheckpoint = false } = {}) {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const projectRoot = path.join(stateRoot, "Project");
  const relPath = "Source/Demo/Atomic.cpp";
  const target = path.join(projectRoot, ...relPath.split("/"));
  const taskSessionId = "task_atomic_recovery";
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, "before", "utf8");
  const journal = beginMutationJournal({
    operation: "replace_in_file",
    projectRoot,
    taskSessionId,
    relativePath: relPath,
    canonicalAbsolutePath: target,
    existedBefore: true,
    preContent: "before",
    intendedPostContent: "after",
    checkpointRequired: true,
  });
  fs.writeFileSync(target, "after", "utf8");
  commitMutationJournal(journal, "after", { taskSessionId, projectRoot });
  let mutation = null;
  if (withMutationState) {
    mutation = await recordMutation(projectRoot, relPath, "after", {
      prepareCompensation: async (receipt, pending) => {
        stageMutationCompensation(journal, receipt, {
          projectRoot,
          taskSessionId,
          ...pending,
        });
      },
    });
  }
  if (withCheckpoint) {
    const taskDir = path.join(stateRoot, "tasks", taskSessionId);
    fs.mkdirSync(taskDir, { recursive: true });
    fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
      mutationGeneration: mutation.mutationGeneration,
      continuity: {
        checkpoint: {
          status: "recorded",
          mutationGeneration: mutation.mutationGeneration,
          modifiedFiles: [relPath],
        },
      },
    }), "utf8");
  }
  return { stateRoot, projectRoot, relPath, target, journal, mutation };
}

async function appendAtomicCrashMutation({
  projectRoot,
  taskSessionId = "",
  relPath,
  postContent,
  checkpointRequired = false,
}) {
  const target = path.join(projectRoot, ...relPath.split("/"));
  const preContent = fs.readFileSync(target, "utf8");
  const journal = beginMutationJournal({
    operation: "replace_in_file",
    projectRoot,
    taskSessionId,
    relativePath: relPath,
    canonicalAbsolutePath: target,
    existedBefore: true,
    preContent,
    intendedPostContent: postContent,
    checkpointRequired,
  });
  fs.writeFileSync(target, postContent, "utf8");
  commitMutationJournal(journal, postContent, { projectRoot, taskSessionId });
  const mutation = await recordMutationBatch(
    projectRoot,
    [{ relPath, content: postContent }],
    {
      prepareCompensation: async (receipt, pending) => {
        stageMutationCompensation(journal, receipt, {
          projectRoot,
          taskSessionId,
          ...pending,
        });
      },
    },
  );
  markMutationStateRecorded(journal, {
    mutationGeneration: mutation.mutationGeneration,
    mutationRevision: mutation.mutationRevision,
  });
  return { journal, mutation, target, relPath, preContent, postContent };
}

function writeDescendantTaskCheckpoint(
  stateRoot,
  projectRoot,
  taskSessionId,
  mutationGeneration,
  modifiedFiles,
) {
  const taskDir = path.join(stateRoot, "tasks", taskSessionId);
  fs.mkdirSync(taskDir, { recursive: true });
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
    taskSessionId,
    status: "running",
    projectFile: projectRoot,
    mutationGeneration,
    continuity: {
      checkpoint: {
        status: "recorded",
        mutationGeneration,
        modifiedFiles,
      },
    },
  }), "utf8");
}

test("startup commits sequential taskless journals on unrelated paths newest-first", async () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const projectRoot = path.join(stateRoot, "Project");
  const firstPath = "Source/Demo/First.cpp";
  const secondPath = "Source/Demo/Second.cpp";
  for (const [relPath, content] of [[firstPath, "first-0"], [secondPath, "second-0"]]) {
    const target = path.join(projectRoot, ...relPath.split("/"));
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, content, "utf8");
  }
  const first = await appendAtomicCrashMutation({
    projectRoot,
    relPath: firstPath,
    postContent: "first-1",
  });
  const second = await appendAtomicCrashMutation({
    projectRoot,
    relPath: secondPath,
    postContent: "second-1",
  });

  const report = await recoverIncompleteJournals(stateRoot);
  assert.deepStrictEqual(report.recoveryRequired, []);
  assert.deepStrictEqual(report.committed, [
    second.journal.transactionId,
    first.journal.transactionId,
  ]);
  assert.strictEqual(fs.readFileSync(first.target, "utf8"), "first-1");
  assert.strictEqual(fs.readFileSync(second.target, "utf8"), "second-1");
  const mutation = await readMutationState(projectRoot);
  assert.strictEqual(mutation.paths[firstPath], sha256Text("first-1"));
  assert.strictEqual(mutation.paths[secondPath], sha256Text("second-1"));
  delete process.env.AGENT_STATE_ROOT;
});

test("startup marks an older same-path journal superseded only through receipt lineage", async () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const projectRoot = path.join(stateRoot, "Project");
  const relPath = "Source/Demo/Same.cpp";
  const target = path.join(projectRoot, ...relPath.split("/"));
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, "version-0", "utf8");
  const first = await appendAtomicCrashMutation({
    projectRoot,
    relPath,
    postContent: "version-1",
  });
  const second = await appendAtomicCrashMutation({
    projectRoot,
    relPath,
    postContent: "version-2",
  });

  const report = await recoverIncompleteJournals(stateRoot);
  assert.deepStrictEqual(report.recoveryRequired, []);
  assert.deepStrictEqual(report.committed, [second.journal.transactionId]);
  assert.deepStrictEqual(report.superseded, [first.journal.transactionId]);
  assert.strictEqual(fs.readFileSync(target, "utf8"), "version-2");
  const archived = JSON.parse(fs.readFileSync(path.join(
    stateRoot,
    "transactions",
    "archive",
    `${first.journal.transactionId}.json`,
  ), "utf8"));
  assert.strictEqual(archived.status, "superseded");
  assert.strictEqual(archived.supersededBy, second.journal.transactionId);
  delete process.env.AGENT_STATE_ROOT;
});

for (const samePath of [false, true]) {
  test(`task-bound ${samePath ? "same-path" : "unrelated"} descendants commit with one complete checkpoint`, async () => {
    const stateRoot = tempStateRoot();
    process.env.AGENT_STATE_ROOT = stateRoot;
    const projectRoot = path.join(stateRoot, "Project");
    const taskSessionId = `task_descendant_${samePath ? "same" : "unrelated"}`;
    const firstPath = "Source/Demo/TaskFirst.cpp";
    const secondPath = samePath ? firstPath : "Source/Demo/TaskSecond.cpp";
    const firstTarget = path.join(projectRoot, ...firstPath.split("/"));
    fs.mkdirSync(path.dirname(firstTarget), { recursive: true });
    fs.writeFileSync(firstTarget, "task-0", "utf8");
    if (!samePath) {
      const secondTarget = path.join(projectRoot, ...secondPath.split("/"));
      fs.writeFileSync(secondTarget, "second-0", "utf8");
    }
    const first = await appendAtomicCrashMutation({
      projectRoot,
      taskSessionId,
      relPath: firstPath,
      postContent: "task-1",
      checkpointRequired: true,
    });
    const second = await appendAtomicCrashMutation({
      projectRoot,
      taskSessionId,
      relPath: secondPath,
      postContent: samePath ? "task-2" : "second-1",
      checkpointRequired: true,
    });
    writeDescendantTaskCheckpoint(
      stateRoot,
      projectRoot,
      taskSessionId,
      second.mutation.mutationGeneration,
      [...new Set([firstPath, secondPath])],
    );

    const report = await recoverIncompleteJournals(stateRoot);
    assert.deepStrictEqual(report.recoveryRequired, []);
    assert.strictEqual(report.committed[0], second.journal.transactionId);
    if (samePath) {
      assert.deepStrictEqual(report.superseded, [first.journal.transactionId]);
      assert.strictEqual(fs.readFileSync(first.target, "utf8"), "task-2");
    } else {
      assert.deepStrictEqual(report.committed, [
        second.journal.transactionId,
        first.journal.transactionId,
      ]);
      assert.strictEqual(fs.readFileSync(first.target, "utf8"), "task-1");
      assert.strictEqual(fs.readFileSync(second.target, "utf8"), "second-1");
    }
    delete process.env.AGENT_STATE_ROOT;
  });

  test(`task-bound ${samePath ? "same-path" : "unrelated"} journals without a checkpoint roll back newest-first`, async () => {
    const stateRoot = tempStateRoot();
    process.env.AGENT_STATE_ROOT = stateRoot;
    const projectRoot = path.join(stateRoot, "Project");
    const taskSessionId = `task_missing_${samePath ? "same" : "unrelated"}`;
    const firstPath = "Source/Demo/MissingFirst.cpp";
    const secondPath = samePath ? firstPath : "Source/Demo/MissingSecond.cpp";
    const firstTarget = path.join(projectRoot, ...firstPath.split("/"));
    fs.mkdirSync(path.dirname(firstTarget), { recursive: true });
    fs.writeFileSync(firstTarget, "first-0", "utf8");
    if (!samePath) {
      fs.writeFileSync(path.join(projectRoot, ...secondPath.split("/")), "second-0", "utf8");
    }
    await appendAtomicCrashMutation({
      projectRoot,
      taskSessionId,
      relPath: firstPath,
      postContent: "first-1",
      checkpointRequired: true,
    });
    await appendAtomicCrashMutation({
      projectRoot,
      taskSessionId,
      relPath: secondPath,
      postContent: samePath ? "first-2" : "second-1",
      checkpointRequired: true,
    });

    const report = await recoverIncompleteJournals(stateRoot);
    assert.deepStrictEqual(report.recoveryRequired, []);
    assert.deepStrictEqual(report.committed, []);
    assert.deepStrictEqual(report.superseded, []);
    assert.strictEqual(fs.readFileSync(firstTarget, "utf8"), "first-0");
    if (!samePath) {
      assert.strictEqual(
        fs.readFileSync(path.join(projectRoot, ...secondPath.split("/")), "utf8"),
        "second-0",
      );
    }
    const mutation = await readMutationState(projectRoot);
    assert.strictEqual(mutation.mutationGeneration, 0);
    assert.deepStrictEqual(mutation.paths, {});
    delete process.env.AGENT_STATE_ROOT;
  });
}

test("external same-path mutation cannot impersonate a journal supersession", async () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const projectRoot = path.join(stateRoot, "Project");
  const relPath = "Source/Demo/External.cpp";
  const target = path.join(projectRoot, ...relPath.split("/"));
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, "base", "utf8");
  await appendAtomicCrashMutation({ projectRoot, relPath, postContent: "agent-1" });
  await appendAtomicCrashMutation({ projectRoot, relPath, postContent: "agent-2" });
  fs.writeFileSync(target, "external", "utf8");
  await recordMutation(projectRoot, relPath, "external");

  const report = await recoverIncompleteJournals(stateRoot);
  assert.strictEqual(report.recoveryRequired.length, 2);
  assert.deepStrictEqual(report.committed, []);
  assert.deepStrictEqual(report.superseded, []);
  assert.strictEqual(fs.readFileSync(target, "utf8"), "external");
  delete process.env.AGENT_STATE_ROOT;
});

test("external mutation of an older unrelated path remains recovery-required", async () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const projectRoot = path.join(stateRoot, "Project");
  const firstPath = "Source/Demo/ExternalFirst.cpp";
  const secondPath = "Source/Demo/ExternalSecond.cpp";
  for (const [relPath, content] of [[firstPath, "first-0"], [secondPath, "second-0"]]) {
    const target = path.join(projectRoot, ...relPath.split("/"));
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, content, "utf8");
  }
  const first = await appendAtomicCrashMutation({
    projectRoot,
    relPath: firstPath,
    postContent: "first-1",
  });
  const second = await appendAtomicCrashMutation({
    projectRoot,
    relPath: secondPath,
    postContent: "second-1",
  });
  fs.writeFileSync(first.target, "external", "utf8");
  await recordMutation(projectRoot, firstPath, "external");

  const report = await recoverIncompleteJournals(stateRoot);
  assert.deepStrictEqual(report.committed, [second.journal.transactionId]);
  assert.deepStrictEqual(report.superseded, []);
  assert.strictEqual(report.recoveryRequired.length, 1);
  assert.strictEqual(report.recoveryRequired[0].transactionId, first.journal.transactionId);
  assert.strictEqual(fs.readFileSync(first.target, "utf8"), "external");
  assert.strictEqual(fs.readFileSync(second.target, "utf8"), "second-1");
  delete process.env.AGENT_STATE_ROOT;
});

test("fault injection: restart converges a disk-only mutation to the pre-image", async () => {
  const fixture = await atomicCrashFixture();
  // A prior checkpoint for generation zero must not be mistaken for evidence
  // that this disk-only write reached mutation state.
  const taskDir = path.join(fixture.stateRoot, "tasks", "task_atomic_recovery");
  fs.mkdirSync(taskDir, { recursive: true });
  fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
    continuity: {
      checkpoint: {
        status: "recorded",
        mutationGeneration: 0,
        modifiedFiles: [fixture.relPath],
      },
    },
  }), "utf8");
  const report = await recoverIncompleteJournals(fixture.stateRoot);
  assert.deepStrictEqual(report.recoveryRequired, []);
  assert.strictEqual(fs.readFileSync(fixture.target, "utf8"), "before");
  const state = await readMutationState(fixture.projectRoot);
  assert.strictEqual(state.mutationGeneration, 0);
  assert.ok(fs.existsSync(path.join(
    fixture.stateRoot,
    "transactions",
    "archive",
    `${fixture.journal.transactionId}.json`,
  )));
  delete process.env.AGENT_STATE_ROOT;
});

test("fault injection: restart compensates disk plus mutation state without a checkpoint", async () => {
  const fixture = await atomicCrashFixture({ withMutationState: true });
  const activeJournal = JSON.parse(fs.readFileSync(path.join(
    fixture.stateRoot,
    "transactions",
    `${fixture.journal.transactionId}.json`,
  ), "utf8"));
  assert.strictEqual(activeJournal.status, "mutation_state_pending");
  assert.ok(activeJournal.mutationCompensationReceipt?.receiptId);

  const report = await recoverIncompleteJournals(fixture.stateRoot);
  assert.deepStrictEqual(report.recoveryRequired, []);
  assert.strictEqual(fs.readFileSync(fixture.target, "utf8"), "before");
  const state = await readMutationState(fixture.projectRoot);
  assert.strictEqual(state.mutationGeneration, 0);
  assert.deepStrictEqual(state.paths, {});
  delete process.env.AGENT_STATE_ROOT;
});

test("fault injection: restart finalizes disk plus mutation state plus task checkpoint", async () => {
  const fixture = await atomicCrashFixture({ withMutationState: true, withCheckpoint: true });
  const report = await recoverIncompleteJournals(fixture.stateRoot);
  assert.deepStrictEqual(report.recoveryRequired, []);
  assert.deepStrictEqual(report.committed, [fixture.journal.transactionId]);
  assert.strictEqual(fs.readFileSync(fixture.target, "utf8"), "after");
  const state = await readMutationState(fixture.projectRoot);
  assert.strictEqual(state.mutationGeneration, 1);
  assert.strictEqual(state.paths[fixture.relPath], sha256Text("after"));
  const pending = pendingBuildJournals({ projectRoot: fixture.projectRoot });
  assert.strictEqual(pending.length, 1);
  assert.strictEqual(pending[0].status, "awaiting_build");
  assert.strictEqual(pending[0].checkpointCommitted, true);
  const second = await recoverIncompleteJournals(fixture.stateRoot);
  assert.deepStrictEqual(second.skippedTerminal, [fixture.journal.transactionId]);
  delete process.env.AGENT_STATE_ROOT;
});

test("late rollback reconciliation publishes the restored disk image as a new mutation", async () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const projectRoot = path.join(stateRoot, "Project");
  const relPath = "Source/Demo/Reconcile.cpp";
  const target = path.join(projectRoot, ...relPath.split("/"));
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, "before", "utf8");
  await recordMutation(projectRoot, relPath, "after");
  fs.writeFileSync(target, "after", "utf8");
  fs.writeFileSync(target, "before", "utf8");

  const reconciliation = await reconcileMutationPathsFromDisk(projectRoot, [relPath]);
  assert.strictEqual(reconciliation.reconciled, true);
  const state = await readMutationState(projectRoot);
  assert.strictEqual(state.paths[relPath], sha256Text("before"));
  assert.strictEqual(state.validationStatus, "pending");
  assert.strictEqual(state.mutationGeneration, 2);
  delete process.env.AGENT_STATE_ROOT;
});

test("bundle finalization cannot publish terminal status before state and checkpoint", async () => {
  const stateRoot = tempStateRoot();
  process.env.AGENT_STATE_ROOT = stateRoot;
  const projectRoot = path.join(stateRoot, "Project");
  const target = path.join(projectRoot, "Content", "Atomic.txt");
  const tx = await applyBundleTransaction(
    { files: [{ path: "Content/Atomic.txt", content: "atomic" }], patches: [] },
    async () => ({ ok: true, absolutePath: target }),
    {
      deferFinalization: true,
      projectRoot,
      taskSessionId: "task_bundle_atomic",
      onCommitted: async ({ journal }) => {
        armAtomicMutationJournal(journal, {
          projectRoot,
          taskSessionId: "task_bundle_atomic",
          checkpointRequired: true,
        });
        return { ok: true };
      },
    },
  );
  assert.strictEqual(tx.ok, true);
  assert.strictEqual(tx.journal.status, "mutation_state_pending");
  tx.journal.status = "build_failed";
  saveJournal(tx.journal);
  assert.strictEqual(tx.journal.status, "mutation_state_pending");
  assert.strictEqual(tx.journal.prematureTerminalStatus, "build_failed");
  assert.ok(fs.existsSync(path.join(
    stateRoot,
    "transactions",
    `${tx.transactionId}.json`,
  )));
  assert.ok(!fs.existsSync(path.join(
    stateRoot,
    "transactions",
    "archive",
    `${tx.transactionId}.json`,
  )));
  delete process.env.AGENT_STATE_ROOT;
});

test("server rollback wiring checkpoints the reconciled generation before terminal journal completion", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "src", "server.js"), "utf8");
  const start = source.indexOf("async function rollbackMutationJournals");
  const end = source.indexOf("async function markPendingMutationJournals", start);
  assert.ok(start >= 0 && end > start);
  const rollbackBody = source.slice(start, end);
  const checkpoint = rollbackBody.indexOf("recordAutomaticContinuityCheckpoint(");
  const terminal = rollbackBody.indexOf("await completeMutationRollback");
  assert.ok(checkpoint >= 0);
  assert.ok(rollbackBody.includes("reconciliation.mutationGeneration"));
  assert.ok(terminal > checkpoint);

  const startupStart = source.indexOf("function recoverRollbackContinuityCheckpoint");
  const startupEnd = source.indexOf("function validationScopeForTask", startupStart);
  const startupBody = source.slice(startupStart, startupEnd);
  assert.ok(startupStart >= 0 && startupEnd > startupStart);
  assert.ok(startupBody.includes("checkpointRollbackViaPython"));
  assert.ok(startupBody.includes("journal?.transactionId"));
  assert.ok(startupBody.includes("reconciliation.mutationGeneration"));

  const staticLoopStart = source.indexOf("if (loopState.blocked)");
  const staticLoopEnd = source.indexOf("let finish;", staticLoopStart);
  const staticLoopBody = source.slice(staticLoopStart, staticLoopEnd);
  assert.ok(staticLoopBody.includes("recordRecoveryObligationViaPython"));
  assert.ok(staticLoopBody.includes("rollbackGeneration"));
});

async function rollbackCrashFixture(stage) {
  const fixture = await atomicCrashFixture({ withMutationState: true, withCheckpoint: true });
  markMutationStateRecorded(fixture.journal, {
    mutationGeneration: fixture.mutation.mutationGeneration,
    mutationRevision: fixture.mutation.mutationRevision,
  });
  await completeMutationJournalCheckpoint(fixture.journal, { checkpointHash: "forward" });
  fixture.journal.status = "build_failed";
  saveJournal(fixture.journal);
  armMutationRollback(fixture.journal, { reason: "fault_injection" });
  const diskRollback = await rollbackJournal(fixture.journal);
  assert.strictEqual(diskRollback.rolledBack, true);
  if (["mutation", "checkpoint"].includes(stage)) {
    fixture.preRecoveryReconciliation = await reconcileMutationPathsFromDisk(
      fixture.projectRoot,
      [fixture.relPath]
    );
  }
  if (stage === "checkpoint") {
    const taskDir = path.join(fixture.stateRoot, "tasks", "task_atomic_recovery");
    fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
      mutationGeneration: fixture.preRecoveryReconciliation.mutationGeneration,
      continuity: {
        checkpoint: {
          status: "recorded",
          mutationGeneration: fixture.preRecoveryReconciliation.mutationGeneration,
          modifiedFiles: [fixture.relPath],
        },
      },
    }), "utf8");
  }
  return fixture;
}

for (const stage of ["disk", "mutation", "checkpoint"]) {
  test(`fault injection: restart converges rollback after ${stage}`, async () => {
    const fixture = await rollbackCrashFixture(stage);
    let checkpointCalls = 0;
    const report = await recoverIncompleteJournals(fixture.stateRoot, {
      checkpointRollback: async ({ reconciliation, relativePaths }) => {
        checkpointCalls += 1;
        const taskDir = path.join(fixture.stateRoot, "tasks", "task_atomic_recovery");
        fs.mkdirSync(taskDir, { recursive: true });
        fs.writeFileSync(path.join(taskDir, "state.json"), JSON.stringify({
          mutationGeneration: reconciliation.mutationGeneration,
          continuity: {
            checkpoint: {
              status: "recorded",
              mutationGeneration: reconciliation.mutationGeneration,
              modifiedFiles: relativePaths,
            },
          },
        }), "utf8");
        return { ok: true, checkpointHash: `rollback-${reconciliation.mutationGeneration}` };
      },
    });
    assert.deepStrictEqual(report.recoveryRequired, []);
    assert.strictEqual(checkpointCalls, 1);
    assert.strictEqual(fs.readFileSync(fixture.target, "utf8"), "before");
    const mutation = await readMutationState(fixture.projectRoot);
    assert.strictEqual(mutation.paths[fixture.relPath], sha256Text("before"));
    const task = JSON.parse(fs.readFileSync(path.join(
      fixture.stateRoot,
      "tasks",
      "task_atomic_recovery",
      "state.json"
    ), "utf8"));
    assert.strictEqual(task.mutationGeneration, mutation.mutationGeneration);
    assert.strictEqual(
      task.continuity.checkpoint.mutationGeneration,
      mutation.mutationGeneration
    );
    assert.ok(fs.existsSync(path.join(
      fixture.stateRoot,
      "transactions",
      "archive",
      `${fixture.journal.transactionId}.json`
    )));
    delete process.env.AGENT_STATE_ROOT;
  });
}

test("startup rollback checkpoints an expired task only through its exact journal binding", async () => {
  const fixture = await rollbackCrashFixture("mutation");
  const workspaceRoot = path.resolve(__dirname, "..", "..");
  const taskSessionId = "task_atomic_recovery";
  const taskDir = path.join(fixture.stateRoot, "tasks", taskSessionId);
  const mutationBeforeStartup = await readMutationState(fixture.projectRoot);
  const taskState = {
    taskSessionId,
    status: "running",
    taskKind: "codegen",
    request: "Recover the exact journal-bound Unreal mutation",
    authToken: "expired-task-token",
    ownerCapability: "owner-capability",
    conversationId: "conversation",
    planId: "plan-rollback",
    planRevision: "1",
    activeSliceId: "slice-rollback",
    projectFile: fixture.projectRoot,
    workspaceRoot,
    // Simulate stale task state from before the crash. Trusted rollback must
    // converge to mutation.json exactly, not preserve this larger generation.
    mutationGeneration: mutationBeforeStartup.mutationGeneration + 50,
    requiredBeforeWrite: [],
    pendingGates: [],
    completedGates: {},
    writeGate: {
      writesAllowed: true,
      completedBeforeWrite: [],
      pendingBeforeWrite: [],
      maxFilesPerEdit: 2,
    },
    planScope: {
      slices: [{ sliceId: "slice-rollback", files: [fixture.relPath] }],
      impactContractFiles: [],
    },
    selectedTargetSnapshots: [],
    continuity: {
      lease: {
        status: "active",
        epoch: 1,
        ttlSeconds: 1800,
        acquiredAt: "2000-01-01T00:00:00+00:00",
        heartbeatAt: "2000-01-01T00:00:00+00:00",
        expiresAt: "2000-01-01T00:00:00+00:00",
      },
      checkpoint: {
        status: "recorded",
        mutationGeneration: fixture.mutation.mutationGeneration,
        modifiedFiles: [fixture.relPath],
        fileSnapshots: [],
      },
      recovery: { status: "checkpoint_current", conflicts: [] },
    },
    toolRoute: {
      phase: "executor",
      routeHash: "pre-restart",
      roleSession: "executor",
      activeTools: ["unreal_task_checkpoint"],
      selectedSlice: { files: [fixture.relPath] },
      pendingGates: [],
    },
  };
  fs.mkdirSync(taskDir, { recursive: true });
  fs.writeFileSync(
    path.join(taskDir, "state.json"),
    JSON.stringify(taskState),
    "utf8",
  );

  const ordinary = checkpointMutationViaPython(
    workspaceRoot,
    { taskAuthorization: taskAuthorizationForState(taskState) },
    [fixture.target],
    {
      requiredNextAction: "static_validate_project",
      validation: { status: "pending" },
      mutationGeneration: mutationBeforeStartup.mutationGeneration,
    },
  );
  assert.strictEqual(ordinary.ok, false);
  assert.strictEqual(ordinary.errorCode, "TASK_RECOVERY_REQUIRED");

  const mismatched = checkpointRollbackViaPython(workspaceRoot, {
    transactionId: fixture.journal.transactionId,
    taskSessionId,
    projectRoot: fixture.projectRoot,
    modifiedFiles: [path.join(fixture.projectRoot, "Source", "Demo", "Other.cpp")],
    mutationGeneration: mutationBeforeStartup.mutationGeneration,
    validation: { status: "pending" },
  });
  assert.strictEqual(mismatched.ok, false);
  assert.strictEqual(mismatched.errorCode, "ROLLBACK_CHECKPOINT_BINDING_INVALID");
  let persistedTask = JSON.parse(fs.readFileSync(path.join(taskDir, "state.json"), "utf8"));
  assert.strictEqual(persistedTask.continuity.lease.expiresAt, "2000-01-01T00:00:00+00:00");

  fs.writeFileSync(fixture.target, "external-after-rollback", "utf8");
  const diskMismatch = checkpointRollbackViaPython(workspaceRoot, {
    transactionId: fixture.journal.transactionId,
    taskSessionId,
    projectRoot: fixture.projectRoot,
    modifiedFiles: [fixture.target],
    mutationGeneration: mutationBeforeStartup.mutationGeneration,
    validation: { status: "pending" },
  });
  assert.strictEqual(diskMismatch.ok, false);
  assert.strictEqual(diskMismatch.errorCode, "ROLLBACK_CHECKPOINT_BINDING_INVALID");
  fs.writeFileSync(fixture.target, "before", "utf8");

  const report = await recoverIncompleteJournals(fixture.stateRoot, {
    checkpointRollback: ({ journal, reconciliation, absolutePaths }) => (
      checkpointRollbackViaPython(workspaceRoot, {
        transactionId: journal.transactionId,
        taskSessionId: journal.taskSessionId,
        projectRoot: journal.projectRoot,
        modifiedFiles: absolutePaths,
        mutationGeneration: reconciliation.mutationGeneration,
        validation: {
          status: "pending",
          proofLevel: "NeedsStaticValidation",
          rollback: { reason: "fault_injection" },
        },
      })
    ),
  });

  assert.deepStrictEqual(report.recoveryRequired, []);
  assert.ok(fs.existsSync(path.join(
    fixture.stateRoot,
    "transactions",
    "archive",
    `${fixture.journal.transactionId}.json`,
  )));
  persistedTask = JSON.parse(fs.readFileSync(path.join(taskDir, "state.json"), "utf8"));
  const mutationAfterStartup = await readMutationState(fixture.projectRoot);
  assert.strictEqual(
    persistedTask.continuity.checkpoint.mutationGeneration,
    mutationAfterStartup.mutationGeneration,
  );
  assert.strictEqual(
    persistedTask.mutationGeneration,
    mutationAfterStartup.mutationGeneration,
  );
  assert.ok(Date.parse(persistedTask.continuity.lease.expiresAt) > Date.now());
  assert.strictEqual(
    persistedTask.continuity.checkpoint.validation.rollback.reason,
    "fault_injection",
  );
  delete process.env.AGENT_STATE_ROOT;
});

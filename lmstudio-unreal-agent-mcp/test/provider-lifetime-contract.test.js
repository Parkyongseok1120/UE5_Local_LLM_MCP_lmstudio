"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createDeleteCapabilities } = require("../src/direct-delete-capabilities");
const { FileSnapshotRegistry } = require("../src/file-snapshot-registry");
const { BuildChangeLedger } = require("../src/direct-build-change-ledger");
const { sha256Text } = require("../src/safe-write");
const { canonicalAbsolutePathIdentity } = require("../src/filesystem-path-identity");
const { tryAcquirePathLock, releasePathLock } = require("../src/write-locks");
const { applyDirectEditBundle } = require("../src/direct-edit-bundle");
const { recoverRuntimeTransactions } = require("../src/direct-transaction-recovery");
const { createRuntimeTransaction, updateRuntimeTransactionEntry, saveRuntimeTransaction,
  transactionBackupPath, transactionFilePath } = require("../src/direct-transaction-store");

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "provider-lifetime-"));
  const target = path.join(root, "Source", "Probe", "Obsolete.cpp");
  const projectPath = path.join(root, "Probe.uproject");
  const stateRoot = path.join(root, "state");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(projectPath, "{}");
  fs.writeFileSync(target, "before\n");
  t.after(() => {
    const absolute = fs.realpathSync(root);
    assert.equal(path.dirname(absolute).toLowerCase(), fs.realpathSync(os.tmpdir()).toLowerCase());
    assert.ok(path.basename(absolute).startsWith("provider-lifetime-"));
    fs.rmSync(absolute, { recursive: true, force: true });
  });
  return { root, target, projectPath, stateRoot };
}

function deletionFixture(t) {
  const f = fixture(t);
  const context = {
    env: { ALLOW_WRITE: "1", ALLOW_SOURCE_DELETE: "1" }, limits: { maxSourceBytes: 8192 },
    runtimeOwner: "direct", stateRoot: f.stateRoot, workspaceRoot: f.root,
    fileSnapshots: new FileSnapshotRegistry(), buildChanges: new BuildChangeLedger(),
    mutationResolution: async () => ({ absolutePath: f.target, activeProject: f.projectPath,
      realPath: f.target, allowedRealRoot: f.root, projectDir: f.root, relativePath: "Source/Probe/Obsolete.cpp" }),
  };
  const api = createDeleteCapabilities(context);
  const details = { completedEditsSummary: "Remove unused implementation", reason: "No references remain",
    ifNotDeleted: "Duplicate source remains", ifDeleted: "Only the replacement remains" };
  const propose = async () => (await api.propose_file_deletions({ completedEditsSummary: details.completedEditsSummary,
    files: [{ path: "project://Source/Probe/Obsolete.cpp", reason: details.reason,
      ifNotDeleted: details.ifNotDeleted, ifDeleted: details.ifDeleted }] })).proposals[0];
  const args = proposal => ({ path: "project://Source/Probe/Obsolete.cpp", approvalToken: proposal.approvalToken,
    userApproved: true, ...details });
  return { ...f, context, api, propose, args };
}

test("delete approval stays bound to its proposed content version and records only a committed deletion", async t => {
  const f = deletionFixture(t), proposal = await f.propose();
  fs.writeFileSync(f.target, "different content\n");
  const stale = await f.api.delete_file({ ...f.args(proposal), expectedHash: proposal.sha256 });
  assert.equal(stale.errorCode, "FILE_VERSION_CONFLICT");
  const currentHash = sha256Text("different content\n");
  const renameBeforeMismatch = fs.promises.rename;
  let mismatchRenames = 0, newer;
  fs.promises.rename = async () => { mismatchRenames++; throw new Error("Unexpected rename with stale approval"); };
  try { newer = await f.api.delete_file({ ...f.args(proposal), expectedHash: currentHash }); }
  finally { fs.promises.rename = renameBeforeMismatch; }
  assert.equal(newer.errorCode, "APPROVAL_VERSION_MISMATCH");
  assert.equal(mismatchRenames, 0);
  assert.equal(fs.readFileSync(f.target, "utf8"), "different content\n");
  assert.deepEqual(f.context.buildChanges.pending(f.projectPath).paths, []);

  const approvedCurrent = await f.propose();
  const originalRename = fs.promises.rename;
  let renamed = 0;
  fs.promises.rename = async (from) => { assert.equal(from, f.target); renamed++; };
  try {
    const result = await f.api.delete_file({ ...f.args(approvedCurrent), fileVersionReceipt: approvedCurrent.fileVersionReceipt });
    assert.equal(result.ok, true);
    assert.equal(result.operation, "moved_to_trash");
  } finally { fs.promises.rename = originalRename; }
  assert.equal(renamed, 1);
  assert.deepEqual(f.context.buildChanges.pending(f.projectPath).paths, ["Source/Probe/Obsolete.cpp"]);
  const consumed = await f.api.delete_file({ ...f.args(approvedCurrent), expectedHash: currentHash });
  assert.equal(consumed.errorCode, "APPROVAL_TOKEN_INVALID");
});

test("an approved deletion notifies the existing build change ledger", async t => {
  const f = deletionFixture(t), proposal = await f.propose();
  const originalRename = fs.promises.rename;
  fs.promises.rename = async () => {};
  try {
    const result = await f.api.delete_file({ ...f.args(proposal), expectedHash: proposal.sha256 });
    assert.equal(result.ok, true);
    assert.deepEqual(f.context.buildChanges.pending(f.projectPath).paths, ["Source/Probe/Obsolete.cpp"]);
    assert.equal(fs.readFileSync(f.target, "utf8"), "before\n", "fake rename never touches fixture source");
  } finally { fs.promises.rename = originalRename; }
});

function interrupted(f, relative = "Source/Probe/Obsolete.cpp") {
  const journal = createRuntimeTransaction({ runtimeOwner: "direct", stateRoot: f.stateRoot,
    projectRoot: f.root, projectPath: f.projectPath });
  const backup = transactionBackupPath(f.stateRoot, "direct", journal.transactionId, relative);
  fs.writeFileSync(backup, "before\n");
  updateRuntimeTransactionEntry(journal, { relativePath: relative, canonicalAbsolutePath: f.target,
    operation: "patch", existedBefore: true, preHash: sha256Text("before\n"), postHash: sha256Text("after\n"),
    intendedPostHashes: [sha256Text("after\n")], preContentBackupPath: backup, writeStarted: true }, f.stateRoot);
  journal.status = "committing";
  saveRuntimeTransaction(journal, f.stateRoot);
  fs.writeFileSync(f.target, "after\n");
  return journal;
}

test("busy legacy recovery leaves every journal byte unchanged and can retry after release", async t => {
  const f = fixture(t), journal = interrupted(f);
  const journalFile = transactionFilePath(f.stateRoot, "direct", journal.transactionId);
  const beforeJournal = fs.readFileSync(journalFile);
  const lock = tryAcquirePathLock(f.target, "active-writer", { stateRoot: f.stateRoot });
  assert.equal(lock.ok, true);
  try {
    const report = await recoverRuntimeTransactions(f.stateRoot, "direct");
    assert.equal(report.recoveryRequired.length, 0);
    assert.equal(report.deferred.length, 1);
    assert.deepEqual(fs.readFileSync(journalFile), beforeJournal);
    assert.equal(fs.readFileSync(f.target, "utf8"), "after\n");
  } finally { releasePathLock(lock); }
  const report = await recoverRuntimeTransactions(f.stateRoot, "direct");
  assert.equal(report.recovered.length, 1);
  assert.equal(fs.readFileSync(f.target, "utf8"), "before\n");
});

test("startup recovery defers a live bundle before taking its paths or changing its journal", async t => {
  const f = fixture(t);
  let observed = 0;
  const result = await applyDirectEditBundle({ patches: [{ path: "Source/Probe/Obsolete.cpp",
    oldText: "before", newText: "after", expectedOccurrences: 1, expectedHash: sha256Text("before\n") }] },
  async () => {
    const identity = value => canonicalAbsolutePathIdentity(value, process.platform, { realpath: false });
    return { ok: true, absolutePath: f.target, expectedIdentity: { absolutePath: identity(f.target),
      realPath: identity(f.target), lexicalRoot: f.root, allowedRealRoot: identity(f.root) } };
  },
  { runtimeOwner: "direct", stateRoot: f.stateRoot, projectRoot: f.root, projectPath: f.projectPath,
    transactionHooks: { afterWriteAhead: async ({ journal }) => {
      const file = transactionFilePath(f.stateRoot, "direct", journal.transactionId), before = fs.readFileSync(file);
      const report = await recoverRuntimeTransactions(f.stateRoot, "direct");
      observed++;
      assert.equal(report.deferred.length, 1);
      assert.equal(report.deferred[0].reason, "live_transaction");
      assert.deepEqual(fs.readFileSync(file), before);
    } } });
  assert.equal(result.ok, true);
  assert.equal(observed, 1);
  assert.equal(fs.readFileSync(f.target, "utf8"), "after\n");
});

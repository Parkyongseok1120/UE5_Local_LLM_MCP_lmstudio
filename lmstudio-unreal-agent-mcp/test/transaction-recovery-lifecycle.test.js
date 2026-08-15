"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const cp = require("child_process");
const test = require("node:test");
const assert = require("node:assert");

const {
  createJournal,
  saveJournal,
  upsertEntry,
  recoverIncompleteJournals,
  resolveRecoveryRequiredJournal,
} = require("../src/transaction-journal.js");

function fixture() {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "journal-recovery-lifecycle-"));
  const projectRoot = path.join(stateRoot, "AnyProject");
  const target = path.join(projectRoot, "Source", "AnyProject", "Thing.cpp");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const journal = createJournal({ operation: "apply_edit_bundle" }, stateRoot);
  journal.status = "recovery_required";
  journal.taskSessionId = "task_journal_recovery";
  journal.projectRoot = projectRoot;
  journal.projectPath = path.join(projectRoot, "AnyProject.uproject");
  journal.recoveryFailure = {
    reason: "external_change_detected",
    paths: ["Source/AnyProject/Thing.cpp"],
  };
  upsertEntry(journal, {
    relativePath: "Source/AnyProject/Thing.cpp",
    canonicalAbsolutePath: target,
    writeStarted: true,
    writeCompleted: true,
    postHash: "deadbeef",
  }, stateRoot);
  saveJournal(journal, stateRoot);
  return { stateRoot, projectRoot, journal };
}

test("recovery_required is reported and promoted on every restart with task metadata", async () => {
  const value = fixture();
  const promoted = [];
  const scan = () => recoverIncompleteJournals(value.stateRoot, {
    promoteRecoveryRequired: async (item) => {
      promoted.push(item);
      return { ok: true, active: true, idempotent: promoted.length > 1 };
    },
  });

  const first = await scan();
  const second = await scan();
  for (const report of [first, second]) {
    assert.strictEqual(report.recoveryRequired.length, 1);
    assert.strictEqual(report.promoted.length, 1);
    assert.strictEqual(report.skippedTerminal.includes(value.journal.transactionId), false);
    assert.deepStrictEqual(report.recoveryRequired[0], {
      reason: "external_change_detected",
      paths: ["Source/AnyProject/Thing.cpp"],
      transactionId: value.journal.transactionId,
      operation: "apply_edit_bundle",
      taskSessionId: "task_journal_recovery",
      projectPath: path.join(value.projectRoot, "AnyProject.uproject"),
      projectRoot: value.projectRoot,
    });
  }
  assert.strictEqual(promoted.length, 2);
  assert.strictEqual(promoted[0].transactionId, promoted[1].transactionId);
});

test("successful exact checkpoint resolution archives journal idempotently", async () => {
  const value = fixture();
  const first = await resolveRecoveryRequiredJournal({
    transactionId: value.journal.transactionId,
    taskSessionId: "task_journal_recovery",
    resolution: {
      strategy: "task_checkpoint_rebase",
      checkpointHash: "checkpoint-one",
    },
  }, value.stateRoot);
  assert.deepStrictEqual(first, {
    ok: true,
    transactionId: value.journal.transactionId,
    archived: true,
    alreadyResolved: false,
  });
  assert.strictEqual(fs.existsSync(path.join(
    value.stateRoot,
    "transactions",
    "archive",
    `${value.journal.transactionId}.json`
  )), true);

  const second = await resolveRecoveryRequiredJournal({
    transactionId: value.journal.transactionId,
    taskSessionId: "task_journal_recovery",
  }, value.stateRoot);
  assert.strictEqual(second.ok, true);
  assert.strictEqual(second.alreadyResolved, true);
  const scan = await recoverIncompleteJournals(value.stateRoot);
  assert.deepStrictEqual(scan.recoveryRequired, []);
});

test("journal resolution refuses a different task owner", async () => {
  const value = fixture();
  const result = await resolveRecoveryRequiredJournal({
    transactionId: value.journal.transactionId,
    taskSessionId: "task_different_owner",
  }, value.stateRoot);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.errorCode, "TRANSACTION_TASK_MISMATCH");
  assert.strictEqual(fs.existsSync(path.join(
    value.stateRoot,
    "transactions",
    `${value.journal.transactionId}.json`
  )), true);
});

test("journal resolution requires exact checkpoint rebase proof", async () => {
  const value = fixture();
  const result = await resolveRecoveryRequiredJournal({
    transactionId: value.journal.transactionId,
    taskSessionId: "task_journal_recovery",
    resolution: { strategy: "task_checkpoint_rebase" },
  }, value.stateRoot);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.errorCode, "TRANSACTION_REBASE_PROOF_REQUIRED");
  assert.strictEqual(fs.existsSync(path.join(
    value.stateRoot,
    "transactions",
    `${value.journal.transactionId}.json`
  )), true);
});

test("journal resolution CLI archives through the configured portable state root", () => {
  const value = fixture();
  const script = path.join(__dirname, "..", "src", "resolve-recovery-journal-cli.js");
  const result = cp.spawnSync(process.execPath, [script, value.projectRoot], {
    env: { ...process.env, AGENT_STATE_ROOT: value.stateRoot },
    input: JSON.stringify({
      transactionId: value.journal.transactionId,
      taskSessionId: "task_journal_recovery",
      resolution: {
        strategy: "task_checkpoint_rebase",
        checkpointHash: "checkpoint-cli",
      },
    }),
    encoding: "utf8",
  });
  assert.strictEqual(result.status, 0, result.stderr);
  const payload = JSON.parse(result.stdout);
  assert.strictEqual(payload.ok, true);
  assert.strictEqual(payload.archived, true);
  assert.strictEqual(fs.existsSync(path.join(
    value.stateRoot,
    "transactions",
    "archive",
    `${value.journal.transactionId}.json`
  )), true);
});

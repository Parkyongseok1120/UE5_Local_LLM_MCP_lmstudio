"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert");

const {
  reserveRouteCall,
  commitRouteReservation,
  rollbackRouteReservation,
  heartbeatRouteReservation,
  environmentRecoveryAttempt,
} = require("../src/task-auth.js");

function routeFixture() {
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "mutation-budget-workspace-"));
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "mutation-budget-state-"));
  const taskSessionId = "task_mutation_budget";
  const taskDirectory = path.join(stateRoot, "tasks", taskSessionId);
  fs.mkdirSync(taskDirectory, { recursive: true });
  const statePath = path.join(taskDirectory, "state.json");
  const state = {
    taskSessionId,
    status: "running",
    planId: "plan-one",
    planRevision: "rev-one",
    activeSliceId: "slice-one",
    projectFile: path.join(workspace, "Demo.uproject"),
    writeGate: { writesAllowed: true },
    toolRoute: {
      status: "active",
      routeHash: "route-before-mutation",
      phase: "executor",
      roleSession: "executor",
      activeTools: ["replace_in_file"],
      allowedPathScopes: ["Source"],
      maxToolCallsPerPhase: 4,
      maxFilesPerSlice: 2,
      selectedSlice: {
        id: "slice-one",
        files: ["Source/Demo/Thing.cpp"],
      },
    },
    toolRouteUsage: {
      routeHash: "route-before-mutation",
      phase: "executor",
      count: 0,
      reserved: 0,
      reservations: [],
      calls: [],
    },
    continuity: {
      lease: {
        status: "active",
        ttlSeconds: 1800,
        heartbeatAt: new Date().toISOString(),
        expiresAt: new Date(Date.now() + 1_800_000).toISOString(),
      },
      recovery: { conflicts: [] },
    },
  };
  fs.writeFileSync(statePath, JSON.stringify(state), "utf8");
  return { workspace, stateRoot, taskSessionId, statePath };
}

function withStateRoot(stateRoot, callback) {
  const previous = process.env.AGENT_STATE_ROOT;
  process.env.AGENT_STATE_ROOT = stateRoot;
  try {
    return callback();
  } finally {
    if (previous === undefined) delete process.env.AGENT_STATE_ROOT;
    else process.env.AGENT_STATE_ROOT = previous;
  }
}

test("mutation reservation commits exactly once after a checkpoint route transition", () => {
  const fixture = routeFixture();
  withStateRoot(fixture.stateRoot, () => {
    const fields = { routeHash: "route-before-mutation", routePhase: "executor" };
    const args = { path: "Source/Demo/Thing.cpp" };
    const reserved = reserveRouteCall(
      fixture.workspace,
      fixture.taskSessionId,
      fields,
      args,
      "replace_in_file"
    );
    assert.strictEqual(reserved.ok, true);

    // Mirrors the checkpoint transition: the new phase gets a fresh counter,
    // while the in-flight capability survives until the handler publishes its
    // final successful outcome.
    const state = JSON.parse(fs.readFileSync(fixture.statePath, "utf8"));
    state.toolRoute = {
      ...state.toolRoute,
      routeHash: "route-after-checkpoint",
      phase: "static_validation",
      activeTools: ["static_validate_project"],
    };
    state.toolRouteUsage = {
      routeHash: "route-after-checkpoint",
      phase: "static_validation",
      count: 0,
      calls: [],
      reserved: 1,
      reservations: state.toolRouteUsage.reservations,
    };
    fs.writeFileSync(fixture.statePath, JSON.stringify(state), "utf8");

    const committed = commitRouteReservation(
      fixture.workspace,
      fixture.taskSessionId,
      fields,
      args,
      "replace_in_file",
      reserved.reservationId,
      { mutationCommit: { transactionId: "tx-one" } }
    );
    assert.strictEqual(committed.ok, true);
    const after = JSON.parse(fs.readFileSync(fixture.statePath, "utf8"));
    assert.strictEqual(after.toolRouteUsage.count, 0);
    assert.strictEqual(after.toolRouteUsage.reserved, 0);
    assert.deepStrictEqual(after.toolRouteUsage.reservations, []);
    assert.strictEqual(after.toolRouteUsage.priorRouteCommits.length, 1);
    assert.strictEqual(after.toolRouteUsage.priorRouteCommits[0].routeHash, "route-before-mutation");
    assert.strictEqual(after.lastToolOutcome.tool, "replace_in_file");
    assert.strictEqual(after.lastToolOutcome.status, "succeeded");

    const replay = commitRouteReservation(
      fixture.workspace,
      fixture.taskSessionId,
      fields,
      args,
      "replace_in_file",
      reserved.reservationId
    );
    assert.strictEqual(replay.ok, false);
    assert.strictEqual(replay.errorCode, "TASK_RESERVATION_NOT_FOUND");
    const finalState = JSON.parse(fs.readFileSync(fixture.statePath, "utf8"));
    assert.strictEqual(finalState.toolRouteUsage.priorRouteCommits.length, 1);
  });
});

test("failed mutation reservation rolls back after recovery changed the route", () => {
  const fixture = routeFixture();
  withStateRoot(fixture.stateRoot, () => {
    const fields = { routeHash: "route-before-mutation", routePhase: "executor" };
    const args = { path: "Source/Demo/Thing.cpp" };
    const reserved = reserveRouteCall(
      fixture.workspace,
      fixture.taskSessionId,
      fields,
      args,
      "replace_in_file"
    );
    assert.strictEqual(reserved.ok, true);
    const state = JSON.parse(fs.readFileSync(fixture.statePath, "utf8"));
    state.toolRoute = {
      ...state.toolRoute,
      routeHash: "route-recovery-read",
      phase: "recovery",
      activeTools: ["read_file_range"],
    };
    state.toolRouteUsage = {
      routeHash: "route-recovery-read",
      phase: "recovery",
      count: 0,
      calls: [],
      reserved: 1,
      reservations: state.toolRouteUsage.reservations,
    };
    fs.writeFileSync(fixture.statePath, JSON.stringify(state), "utf8");

    const rolledBack = rollbackRouteReservation(
      fixture.workspace,
      fixture.taskSessionId,
      fields,
      args,
      "replace_in_file",
      reserved.reservationId,
      { mutationFailure: { errorCode: "OLD_TEXT_NOT_FOUND" } }
    );
    assert.strictEqual(rolledBack.ok, true);
    const after = JSON.parse(fs.readFileSync(fixture.statePath, "utf8"));
    assert.strictEqual(after.toolRouteUsage.count, 0);
    assert.strictEqual(after.toolRouteUsage.reserved, 0);
    assert.deepStrictEqual(after.toolRouteUsage.reservations, []);
  });
});

test("independent replan makes mutation commit and heartbeat stale but still permits cleanup", () => {
  const fixture = routeFixture();
  withStateRoot(fixture.stateRoot, () => {
    const fields = { routeHash: "route-before-mutation", routePhase: "executor" };
    const args = { path: "Source/Demo/Thing.cpp" };
    const reserved = reserveRouteCall(
      fixture.workspace,
      fixture.taskSessionId,
      fields,
      args,
      "replace_in_file"
    );
    assert.strictEqual(reserved.ok, true);
    const state = JSON.parse(fs.readFileSync(fixture.statePath, "utf8"));
    const reservation = state.toolRouteUsage.reservations[0];
    assert.strictEqual(reservation.planRevision, "rev-one");
    assert.strictEqual(reservation.activeSliceId, "slice-one");
    assert.strictEqual(reservation.taskStatus, "running");
    state.planRevision = "rev-independent-replan";
    state.activeSliceId = "slice-replanned";
    fs.writeFileSync(fixture.statePath, JSON.stringify(state), "utf8");

    const heartbeat = heartbeatRouteReservation(
      fixture.workspace,
      fixture.taskSessionId,
      fields,
      args,
      "replace_in_file",
      reserved.reservationId
    );
    assert.strictEqual(heartbeat.ok, false);
    assert.strictEqual(heartbeat.errorCode, "TASK_RESERVATION_SCOPE_STALE");
    assert.deepStrictEqual(heartbeat.staleFields, ["planRevision", "activeSliceId"]);

    const committed = commitRouteReservation(
      fixture.workspace,
      fixture.taskSessionId,
      fields,
      args,
      "replace_in_file",
      reserved.reservationId
    );
    assert.strictEqual(committed.ok, false);
    assert.strictEqual(committed.errorCode, "TASK_RESERVATION_SCOPE_STALE");

    const cleaned = rollbackRouteReservation(
      fixture.workspace,
      fixture.taskSessionId,
      fields,
      args,
      "replace_in_file",
      reserved.reservationId
    );
    assert.strictEqual(cleaned.ok, true);
    const after = JSON.parse(fs.readFileSync(fixture.statePath, "utf8"));
    assert.strictEqual(after.toolRouteUsage.reserved, 0);
  });
});

test("task cancellation invalidates a live mutation reservation", () => {
  const fixture = routeFixture();
  withStateRoot(fixture.stateRoot, () => {
    const fields = { routeHash: "route-before-mutation", routePhase: "executor" };
    const args = { path: "Source/Demo/Thing.cpp" };
    const reserved = reserveRouteCall(
      fixture.workspace,
      fixture.taskSessionId,
      fields,
      args,
      "replace_in_file"
    );
    assert.strictEqual(reserved.ok, true);
    const state = JSON.parse(fs.readFileSync(fixture.statePath, "utf8"));
    state.status = "cancelled";
    fs.writeFileSync(fixture.statePath, JSON.stringify(state), "utf8");
    const committed = commitRouteReservation(
      fixture.workspace,
      fixture.taskSessionId,
      fields,
      args,
      "replace_in_file",
      reserved.reservationId
    );
    assert.strictEqual(committed.ok, false);
    assert.strictEqual(committed.errorCode, "TASK_RESERVATION_SCOPE_STALE");
    assert.deepStrictEqual(committed.staleFields, ["status"]);
    assert.strictEqual(rollbackRouteReservation(
      fixture.workspace,
      fixture.taskSessionId,
      fields,
      args,
      "replace_in_file",
      reserved.reservationId
    ).ok, true);
  });
});

test("server mutation handlers use deferred reservation and contain no pre-write budget consume", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "src", "server.js"), "utf8");
  const deferredSet = source.match(/const DEFER_BUDGET_UNTIL_SUCCESS = new Set\(\[([\s\S]*?)\]\);/u);
  assert.ok(deferredSet);
  for (const tool of ["write_file", "replace_in_file", "delete_file", "apply_edit_bundle"]) {
    assert.match(deferredSet[1], new RegExp(`"${tool}"`));
  }
  assert.doesNotMatch(source, /commitMutationRouteBudget/u);
  assert.doesNotMatch(source, /if \(ALLOW_EXISTING_SOURCE_WRITE\)/u);
  assert.match(source, /allowExistingWrite: false/u);
  for (const operation of ["write_file", "replace_in_file", "delete_file", "apply_edit_bundle"]) {
    assert.match(source, new RegExp(`operation: "${operation}"`));
  }
});

test("delete validation and durable checkpoint precede its single budget commit", () => {
  const source = fs.readFileSync(path.join(__dirname, "..", "src", "server.js"), "utf8");
  const start = source.indexOf('if (name === "delete_file")');
  const end = source.indexOf('if (name === "apply_edit_bundle")', start);
  assert.ok(start >= 0 && end > start);
  const handler = source.slice(start, end);
  const diskMutation = handler.indexOf("await fsp.unlink(target)");
  const validation = handler.indexOf("await validateAfterDelete(");
  const checkpoint = handler.indexOf("await completeMutationJournalCheckpoint(");
  const budgetCommit = handler.indexOf("commitDeferredBudgetOrFail({");
  assert.ok(diskMutation >= 0);
  assert.ok(validation > diskMutation);
  assert.ok(checkpoint > validation);
  assert.ok(budgetCommit > checkpoint);
  assert.strictEqual((handler.match(/commitDeferredBudgetOrFail\(\{/gu) || []).length, 1);
});

test("environment recovery attempt id ignores Node-proposed commands and control projection churn", () => {
  const recovery = {
    status: "environment_recovery",
    errorCode: "VALIDATOR_TIMEOUT",
    attemptId: "caller-supplied-id-must-not-win",
    attemptOutcome: "succeeded",
    requiredTool: {
      name: "static_validate_project",
      args: { projectRoot: "D:/Projects/Portable", fullAudit: false },
    },
  };
  const first = environmentRecoveryAttempt(recovery, {}, {
    taskSessionId: "task_environment_retry",
    controlEpoch: 4,
    toolRoute: { routeHash: "route-four" },
  });
  const replay = environmentRecoveryAttempt({
    requiredTool: {
      args: { fullAudit: false, projectRoot: "D:/Projects/Portable" },
      name: "static_validate_project",
    },
    errorCode: "VALIDATOR_TIMEOUT",
    status: "environment_recovery",
  }, {}, {
    toolRoute: { routeHash: "route-four" },
    controlEpoch: 4,
    taskSessionId: "task_environment_retry",
  });
  const nextAttempt = environmentRecoveryAttempt(recovery, {}, {
    taskSessionId: "task_environment_retry",
    controlEpoch: 5,
    toolRoute: { routeHash: "route-five" },
  });
  const forgedProposal = environmentRecoveryAttempt({
    ...recovery,
    status: "evidence_complete",
    requiredTool: { name: "proposal_two", args: { forged: true } },
  }, {}, {
    taskSessionId: "task_environment_retry",
    controlEpoch: 99,
    toolRoute: { routeHash: "forged-route" },
  });
  assert.strictEqual(first.attemptId, replay.attemptId);
  assert.strictEqual(first.attemptId, nextAttempt.attemptId);
  assert.strictEqual(first.attemptId, forgedProposal.attemptId);
  assert.notStrictEqual(first.attemptId, "caller-supplied-id-must-not-win");
  assert.strictEqual(first.attemptOutcome, "failed");
});

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const { attachControlEnvelope } = require("../src/control-envelope.js");
const {
  routeAuthorizationFailureOptions,
} = require("../src/route-authorization-failure-options.js");

test("budget failure adapter preserves the transaction-committed authoritative control", () => {
  const checkpointArgs = {
    action: "record",
    phase: "planner",
    requiredNextAction: "read_file",
    includeGitChanges: false,
    taskAuthorization: {
      taskSessionId: "task-budget-adapter",
      ownerCapability: "owner-budget-adapter",
    },
  };
  const committedControl = {
    version: 2,
    authoritative: true,
    taskSessionId: "task-budget-adapter",
    taskMode: "agent_edit",
    planRevision: "1",
    activeSliceId: "task",
    phase: "planner",
    disposition: "checkpoint",
    requiredTool: {
      name: "unreal_task_checkpoint",
      args: checkpointArgs,
    },
    allowedTools: ["unreal_task_checkpoint"],
    routeHash: "route-budget-adapter",
    pendingGates: ["unreal_feature_intent_resolve"],
    retryPolicy: { sameSemanticInput: "once" },
    blocker: null,
    mutationGeneration: 0,
    epoch: 8,
    fingerprint: "8".repeat(64),
  };
  const options = routeAuthorizationFailureOptions({
    ok: false,
    taskSessionId: "task-budget-adapter",
    controlEpoch: 8,
    errorCode: "TASK_PHASE_TOOL_BUDGET_EXHAUSTED",
    error: "Phase tool-call budget exhausted (12/12).",
    nextAction: "unreal_task_checkpoint",
    nextActionArgs: checkpointArgs,
    taskAuthorization: checkpointArgs.taskAuthorization,
    toolRoute: {
      routeHash: "route-budget-adapter",
      phase: "planner",
      activeTools: ["read_file"],
    },
    control: committedControl,
  }, "read_file");

  assert.deepEqual(options.control, committedControl);
  assert.notStrictEqual(options.control, committedControl);
  const emitted = attachControlEnvelope(options, "read_file");
  assert.deepEqual(emitted.control, committedControl);
  assert.equal(emitted.control.authoritative, true);
  assert.equal(emitted.control.epoch, 8);
  assert.equal(emitted.control.retryPolicy.sameSemanticInput, "once");
  assert.equal(emitted.control.blocker, null);
});

test("inactive-tool fallback selects the executable active route before a future pending gate", () => {
  const options = routeAuthorizationFailureOptions({
    ok: false,
    errorCode: "TASK_TOOL_NOT_ACTIVE",
    toolRoute: {
      phase: "continuity",
      activeTools: ["unreal_task_checkpoint"],
      pendingGates: ["unreal_feature_intent_resolve"],
    },
  }, "read_file");

  assert.equal(options.nextAction, "unreal_task_checkpoint");
  assert.equal(options.nextActionIsTool, true);
});

test("already-satisfied route failures preserve their replay-blocking evidence", () => {
  const options = routeAuthorizationFailureOptions({
    ok: false,
    errorCode: "TASK_CONTROL_OBLIGATION_REQUIRED",
    alreadySatisfied: true,
    reexecutionBlocked: true,
    control: {
      version: 2,
      authoritative: true,
      epoch: 6,
      taskSessionId: "task-synthesis",
      routeHash: "route-synthesis",
      phase: "synthesis",
      disposition: "continue",
      requiredTool: null,
      allowedTools: [],
      retryPolicy: { sameSemanticInput: "forbidden" },
    },
    nextAction: "use_authoritative_control",
    nextActionIsTool: false,
  }, "read_file");

  assert.equal(options.alreadySatisfied, true);
  assert.equal(options.reexecutionBlocked, true);
  assert.equal(options.control.epoch, 6);
  assert.equal(options.nextActionIsTool, false);
});

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  commitControlTransition,
  deriveNextObligation,
} = require("../src/task-control-transition");
const { validateToolRoute } = require("../src/task-auth");

function state() {
  return {
    taskSessionId: "task_transition",
    status: "running",
    planRevision: "7",
    activeSliceId: "gameplay",
    requiredGateSetHash: "gate-set",
    mutationGeneration: 0,
    completedGates: {
      unreal_code_sketch_claim_validate: {
        status: "completed",
        gateSetHash: "gate-set",
        planRevision: "7",
        activeSliceId: "gameplay",
        mutationGeneration: 0,
      },
    },
    selectedTargetSnapshots: [
      { path: "Source/Sample/Feature.cpp", exists: true, fileHash: "a" },
    ],
    continuity: { checkpoint: {} },
    toolRoute: {
      phase: "executor",
      routeHash: "route",
      pendingGates: [],
      selectedSlice: { files: ["Source/Sample/Feature.cpp"] },
      activeTools: [
        "replace_in_file", "write_file", "apply_edit_bundle",
        "static_validate_project", "build_unreal_project",
        "run_unreal_automation_tests", "read_file",
        "unreal_code_sketch_claim_validate",
      ],
    },
  };
}

function requiredName(value) {
  return deriveNextObligation(value).requiredTool?.name || "";
}

test("late pipeline transitions are derived from facts without an LM", () => {
  const value = state();
  assert.equal(requiredName(value), "replace_in_file");

  value.mutationGeneration = 1;
  value.continuity.checkpoint = {
    mutationGeneration: 1,
    modifiedFiles: ["Source/Sample/Feature.cpp"],
    requiredNextAction: "read_file",
    validation: {},
  };
  assert.equal(requiredName(value), "static_validate_project");

  value.continuity.checkpoint.validation = { status: "passed", proofLevel: "StaticVerified" };
  assert.equal(requiredName(value), "build_unreal_project");

  value.buildVerification = {
    status: "pending_automation",
    mutationGeneration: 1,
    testFilter: "Sample.Project",
  };
  assert.deepEqual(deriveNextObligation(value).requiredTool, {
    name: "run_unreal_automation_tests",
    args: { testFilter: "Sample.Project" },
  });

  value.status = "completed";
  const complete = deriveNextObligation(value);
  assert.equal(complete.disposition, "complete");
  assert.equal(complete.requiredTool, null);
  assert.deepEqual(complete.allowedTools, []);
});

test("control epoch is monotonic and semantic", () => {
  const value = state();
  commitControlTransition(value);
  const epoch = value.controlEpoch;
  const fingerprint = value.controlFingerprint;

  commitControlTransition(value);
  assert.equal(value.controlEpoch, epoch);
  assert.equal(value.controlFingerprint, fingerprint);

  value.updatedAt = "metadata-only";
  commitControlTransition(value);
  assert.equal(value.controlEpoch, epoch);

  value.mutationGeneration = 1;
  value.continuity.checkpoint = {
    mutationGeneration: 1,
    modifiedFiles: ["Source/Sample/Feature.cpp"],
    validation: {},
  };
  commitControlTransition(value);
  assert.equal(value.controlEpoch, epoch + 1);
  assert.equal(value.controlState.requiredTool.name, "static_validate_project");
});

test("placeholder feature scope exposes discovery without forcing the gate", () => {
  const value = state();
  value.completedGates = {};
  value.slicePlanningRequired = true;
  value.toolRoute.pendingGates = ["unreal_feature_intent_resolve"];
  value.toolRoute.activeTools.push("unreal_feature_intent_resolve", "search_files");

  const control = deriveNextObligation(value);
  assert.equal(control.requiredTool, null);
  assert.equal(control.disposition, "continue");
  assert.ok(control.allowedTools.includes("read_file"));
  assert.ok(control.allowedTools.includes("search_files"));
  assert.ok(control.allowedTools.includes("unreal_feature_intent_resolve"));
});

test("compile repair starts with one authoritative reproduction build", () => {
  const value = state();
  value.taskKind = "compile_fix";
  value.completedGates = {};
  value.toolRoute.phase = "planner";
  value.toolRoute.pendingGates = ["unreal_code_sketch_claim_validate"];

  assert.equal(requiredName(value), "build_unreal_project");
  value.buildRecovery = {
    status: "repair_planning_required",
    evidenceSatisfied: true,
  };
  assert.equal(requiredName(value), "unreal_code_sketch_claim_validate");
});

test("every successful late-stage tool replay is blocked and redirected", () => {
  const value = state();
  const fields = { routeHash: "route", routePhase: "executor" };
  const assertBlocked = (repeatedTool, expectedNext) => {
    commitControlTransition(value);
    const blocked = validateToolRoute(value, fields, {}, repeatedTool);
    assert.equal(blocked.ok, false);
    assert.equal(blocked.errorCode, "TASK_CONTROL_OBLIGATION_REQUIRED");
    assert.equal(blocked.reexecutionBlocked, true);
    assert.equal(blocked.nextAction, expectedNext);
    assert.deepEqual(blocked.control, value.controlState);
  };

  assertBlocked("unreal_code_sketch_claim_validate", "replace_in_file");

  value.mutationGeneration = 1;
  value.continuity.checkpoint = {
    mutationGeneration: 1,
    modifiedFiles: ["Source/Sample/Feature.cpp"],
    validation: {},
  };
  assertBlocked("replace_in_file", "static_validate_project");

  value.continuity.checkpoint.validation = { status: "passed" };
  assertBlocked("static_validate_project", "build_unreal_project");

  value.buildVerification = { status: "pending_automation", testFilter: "Sample.Project" };
  assertBlocked("build_unreal_project", "run_unreal_automation_tests");

  value.status = "completed";
  assertBlocked("run_unreal_automation_tests", "use_authoritative_control");
});

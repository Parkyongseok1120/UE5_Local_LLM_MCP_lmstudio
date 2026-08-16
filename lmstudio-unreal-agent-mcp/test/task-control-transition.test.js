"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
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

  value.continuity.checkpoint.validation = {
    status: "failed",
    firstFinding: { path: "Source/Sample/Feature.cpp" },
    recovery: {
      status: "evidence_required",
      mutationGeneration: 1,
      targetPath: "Source/Sample/Feature.cpp",
    },
  };
  assert.deepEqual(deriveNextObligation(value).requiredTool, {
    name: "read_file",
    args: { path: "Source/Sample/Feature.cpp" },
  });

  value.continuity.checkpoint.validation.recovery.status = "evidence_satisfied";
  assert.equal(requiredName(value), "replace_in_file");

  value.mutationGeneration = 2;
  value.continuity.checkpoint = {
    mutationGeneration: 2,
    modifiedFiles: ["Source/Sample/Feature.cpp"],
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

test("evidence exhaustion either routes a bounded repair or permits synthesis with no tools", () => {
  const writeTask = state();
  writeTask.recoveryObligation = {
    source: "evidence",
    status: "repair_planning_required",
    fingerprint: "evidence-write",
    errorCode: "EVIDENCE_STAGNATION",
    requiredTool: {
      name: "unreal_code_sketch_claim_validate",
      args: { targetFiles: ["Source/Sample/Feature.cpp"] },
    },
  };
  assert.deepEqual(deriveNextObligation(writeTask).requiredTool, {
    name: "unreal_code_sketch_claim_validate",
    args: { targetFiles: ["Source/Sample/Feature.cpp"] },
  });

  const readOnlyTask = state();
  readOnlyTask.writesAllowed = false;
  readOnlyTask.writeGate = { writesAllowed: false };
  readOnlyTask.recoveryObligation = {
    source: "evidence",
    status: "evidence_complete",
    fingerprint: "evidence-read",
    errorCode: "EVIDENCE_STAGNATION",
    requiredTool: {},
  };
  const control = deriveNextObligation(readOnlyTask);
  assert.equal(control.disposition, "continue");
  assert.equal(control.requiredTool, null);
  assert.deepEqual(control.allowedTools, []);
  assert.equal(control.retryPolicy.sameSemanticInput, "forbidden");
});

test("expired gate route fallback atomically recommits authoritative control", () => {
  const value = state();
  commitControlTransition(value);
  const oldEpoch = value.controlEpoch;
  const fallback = {
    phase: "planner",
    routeHash: "expired-gate-fallback",
    roleSession: "planner",
    pendingGates: ["unreal_code_sketch_claim_validate"],
    selectedSlice: { files: ["Source/Sample/Feature.cpp"] },
    activeTools: ["unreal_code_sketch_claim_validate", "read_file"],
  };
  value.toolRoute.expiryTransition = {
    at: "2000-01-01T00:00:00.000Z",
    route: fallback,
  };

  const validated = validateToolRoute(
    value,
    { routeHash: fallback.routeHash, routePhase: fallback.phase },
    {},
    "unreal_code_sketch_claim_validate"
  );

  assert.equal(validated.ok, true);
  assert.equal(value.toolRoute.routeHash, fallback.routeHash);
  assert.equal(value.controlState.routeHash, fallback.routeHash);
  assert.deepEqual(value.controlState.requiredTool, {
    name: "unreal_code_sketch_claim_validate",
    args: {},
  });
  assert.equal(value.controlEpoch, oldEpoch + 1);
  const recommittedFingerprint = value.controlFingerprint;
  validateToolRoute(
    value,
    { routeHash: fallback.routeHash, routePhase: fallback.phase },
    {},
    "unreal_code_sketch_claim_validate"
  );
  assert.equal(value.controlEpoch, oldEpoch + 1);
  assert.equal(value.controlFingerprint, recommittedFingerprint);
});

test("expired recovery sketch approval reopens the gate before repair mutation", () => {
  const value = state();
  value.recoveryObligation = {
    source: "build",
    status: "repair_required",
    fingerprint: "repair-after-expiry",
    requiredTool: {},
    targetFiles: ["Source/Sample/Feature.cpp"],
  };
  commitControlTransition(value);
  const oldEpoch = value.controlEpoch;
  const fallback = {
    phase: "verifier",
    routeHash: "expired-recovery-sketch",
    roleSession: "verifier",
    pendingGates: ["unreal_code_sketch_claim_validate"],
    selectedSlice: { files: ["Source/Sample/Feature.cpp"] },
    activeTools: ["unreal_code_sketch_claim_validate", "read_file"],
  };
  value.toolRoute.expiryTransition = {
    at: "2000-01-01T00:00:00.000Z",
    route: fallback,
  };

  const validated = validateToolRoute(
    value,
    { routeHash: fallback.routeHash, routePhase: fallback.phase },
    { sketch: "void Repair();", targetFiles: ["Source/Sample/Feature.cpp"] },
    "unreal_code_sketch_claim_validate"
  );

  assert.equal(validated.ok, true);
  assert.equal(value.controlState.phase, "verifier");
  assert.deepEqual(value.controlState.requiredTool, {
    name: "unreal_code_sketch_claim_validate",
    args: {},
  });
  assert.deepEqual(value.controlState.allowedTools, ["unreal_code_sketch_claim_validate"]);
  assert.equal(value.controlEpoch, oldEpoch + 1);

  value.toolRoute.pendingGates = [];
  value.toolRoute.phase = "executor";
  value.toolRoute.activeTools = ["replace_in_file", "apply_edit_bundle"];
  assert.deepEqual(deriveNextObligation(value).requiredTool, {
    name: "replace_in_file",
    args: {},
  });
});

test("authoritative required args are a server-owned subset", () => {
  const value = state();
  value.toolRoute.phase = "planner";
  value.toolRoute.routeHash = "required-args-route";
  value.toolRoute.activeTools = ["read_file_range"];
  value.toolRoute.pendingGates = [];
  value.controlState = {
    authoritative: true,
    epoch: 3,
    requiredTool: {
      name: "read_file_range",
      args: {
        path: "project://Source/Sample/Feature.cpp",
        startLine: 17,
        endLine: 31,
      },
    },
    allowedTools: ["read_file_range"],
  };
  const fields = { routeHash: "required-args-route", routePhase: "planner" };

  const allowed = validateToolRoute(
    value,
    fields,
    {
      path: "Source/Sample/Feature.cpp",
      startLine: 17,
      endLine: 31,
      encoding: "utf-8",
    },
    "read_file_range"
  );
  assert.equal(allowed.ok, true);

  const denied = validateToolRoute(
    value,
    fields,
    { path: "Source/Sample/Feature.cpp", startLine: 18, endLine: 31 },
    "read_file_range"
  );
  assert.equal(denied.ok, false);
  assert.equal(denied.errorCode, "TASK_CONTROL_ARGUMENT_MISMATCH");
  assert.equal(denied.nextAction, "read_file_range");
  assert.equal(denied.nextActionArgs.startLine, 17);
});

test("build recovery projects evidence, sketch, mutation, static, and build obligations", () => {
  const value = state();
  const target = "Source/Runtime/Feature.cpp";
  value.toolRoute.selectedSlice.files = [target];
  value.selectedTargetSnapshots = [
    { path: target, exists: true, fileHash: "before" },
  ];
  value.recoveryObligation = {
    source: "build",
    status: "evidence_required",
    fingerprint: "build-failure",
    requiredTool: {
      name: "read_file_range",
      args: {
        path: `project://${target}`,
        startLine: 8,
        endLine: 24,
      },
    },
  };
  assert.deepEqual(deriveNextObligation(value).requiredTool, value.recoveryObligation.requiredTool);

  value.recoveryObligation = {
    ...value.recoveryObligation,
    status: "repair_planning_required",
    requiredTool: {
      name: "unreal_code_sketch_claim_validate",
      args: { targetFiles: [target] },
    },
  };
  assert.deepEqual(deriveNextObligation(value).requiredTool, value.recoveryObligation.requiredTool);

  value.recoveryObligation = {
    ...value.recoveryObligation,
    status: "repair_required",
    requiredTool: {},
  };
  assert.deepEqual(deriveNextObligation(value).requiredTool, {
    name: "replace_in_file",
    args: {},
  });

  value.recoveryObligation = {
    ...value.recoveryObligation,
    status: "revalidate_required",
    requiredTool: { name: "static_validate_project", args: {} },
  };
  assert.equal(requiredName(value), "static_validate_project");

  value.recoveryObligation.requiredTool = { name: "build_unreal_project", args: {} };
  assert.equal(requiredName(value), "build_unreal_project");
});

test("out-of-slice recovery blocks the route for user direction", () => {
  const value = state();
  value.recoveryObligation = {
    source: "build",
    status: "external_blocker",
    errorCode: "BUILD_FAILURE_OUTSIDE_ACTIVE_SLICE",
    fingerprint: "outside-active-slice",
    requiredTool: {},
  };

  const control = deriveNextObligation(value);
  assert.equal(control.disposition, "await_user");
  assert.equal(control.requiredTool, null);
  assert.deepEqual(control.allowedTools, []);
  assert.deepEqual(control.retryPolicy, { sameSemanticInput: "forbidden" });
  assert.deepEqual(control.blocker, {
    code: "BUILD_FAILURE_OUTSIDE_ACTIVE_SLICE",
    fingerprint: "outside-active-slice",
  });
});

test("Automation failure recovery overrides pending Automation through repair mutation", () => {
  const value = state();
  const target = "Source/Runtime/Feature.cpp";
  value.toolRoute.selectedSlice.files = [target];
  value.selectedTargetSnapshots = [
    { path: target, exists: true, fileHash: "before" },
  ];
  value.buildVerification = {
    status: "pending_automation",
    mutationGeneration: 0,
    testFilters: ["Runtime.Feature", "Plugin.Tools"],
  };
  value.recoveryObligation = {
    source: "automation",
    status: "evidence_required",
    fingerprint: "automation-failure",
    requiredTool: {
      name: "read_unreal_logs",
      args: { mode: "first_error", maxFiles: 1, maxLines: 200, summaryOnly: true },
    },
    targetFiles: [target],
  };
  assert.equal(requiredName(value), "read_unreal_logs");

  value.recoveryObligation = {
    ...value.recoveryObligation,
    status: "repair_planning_required",
    requiredTool: {
      name: "unreal_code_sketch_claim_validate",
      args: { targetFiles: [target] },
    },
  };
  assert.equal(requiredName(value), "unreal_code_sketch_claim_validate");

  value.recoveryObligation = {
    ...value.recoveryObligation,
    status: "repair_required",
    requiredTool: {},
  };
  assert.equal(requiredName(value), "replace_in_file");
});

test("pending Automation preserves deterministic multi-filter arguments", () => {
  const value = state();
  value.buildVerification = {
    status: "pending_automation",
    mutationGeneration: 0,
    testFilter: "",
    testFilters: ["Runtime.Feature", "Plugin.Tools"],
  };

  assert.deepEqual(deriveNextObligation(value).requiredTool, {
    name: "run_unreal_automation_tests",
    args: { testFilters: ["Runtime.Feature", "Plugin.Tools"] },
  });
});

test("late build and Automation obligations publish authoritative project proof args", () => {
  const value = state();
  const projectFile = path.resolve("external-project", "ProjectA.uproject");
  const engineRoot = path.resolve("engines", "UE_5.5");
  value.projectFile = projectFile;
  value.mutationGeneration = 2;
  value.continuity.checkpoint = {
    mutationGeneration: 2,
    validation: { status: "passed", proofLevel: "StaticVerified" },
  };

  assert.deepEqual(deriveNextObligation(value).requiredTool, {
    name: "build_unreal_project",
    args: {
      project: projectFile,
      allowAbsoluteProject: true,
      allowEngineFallback: false,
    },
  });

  value.buildVerification = {
    status: "pending_automation",
    mutationGeneration: 2,
    testFilters: ["ProjectA.Runtime", "ProjectA.Tools"],
    projectFile,
    engineRoot,
  };
  assert.deepEqual(deriveNextObligation(value).requiredTool, {
    name: "run_unreal_automation_tests",
    args: {
      testFilters: ["ProjectA.Runtime", "ProjectA.Tools"],
      project: projectFile,
      engineRoot,
    },
  });
});

test("task build contract exact-binds target platform and configuration", () => {
  const value = state();
  const projectFile = path.resolve("external-project", "ProjectA.uproject");
  value.projectFile = projectFile;
  value.mutationGeneration = 2;
  value.continuity.checkpoint = {
    mutationGeneration: 2,
    validation: { status: "passed", proofLevel: "StaticVerified" },
  };
  value.buildContract = {
    project: projectFile,
    engineRoot: path.resolve("engines", "UE_5.5"),
    target: "ProjectAEditor",
    platform: "Win64",
    configuration: "Development",
    allowAbsoluteProject: true,
    allowEngineFallback: false,
  };
  commitControlTransition(value);

  assert.deepEqual(value.controlState.requiredTool, {
    name: "build_unreal_project",
    args: value.buildContract,
  });
  const fields = { routeHash: value.toolRoute.routeHash, routePhase: value.toolRoute.phase };
  assert.equal(validateToolRoute(value, fields, value.buildContract, "build_unreal_project").ok, true);

  const otherValidProjectTarget = {
    ...value.buildContract,
    target: "ProjectAServer",
    configuration: "Shipping",
  };
  const denied = validateToolRoute(
    value,
    fields,
    otherValidProjectTarget,
    "build_unreal_project"
  );
  assert.equal(denied.ok, false);
  assert.equal(denied.errorCode, "TASK_CONTROL_ARGUMENT_MISMATCH");
  assert.equal(denied.nextActionArgs.target, "ProjectAEditor");
  assert.equal(denied.nextActionArgs.configuration, "Development");
});

test("failed gate recovery preserves exact tool args before repeat blocking", () => {
  const value = state();
  const gate = "unreal_code_sketch_claim_validate";
  const exactArgs = {
    path: "project://Source/Sample/Feature.cpp",
    startLine: 17,
    endLine: 31,
  };
  value.completedGates = {};
  value.toolRoute.phase = "planner";
  value.toolRoute.pendingGates = [gate];
  value.toolRoute.activeTools.push("read_file_range");
  value.failedGateAttempts = {
    [gate]: {
      attemptCount: 1,
      fingerprint: "same-gate-input",
      gateSetHash: value.requiredGateSetHash,
      planRevision: value.planRevision,
      activeSliceId: value.activeSliceId,
      mutationGeneration: value.mutationGeneration,
      nextAction: "read_file_range",
      nextActionIsTool: true,
      nextActionArgs: exactArgs,
    },
  };

  const first = deriveNextObligation(value);
  assert.equal(first.disposition, "require_tool");
  assert.deepEqual(first.requiredTool, { name: "read_file_range", args: exactArgs });
  assert.deepEqual(first.retryPolicy, { sameSemanticInput: "once" });

  value.failedGateAttempts[gate].attemptCount = 2;
  const repeated = deriveNextObligation(value);
  assert.equal(repeated.disposition, "rediscover");
  assert.equal(repeated.requiredTool, null);
  assert.deepEqual(repeated.retryPolicy, { sameSemanticInput: "forbidden" });
  assert.equal(repeated.blocker.code, "REPEATED_GATE_BLOCKER");
  assert.ok(!repeated.allowedTools.includes(gate));
  assert.ok(repeated.allowedTools.includes("read_file"));

  commitControlTransition(value);
  const deniedThirdGate = validateToolRoute(
    value,
    { routeHash: value.toolRoute.routeHash, routePhase: value.toolRoute.phase },
    {},
    gate
  );
  assert.equal(deniedThirdGate.ok, false);
  assert.equal(deniedThirdGate.errorCode, "TASK_CONTROL_OBLIGATION_REQUIRED");
  assert.equal(deniedThirdGate.reexecutionBlocked, true);
});

test("checkpoint conflict publishes one exact rebase obligation", () => {
  const value = state();
  value.recoveryObligation = {
    source: "checkpoint",
    status: "checkpoint_rebase_required",
    fingerprint: "checkpoint-conflict",
    requiredTool: {
      name: "unreal_task_checkpoint",
      args: {
        action: "rebase",
        acceptCurrentFiles: true,
        includeGitChanges: false,
      },
    },
  };

  const control = deriveNextObligation(value);
  assert.equal(control.disposition, "checkpoint");
  assert.deepEqual(control.requiredTool, value.recoveryObligation.requiredTool);
  assert.deepEqual(control.allowedTools, ["unreal_task_checkpoint"]);
  assert.deepEqual(control.retryPolicy, { sameSemanticInput: "once" });
});

test("phase budget exhaustion publishes one exact record checkpoint obligation", () => {
  const value = state();
  value.recoveryObligation = {
    source: "phase_tool_budget",
    status: "phase_budget_checkpoint_required",
    fingerprint: "route:planner:2:2:list_directory",
    requiredTool: {
      name: "unreal_task_checkpoint",
      args: {
        action: "record",
        phase: "planner",
        requiredNextAction: "list_directory",
        includeGitChanges: false,
      },
    },
  };

  const control = deriveNextObligation(value);
  assert.equal(control.disposition, "checkpoint");
  assert.deepEqual(control.requiredTool, value.recoveryObligation.requiredTool);
  assert.deepEqual(control.allowedTools, ["unreal_task_checkpoint"]);
  assert.deepEqual(control.retryPolicy, { sameSemanticInput: "once" });
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

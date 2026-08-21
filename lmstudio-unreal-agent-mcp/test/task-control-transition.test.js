"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const crypto = require("node:crypto");
const {
  commitControlTransition,
  deriveNextObligation,
} = require("../src/task-control-transition");
const { validateToolRoute } = require("../src/task-auth");

function completeEvidence(pathValue, sourceKind, contentHash, text) {
  return {
    path: pathValue,
    sourceKind,
    evidenceId: `${sourceKind}-ready`,
    contentHash,
    evidenceSnapshotGeneration: 0,
    coveredRanges: [[1, 2]],
    wholeFileComplete: true,
    truncated: false,
    lineCount: 2,
    coverageLevel: "FILE_COMPLETE",
    supportingExcerpts: [{
      startLine: 1,
      endLine: 2,
      text,
      excerptDigest: crypto.createHash("sha256").update(text).digest("hex"),
    }],
  };
}

test("repository audit frontier requires the next unvisited source and only then synthesizes", () => {
  const current = state();
  current.mode = "read_only";
  current.toolRoute.phase = "planner";
  current.toolRoute.activeTools = ["read_file", "read_file_range", "search_files"];
  current.repoAuditLedger = {
    version: 1,
    required: true,
    status: "active",
    inventoryHash: "a".repeat(64),
    queuedTargets: ["Source/Sample/A.cpp", "Source/Sample/B.h"],
    cursor: 1,
    remainingCount: 1,
  };

  const pending = deriveNextObligation(current);
  assert.deepEqual(pending.requiredTool, {
    name: "read_file",
    args: { path: "Source/Sample/B.h" },
  });
  assert.deepEqual(pending.allowedTools, ["read_file"]);

  current.repoAuditLedger.status = "complete";
  current.repoAuditLedger.cursor = 2;
  current.repoAuditLedger.remainingCount = 0;
  current.taskKind = "cpp_analysis";
  current.inspectionContract = { intent: "cpp_analysis", evidenceBudget: { representativePairs: 1 } };
  current.sourceEvidence = {
    planRevision: current.planRevision,
    files: {
      header: completeEvidence("Source/Sample/Public/Sample.h", "declaration", "a".repeat(64), "class FSample {};"),
      source: completeEvidence("Source/Sample/Private/Sample.cpp", "implementation", "b".repeat(64), "void FSample::Run() {}"),
    },
  };
  const complete = deriveNextObligation(current);
  assert.equal(complete.requiredTool, null);
  assert.deepEqual(complete.allowedTools, []);
  assert.equal(complete.disposition, "continue");
});

test("readiness false never publishes tool-free continue", () => {
  const value = state();
  value.mode = "read_only";
  value.writesAllowed = false;
  value.writeGate = { writesAllowed: false };
  value.taskKind = "cpp_analysis";
  value.planRevision = "readiness-not-ready";
  value.toolRoute.phase = "planner";
  value.toolRoute.activeTools = ["read_file", "search_files"];
  value.recoveryObligation = {
    source: "evidence",
    status: "evidence_complete",
    requiredTool: {},
  };

  const control = deriveNextObligation(value);
  assert.notEqual(control.disposition, "continue");
  assert.ok(control.requiredTool || control.blocker);
  if (control.requiredTool) {
    assert.equal(control.allowedTools.length, 1);
    assert.equal(control.allowedTools[0], control.requiredTool.name);
  } else {
    assert.equal(control.disposition, "workflow_stop");
    assert.equal(control.blocker.code, "EVIDENCE_DISCOVERY_EXHAUSTED");
    assert.equal(control.retryPolicy.sameSemanticInput, "forbidden");
  }
});

test("frontier lost requires failed reconstruction, bounded replan, and bounded search proof", () => {
  const value = state();
  value.mode = "read_only";
  value.writesAllowed = false;
  value.writeGate = { writesAllowed: false };
  value.taskKind = "cpp_analysis";
  value.planRevision = "frontier-loss-proof";
  value.toolRoute.phase = "planner";
  value.toolRoute.activeTools = [];
  value.inspectionContract = {
    intent: "cpp_analysis",
    evidenceBudget: { representativePairs: 1 },
  };
  value.sourceEvidence = { planRevision: value.planRevision, files: {} };

  value.inspectionProgress = {
    discoveryStarted: false,
    everHadFrontier: true,
    discoveryAttempts: 2,
    remainingFrontier: [],
  };
  const neverStarted = deriveNextObligation(value);
  assert.equal(neverStarted.disposition, "workflow_stop");
  assert.equal(neverStarted.blocker.code, "EVIDENCE_DISCOVERY_EXHAUSTED");

  value.recoveryObligation = {};
  value.inspectionProgress = {
    discoveryStarted: true,
    everHadFrontier: false,
    discoveryAttempts: 2,
    remainingFrontier: [],
  };
  const neverHadFrontier = deriveNextObligation(value);
  assert.equal(neverHadFrontier.disposition, "workflow_stop");
  assert.equal(neverHadFrontier.blocker.code, "EVIDENCE_DISCOVERY_EXHAUSTED");

  value.recoveryObligation = {};
  value.inspectionProgress = {
    discoveryStarted: true,
    everHadFrontier: true,
    discoveryAttempts: 2,
    remainingFrontier: [],
  };
  const reconstructionRequired = deriveNextObligation(value);
  assert.equal(reconstructionRequired.disposition, "require_tool");
  assert.equal(reconstructionRequired.requiredTool.name, "unreal_agent_plan");
  assert.equal(reconstructionRequired.transitionReason, "EVIDENCE_FRONTIER_RECONSTRUCTION_REPLAN");

  value.recoveryObligation = {};
  value.inspectionProgress.frontierReconstruction = {
    failedReconstruction: true,
    noDeterministicPair: true,
    boundedReplanApplied: true,
    boundedSearchAttempted: true,
    noBoundedSearchCandidates: true,
  };
  const genuinelyLost = deriveNextObligation(value);
  assert.equal(genuinelyLost.disposition, "workflow_stop");
  assert.equal(genuinelyLost.blocker.code, "EVIDENCE_FRONTIER_LOST");
});

test("missing declaration selects a bounded matching header read", () => {
  const value = state();
  value.mode = "read_only";
  value.writesAllowed = false;
  value.writeGate = { writesAllowed: false };
  value.taskKind = "cpp_analysis";
  value.planRevision = "missing-declaration";
  value.inspectionContract = { intent: "cpp_analysis", evidenceBudget: { representativePairs: 1 } };
  value.sourceEvidence = {
    planRevision: value.planRevision,
    files: {
      implementation: {
        path: "Source/Sample/Private/Feature.cpp",
        sourceKind: "implementation",
        evidenceId: "impl-only",
        includePath: "Source/Sample/Public/Feature.h",
      },
    },
  };
  value.recoveryObligation = { source: "evidence", status: "evidence_complete", requiredTool: {} };

  const control = deriveNextObligation(value);
  assert.deepEqual(control.requiredTool, {
    name: "read_file",
    args: { path: "Source/Sample/Public/Feature.h" },
  });
  assert.deepEqual(control.allowedTools, ["read_file"]);
});

test("empty frontier rebuilds a source pair search or returns an explicit blocker", () => {
  const value = state();
  value.mode = "read_only";
  value.writesAllowed = false;
  value.writeGate = { writesAllowed: false };
  value.taskKind = "cpp_analysis";
  value.planRevision = "empty-frontier";
  value.inspectionContract = { intent: "cpp_analysis", evidenceBudget: { representativePairs: 2 } };
  value.sourceEvidence = {
    planRevision: value.planRevision,
    files: {
      implementation: {
        path: "Source/Sample/Private/Feature.cpp",
        sourceKind: "implementation",
        evidenceId: "impl-only",
      },
    },
  };
  value.inspectionProgress = { remainingFrontier: [] };
  value.recoveryObligation = { source: "evidence", status: "evidence_complete", requiredTool: {} };

  const control = deriveNextObligation(value);
  assert.ok(control.requiredTool || control.blocker);
  if (control.requiredTool) {
    assert.equal(control.requiredTool.name, "search_files");
    assert.equal(control.requiredTool.args.query, "Feature.h");
    assert.equal(control.requiredTool.args.matchFileNames, true);
    assert.equal(control.allowedTools[0], "search_files");
  } else {
    assert.equal(control.disposition, "workflow_stop");
    assert.equal(control.blocker.code, "EVIDENCE_DISCOVERY_EXHAUSTED");
  }
});

test("accepted evidence is never selected again as the recovery read", () => {
  const value = state();
  value.mode = "read_only";
  value.writesAllowed = false;
  value.writeGate = { writesAllowed: false };
  value.taskKind = "cpp_analysis";
  value.planRevision = "accepted-not-repeat";
  value.inspectionContract = { intent: "cpp_analysis", evidenceBudget: { representativePairs: 2 } };
  value.sourceEvidence = {
    planRevision: value.planRevision,
    files: {
      header: {
        path: "Source/Sample/Public/Feature.h",
        sourceKind: "declaration",
        evidenceId: "header-accepted",
      },
    },
  };
  value.inspectionProgress = { remainingFrontier: ["Source/Sample/Public/Feature.h"] };
  value.recoveryObligation = { source: "evidence", status: "evidence_complete", requiredTool: {} };

  const control = deriveNextObligation(value);
  assert.notDeepEqual(control.requiredTool, {
    name: "read_file",
    args: { path: "Source/Sample/Public/Feature.h" },
  });
  assert.ok(control.requiredTool || control.blocker);
});

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
  readOnlyTask.mode = "read_only";
  readOnlyTask.writesAllowed = false;
  readOnlyTask.writeGate = { writesAllowed: false };
  readOnlyTask.taskKind = "cpp_analysis";
  readOnlyTask.planRevision = "plan-ready";
  readOnlyTask.sourceEvidence = {
    planRevision: "plan-ready",
    files: {
      "Source/Sample/Feature.h": completeEvidence("Source/Sample/Feature.h", "declaration", "c".repeat(64), "class FFeature {};"),
      "Source/Sample/Feature.cpp": completeEvidence("Source/Sample/Feature.cpp", "implementation", "d".repeat(64), "void FFeature::Run() {}"),
    },
  };
  readOnlyTask.inspectionContract = { intent: "cpp_analysis", evidenceBudget: { representativePairs: 1 } };
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

test("evidence exhaustion cannot synthesize a C++ analysis with discovery-only evidence", () => {
  const value = state();
  value.mode = "read_only";
  value.writesAllowed = false;
  value.writeGate = { writesAllowed: false };
  value.taskKind = "cpp_analysis";
  value.planRevision = "plan-discovery-only";
  value.inspectionContract = { intent: "cpp_analysis", evidenceBudget: { representativePairs: 1 } };
  value.inspectionProgress = { remainingFrontier: ["Source/Sample/Feature.cpp"] };
  value.recoveryObligation = {
    source: "evidence",
    status: "evidence_complete",
    fingerprint: "discovery-only",
    errorCode: "EVIDENCE_STAGNATION",
    requiredTool: {},
  };

  const control = deriveNextObligation(value);
  assert.equal(control.disposition, "require_tool");
  assert.equal(control.requiredTool.name, "read_file");
  assert.equal(control.requiredTool.args.path, "Source/Sample/Feature.cpp");
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
  assert.notEqual(value.toolRoute.routeHash, fallback.routeHash);
  assert.equal(value.controlState.routeHash, value.toolRoute.routeHash);
  assert.deepEqual(value.toolRoute.activeTools, value.controlState.allowedTools);
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
  assert.equal(control.requiredUserInput.kind, "choose_option");
  assert.match(control.requiredUserInput.prompt, /external blocker/i);
  assert.equal(control.requiredUserInput.schema.type, "object");
  assert.match(control.requiredUserInput.resumeToken, /^[a-f0-9]{64}$/u);
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

test("phase budget replan publishes one exact planner obligation", () => {
  const value = state();
  value.recoveryObligation = {
    source: "phase_tool_budget",
    status: "phase_budget_replan_required",
    fingerprint: "bounded-replan",
    requiredTool: {
      name: "unreal_agent_plan",
      args: { request: "Continue bounded source analysis" },
    },
  };
  const control = deriveNextObligation(value);
  assert.equal(control.disposition, "require_tool");
  assert.deepEqual(control.requiredTool, value.recoveryObligation.requiredTool);
  assert.deepEqual(control.allowedTools, ["unreal_agent_plan"]);
  assert.deepEqual(control.retryPolicy, { sameSemanticInput: "once" });
});

test("phase budget checkpoint outranks an open repository audit frontier", () => {
  const value = state();
  value.repoAuditLedger = {
    required: true,
    status: "active",
    queuedTargets: ["Source/Sample/A.cpp"],
    cursor: 0,
    remainingCount: 1,
  };
  value.recoveryObligation = {
    source: "phase_tool_budget",
    status: "phase_budget_checkpoint_required",
    fingerprint: "repo-checkpoint",
    requiredTool: { name: "unreal_task_checkpoint", args: { action: "record" } },
  };
  const control = deriveNextObligation(value);
  assert.deepEqual(control.requiredTool, value.recoveryObligation.requiredTool);
  assert.deepEqual(control.allowedTools, ["unreal_task_checkpoint"]);
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

test("tool-free synthesis rejects stale work with the committed authoritative control", () => {
  const value = state();
  value.toolRoute = {
    routeHash: "route-synthesis-6",
    phase: "synthesis",
    roleSession: "synthesis",
    activeTools: [],
    pendingGates: [],
  };
  value.controlEpoch = 6;
  value.controlState = {
    version: 2,
    authoritative: true,
    epoch: 6,
    taskSessionId: value.taskSessionId,
    routeHash: "route-synthesis-6",
    phase: "synthesis",
    disposition: "continue",
    requiredTool: null,
    allowedTools: [],
    retryPolicy: { sameSemanticInput: "forbidden" },
  };

  const blocked = validateToolRoute(
    value,
    { routeHash: "route-planner-5", routePhase: "planner" },
    { path: "Source/Sample/Feature.h" },
    "read_file",
  );

  assert.equal(blocked.ok, false);
  assert.equal(blocked.errorCode, "TASK_CONTROL_OBLIGATION_REQUIRED");
  assert.equal(blocked.alreadySatisfied, true);
  assert.equal(blocked.reexecutionBlocked, true);
  assert.equal(blocked.controlEpoch, 6);
  assert.deepEqual(blocked.control, value.controlState);
  assert.equal(blocked.nextAction, "use_authoritative_control");
  assert.equal(blocked.nextActionIsTool, false);
});

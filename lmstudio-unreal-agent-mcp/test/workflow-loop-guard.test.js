"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  recordValidationFailure,
  recordValidationSuccess,
  recordBuildGateFailure,
  beginBuildAttempt,
  finishBuildAttempt,
  cancelBuildAttempt,
  recordBuildRecoveryContract,
  recordRecoveryEvidenceCall,
  resetWorkflowLoopGuardForTests,
} = require("../src/workflow-loop-guard");

const validation = {
  findings: [
    {
      severity: "error",
      code: "BLUEPRINT_ASSIGNABLE_DELEGATE_UNDECLARED",
      path: "Source/Demo/StaminaComponent.h",
      line: 20,
      message: "delegate missing",
    },
  ],
};

test.beforeEach(() => resetWorkflowLoopGuardForTests());

test("same validation failure is blocked until mutation generation changes", () => {
  const project = "/tmp/Demo";
  const first = recordValidationFailure(project, 4, validation);
  const repeated = recordValidationFailure(project, 4, validation);
  const afterMutation = recordValidationFailure(project, 5, validation);

  assert.equal(first.blocked, false);
  assert.equal(repeated.blocked, true);
  assert.equal(repeated.reason, "same_validation_failure");
  assert.equal(afterMutation.blocked, false);
});

test("failed override build blocks validation-build alternation", () => {
  const project = "/tmp/Demo";
  recordValidationFailure(project, 8, validation);
  assert.equal(beginBuildAttempt(project, 8).ok, true);
  finishBuildAttempt(project, 8, {
    commandSucceeded: false,
    stderr: "error C2065: undeclared identifier",
  });

  const repeatedValidation = recordValidationFailure(project, 8, validation);
  assert.equal(repeatedValidation.blocked, true);
  assert.equal(repeatedValidation.reason, "build_failed_without_intervening_mutation");
});

test("only one build runs per mutation generation", () => {
  const project = "/tmp/Demo";
  assert.equal(beginBuildAttempt(project, 10).ok, true);
  finishBuildAttempt(project, 10, { commandSucceeded: false, error: "UBT failed" });
  assert.equal(beginBuildAttempt(project, 10).ok, false);
  assert.equal(beginBuildAttempt(project, 11).ok, true);
});

test("environmental build cancellation permits a corrected same-generation retry", () => {
  const project = "/tmp/Demo";
  assert.equal(beginBuildAttempt(project, 10).ok, true);
  cancelBuildAttempt(project, 10);
  assert.equal(beginBuildAttempt(project, 10).ok, true);
});

test("failed build caps evidence reads until a mutation changes generation", () => {
  const project = "/tmp/Demo";
  finishBuildAttempt(project, 20, {
    commandSucceeded: false,
    stderr: "error C2039: missing member",
  });

  for (let index = 1; index <= 3; index += 1) {
    const allowed = recordRecoveryEvidenceCall(project, 20, { budget: 3 });
    assert.equal(allowed.blocked, false);
    assert.equal(allowed.count, index);
  }
  const blocked = recordRecoveryEvidenceCall(project, 20, { budget: 3 });
  assert.equal(blocked.blocked, true);
  assert.equal(blocked.reason, "build_recovery_evidence_budget_exhausted");

  const afterMutation = recordRecoveryEvidenceCall(project, 21, { budget: 3 });
  assert.equal(afterMutation.blocked, false);
  assert.equal(afterMutation.active, false);
});

test("planned recovery evidence has a fresh scope without resetting pre-task limits", () => {
  const project = "/tmp/Demo";
  finishBuildAttempt(project, 21, {
    commandSucceeded: false,
    stderr: "error C2039: missing member",
  });

  for (let index = 0; index < 2; index += 1) {
    assert.equal(recordRecoveryEvidenceCall(project, 21, {
      budget: 2,
      scopeKey: "pre_task",
    }).blocked, false);
  }
  assert.equal(recordRecoveryEvidenceCall(project, 21, {
    budget: 2,
    scopeKey: "pre_task",
  }).blocked, true);

  const planned = recordRecoveryEvidenceCall(project, 21, {
    budget: 2,
    scopeKey: "task_12345678",
  });
  assert.equal(planned.blocked, false);
  assert.equal(planned.count, 1);
  assert.equal(planned.scopeKey, "task_12345678");
});

test("successful build does not activate the recovery evidence budget", () => {
  const project = "/tmp/Demo";
  finishBuildAttempt(project, 30, { commandSucceeded: true });
  for (let index = 0; index < 10; index += 1) {
    assert.equal(recordRecoveryEvidenceCall(project, 30, { budget: 2 }).blocked, false);
  }
});

test("failed build enforces the exact first-error range before other reads", () => {
  const project = "/tmp/Demo";
  finishBuildAttempt(project, 31, {
    commandSucceeded: false,
    stderr: "Source/Demo/Foo.cpp:40:2: error: bad call",
  });
  recordBuildRecoveryContract(project, 31, {
    targetFile: "Source/Demo/Foo.cpp",
    requiredNextTool: "read_file_range",
    requiredNextToolArgs: {
      path: "Source/Demo/Foo.cpp",
      startLine: 25,
      endLine: 55,
    },
  });

  const wrongTool = recordRecoveryEvidenceCall(project, 31, {
    budget: 8,
    tool: "read_file",
    fileAbsPath: "/tmp/Demo/Source/Demo/Foo.cpp",
  });
  assert.equal(wrongTool.blocked, true);
  assert.equal(wrongTool.reason, "build_recovery_required_tool_mismatch");
  assert.equal(wrongTool.count, 0);

  const wrongFile = recordRecoveryEvidenceCall(project, 31, {
    budget: 8,
    tool: "read_file_range",
    fileAbsPath: "/tmp/Demo/Source/Demo/Bar.cpp",
  });
  assert.equal(wrongFile.blocked, true);
  assert.equal(wrongFile.reason, "build_recovery_target_mismatch");

  const exact = recordRecoveryEvidenceCall(project, 31, {
    budget: 8,
    tool: "read_file_range",
    fileAbsPath: "/tmp/Demo/Source/Demo/Foo.cpp",
  });
  assert.equal(exact.blocked, false);
  assert.equal(exact.count, 1);

  const extra = recordRecoveryEvidenceCall(project, 31, {
    budget: 8,
    tool: "read_file",
    fileAbsPath: "/tmp/Demo/Source/Demo/Bar.cpp",
  });
  assert.equal(extra.blocked, true);
  assert.equal(extra.reason, "build_recovery_evidence_complete");
});

test("validation success clears a prior validation fingerprint", () => {
  const project = "/tmp/Demo";
  recordValidationFailure(project, 12, validation);
  recordValidationSuccess(project, 12);
  const later = recordValidationFailure(project, 12, validation);

  assert.equal(later.blocked, false);
});

test("same pre-build gate failure is blocked on the second call", () => {
  const project = "/tmp/Demo";
  const first = recordBuildGateFailure(project, 14, "VALIDATION_PROOF_STALE");
  const repeated = recordBuildGateFailure(project, 14, "VALIDATION_PROOF_STALE");
  const differentGate = recordBuildGateFailure(project, 14, "VALIDATION_REQUIRED");
  const afterMutation = recordBuildGateFailure(project, 15, "VALIDATION_PROOF_STALE");

  assert.equal(first.blocked, false);
  assert.equal(repeated.blocked, true);
  assert.equal(repeated.reason, "same_build_gate_failure");
  assert.equal(differentGate.blocked, false);
  assert.equal(afterMutation.blocked, false);
});

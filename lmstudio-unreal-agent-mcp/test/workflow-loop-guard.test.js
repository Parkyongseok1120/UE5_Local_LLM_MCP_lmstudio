"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  recordValidationFailure,
  recordValidationSuccess,
  recordBuildGateFailure,
  beginBuildAttempt,
  finishBuildAttempt,
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

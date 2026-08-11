"use strict";

const test = require("node:test");
const assert = require("node:assert");

const {
  POLICY,
  SAME_CALL_RETRY_CODES,
  REDIRECT_CODES,
  RECOVERY_ACTION_CODES,
  recoveryAction,
} = require("../src/route-recovery-policy");

test("route recovery policy has one action for every recovery code", () => {
  assert.deepStrictEqual(
    new Set(Object.keys(POLICY.defaultActions)),
    RECOVERY_ACTION_CODES
  );
});

test("route recovery policy classifications are loaded from shared JSON", () => {
  assert.deepStrictEqual(SAME_CALL_RETRY_CODES, new Set(POLICY.sameCallRetryCodes));
  assert.deepStrictEqual(REDIRECT_CODES, new Set(POLICY.redirectCodes));
});

test("auth mismatch routes to the same executable action as Python", () => {
  assert.deepStrictEqual(
    recoveryAction("TASK_AUTH_MISMATCH"),
    { action: "unreal_agent_plan", isTool: true }
  );
});

test("unknown recovery code uses the stable fallback", () => {
  assert.deepStrictEqual(
    recoveryAction("SOMETHING_NEW"),
    { action: "unreal_task_list_active", isTool: true }
  );
});

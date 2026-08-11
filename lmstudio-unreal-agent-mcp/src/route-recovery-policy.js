"use strict";

const fs = require("fs");
const path = require("path");

const POLICY_PATH = path.resolve(
  __dirname,
  "../../config/task_route_recovery_policy.json"
);

function loadRouteRecoveryPolicy() {
  const policy = JSON.parse(fs.readFileSync(POLICY_PATH, "utf8"));
  if (!Number.isInteger(policy.version) || policy.version < 1) {
    throw new Error("task route recovery policy version is missing");
  }
  if (!policy.defaultActions || typeof policy.defaultActions !== "object") {
    throw new Error("task route recovery defaultActions must be an object");
  }
  return policy;
}

const POLICY = loadRouteRecoveryPolicy();
const SAME_CALL_RETRY_CODES = new Set(POLICY.sameCallRetryCodes || []);
const REDIRECT_CODES = new Set(POLICY.redirectCodes || []);
const RECOVERY_ACTION_CODES = new Set(POLICY.recoveryActionCodes || []);

function recoveryAction(errorCode = "") {
  const selected = POLICY.defaultActions[String(errorCode || "")]
    || POLICY.fallbackAction
    || { action: "unreal_task_list_active", isTool: true };
  return {
    action: String(selected.action || "unreal_task_list_active"),
    isTool: Boolean(selected.isTool),
  };
}

module.exports = {
  POLICY,
  POLICY_PATH,
  SAME_CALL_RETRY_CODES,
  REDIRECT_CODES,
  RECOVERY_ACTION_CODES,
  recoveryAction,
};

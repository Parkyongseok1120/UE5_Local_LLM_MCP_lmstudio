"use strict";

const { looksLikeToolAction } = require("./control-envelope.js");
const {
  SAME_CALL_RETRY_CODES,
  REDIRECT_CODES,
  recoveryAction,
} = require("./route-recovery-policy.js");

function routeAuthorizationFailureOptions(result = {}, toolName = "") {
  const errorCode = String(result.errorCode || "TASK_ROUTE_AUTH_FAILED");
  const sameCallRetry = SAME_CALL_RETRY_CODES.has(errorCode);
  const routeRedirect = REDIRECT_CODES.has(errorCode);
  let nextAction = String(result.nextAction || "").trim();
  if (!nextAction && errorCode === "TASK_TOOL_NOT_ACTIVE") {
    const route = result.toolRoute && typeof result.toolRoute === "object"
      ? result.toolRoute
      : {};
    const pending = Array.isArray(route.pendingGates)
      ? route.pendingGates.map(String).filter(Boolean)
      : [];
    const active = Array.isArray(route.activeTools)
      ? route.activeTools.map(String).filter(Boolean)
      : [];
    nextAction = pending[0] || active[0] || "unreal_task_checkpoint";
  }
  const policyRecovery = recoveryAction(errorCode);
  if (!nextAction) nextAction = policyRecovery.action;
  const nextActionIsPolicyRecovery = Boolean(
    nextAction && nextAction === String(policyRecovery.action || "")
  );
  const advertisedActions = Array.isArray(result.nextActions)
    ? result.nextActions.map(String).filter(Boolean)
    : [];
  const recoveryActionRequired = Boolean(nextAction || advertisedActions.length);
  const canContinueWorkflow = sameCallRetry || recoveryActionRequired;
  const nextActionIsTool = nextActionIsPolicyRecovery
    ? policyRecovery.isTool
    : result.nextActionIsTool === undefined
      ? looksLikeToolAction(nextAction)
      : Boolean(result.nextActionIsTool);
  const instruction = errorCode === "TASK_PHASE_TOOL_BUDGET_EXHAUSTED"
    ? "Do not retry the budgeted work tool. Call unreal_task_checkpoint with nextActionArgs exactly as returned (action=record); action=status does not renew the budget. Continue requiredNextAction with the returned taskAuthorization."
    : errorCode === "TASK_AUTH_INVALID_FORMAT" || errorCode === "TASK_STATE_MISSING"
      ? "The supplied taskAuthorization was not server-issued or no longer exists. Never fabricate authorization. Call unreal_agent_plan once with the original request, then continue the returned route."
      : errorCode === "TASK_ROUTE_OWNERSHIP_REQUIRED"
        ? "Retry the same tool once with the complete taskAuthorization previously returned by unreal_agent_plan, a successful gate, or a continuity checkpoint. Do not recover, cancel, or create another task."
        : sameCallRetry
          ? "Retry the same tool once using the complete server-issued taskAuthorization returned by the latest response."
          : recoveryActionRequired && nextActionIsTool
            ? `Do not retry ${String(toolName || "the blocked tool")}. Call ${nextAction || advertisedActions[0]} and continue the same user workflow.`
            : recoveryActionRequired
              ? `Do not retry ${String(toolName || "the blocked tool")}. Follow the routing instruction '${nextAction || advertisedActions[0]}'; it is not a tool name and must not be called.`
              : "Stop the current workflow and report the exact routing integrity failure.";
  return {
    errorCode,
    ...(result.taskSessionId ? { taskSessionId: String(result.taskSessionId) } : {}),
    ...(Number.isInteger(Number(result.controlEpoch))
      ? { controlEpoch: Math.max(0, Number(result.controlEpoch)) }
      : {}),
    retryable: sameCallRetry || routeRedirect,
    stopCurrentWorkflow: !canContinueWorkflow,
    recoveryActionRequired,
    taskAuthorizationSource: "server_only",
    doNotFabricateTaskAuthorization: true,
    ...(toolName && !sameCallRetry ? { doNotRetry: [String(toolName)] } : {}),
    ...(nextAction ? { nextAction } : {}),
    nextActionIsTool,
    ...(result.nextActionArgs && typeof result.nextActionArgs === "object"
      ? { nextActionArgs: result.nextActionArgs }
      : {}),
    ...(advertisedActions.length ? { nextActions: advertisedActions } : {}),
    ...(result.taskAuthorization ? { taskAuthorization: result.taskAuthorization } : {}),
    ...(result.toolRoute ? { toolRoute: result.toolRoute } : {}),
    ...(result.toolRouteUsage ? { toolRouteUsage: result.toolRouteUsage } : {}),
    // A transaction-committed authoritative v2 envelope must cross the MCP
    // response adapter byte-for-byte. Reconstructing it from legacy recovery
    // fields can create a different semantic control at the same epoch.
    ...(result.control && typeof result.control === "object"
      ? { control: { ...result.control } }
      : {}),
    agentInstruction: instruction,
  };
}

module.exports = { routeAuthorizationFailureOptions };

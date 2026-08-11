"use strict";

const crypto = require("crypto");

function actionName(value) {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return String(value.name || value.tool || "");
  }
  return String(value || "");
}

function blockerFingerprint(payload) {
  if (payload.blockerFingerprint) return String(payload.blockerFingerprint);
  const material = {
    errorCode: payload.errorCode,
    blockers: payload.blockers || payload.firstBlocker,
    missing: payload.missingFields || payload.missingJsonPaths,
  };
  if (Object.values(material).every((value) => value == null || value === "")) return "";
  return crypto.createHash("sha256")
    .update(JSON.stringify(material))
    .digest("hex")
    .slice(0, 24);
}

function attachControlEnvelope(payload, toolName = "") {
  const result = { ...(payload || {}) };
  const existing = result.control && typeof result.control === "object" ? result.control : {};
  const taskAuthorization = result.taskAuthorization && typeof result.taskAuthorization === "object"
    ? result.taskAuthorization
    : {};
  const nextAction = actionName(
    result.nextAction || result.requiredNextTool || result.requiredNextAction || ""
  );
  const nextActionIsTool = Object.prototype.hasOwnProperty.call(result, "nextActionIsTool")
    ? Boolean(result.nextActionIsTool)
    : Boolean(result.requiredNextTool);
  const status = String(existing.status || (
    result.ok === false || result.writeGateClosed === true
      ? "Blocked"
      : nextAction ? "NeedsAction" : "Completed"
  ));
  const retryPolicy = result.doNotRetryUnchanged || result.retryable === false
    ? "forbidden"
    : result.retryable === true ? "once" : "none";
  const control = {
    version: 1,
    taskId: String(taskAuthorization.taskSessionId || result.taskSessionId || ""),
    phase: String(existing.phase || toolName || result.phase || "Unknown"),
    status,
    nextAction,
    nextActionIsTool,
    retryPolicy,
    blockerFingerprint: blockerFingerprint(result),
    continuationToken: String(existing.continuationToken || result.proposalRevision || ""),
  };
  result.control = Object.fromEntries(
    Object.entries(control).filter(([, value]) => value !== "")
  );
  return result;
}

function conciseControlText(payload) {
  const control = payload.control && typeof payload.control === "object" ? payload.control : {};
  const ok = payload.ok !== false;
  const phase = String(control.phase || payload.tool || "tool");
  const status = String(control.status || (ok ? "Completed" : "Blocked"));
  const errorCode = String(payload.errorCode || "");
  const lines = [`${ok ? "OK" : "FAILED"} [${phase}] ${status}${errorCode ? ` (${errorCode})` : ""}`];
  const summary = String(
    payload.verdictSummary
      || payload.summary
      || payload.userMessage
      || payload.message
      || payload.error
      || ""
  ).trim();
  if (summary) lines.push(summary.slice(0, 800));
  if (control.nextAction) {
    lines.push(`nextAction=${control.nextAction} (tool=${Boolean(control.nextActionIsTool)})`);
  }
  lines.push("Detailed result is available in structuredContent.control and structuredContent data.");
  return lines.join("\n");
}

module.exports = { attachControlEnvelope, conciseControlText };

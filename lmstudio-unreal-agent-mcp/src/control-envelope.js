"use strict";

const crypto = require("crypto");

const CONTROL_VERSION = 2;

function actionName(value) {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return String(value.name || value.tool || "");
  }
  return String(value || "");
}

function looksLikeToolAction(value) {
  const name = actionName(value).trim();
  return /^(?:unreal_|get_|set_|open_|read_|write_|replace_|apply_|delete_|build_|run_|search_|list_|detect_|record_|cancel_|quarantine_|static_|refactor_|propose_)[a-z0-9_]*(?::[a-z0-9_-]+)?$/i.test(name);
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
  if (Number(existing.version || 0) >= CONTROL_VERSION && existing.authoritative === true) {
    // The task-state transaction already committed this semantic envelope.
    // Node response adapters forward it without mining legacy action fields.
    result.control = { ...existing };
    return result;
  }
  // Never synthesize a v2 semantic control from legacy response fields here.
  // The Python reducer is the sole production owner of v2 phase/disposition/
  // required-tool decisions. Missing or non-authoritative control is projected
  // only as a diagnostic v1 envelope below and cannot drive task routing.
  const taskAuthorization = result.taskAuthorization && typeof result.taskAuthorization === "object"
    ? result.taskAuthorization
    : {};
  const taskState = result.state && typeof result.state === "object" ? result.state : {};
  const nonAuthoritativeTaskId = String(
    taskAuthorization.taskSessionId
      || result.taskSessionId
      || taskState.taskSessionId
      || existing.taskSessionId
      || ""
  ).trim();
  if (nonAuthoritativeTaskId) {
    result.control = {
      version: 1,
      taskId: nonAuthoritativeTaskId,
      phase: String(toolName || result.phase || "task_control_missing"),
      status: "MissingAuthoritativeControl",
      nextAction: "",
      nextActionIsTool: false,
      retryPolicy: "forbidden",
      blockerFingerprint: blockerFingerprint(result),
    };
    return result;
  }
  const hasDirectAction = ["nextAction", "requiredNextTool", "requiredNextAction"]
    .some((key) => Object.prototype.hasOwnProperty.call(result, key));
  const nextAction = actionName(hasDirectAction
    ? (result.nextAction || result.requiredNextTool || result.requiredNextAction || "")
    : existing.nextAction || "");
  const nextActionIsTool = Object.prototype.hasOwnProperty.call(result, "nextActionIsTool")
    ? Boolean(result.nextActionIsTool)
    : Object.prototype.hasOwnProperty.call(result, "requiredNextTool")
      ? Boolean(result.requiredNextTool)
      : hasDirectAction
        ? looksLikeToolAction(nextAction)
        : existing.nextActionIsTool === true;
  const status = String(existing.status || (
    result.ok === false || result.writeGateClosed === true
      ? "Blocked"
      : nextAction ? "NeedsAction" : "Completed"
  ));
  const retryPolicy = (
    Object.prototype.hasOwnProperty.call(result, "doNotRetryUnchanged")
    || Object.prototype.hasOwnProperty.call(result, "retryable")
  )
    ? (result.doNotRetryUnchanged || result.retryable === false
      ? "forbidden"
      : result.retryable === true ? "once" : "none")
    : String(existing.retryPolicy || "none");
  const fingerprint = blockerFingerprint(result) || String(existing.blockerFingerprint || "");
  const control = {
    version: 1,
    taskId: String(taskAuthorization.taskSessionId || result.taskSessionId || existing.taskId || ""),
    phase: String(existing.phase || toolName || result.phase || "Unknown"),
    status,
    nextAction,
    nextActionIsTool,
    retryPolicy,
    blockerFingerprint: fingerprint,
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
  const status = String(control.disposition || control.status || (ok ? "Completed" : "Blocked"));
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
  const nextAction = String(control.requiredTool?.name || control.nextAction || "");
  if (nextAction) {
    lines.push(`nextAction=${nextAction} (tool=${Boolean(control.requiredTool || control.nextActionIsTool)})`);
  }
  lines.push("Detailed result is available in structuredContent.control and structuredContent data.");
  return lines.join("\n");
}

function modelVisibleControlText(
  payload,
  frontend = process.env.MCP_FRONTEND || "",
  maxChars = 32_000
) {
  if (String(frontend || "").trim().toLowerCase() !== "lmstudio") {
    return conciseControlText(payload);
  }

  const budget = Math.max(2_000, Math.min(Number(maxChars) || 32_000, 32_000));
  const rendered = JSON.stringify(payload, null, 2);
  if (rendered.length <= budget) return rendered;

  const projectRows = (rows, limit) => Array.isArray(rows) ? rows.slice(0, limit) : undefined;
  const boundedControl = payload.control && typeof payload.control === "object"
    ? Object.fromEntries(Object.entries(payload.control).map(([key, value]) => [
      key,
      typeof value === "string" ? value.slice(0, 500) : value,
    ]))
    : payload.control;
  const fallback = {
    ok: payload.ok,
    errorCode: payload.errorCode,
    control: boundedControl,
    summary: payload.summary || payload.message || payload.error,
    error: payload.error,
    path: payload.path,
    entries: projectRows(payload.entries, 80),
    fileNameResults: projectRows(payload.fileNameResults, 80),
    results: projectRows(payload.results, 40),
    retryable: payload.retryable,
    doNotRetry: payload.doNotRetry,
    doNotRetryTools: payload.doNotRetryTools,
    stopCurrentWorkflow: payload.stopCurrentWorkflow,
    stopCurrentPhase: payload.stopCurrentPhase,
    phaseBoundary: payload.phaseBoundary,
    agentInstruction: payload.agentInstruction,
    requiredNextTool: payload.requiredNextTool,
    requiredNextToolArgs: payload.requiredNextToolArgs,
    nextAction: payload.nextAction,
    nextActionArgs: payload.nextActionArgs,
    nextSteps: projectRows(payload.nextSteps, 5),
    suggestedToolCalls: projectRows(payload.suggestedToolCalls, 3),
    _textFallbackTruncated: true,
  };
  const cleaned = Object.fromEntries(
    Object.entries(fallback).filter(([, value]) => (
      value !== undefined && value !== null && value !== ""
      && (!Array.isArray(value) || value.length > 0)
    ))
  );
  let compact = JSON.stringify(cleaned, null, 2);
  if (compact.length <= budget) return compact;

  cleaned.entries = projectRows(cleaned.entries, 15);
  cleaned.fileNameResults = projectRows(cleaned.fileNameResults, 20);
  cleaned.results = projectRows(cleaned.results, 10);
  if (typeof cleaned.error === "string") cleaned.error = cleaned.error.slice(0, 1_000);
  if (typeof cleaned.summary === "string") cleaned.summary = cleaned.summary.slice(0, 1_000);
  if (typeof cleaned.agentInstruction === "string") {
    cleaned.agentInstruction = cleaned.agentInstruction.slice(0, 1_500);
  }
  compact = JSON.stringify(cleaned, null, 2);
  if (compact.length <= budget) return compact;

  delete cleaned.entries;
  delete cleaned.results;
  delete cleaned.fileNameResults;
  compact = JSON.stringify(cleaned, null, 2);
  if (compact.length <= budget) return compact;

  const minimalControl = boundedControl && typeof boundedControl === "object"
    ? Object.fromEntries([
      "version", "epoch", "taskSessionId", "routeHash", "phase", "disposition",
      "requiredTool", "allowedTools", "retryPolicy", "blocker", "taskId", "status",
      "nextAction", "nextActionIsTool", "blockerFingerprint", "continuationToken",
    ].filter((key) => boundedControl[key] !== undefined && boundedControl[key] !== "")
      .map((key) => [
        key,
        typeof boundedControl[key] === "string"
          ? boundedControl[key].slice(0, 200)
          : boundedControl[key],
      ]))
    : boundedControl;
  return JSON.stringify({
    ok: payload.ok,
    errorCode: String(payload.errorCode || "").slice(0, 200) || undefined,
    control: minimalControl,
    retryable: payload.retryable,
    doNotRetry: payload.doNotRetry,
    doNotRetryTools: payload.doNotRetryTools,
    stopCurrentWorkflow: payload.stopCurrentWorkflow,
    stopCurrentPhase: payload.stopCurrentPhase,
    phaseBoundary: payload.phaseBoundary,
    agentInstruction: String(payload.agentInstruction || "").slice(0, 600) || undefined,
    _textFallbackTruncated: true,
  }, null, 2);
}

module.exports = {
  attachControlEnvelope,
  conciseControlText,
  looksLikeToolAction,
  modelVisibleControlText,
};

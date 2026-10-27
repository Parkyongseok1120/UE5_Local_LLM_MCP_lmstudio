"use strict";

// Pure interpretation of returned data. This module owns neither execution
// authority nor input availability; callers supply trusted provider identity.
function resultSemantics(value, provider = "") {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {
    delivery: "unknown", operationStatus: "unknown", effect: "unknown", observation: "unknown", executionState: "unknown",
  };
  const status = String(value.status || "").toLowerCase();
  const kind = String(value.kind || "");
  const observation = ["archived_tool_result_projection", "historical_evidence_index", "historical_evidence_range"].includes(kind)
    ? "historical" : kind === "no_new_information" || status === "no_new_information" || value.no_new_information === true
      ? "reused" : "returned";
  let operationStatus = "unknown", delivery = "returned", effect = "unknown";
  // A timeout/partial publication cannot be demoted to an ordinary failure:
  // effects may already exist. A receipt alone says nothing about freshness.
  if (status === "outcome_unknown" || value.timedOut === true) {
    operationStatus = "outcome_unknown";
    if (status === "outcome_unknown") delivery = "transport_error";
  } else if (["partially_applied", "partial"].includes(status)) {
    operationStatus = "outcome_unknown"; effect = "partial";
  } else if (["canceled", "cancelled"].includes(status)) {
    operationStatus = "canceled"; delivery = "canceled";
  } else if (status === "accepted") operationStatus = "accepted";
  else if (["pending", "running", "compiling", "queued", "preparing", "cancel_requested"].includes(status)) operationStatus = "pending";
  else if (value.envelopeIsError === true || value.ok === false || value.resultStatus === false || value.errorCode
    || value.error || ["failed", "error", "not_applied", "denied", "timeout", "timed_out"].includes(status)) {
    operationStatus = "failed";
    if (["not_applied", "denied"].includes(status)) effect = "not_applied";
  } else if (value.ok === true || value.resultStatus === true || ["completed", "applied", "saved", "observed", "no_change"].includes(status)) {
    operationStatus = "completed";
  }
  if (provider === "mcp/unity-tools") {
    if (["applied", "saved"].includes(status) && value.verification !== "changed_after_write")
      effect = value.changed === false ? "no_change" : "applied";
  } else if (provider === "mcp/unreal-agent" && value.ok === true
    && ["written", "created", "modified", "deleted", "bundle_applied", "replaced"].includes(value.operation)) {
    effect = value.changed === false ? "no_change" : "applied";
  }
  return { delivery, operationStatus, effect, observation,
    executionState: operationStatus === "completed" ? "succeeded" : operationStatus };
}

// Qualified effects survive compaction. No byte hashes/hunks are manufactured
// for Editor objects; origin and current lookup delivery remain separate.
function retainedEffectFacts(value) {
  const out = {};
  for (const key of ["operationId", "target", "origin", "delivery", "editorSessionId", "domainGeneration",
    "changed", "saved", "dirty", "import", "compilation", "verification", "compilationId", "compilationIdAtRequest",
    "currentStatus", "reason", "sourceAssemblyVerification", "playerBuildVerification", "scope", "includedExistingDirty",
    "sideEffects", "undoAttempted", "undoCompleted", "journalPersistence", "testStatus", "passed", "failed", "skipped",
    "inconclusive", "selectedCount", "domainGenerationAtRequest", "filter", "testMode"])
    if (value?.[key] !== undefined) out[key] = value[key];
  return out;
}
// Reuse the bounded, ambiguity-rejecting transport decoder. Lazy loading keeps
// the compaction memory's existing semantics dependency acyclic at load time.
function decodeEnvelope(result) {
  const { value, error } = require("./compaction-tool-memory.js").decodeToolResultRecord(result);
  if (!value) return { ok: false, errorCode: "INVALID_RESULT_ENVELOPE", message: error || "Invalid result",
    retry: { allowed: false, mode: "none" } };
  const bareSdkError = typeof value.error === "string" && Object.keys(value).every(k => k === "error" || k === "envelopeIsError");
  const rawCode = value.errorCode || value.errorcode || value.error_code || value.code;
  const failed = resultSemantics(value).operationStatus === "failed" || Boolean(rawCode);
  const errorCode = failed ? String(rawCode || (bareSdkError ? "HARNESS_BUDGET_DENIED" : "TOOL_FAILED")).toUpperCase() : undefined;
  const retry = value.retry && typeof value.retry === "object" ? value.retry : {};
  const modes = ["none", "same_arguments", "different_arguments", "reduce_scope", "after_state_change"];
  const allowed = failed && retry.allowed === true && modes.includes(retry.mode) && retry.mode !== "none";
  const minimum = retry.minimum;
  const validMinimum = allowed && minimum && ["maxChars", "byteBudget", "maxBytes"].includes(minimum.field)
    && Number.isSafeInteger(minimum.value) && minimum.value > 0;
  return { ok: !failed && resultSemantics(value).operationStatus === "completed", errorCode,
    message: String(value.message || value.error || "").slice(0, 4000),
    retry: { allowed, mode: allowed ? retry.mode : "none", ...(validMinimum ? { minimum: { field: minimum.field, value: minimum.value } } : {}) },
    // A bare SDK error is not proof of budget denial. The guard trace supplies
    // that authority (the same SDK envelope also carries scope/user denials).
    sdkDenial: bareSdkError, value };
}
module.exports = { resultSemantics, retainedEffectFacts, decodeEnvelope };

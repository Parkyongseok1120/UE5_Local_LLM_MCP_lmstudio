"use strict";

const crypto = require("crypto");
const path = require("path");

const projectStates = new Map();

function projectKey(projectRoot) {
  const resolved = path.resolve(String(projectRoot || "."));
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

function generationNumber(value) {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function stateFor(projectRoot, mutationGeneration) {
  const key = projectKey(projectRoot);
  const generation = generationNumber(mutationGeneration);
  let state = projectStates.get(key);
  if (!state || state.mutationGeneration !== generation) {
    state = {
      mutationGeneration: generation,
      validationFingerprint: "",
      validationFailureCount: 0,
      buildGateFailureCode: "",
      buildGateFailureCount: 0,
      buildAttempted: false,
      buildFailed: false,
      buildFingerprint: "",
    };
    projectStates.set(key, state);
  }
  return state;
}

function hashParts(parts) {
  return crypto.createHash("sha256").update(JSON.stringify(parts)).digest("hex").slice(0, 20);
}

function validationFingerprint(validation) {
  const errors = (validation?.findings || [])
    .filter((finding) => String(finding?.severity || "").toLowerCase() === "error")
    .slice(0, 12)
    .map((finding) => [finding.code || "", finding.path || "", finding.line || 0, finding.message || ""]);
  return hashParts(errors);
}

function buildFingerprint(outcome) {
  const combined = `${outcome?.errorCode || ""}\n${outcome?.error || ""}\n${outcome?.stderr || ""}\n${outcome?.stdout || ""}`;
  const actionable = combined
    .split(/\r?\n/)
    .filter((line) => /(?:error\s+[A-Z]?\d+|fatal error|error:|UnrealHeaderTool failed|BUILD_)/i.test(line))
    .slice(0, 12);
  return hashParts(actionable.length ? actionable : [combined.slice(0, 4000)]);
}

function recordValidationFailure(projectRoot, mutationGeneration, validation) {
  const state = stateFor(projectRoot, mutationGeneration);
  const fingerprint = validationFingerprint(validation);
  const repeated = state.validationFailureCount > 0 && state.validationFingerprint === fingerprint;
  state.validationFingerprint = fingerprint;
  state.validationFailureCount += 1;
  return {
    blocked: repeated || state.buildFailed,
    reason: state.buildFailed ? "build_failed_without_intervening_mutation" : repeated ? "same_validation_failure" : "",
    mutationGeneration: state.mutationGeneration,
    fingerprint,
  };
}

function recordValidationSuccess(projectRoot, mutationGeneration) {
  const state = stateFor(projectRoot, mutationGeneration);
  state.validationFingerprint = "";
  state.validationFailureCount = 0;
  return state;
}

function recordBuildGateFailure(projectRoot, mutationGeneration, errorCode) {
  const state = stateFor(projectRoot, mutationGeneration);
  const normalizedCode = String(errorCode || "BUILD_GATE_FAILED");
  const repeated = state.buildGateFailureCount > 0 && state.buildGateFailureCode === normalizedCode;
  state.buildGateFailureCode = normalizedCode;
  state.buildGateFailureCount = repeated ? state.buildGateFailureCount + 1 : 1;
  return {
    blocked: repeated,
    reason: repeated ? "same_build_gate_failure" : "",
    errorCode: normalizedCode,
    mutationGeneration: state.mutationGeneration,
  };
}

function beginBuildAttempt(projectRoot, mutationGeneration) {
  const state = stateFor(projectRoot, mutationGeneration);
  if (state.buildAttempted) {
    return {
      ok: false,
      reason: "build_already_attempted_without_intervening_mutation",
      mutationGeneration: state.mutationGeneration,
      buildFingerprint: state.buildFingerprint,
    };
  }
  state.buildAttempted = true;
  return { ok: true, mutationGeneration: state.mutationGeneration };
}

function finishBuildAttempt(projectRoot, mutationGeneration, outcome) {
  const state = stateFor(projectRoot, mutationGeneration);
  state.buildAttempted = true;
  state.buildFailed = outcome?.commandSucceeded !== true;
  state.buildFingerprint = state.buildFailed ? buildFingerprint(outcome) : "";
  return state;
}

function resetWorkflowLoopGuardForTests() {
  projectStates.clear();
}

module.exports = {
  validationFingerprint,
  buildFingerprint,
  recordValidationFailure,
  recordValidationSuccess,
  recordBuildGateFailure,
  beginBuildAttempt,
  finishBuildAttempt,
  resetWorkflowLoopGuardForTests,
};

"use strict";

const crypto = require("crypto");
const {
  absolutePathIdentity,
  filesystemPathIdentity,
  pathHasSuffixIdentity,
} = require("./filesystem-path-identity");

const projectStates = new Map();

function projectKey(projectRoot, hostPlatform = process.platform) {
  return absolutePathIdentity(projectRoot, hostPlatform);
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
      buildRecoveryContract: null,
      recoveryEvidenceByScope: new Map(),
      recoveryEvidencePrechecks: new Map(),
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
  state.buildRecoveryContract = null;
  state.recoveryEvidenceByScope = new Map();
  state.recoveryEvidencePrechecks = new Map();
  return state;
}

function cancelBuildAttempt(projectRoot, mutationGeneration) {
  const state = stateFor(projectRoot, mutationGeneration);
  state.buildAttempted = false;
  state.buildFailed = false;
  state.buildFingerprint = "";
  return state;
}

function recordBuildRecoveryContract(projectRoot, mutationGeneration, recovery) {
  const state = stateFor(projectRoot, mutationGeneration);
  if (!state.buildFailed || !recovery || typeof recovery !== "object") {
    state.buildRecoveryContract = null;
    return null;
  }
  state.buildRecoveryContract = {
    requiredNextTool: String(recovery.requiredNextTool || ""),
    requiredNextToolArgs: recovery.requiredNextToolArgs && typeof recovery.requiredNextToolArgs === "object"
      ? { ...recovery.requiredNextToolArgs }
      : {},
    targetFile: String(recovery.targetFile || "").replace(/\\/g, "/"),
    evidenceSatisfied: false,
  };
  state.recoveryEvidencePrechecks = new Map();
  return { ...state.buildRecoveryContract };
}

function recoveryArgsMatch(expectedValue, actualValue, hostPlatform = process.platform) {
  const expected = expectedValue && typeof expectedValue === "object"
    ? expectedValue
    : {};
  const actual = actualValue && typeof actualValue === "object"
    ? actualValue
    : {};
  return Object.entries(expected).every(([key, value]) => {
    // Authorization is a transport lease and is validated by task auth. It is
    // not part of the compiler diagnostic's semantic evidence identity.
    if (key === "taskAuthorization" || key === "task_authorization") return true;
    const supplied = actual[key];
    if (key === "path") {
      return filesystemPathIdentity(supplied, hostPlatform, { trimOuterSlashes: true })
        === filesystemPathIdentity(value, hostPlatform, { trimOuterSlashes: true });
    }
    if (typeof value === "number") return Number(supplied) === value;
    return JSON.stringify(supplied) === JSON.stringify(value);
  });
}

function recoveryBudget(options = {}) {
  const parsed = Number(options.budget);
  return Number.isFinite(parsed) ? Math.max(1, Math.floor(parsed)) : 5;
}

function normalizedRecoveryArgs(value, hostPlatform) {
  const input = value && typeof value === "object" ? value : {};
  const output = {};
  for (const key of Object.keys(input).sort()) {
    if (key === "taskAuthorization" || key === "task_authorization") continue;
    const item = input[key];
    output[key] = key === "path"
      ? filesystemPathIdentity(item, hostPlatform, { trimOuterSlashes: true })
      : item;
  }
  return output;
}

function recoveryEvidenceIdentity(options, hostPlatform) {
  return hashParts([
    String(options.tool || ""),
    filesystemPathIdentity(options.fileAbsPath || "", hostPlatform, {
      stripProjectUri: false,
    }),
    normalizedRecoveryArgs(options.toolArgs, hostPlatform),
  ]);
}

function validateRecoveryContract(state, contract, options, budget, hostPlatform) {
  if (!contract?.requiredNextTool) return null;
  if (contract.evidenceSatisfied) {
    return {
      blocked: true,
      active: true,
      count: 1,
      budget,
      reason: "build_recovery_evidence_complete",
      buildFingerprint: state.buildFingerprint,
      recoveryContract: { ...contract },
    };
  }
  const requestedTool = String(options.tool || "");
  if (requestedTool !== String(contract.requiredNextTool || "")) {
    return {
      blocked: true,
      active: true,
      count: 0,
      budget,
      reason: "build_recovery_required_tool_mismatch",
      buildFingerprint: state.buildFingerprint,
      recoveryContract: { ...contract },
    };
  }
  const requestedFile = String(options.fileAbsPath || "");
  const targetFile = String(contract.targetFile || "");
  if (targetFile && !pathHasSuffixIdentity(requestedFile, targetFile, hostPlatform)) {
    return {
      blocked: true,
      active: true,
      count: 0,
      budget,
      reason: "build_recovery_target_mismatch",
      buildFingerprint: state.buildFingerprint,
      recoveryContract: { ...contract },
    };
  }
  if (!recoveryArgsMatch(contract.requiredNextToolArgs, options.toolArgs, hostPlatform)) {
    return {
      blocked: true,
      active: true,
      count: 0,
      budget,
      reason: "build_recovery_required_args_mismatch",
      buildFingerprint: state.buildFingerprint,
      recoveryContract: { ...contract },
    };
  }
  return null;
}

function commitRecoveryEvidence(state, contract, options) {
  const evidenceByScope = state.recoveryEvidenceByScope instanceof Map
    ? state.recoveryEvidenceByScope
    : new Map();
  state.recoveryEvidenceByScope = evidenceByScope;
  const count = Number(evidenceByScope.get(options.scopeKey) || 0);
  if (count >= options.budget) {
    return {
      blocked: true,
      active: true,
      count,
      budget: options.budget,
      scopeKey: options.scopeKey,
      reason: "build_recovery_evidence_budget_exhausted",
      buildFingerprint: state.buildFingerprint,
    };
  }
  const nextCount = count + 1;
  evidenceByScope.set(options.scopeKey, nextCount);
  if (contract?.requiredNextTool) contract.evidenceSatisfied = true;
  return {
    blocked: false,
    active: true,
    count: nextCount,
    budget: options.budget,
    scopeKey: options.scopeKey,
    buildFingerprint: state.buildFingerprint,
    recoveryContract: contract ? { ...contract } : null,
  };
}

/**
 * Limit source-evidence wandering after a failed build. A mutation generation
 * change creates a fresh state, so the budget never carries across a real fix.
 */
function recordRecoveryEvidenceCall(projectRoot, mutationGeneration, options = {}) {
  const state = stateFor(projectRoot, mutationGeneration);
  const hostPlatform = String(options.hostPlatform || process.platform);
  const budget = recoveryBudget(options);
  if (!state.buildFailed) {
    return { blocked: false, active: false, count: 0, budget };
  }
  const contract = state.buildRecoveryContract && typeof state.buildRecoveryContract === "object"
    ? state.buildRecoveryContract
    : null;
  const scopeKey = String(options.scopeKey || "pre_task");
  const contractFailure = validateRecoveryContract(
    state,
    contract,
    options,
    budget,
    hostPlatform
  );
  if (contractFailure) return contractFailure;
  const evidenceByScope = state.recoveryEvidenceByScope instanceof Map
    ? state.recoveryEvidenceByScope
    : new Map();
  state.recoveryEvidenceByScope = evidenceByScope;
  const recoveryEvidenceCount = Number(evidenceByScope.get(scopeKey) || 0);
  if (recoveryEvidenceCount >= budget) {
    return {
      blocked: true,
      active: true,
      count: recoveryEvidenceCount,
      budget,
      scopeKey,
      reason: "build_recovery_evidence_budget_exhausted",
      buildFingerprint: state.buildFingerprint,
    };
  }
  if (options.commitEvidence === false) {
    const prechecks = state.recoveryEvidencePrechecks instanceof Map
      ? state.recoveryEvidencePrechecks
      : new Map();
    state.recoveryEvidencePrechecks = prechecks;
    const identity = recoveryEvidenceIdentity(options, hostPlatform);
    prechecks.set(identity, { budget, scopeKey, hostPlatform, checkedAt: Date.now() });
    while (prechecks.size > 64) prechecks.delete(prechecks.keys().next().value);
    return {
      blocked: false,
      active: true,
      count: recoveryEvidenceCount,
      budget,
      scopeKey,
      reservationPending: true,
      buildFingerprint: state.buildFingerprint,
      recoveryContract: contract ? { ...contract } : null,
    };
  }
  return commitRecoveryEvidence(state, contract, { budget, scopeKey });
}

function markRecoveryEvidenceSatisfied(projectRoot, mutationGeneration, options = {}) {
  const state = stateFor(projectRoot, mutationGeneration);
  const contract = state.buildRecoveryContract && typeof state.buildRecoveryContract === "object"
    ? state.buildRecoveryContract
    : null;
  if (!state.buildFailed) return { ok: true, active: false };
  const hostPlatform = String(options.hostPlatform || process.platform);
  const provisionalBudget = recoveryBudget(options);
  const contractFailure = validateRecoveryContract(
    state,
    contract,
    options,
    provisionalBudget,
    hostPlatform
  );
  if (contractFailure) {
    return { ok: false, ...contractFailure };
  }
  const prechecks = state.recoveryEvidencePrechecks instanceof Map
    ? state.recoveryEvidencePrechecks
    : new Map();
  state.recoveryEvidencePrechecks = prechecks;
  const identity = recoveryEvidenceIdentity(options, hostPlatform);
  const pending = prechecks.get(identity);
  if (!pending) {
    return {
      ok: false,
      active: true,
      count: 0,
      reason: "build_recovery_evidence_precheck_required",
    };
  }
  prechecks.delete(identity);
  const committed = commitRecoveryEvidence(state, contract, pending);
  return {
    ok: committed.blocked !== true,
    evidenceCommitted: committed.blocked !== true,
    ...committed,
  };
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
  cancelBuildAttempt,
  recordBuildRecoveryContract,
  recordRecoveryEvidenceCall,
  markRecoveryEvidenceSatisfied,
  resetWorkflowLoopGuardForTests,
};

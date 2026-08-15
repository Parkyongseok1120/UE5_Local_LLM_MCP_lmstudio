"use strict";

const crypto = require("crypto");
const path = require("path");
const {
  absolutePathIdentity,
  filesystemPathIdentity,
  pathHasSuffixIdentity,
} = require("./filesystem-path-identity");
const {
  deleteGuardState,
  loadGuardState,
  normalizeGuardScope,
  saveGuardState,
  scopeIdentity,
} = require("./durable-guard-store");
const { resolveAgentStateRoot } = require("./state-root");

const projectStates = new Map();
const loadedScopes = new Set();
const COMPONENT = "workflow-loop-guard";
const MAX_PROJECT_STATES = 128;

function projectKey(projectRoot, hostPlatform = process.platform) {
  return absolutePathIdentity(projectRoot, hostPlatform);
}

function generationNumber(value) {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function operationScope(projectRoot, mutationGeneration, options = {}) {
  const scope = normalizeGuardScope({ ...options, projectRoot, mutationGeneration });
  if (!scope) return { scope: null, key: projectKey(projectRoot), storageKey: projectKey(projectRoot), options };
  const stateRoot = path.resolve(options.stateRoot || resolveAgentStateRoot());
  const key = scopeIdentity(scope);
  return { scope, key, storageKey: `${stateRoot}:${key}`, options: { ...options, stateRoot } };
}

function emptyState(generation) {
  return {
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
}

function serializeState(state) {
  return {
    mutationGeneration: generationNumber(state.mutationGeneration),
    validationFingerprint: String(state.validationFingerprint || ""),
    validationFailureCount: Math.max(0, Number(state.validationFailureCount || 0)),
    buildGateFailureCode: String(state.buildGateFailureCode || ""),
    buildGateFailureCount: Math.max(0, Number(state.buildGateFailureCount || 0)),
    buildAttempted: state.buildAttempted === true,
    buildFailed: state.buildFailed === true,
    buildFingerprint: String(state.buildFingerprint || ""),
    buildRecoveryContract: state.buildRecoveryContract && typeof state.buildRecoveryContract === "object"
      ? { ...state.buildRecoveryContract }
      : null,
    recoveryEvidenceByScope: [...(state.recoveryEvidenceByScope instanceof Map
      ? state.recoveryEvidenceByScope
      : new Map())].slice(-64),
    recoveryEvidencePrechecks: [...(state.recoveryEvidencePrechecks instanceof Map
      ? state.recoveryEvidencePrechecks
      : new Map())].slice(-64),
  };
}

function hydrateState(saved, generation) {
  const state = emptyState(generation);
  if (!saved || typeof saved !== "object" || generationNumber(saved.mutationGeneration) !== generation) {
    return state;
  }
  state.validationFingerprint = String(saved.validationFingerprint || "");
  state.validationFailureCount = Math.max(0, Number(saved.validationFailureCount || 0));
  state.buildGateFailureCode = String(saved.buildGateFailureCode || "");
  state.buildGateFailureCount = Math.max(0, Number(saved.buildGateFailureCount || 0));
  state.buildAttempted = saved.buildAttempted === true;
  state.buildFailed = saved.buildFailed === true;
  state.buildFingerprint = String(saved.buildFingerprint || "");
  state.buildRecoveryContract = saved.buildRecoveryContract && typeof saved.buildRecoveryContract === "object"
    ? { ...saved.buildRecoveryContract }
    : null;
  state.recoveryEvidenceByScope = new Map(
    (Array.isArray(saved.recoveryEvidenceByScope) ? saved.recoveryEvidenceByScope : []).slice(-64)
  );
  state.recoveryEvidencePrechecks = new Map(
    (Array.isArray(saved.recoveryEvidencePrechecks) ? saved.recoveryEvidencePrechecks : []).slice(-64)
  );
  return state;
}

function attachContext(state, context) {
  Object.defineProperty(state, "guardContext", {
    value: context,
    writable: true,
    configurable: true,
    enumerable: false,
  });
  return state;
}

function persistState(state) {
  const context = state?.guardContext;
  if (!context?.scope) return { persisted: false, reason: "scope_incomplete" };
  return saveGuardState(COMPONENT, context.scope, serializeState(state), context.options);
}

function stateFor(projectRoot, mutationGeneration, options = {}) {
  const context = operationScope(projectRoot, mutationGeneration, options);
  const key = context.storageKey;
  const generation = generationNumber(mutationGeneration);
  let state = projectStates.get(key);
  if (!state || state.mutationGeneration !== generation) {
    let saved = null;
    if (context.scope && !loadedScopes.has(context.storageKey)) {
      loadedScopes.add(context.storageKey);
      saved = loadGuardState(COMPONENT, context.scope, context.options);
    }
    state = attachContext(hydrateState(saved, generation), context);
    projectStates.set(key, state);
    while (projectStates.size > MAX_PROJECT_STATES) {
      projectStates.delete(projectStates.keys().next().value);
    }
  } else {
    attachContext(state, context);
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

function recordValidationFailure(projectRoot, mutationGeneration, validation, options = {}) {
  const state = stateFor(projectRoot, mutationGeneration, options);
  const fingerprint = validationFingerprint(validation);
  const repeated = state.validationFailureCount > 0 && state.validationFingerprint === fingerprint;
  state.validationFingerprint = fingerprint;
  state.validationFailureCount += 1;
  persistState(state);
  return {
    blocked: repeated || state.buildFailed,
    reason: state.buildFailed ? "build_failed_without_intervening_mutation" : repeated ? "same_validation_failure" : "",
    mutationGeneration: state.mutationGeneration,
    fingerprint,
  };
}

function recordValidationSuccess(projectRoot, mutationGeneration, options = {}) {
  const state = stateFor(projectRoot, mutationGeneration, options);
  state.validationFingerprint = "";
  state.validationFailureCount = 0;
  persistState(state);
  return state;
}

function recordBuildGateFailure(projectRoot, mutationGeneration, errorCode, options = {}) {
  const state = stateFor(projectRoot, mutationGeneration, options);
  const normalizedCode = String(errorCode || "BUILD_GATE_FAILED");
  const repeated = state.buildGateFailureCount > 0 && state.buildGateFailureCode === normalizedCode;
  state.buildGateFailureCode = normalizedCode;
  state.buildGateFailureCount = repeated ? state.buildGateFailureCount + 1 : 1;
  persistState(state);
  return {
    blocked: repeated,
    reason: repeated ? "same_build_gate_failure" : "",
    errorCode: normalizedCode,
    mutationGeneration: state.mutationGeneration,
  };
}

function beginBuildAttempt(projectRoot, mutationGeneration, options = {}) {
  const state = stateFor(projectRoot, mutationGeneration, options);
  if (state.buildAttempted) {
    return {
      ok: false,
      reason: "build_already_attempted_without_intervening_mutation",
      mutationGeneration: state.mutationGeneration,
      buildFingerprint: state.buildFingerprint,
    };
  }
  state.buildAttempted = true;
  persistState(state);
  return { ok: true, mutationGeneration: state.mutationGeneration };
}

function finishBuildAttempt(projectRoot, mutationGeneration, outcome, options = {}) {
  const state = stateFor(projectRoot, mutationGeneration, options);
  state.buildAttempted = true;
  state.buildFailed = outcome?.commandSucceeded !== true;
  state.buildFingerprint = state.buildFailed ? buildFingerprint(outcome) : "";
  state.buildRecoveryContract = null;
  state.recoveryEvidenceByScope = new Map();
  state.recoveryEvidencePrechecks = new Map();
  persistState(state);
  return state;
}

function cancelBuildAttempt(projectRoot, mutationGeneration, options = {}) {
  const state = stateFor(projectRoot, mutationGeneration, options);
  state.buildAttempted = false;
  state.buildFailed = false;
  state.buildFingerprint = "";
  persistState(state);
  return state;
}

function recordBuildRecoveryContract(projectRoot, mutationGeneration, recovery, options = {}) {
  const state = stateFor(projectRoot, mutationGeneration, options);
  if (!state.buildFailed || !recovery || typeof recovery !== "object") {
    state.buildRecoveryContract = null;
    persistState(state);
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
  persistState(state);
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
  persistState(state);
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
  const state = stateFor(projectRoot, mutationGeneration, options);
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
    persistState(state);
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
  const state = stateFor(projectRoot, mutationGeneration, options);
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
  persistState(state);
  const committed = commitRecoveryEvidence(state, contract, pending);
  return {
    ok: committed.blocked !== true,
    evidenceCommitted: committed.blocked !== true,
    ...committed,
  };
}

function resetWorkflowLoopGuardForTests() {
  projectStates.clear();
  loadedScopes.clear();
}

function exportWorkflowLoopGuard(projectRoot, mutationGeneration, options = {}) {
  return serializeState(stateFor(projectRoot, mutationGeneration, options));
}

function importWorkflowLoopGuard(projectRoot, mutationGeneration, snapshot, options = {}) {
  const context = operationScope(projectRoot, mutationGeneration, options);
  if (!context.scope || !snapshot || typeof snapshot !== "object") return false;
  const state = attachContext(hydrateState(snapshot, generationNumber(mutationGeneration)), context);
  projectStates.set(context.storageKey, state);
  loadedScopes.add(context.storageKey);
  persistState(state);
  return true;
}

function clearWorkflowLoopGuardScope(projectRoot, mutationGeneration, options = {}) {
  const context = operationScope(projectRoot, mutationGeneration, options);
  projectStates.delete(context.storageKey);
  loadedScopes.delete(context.storageKey);
  return context.scope ? deleteGuardState(COMPONENT, context.scope, context.options) : false;
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
  exportWorkflowLoopGuard,
  importWorkflowLoopGuard,
  clearWorkflowLoopGuardScope,
};

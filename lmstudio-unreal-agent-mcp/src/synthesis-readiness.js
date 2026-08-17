"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

function loadPolicy() {
  const candidates = [
    path.resolve(__dirname, "..", "..", "config", "synthesis_readiness_policy.json"),
    path.resolve(__dirname, "..", "config", "synthesis_readiness_policy.json"),
  ];
  for (const candidate of candidates) {
    try { return JSON.parse(fs.readFileSync(candidate, "utf8")); } catch { /* next */ }
  }
  throw new Error("synthesis readiness policy is unavailable");
}

const POLICY = loadPolicy();

function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stable(value[key])]));
}

function hash(value) {
  return crypto.createHash("sha256").update(JSON.stringify(stable(value))).digest("hex");
}

function strings(value, limit = 32) {
  return [...new Set((Array.isArray(value) ? value : []).map((item) => String(item || "").replace(/\\/g, "/").trim()).filter(Boolean))]
    .sort().slice(0, limit);
}

function pairingKey(filePath) {
  const normalized = String(filePath || "").replace(/\\/g, "/").toLowerCase();
  const withoutExtension = normalized.replace(/\.(?:h|hpp|inl|cpp|c|cc|cxx)$/u, "");
  const parts = withoutExtension.split("/").filter(Boolean);
  const sourceIndex = parts.lastIndexOf("source");
  if (sourceIndex >= 0 && parts.length > sourceIndex + 2) {
    const module = parts[sourceIndex + 1];
    const relative = parts.slice(sourceIndex + 2);
    if (["public", "private", "classes"].includes(relative[0])) relative.shift();
    return `${parts.slice(0, sourceIndex + 2).join("/")}:${relative.join("/")}`;
  }
  return withoutExtension;
}

function nonnegativeInteger(value) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : 0;
}

function deriveSynthesisReadiness(state = {}) {
  const planRevision = String(state.planRevision || "");
  const ledger = state.sourceEvidence && typeof state.sourceEvidence === "object" ? state.sourceEvidence : {};
  const files = String(ledger.planRevision || "") === planRevision && ledger.files && typeof ledger.files === "object"
    ? Object.values(ledger.files).filter((entry) => entry && typeof entry === "object")
    : [];
  const acceptedIds = strings(files.map((entry) => entry.evidenceId || `${entry.path || ""}:${entry.contentHash || ""}`));
  const declarations = files.filter((entry) => String(entry.sourceKind || "") === "declaration");
  const implementations = files.filter((entry) => String(entry.sourceKind || "") === "implementation");
  const declarationKeys = new Set(declarations.map((entry) => pairingKey(entry.path)));
  const implementationKeys = new Set(implementations.map((entry) => pairingKey(entry.path)));
  const representativePairCount = [...declarationKeys].filter((key) => key && implementationKeys.has(key)).length;
  const contract = state.inspectionContract && typeof state.inspectionContract === "object" ? state.inspectionContract : {};
  const budget = contract.evidenceBudget && typeof contract.evidenceBudget === "object" ? contract.evidenceBudget : {};
  const progress = state.inspectionProgress && typeof state.inspectionProgress === "object" ? state.inspectionProgress : {};
  const frontier = strings(progress.remainingFrontier || state.remainingFrontier, Number(POLICY.maximumFrontierEntries || 32));
  const requiredPairs = Math.max(1, Number(budget.representativePairs || POLICY.defaultRepresentativePairs || 1));
  const maxReads = Math.max(1, Number(budget.maxDirectSourceReadsPerPhase || Number.MAX_SAFE_INTEGER));
  const maxChars = Math.max(1, Number(budget.maxEvidenceCharsPerPhase || Number.MAX_SAFE_INTEGER));
  const boundReached = Number(progress.directSourceReads || 0) >= maxReads
    || Number(progress.evidenceCharacters || 0) >= maxChars;
  const taskKind = String(state.taskKind || "").toLowerCase();
  const directRequired = !POLICY.evidenceFreeTaskKinds.includes(taskKind);
  const directSatisfied = !directRequired || (
    acceptedIds.length >= Number(POLICY.minimumAcceptedDirectEvidence || 2)
    && declarations.length >= Number(POLICY.minimumDeclarationEvidence || 1)
    && implementations.length >= Number(POLICY.minimumImplementationEvidence || 1)
  );
  const representativeSatisfied = !directRequired || representativePairCount >= requiredPairs;
  const repo = state.repoAuditLedger && typeof state.repoAuditLedger === "object" ? state.repoAuditLedger : {};
  const repoFrontierOpen = repo.required === true
    && (Number(repo.remainingCount || 0) > 0 || repo.overflow === true || String(repo.status || "") !== "complete");
  const partialCoverageAllowed = directSatisfied && representativePairCount > 0 && boundReached && frontier.length > 0;
  const coverageSatisfied = !repoFrontierOpen && (representativeSatisfied || partialCoverageAllowed);
  const coverageIncomplete = repoFrontierOpen || !representativeSatisfied;
  const recovery = state.recoveryObligation && typeof state.recoveryObligation === "object" ? state.recoveryObligation : {};
  const pendingEvidenceObligation = ["evidence_required", "repair_planning_required", "revalidate_required"]
    .includes(String(recovery.status || "").toLowerCase());
  const modeEligible = String(state.mode || "").toLowerCase() === "read_only"
    && state.writesAllowed !== true && state.writeGate?.writesAllowed !== true;
  const frontierDurable = !partialCoverageAllowed || frontier.length > 0;
  const ready = modeEligible && directSatisfied && coverageSatisfied && frontierDurable && !pendingEvidenceObligation;
  let reason = "ready";
  if (!modeEligible) reason = "task_not_read_only";
  else if (!directSatisfied) reason = acceptedIds.length === 0 ? "direct_source_evidence_missing" : "direct_source_evidence_insufficient";
  else if (repoFrontierOpen) reason = "repository_frontier_open";
  else if (!representativeSatisfied && !partialCoverageAllowed) reason = "representative_coverage_insufficient";
  else if (pendingEvidenceObligation) reason = "pending_evidence_obligation";
  const acceptedEvidenceHash = hash(acceptedIds);
  const remainingFrontierHash = hash(frontier);
  return {
    version: 1,
    ready,
    reason,
    acceptedDirectEvidenceCount: acceptedIds.length,
    acceptedEvidenceIds: acceptedIds,
    acceptedEvidenceHash,
    declarationCount: declarations.length,
    implementationCount: implementations.length,
    representativePairCount,
    requiredRepresentativePairs: requiredPairs,
    coverageMode: String(contract.coverageMode || ""),
    coverageSatisfied,
    coverageIncomplete,
    remainingFrontier: frontier,
    remainingFrontierHash,
    pendingEvidenceObligation,
    sourceEvidencePlanRevision: String(ledger.planRevision || ""),
    planRevision,
    controlEpoch: nonnegativeInteger(state.controlEpoch),
    commitEligible: ready,
    boundReached,
  };
}

function synthesisLatchMatches(state = {}, readiness = deriveSynthesisReadiness(state)) {
  const action = state.postBudgetAction && typeof state.postBudgetAction === "object" ? state.postBudgetAction : {};
  const actionEpoch = Number(action.controlEpoch);
  const stateEpoch = Number(state.controlEpoch);
  return String(action.name || "") === "synthesize_current_evidence"
    && Number.isInteger(actionEpoch) && actionEpoch >= 0
    && Number.isInteger(stateEpoch) && stateEpoch >= 0
    && actionEpoch === stateEpoch
    && String(action.planRevision || "") === String(state.planRevision || "")
    && String(action.acceptedEvidenceHash || "") === readiness.acceptedEvidenceHash
    && String(action.remainingFrontierHash || "") === readiness.remainingFrontierHash
    && readiness.ready === true;
}

module.exports = { POLICY, deriveSynthesisReadiness, synthesisLatchMatches };

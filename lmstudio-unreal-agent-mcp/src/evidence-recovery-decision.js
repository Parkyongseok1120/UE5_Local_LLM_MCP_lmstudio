"use strict";

const { deriveSynthesisReadiness } = require("./synthesis-readiness");

function evidenceRecoveryDecision(taskState = {}, options = {}) {
  const errorCode = String(options.errorCode || "EVIDENCE_STAGNATION");
  const recoveryHint = options.recoveryHint || null;
  const targetFiles = Array.isArray(options.targetFiles)
    ? options.targetFiles.map((value) => String(value || "")).filter(Boolean).slice(0, 4)
    : [];
  const writesAllowed = taskState.writesAllowed === true
    || taskState.writeGate?.writesAllowed === true;
  const readiness = deriveSynthesisReadiness(taskState);
  const acceptedPaths = new Set(Object.values(taskState.sourceEvidence?.files || {})
    .map((entry) => String(entry?.path || "").replace(/\\/g, "/")));
  const nextEvidencePath = readiness.remainingFrontier.find((candidate) => !acceptedPaths.has(candidate));
  const common = {
    source: "evidence",
    scopeDisposition: "in_slice",
    mutationGeneration: Number(taskState.mutationGeneration || options.mutationGeneration || 0),
  };
  if (writesAllowed) {
    return {
      ...common,
      status: "repair_planning_required",
      errorCode,
      requiredTool: {
        name: "unreal_code_sketch_claim_validate",
        args: targetFiles.length ? { targetFiles } : {},
      },
      targetFiles,
      message: "Evidence reads are exhausted. Reuse retained source evidence and validate the bounded repair claim before writing.",
    };
  }
  if (readiness.ready === true) {
    return {
      ...common,
      status: "evidence_complete",
      errorCode,
      requiredTool: {},
      targetFiles,
      synthesisReadiness: readiness,
      message: "Evidence reads are exhausted. Answer from retained source evidence; no additional tool call is permitted for this turn.",
    };
  }
  if (nextEvidencePath) {
    return {
      ...common,
      status: "evidence_required",
      errorCode: "EVIDENCE_ROUTE_EXHAUSTED",
      requiredTool: { name: "read_file", args: { path: nextEvidencePath } },
      targetFiles: [nextEvidencePath],
      synthesisReadiness: readiness,
      message: "The current read route stagnated before synthesis readiness; continue with the next server-owned direct-source target.",
    };
  }
  return {
    ...common,
    status: "phase_budget_replan_required",
    errorCode: "EVIDENCE_ROUTE_EXHAUSTED",
    requiredTool: {
      name: "unreal_agent_plan",
      args: { request: String(taskState.objective || taskState.request || "Continue bounded source analysis") },
    },
    targetFiles,
    synthesisReadiness: readiness,
    message: "The current evidence route is exhausted but synthesis readiness is false; perform one bounded server-owned replan.",
  };
}

module.exports = { evidenceRecoveryDecision };

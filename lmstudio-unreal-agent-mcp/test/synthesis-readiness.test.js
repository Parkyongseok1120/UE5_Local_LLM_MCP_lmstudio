"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  deriveSynthesisReadiness,
  synthesisLatchMatches,
} = require("../src/synthesis-readiness");
const { phaseBudgetRecoveryDecision } = require("../src/task-auth");

function state(files = {}, overrides = {}) {
  return {
    mode: "read_only",
    writesAllowed: false,
    writeGate: { writesAllowed: false },
    taskKind: "cpp_analysis",
    planRevision: "plan-7",
    controlEpoch: 4,
    inspectionContract: {
      intent: "cpp_analysis",
      coverageMode: "representative",
      evidenceBudget: { representativePairs: 1 },
    },
    sourceEvidence: { planRevision: "plan-7", files },
    ...overrides,
  };
}

const declaration = {
  path: "Source/Cinematic/Public/CinematicSystem.h",
  sourceKind: "declaration",
  evidenceId: "decl-1",
};
const implementation = {
  path: "Source/Cinematic/Private/CinematicSystem.cpp",
  sourceKind: "implementation",
  evidenceId: "impl-1",
};

test("zero direct reads cannot authorize C++ synthesis", () => {
  const result = deriveSynthesisReadiness(state());
  assert.equal(result.ready, false);
  assert.equal(result.reason, "direct_source_evidence_missing");
  assert.equal(result.acceptedDirectEvidenceCount, 0);
});

test("legacy read-only state with no task kind fails closed", () => {
  const result = deriveSynthesisReadiness(state({}, { taskKind: "", inspectionContract: {} }));
  assert.equal(result.ready, false);
  assert.equal(result.reason, "direct_source_evidence_missing");
});

test("search and directory candidates remain discovery-only", () => {
  const result = deriveSynthesisReadiness(state({}, {
    inspectionProgress: {
      listedDirectories: 3,
      searchQueries: 4,
      remainingFrontier: [declaration.path, implementation.path],
    },
  }));
  assert.equal(result.ready, false);
  assert.equal(result.acceptedDirectEvidenceCount, 0);
  assert.deepEqual(result.remainingFrontier, [implementation.path, declaration.path].sort());
});

test("header-only evidence cannot authorize synthesis", () => {
  const result = deriveSynthesisReadiness(state({ [declaration.path]: declaration }));
  assert.equal(result.ready, false);
  assert.equal(result.declarationCount, 1);
  assert.equal(result.implementationCount, 0);
});

test("a directly-read representative header and implementation authorize synthesis", () => {
  const result = deriveSynthesisReadiness(state({
    [declaration.path]: declaration,
    [implementation.path]: implementation,
  }));
  assert.equal(result.ready, true);
  assert.equal(result.commitEligible, true);
  assert.equal(result.representativePairCount, 1);
});

test("same basenames in different Unreal modules are not representative pairs", () => {
  const foreignImplementation = {
    path: "Plugins/Other/Source/OtherRuntime/Private/CinematicSystem.cpp",
    sourceKind: "implementation",
    evidenceId: "foreign-impl",
  };
  const result = deriveSynthesisReadiness(state({
    [declaration.path]: declaration,
    [foreignImplementation.path]: foreignImplementation,
  }));
  assert.equal(result.representativePairCount, 0);
  assert.equal(result.ready, false);
});

test("bounded partial synthesis is eligible only with a durable remaining frontier", () => {
  const files = { [declaration.path]: declaration, [implementation.path]: implementation };
  const partial = deriveSynthesisReadiness(state(files, {
    inspectionContract: {
      intent: "cpp_analysis",
      coverageMode: "targeted_overview",
      evidenceBudget: { representativePairs: 4, maxDirectSourceReadsPerPhase: 2 },
    },
    inspectionProgress: {
      directSourceReads: 2,
      remainingFrontier: ["Source/Cinematic/Private/Next.cpp"],
    },
  }));
  assert.equal(partial.ready, true);
  assert.equal(partial.coverageIncomplete, true);
  assert.equal(partial.remainingFrontier.length, 1);
  const missingFrontier = deriveSynthesisReadiness(state(files, {
    inspectionContract: {
      intent: "cpp_analysis",
      evidenceBudget: { representativePairs: 4, maxDirectSourceReadsPerPhase: 2 },
    },
    inspectionProgress: { directSourceReads: 2, remainingFrontier: [] },
  }));
  assert.equal(missingFrontier.ready, false);
});

test("failed or stale-plan reads do not count as accepted evidence", () => {
  const result = deriveSynthesisReadiness(state({
    [declaration.path]: declaration,
    [implementation.path]: implementation,
  }, { sourceEvidence: { planRevision: "old-plan", files: {
    [declaration.path]: declaration,
    [implementation.path]: implementation,
  } } }));
  assert.equal(result.ready, false);
  assert.equal(result.acceptedDirectEvidenceCount, 0);
});

test("failed read and phase exhaustion route to bounded replan", () => {
  const value = state({}, {
    lastToolOutcome: { tool: "read_file", status: "failed", errorCode: "NOT_FOUND" },
    absentEvidence: { planRevision: "plan-7", files: { "Source/Missing.cpp": { status: "absent" } } },
  });
  const recovery = phaseBudgetRecoveryDecision(value);
  assert.equal(recovery.requiredNextAction, "replan_after_phase_budget");
  assert.equal(recovery.readiness.ready, false);
});

test("zero-result search and RAG-only discovery cannot satisfy direct-source readiness", () => {
  for (const discovery of [
    { tool: "search_files", status: "succeeded", matchCount: 0 },
    { tool: "unreal_rag_search", status: "succeeded", snippets: ["candidate only"] },
  ]) {
    const value = state({}, { lastToolOutcome: discovery });
    const recovery = phaseBudgetRecoveryDecision(value);
    assert.equal(recovery.requiredNextAction, "replan_after_phase_budget");
    assert.equal(recovery.readiness.acceptedDirectEvidenceCount, 0);
  }
});

test("synthesis latch is invalidated by evidence and epoch changes", () => {
  const value = state({
    [declaration.path]: declaration,
    [implementation.path]: implementation,
  });
  const readiness = deriveSynthesisReadiness(value);
  value.postBudgetAction = {
    name: "synthesize_current_evidence",
    controlEpoch: value.controlEpoch,
    planRevision: value.planRevision,
    acceptedEvidenceHash: readiness.acceptedEvidenceHash,
    remainingFrontierHash: readiness.remainingFrontierHash,
  };
  assert.equal(synthesisLatchMatches(value, readiness), true);
  value.sourceEvidence.files[implementation.path].evidenceId = "impl-changed";
  assert.equal(synthesisLatchMatches(value), false);
  value.sourceEvidence.files[implementation.path].evidenceId = "impl-1";
  value.controlEpoch += 1;
  assert.equal(synthesisLatchMatches(value), false);
});

test("synthesis latch accepts epoch zero and rejects invalid epochs", () => {
  const value = state({
    [declaration.path]: declaration,
    [implementation.path]: implementation,
  }, { controlEpoch: 0 });
  const readiness = deriveSynthesisReadiness(value);
  value.postBudgetAction = {
    name: "synthesize_current_evidence",
    controlEpoch: 0,
    planRevision: value.planRevision,
    acceptedEvidenceHash: readiness.acceptedEvidenceHash,
    remainingFrontierHash: readiness.remainingFrontierHash,
  };
  assert.equal(synthesisLatchMatches(value, readiness), true);
  value.postBudgetAction.controlEpoch = -1;
  assert.equal(synthesisLatchMatches(value, readiness), false);
  value.postBudgetAction.controlEpoch = "invalid";
  assert.equal(synthesisLatchMatches(value, readiness), false);
});

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const { evidenceRecoveryDecision } = require("../src/evidence-recovery-decision");

function task(files = {}, progress = {}) {
  return {
    mode: "read_only",
    writesAllowed: false,
    writeGate: { writesAllowed: false },
    taskKind: "cpp_analysis",
    planRevision: "plan-1",
    inspectionContract: { intent: "cpp_analysis", evidenceBudget: { representativePairs: 1 } },
    inspectionProgress: progress,
    sourceEvidence: { planRevision: "plan-1", files },
  };
}

function complete(path, sourceKind, evidenceId, contentHash, text) {
  return {
    path, sourceKind, evidenceId, contentHash,
    evidenceSnapshotGeneration: 0,
    coveredRanges: [[1, 3]],
    wholeFileComplete: true,
    truncated: false,
    lineCount: 3,
    coverageLevel: "FILE_COMPLETE",
    supportingExcerpts: [{
      startLine: 1,
      endLine: 3,
      text,
      excerptDigest: crypto.createHash("sha256").update(text).digest("hex"),
    }],
  };
}
const header = complete(
  "Source/Cine/Public/Cine.h", "declaration", "header", "a".repeat(64), "class FCine {};",
);
const source = complete(
  "Source/Cine/Private/Cine.cpp", "implementation", "source", "b".repeat(64), "void FCine::Run() {}",
);

test("read stagnation with no accepted source evidence cannot become evidence_complete", () => {
  const recovery = evidenceRecoveryDecision(task());
  assert.equal(recovery.status, "phase_budget_replan_required");
  assert.equal(recovery.requiredTool.name, "unreal_agent_plan");
  assert.equal(recovery.synthesisReadiness.ready, false);
});

test("a stale mutation hint cannot turn a read-only task into repair planning", () => {
  const recovery = evidenceRecoveryDecision(task(), {
    recoveryHint: { reason: "BOUNDED_PATCH_REQUIRED", at: Date.now() },
  });
  assert.equal(recovery.status, "phase_budget_replan_required");
  assert.equal(recovery.requiredTool.name, "unreal_agent_plan");
});

test("read stagnation continues with a deterministic uninspected candidate", () => {
  const recovery = evidenceRecoveryDecision(task({}, {
    remainingFrontier: ["Source/Cine/Private/Cine.cpp"],
  }));
  assert.equal(recovery.status, "evidence_required");
  assert.deepEqual(recovery.requiredTool, {
    name: "read_file", args: { path: "Source/Cine/Private/Cine.cpp" },
  });
});

test("read stagnation may complete evidence only after representative direct reads", () => {
  const recovery = evidenceRecoveryDecision(task({
    [header.path]: header,
    [source.path]: source,
  }));
  assert.equal(recovery.status, "evidence_complete");
  assert.deepEqual(recovery.requiredTool, {});
  assert.equal(recovery.synthesisReadiness.ready, true);
});

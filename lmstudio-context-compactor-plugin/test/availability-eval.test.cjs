"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  auditAnswerMatchesOracle,
  answerMatchesOracle,
  calculateReadMetrics,
  evaluateCausalCalls,
  oracleObservation,
  oracleProjection,
  projectionMatchesOracle,
} = require("../scripts/eval-input-availability-ab.cjs");
const pressureFixture = require("../scripts/audit-pressure-fixture.cjs");
const auditEval = require("../scripts/eval-audit-pressure.cjs");
const { reclassifyReport } = require("../scripts/reclassify-audit-results.cjs");
const compactionCore = require("../src/direct-compaction-core.js");
const predictionTest = require("../dist/prediction-loop.js").__test;

test("causal range metrics distinguish input overlap from historical reacquisition", () => {
  const metrics = calculateReadMetrics({
    rangeUnit: "line",
    returnedRanges: [[21, 60]],
    inputRanges: [[1, 40]],
    historicalRanges: [[1, 100]],
  });
  assert.deepEqual(metrics, {
    rangeUnit: "line",
    returnedUnits: 40,
    inputOverlapUnits: 20,
    historicalReacquisitionUnits: 20,
    newEvidenceUnits: 0,
  });
});

test("availability oracle compares every source and range instead of trusting entry zero", () => {
  const productEntries = [
    { sourceKey: "a", rawPresence: "full", rawRangesInThisInput: [[1, 20]] },
    { sourceKey: "b", rawPresence: "full", rawRangesInThisInput: [[1, 40]] },
  ];
  const oracleEntries = [
    { sourceKey: "a", rawPresence: "full", rawRangesInThisInput: [[1, 20]] },
    { sourceKey: "b", rawPresence: "partial", rawRangesInThisInput: [[1, 20]] },
  ];
  const compared = projectionMatchesOracle(productEntries, oracleEntries);
  assert.equal(compared.matches, false);
  assert.equal(compared.mismatches.length, 1);
  assert.equal(compared.mismatches[0].sourceKey, "b");
});

test("answer rubric rejects negated keyword stuffing", () => {
  const result = answerMatchesOracle(
    "DailySales is not the settlement owner, and DuplicateGuard=true is false.",
    { settlementOwner: "DailySales", duplicateGuard: true },
  );
  assert.equal(result.pass, false);
  assert.match(result.reasons.join(" "), /owner|DuplicateGuard/iu);
});

function toolResult(id, payload) {
  return { role: "tool", toolResults: [{ toolCallId: id, content: JSON.stringify(payload) }] };
}

function linePayload(startLine, endLine) {
  return {
    ok: true,
    projectIdentity: "project-a",
    path: "Assets/A.cs",
    sha256: "a".repeat(64),
    startLine,
    endLine,
    totalLines: 100,
    returnedLineCount: endLine - startLine + 1,
    content: Array.from({ length: endLine - startLine + 1 }, (_, index) => `line ${startLine + index}`).join("\n"),
  };
}

test("causal evaluator uses the request-producing input and pre-result history", () => {
  const initialMessages = [toolResult("initial", linePayload(1, 100))];
  const causalInput = [toolResult("retained", linePayload(1, 40))];
  const modelInputs = new Map([["exec:prediction-2", causalInput]]);
  const measured = evaluateCausalCalls({
    initialMessages,
    modelInputs,
    calls: [{
      callKey: "exec:1:read",
      causalModelInputId: "exec:prediction-2",
      executionState: "succeeded",
      payload: linePayload(21, 60),
    }],
  });
  assert.deepEqual(measured.byUnit.line, {
    returnedUnits: 40,
    inputOverlapUnits: 20,
    historicalReacquisitionUnits: 20,
    newEvidenceUnits: 0,
    overlapRate: 0.5,
    rangeUnit: "line",
  });
});

test("independent oracle recognizes line and UTF-8 byte contracts separately", () => {
  const line = oracleObservation(linePayload(1, 2));
  const content = "한글\r\n🙂";
  const bytes = oracleObservation({
    ok: true,
    activeProject: "C:\\Game\\Game.uproject",
    path: "project://Config/A.ini",
    sha256: "b".repeat(64),
    size: Buffer.byteLength(content, "utf8"),
    offsetBytes: 0,
    nextOffsetBytes: Buffer.byteLength(content, "utf8"),
    content,
  });
  assert.equal(line.rangeUnit, "line");
  assert.equal(bytes.rangeUnit, "utf8_byte");
  assert.equal(bytes.verified, true);
});

test("independent projection does not promote bodyless ranges sharing one key", () => {
  const verified = linePayload(1, 20);
  const bodyless = { ...linePayload(21, 40), content: undefined };
  const entries = oracleProjection([
    toolResult("verified", verified),
    toolResult("bodyless", bodyless),
  ], [oracleObservation(linePayload(1, 40))]);
  assert.deepEqual(entries[0].rawRangesInThisInput, [[1, 20]]);
  assert.equal(entries[0].rawPresence, "partial");
});

test("audit fixture has eight versioned sources and preserves the required exact evidence", () => {
  assert.equal(pressureFixture.files.length, 8);
  assert.equal(pressureFixture.readPayload(
    "Assets/03.Scripts/Tycoon/GuestManager.cs", 189, 189,
  ).content, "    dailySales.Reset();");
  assert.equal(pressureFixture.readPayload(
    "Assets/03.Scripts/Tycoon/GuestManager.cs", 1036, 1036,
  ).content, "    playerDataWriter.AddMoney(sessionTotal);");
  assert.equal(pressureFixture.readPayload("missing.cs", 1, 1).errorCode, "NOT_FOUND");
  const payload = pressureFixture.readPayload("Assets/03.Scripts/Tycoon/GuestManager.cs", 189, 189);
  assert.equal(payload.activeProject, pressureFixture.PROJECT_IDENTITY);
  assert.equal(payload.resolvedRootType, "active_project");
  assert.equal(payload.path, "project://Assets/03.Scripts/Tycoon/GuestManager.cs");
  assert.equal(payload.projectRelativePath, "Assets/03.Scripts/Tycoon/GuestManager.cs");
});

test("real-contract audit locators survive the synthetic compaction boundary", () => {
  const normalized = predictionTest.normalizeHistory(auditEval.buildInitialHistory());
  const checkpoint = compactionCore.buildCheckpoint(normalized, {
    recentCompleteTurns: 0,
    maxCheckpointChars: 16000,
    maxToolResultChars: 1400,
    maxCurrentTurnMessages: 2,
  });
  assert.match(checkpoint.checkpoint, /GuestManager\.cs/u);
  assert.match(checkpoint.checkpoint, /ProjectLifeScope\.prefab/u);
  assert.match(checkpoint.checkpoint, /AuditProject\.uproject/u);
});

test("empty audit answers are not rewarded for avoiding an overclaim", () => {
  const empty = auditAnswerMatchesOracle("");
  assert.equal(empty.reportPresent, false);
  assert.equal(empty.score, 0);
  assert.equal(empty.checks.noRuntimeOverclaim, null);
  assert.deepEqual(empty.reasons, ["not_evaluable:no_answer"]);
});

test("audit run outcomes are mutually exclusive and separate truncation from timeout", () => {
  const truncated = auditEval.classifyRunOutcome({
    visibleAnswer: "bounded report",
    trace: { roundFinishReasons: ["maxPredictedTokensReached"] },
    timedOut: false,
    error: null,
  });
  assert.equal(truncated.executionOutcome, "truncated");
  assert.equal(truncated.deliveryState, "truncated");

  const timedOut = auditEval.classifyRunOutcome({
    visibleAnswer: "",
    trace: { roundFinishReasons: ["userStopped"] },
    timedOut: true,
    error: null,
  });
  assert.equal(timedOut.executionOutcome, "timed_out");
  assert.equal(timedOut.deliveryState, "no_answer");

  const exhaustedWithoutAnswer = auditEval.classifyRunOutcome({
    visibleAnswer: "",
    trace: { roundFinishReasons: ["maxPredictedTokensReached"] },
    timedOut: false,
    error: null,
  });
  assert.equal(exhaustedWithoutAnswer.executionOutcome, "truncated");
  assert.equal(exhaustedWithoutAnswer.deliveryState, "no_answer");

  const boundedCompleted = auditEval.classifyRunOutcome({
    visibleAnswer: "final report",
    boundedFinalizations: [{ deliveryState: "complete", finishReason: "eosFound" }],
    trace: { roundFinishReasons: ["eosFound"] },
    timedOut: false,
    error: null,
  });
  assert.equal(boundedCompleted.executionOutcome, "completed");
  assert.equal(boundedCompleted.deliveryState, "complete");

  const boundedNoAnswer = auditEval.classifyRunOutcome({
    visibleAnswer: "",
    boundedFinalizations: [{ deliveryState: "no_answer", finishReason: "eosFound" }],
    trace: { roundFinishReasons: ["eosFound"] },
    timedOut: false,
    error: null,
  });
  assert.equal(boundedNoAnswer.executionOutcome, "no_answer");
  assert.equal(boundedNoAnswer.deliveryState, "no_answer");

  const boundedFinalTimeout = auditEval.classifyRunOutcome({
    visibleAnswer: "partial report",
    boundedFinalizations: [{ deliveryState: "partial", finishReason: "final_timeout" }],
    trace: { roundFinishReasons: ["userStopped"] },
    timedOut: false,
    error: null,
  });
  assert.equal(boundedFinalTimeout.executionOutcome, "timed_out");
  assert.equal(boundedFinalTimeout.deliveryState, "partial");
});

test("audit summaries do not count a timeout as success and mark zero-return reacquisition not evaluable", () => {
  const summary = auditEval.summarize([{
    group: "C",
    visibleAnswer: "",
    trace: { roundFinishReasons: ["userStopped"] },
    timedOut: true,
    error: null,
    pressureExposure: true,
    compactionCount: 1,
    generatedToolCalls: 3,
    dispatchedToolCalls: 3,
    implementationEnteredToolCalls: 3,
    callStatusCounts: { succeeded: 0, failed: 3 },
    rangeMetricsByUnit: {},
    answerEvaluation: auditAnswerMatchesOracle(""),
    availabilityProjectionMatchesOracle: true,
    elapsedMs: 100,
  }], "C");
  assert.equal(summary.successfulRuns, 0);
  assert.equal(summary.timeouts, 1);
  assert.equal(summary.outcomeCounts.timed_out, 1);
  assert.equal(summary.successfulRawReturns, 0);
  assert.equal(summary.historicalReacquisitionLines, 0);
  assert.equal(summary.historicalReacquisitionEvaluation, "not_evaluable_no_successful_line_return");
});

test("saved audit results are reclassified without inventing a missing legacy timeout", () => {
  const source = {
    generatedAt: "2026-09-21T00:00:00.000Z",
    sourceHead: "a".repeat(40),
    runs: [{
      group: "C",
      visibleAnswer: "",
      trace: { roundFinishReasons: ["userStopped"] },
      error: null,
      pressureExposure: true,
      compactionCount: 1,
      generatedToolCalls: 1,
      callStatusCounts: { succeeded: 0, failed: 1 },
      rangeMetricsByUnit: {},
      answerEvaluation: auditAnswerMatchesOracle(""),
      availabilityProjectionMatchesOracle: true,
      elapsedMs: 240000,
    }],
  };
  const classified = reclassifyReport(source, "legacy.json");
  assert.equal(classified.runs[0].outcome.executionOutcome, "canceled");
  assert.equal(classified.runs[0].legacyTimedOutFieldPresent, false);
  assert.equal(classified.summaries.C.timeouts, 0);
  assert.match(classified.limitations.join(" "), /not inferred/iu);
});

test("audit answer rubric rewards bounded evidence updates and rejects runtime overclaim", () => {
  const grounded = auditAnswerMatchesOracle([
    "GuestManager.cs:189 calls Reset; GuestManager.cs:1036 calls AddMoney.",
    "The per-session wallet payment is AddMoney, while DailySales day-end total is reporting only.",
    "A direct C# search is not enough to declare the UI absent: ProjectLifeScope.prefab has a serialized binding to UICashPanel.AddCurrency.",
    "Self-removal of the current listener is tolerated, but removing a lower unvisited listener can invoke one twice or skip work.",
    "The runtime cause remains unproven. Capture the exact log and whether AddCurrency callback ran on the active instance, plus amountText assignment.",
  ].join("\n"));
  assert.equal(grounded.pass, true);
  assert.equal(grounded.humanReviewRequired, true);

  const overclaim = auditAnswerMatchesOracle([
    "GuestManager.cs:189 Reset; GuestManager.cs:1036 AddMoney.",
    "The root cause is definitely UICashPanel.",
  ].join("\n"));
  assert.equal(overclaim.pass, false);
  assert.equal(overclaim.checks.noRuntimeOverclaim, false);
});

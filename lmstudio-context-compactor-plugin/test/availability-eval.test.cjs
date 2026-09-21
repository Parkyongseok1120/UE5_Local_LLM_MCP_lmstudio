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
const boundedEval = require("../scripts/eval-bounded-completion.cjs");
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
  assert.equal(Object.hasOwn(payload, "returnedLineCount"), false);
  assert.equal(oracleObservation(payload).verified, true);
});

test("audit pressure profiles distinguish normal compaction from threshold-forced stress", () => {
  const controlled = auditEval.pressureProfileConfig("controlled");
  const forced = auditEval.pressureProfileConfig("forced");
  const loadedContextLength = 35328;
  const controlledMaximumRemaining = loadedContextLength
    - controlled.maxOutputReserve - controlled.safetyMarginTokens;
  const forcedMaximumRemaining = loadedContextLength
    - forced.maxOutputReserve - forced.safetyMarginTokens;
  assert.ok(controlled.softRemainingTokens < controlledMaximumRemaining);
  assert.ok(forced.softRemainingTokens > forcedMaximumRemaining);
  assert.throws(() => auditEval.pressureProfileConfig("unknown"), /Unknown pressure profile/u);
});

test("targeted access baseline contains exact evidence without fixture-only line counts", () => {
  const text = auditEval.buildInitialHistory("", "targeted").toString();
  assert.match(text, /dailySales\.Reset\(\)/u);
  assert.match(text, /AddMoney\(sessionTotal\)/u);
  assert.match(text, /m_MethodName:\s*AddCurrency/u);
  assert.match(text, /visible money value does not change/u);
  assert.doesNotMatch(text, /returnedLineCount/u);
  assert.doesNotMatch(text, /MissingCashPanel/u);
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

test("audit result collection preserves a finalization with no visible answer", () => {
  assert.equal(auditEval.selectVisibleAnswer([], [{ deliveryState: "no_answer" }]), "");
  assert.equal(auditEval.selectVisibleAnswer([" first ", " final "], [{}]), "final");
  assert.equal(auditEval.selectVisibleAnswer(["first", "second"], []), "first\nsecond");
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

test("bounded evaluation separates valid design from completed-report comparability", () => {
  const baseline = {
    group: "BASELINE", compactionCount: 0,
    outcome: { executionOutcome: "completed" },
  };
  const off = {
    group: "OFF", pressureExposure: true,
    structureEvaluation: { passed: true }, outcome: { executionOutcome: "completed" },
  };
  const boundedTimeout = {
    group: "BOUNDED", pressureExposure: true,
    structureEvaluation: { passed: true }, outcome: { executionOutcome: "timed_out" },
  };
  const options = {
    baselineRunsRequested: 1, requestedPairs: 1,
    pressureProfile: "controlled", thresholdForcesEveryRound: false,
  };
  const incomplete = boundedEval.evaluateExperimentValidity(
    [baseline, off, boundedTimeout], options,
  );
  assert.equal(incomplete.designValid, true);
  assert.equal(incomplete.completedReportQualityComparable, false);
  assert.equal(incomplete.completedReportPairs, 0);

  const complete = boundedEval.evaluateExperimentValidity([
    baseline, off, { ...boundedTimeout, outcome: { executionOutcome: "completed" } },
  ], options);
  assert.equal(complete.designValid, true);
  assert.equal(complete.completedReportQualityComparable, true);
  assert.equal(complete.completedReportPairs, 1);
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
    "A direct C# search is not enough to declare the UI absent: ProjectLifeScope.prefab has m_PersistentCalls with m_MethodName AddCurrency targeting UICashPanel.",
    "Self-removal of the current listener is tolerated, but removing a lower unvisited listener can invoke one twice or skip work.",
    "The visible money value did not change, but the runtime cause remains unproven. Capture the exact log and whether AddCurrency callback ran on the active instance, plus coroutine completion and amountText assignment.",
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

test("audit rubric accepts multiline caution but rejects invented evidence-rich details", () => {
  const result = auditAnswerMatchesOracle([
    "GuestManager.cs:189 calls Reset; GuestManager.cs:1036 calls AddMoney.",
    "The per-session wallet payment is AddMoney, while DailySales day-end total is reporting only.",
    "The direct C# search is not sufficient",
    "to declare the UI absent. ProjectLifeScope.prefab serializes UICashPanel.AddCurrency.",
    "It binds OnMoneyChanged and OnDayEnd using dayEnded.",
    "Self-removal is tolerated; removing a lower unvisited listener can invoke it twice.",
    "The visible money value did not change, although there were no exceptions and wallet totals were consistent.",
    "Runtime cause is unproven. Capture the exact log, AddCurrency callback on the active instance, coroutine completion, and amountText assignment.",
    "PaidAmount is passed to RecordSale and then marked Visited.",
  ].join("\n"));
  assert.equal(result.checks.directSearchNotConclusive, true);
  assert.equal(result.checks.serializedBinding, false);
  assert.equal(result.checks.noFabricatedSerializedBinding, false);
  assert.equal(result.checks.noFabricatedPaymentFlow, false);
  assert.equal(result.checks.runtimePremiseMatchesFixture, false);
  assert.equal(result.pass, false);
});

test("audit rubric recognizes ranged exact locations and rejects a negated prefab finding", () => {
  const ranged = auditAnswerMatchesOracle([
    "GuestManager.cs:189 calls Reset.",
    "GuestManager.cs:1034-1041 contains L1036 playerDataWriter.AddMoney(sessionTotal).",
    "Per-session AddMoney is distinct from the DailySales day-end report.",
    "A zero C# search proves nothing about UI wiring or absence.",
    "ProjectLifeScope.prefab has m_PersistentCalls with m_MethodName AddCurrency targeting UICashPanel.",
    "Self-removal is safe; lower unvisited removal can invoke the current listener twice.",
    "The visible money value did not change and the runtime cause remains unproven.",
    "Capture the exact log, AddCurrency callback on the active instance, coroutine completion, and amountText assignment.",
  ].join("\n"));
  assert.equal(ranged.checks.addMoneyLocation, true);
  assert.equal(ranged.checks.directSearchNotConclusive, true);
  assert.equal(ranged.checks.serializedBinding, true);
  assert.equal(ranged.pass, true);

  const negated = auditAnswerMatchesOracle([
    rangedTextWithoutBinding(ranged),
    "ProjectLifeScope.prefab is only a candidate. I found no explicit serialized listener line, so no confirmed binding is present.",
  ].join("\n"));
  assert.equal(negated.checks.serializedBinding, false);
  assert.equal(negated.pass, false);
});

function rangedTextWithoutBinding(result) {
  assert.equal(result.reportPresent, true);
  return [
    "GuestManager.cs:189 calls Reset; GuestManager.cs:1034-1041 contains L1036 AddMoney.",
    "Per-session AddMoney differs from the DailySales day-end report.",
    "A zero C# search proves nothing about UI wiring or absence.",
    "Self-removal is safe; lower unvisited removal can invoke the current listener twice.",
    "The visible money value did not change and runtime cause is unproven.",
    "Capture the exact log, AddCurrency callback on the active instance, coroutine completion, and amountText assignment.",
  ].join("\n");
}

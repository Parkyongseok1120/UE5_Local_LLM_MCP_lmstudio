#!/usr/bin/env node
"use strict";

const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { LMStudioClient } = require("@lmstudio/sdk");
const { oneRun, pressureProfileConfig, summarize } = require("./eval-audit-pressure.cjs");
const fixture = require("./audit-pressure-fixture.cjs");

function valueAfter(flag, fallback = "") {
  const index = process.argv.indexOf(flag);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

function integerAfter(flag, fallback) {
  const value = Number(valueAfter(flag, fallback));
  return Number.isSafeInteger(value) && value >= 0 ? value : fallback;
}

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function structureEvaluation(run) {
  const finalizations = run.boundedFinalizations || [];
  const finalActs = (run.modelActs || []).filter(act => act.modelInputId.endsWith(":final-report"));
  const recoveryActs = (run.modelActs || []).filter(act => act.modelInputId.includes(":output-recovery-"));
  const finalAct = finalActs.at(-1) || null;
  const recoveryAct = recoveryActs.at(-1) || null;
  const finalActSequence = finalAct?.sequence || null;
  const terminalSequence = finalActSequence ?? recoveryAct?.sequence ?? null;
  const callsAfterFinal = terminalSequence === null ? 0 : (run.modelActs || [])
    .filter(act => act.sequence > terminalSequence).length;
  const bounded = run.auditCompletionMode === "bounded";
  const passed = bounded
    ? finalizations.length === 1
      && finalActs.length === 1
      && finalAct.toolCount === 0
      && run.boundedFinalToolCalls === 0
      && callsAfterFinal === 0
      && finalizations[0].attempt === 1
      && finalizations[0].maxAttempts === 1
    : finalizations.length === 0
      && recoveryActs.length <= 1
      && (recoveryAct === null || recoveryAct.toolCount === 0)
      && callsAfterFinal === 0;
  return {
    expectedMode: bounded ? "bounded" : "off",
    passed,
    finalizationEventCount: finalizations.length,
    finalModelCallCount: finalActs.length,
    finalModelCallToolCount: finalAct?.toolCount ?? null,
    finalModelCallMaxTokens: finalAct?.maxTokens ?? null,
    outputRecoveryModelCallCount: recoveryActs.length,
    outputRecoveryModelCallToolCount: recoveryAct?.toolCount ?? null,
    outputRecoveryModelCallMaxTokens: recoveryAct?.maxTokens ?? null,
    implementationCallsAttributedToFinalInput: run.boundedFinalToolCalls,
    modelCallsAfterFinal: callsAfterFinal,
    deliveryState: finalizations.at(-1)?.deliveryState || null,
    finishReason: finalizations.at(-1)?.finishReason || null,
  };
}

function evaluateExperimentValidity(runs, {
  baselineRunsRequested,
  requestedPairs,
  pressureProfile,
  thresholdForcesEveryRound,
}) {
  const baselineRuns = runs.filter(run => run.group === "BASELINE");
  const pressureRuns = runs.filter(run => run.group !== "BASELINE");
  const completedOffReports = pressureRuns.filter(run => (
    run.group === "OFF" && run.outcome?.executionOutcome === "completed"
  )).length;
  const completedBoundedReports = pressureRuns.filter(run => (
    run.group === "BOUNDED" && run.outcome?.executionOutcome === "completed"
  )).length;
  const result = {
    accessBaselineRecorded: baselineRuns.length === baselineRunsRequested,
    accessBaselineNoCompaction: baselineRuns.every(run => run.compactionCount === 0),
    accessBaselineReportsCompleted: baselineRuns.every(run => run.outcome?.executionOutcome === "completed"),
    pressureExposureInEveryComparedRun: pressureRuns.every(run => run.pressureExposure),
    pressureThresholdForcesEveryRound: thresholdForcesEveryRound,
    structurallyValidComparedRuns: pressureRuns.every(run => run.structureEvaluation?.passed === true),
    completedOffReports,
    completedBoundedReports,
    completedReportPairs: Math.min(completedOffReports, completedBoundedReports),
    requestedPairs,
  };
  result.designValid = result.accessBaselineRecorded
    && result.accessBaselineNoCompaction
    && result.accessBaselineReportsCompleted
    && result.pressureExposureInEveryComparedRun
    && result.structurallyValidComparedRuns
    && (pressureProfile === "forced" || thresholdForcesEveryRound === false);
  result.completedReportQualityComparable = result.designValid
    && result.completedReportPairs >= requestedPairs;
  return result;
}

async function main() {
  const identifier = valueAfter("--model", "swift-qwen3.8-27b");
  const pairs = integerAfter("--pairs", 1);
  const baselineRunsRequested = integerAfter("--baseline-runs", 1);
  const pressureProfile = valueAfter("--pressure-profile", "controlled");
  if (!["controlled", "forced"].includes(pressureProfile)) {
    throw new Error(`--pressure-profile must be controlled or forced, received: ${pressureProfile}`);
  }
  const researchSeconds = integerAfter("--research-seconds", 100);
  const researchRounds = integerAfter("--research-rounds", 12);
  const finalSeconds = integerAfter("--final-seconds", researchSeconds);
  const finalMaxTokens = integerAfter("--final-max-tokens", 4096);
  const researchMaxTokens = integerAfter("--research-max-tokens", finalMaxTokens);
  const evaluationTimeoutSeconds = integerAfter(
    "--run-timeout-seconds", Math.max(240, researchSeconds + finalSeconds + 30),
  );
  const output = path.resolve(valueAfter(
    "--output", path.join(process.cwd(), "eval-results", "bounded-completion-ab.json"),
  ));

  const client = new LMStudioClient();
  const loaded = await client.llm.listLoaded();
  const loadedHandle = loaded.find(item => item.identifier === identifier);
  if (!loadedHandle) throw new Error(`Loaded model not found: ${identifier}`);
  let processInfo = {};
  try {
    const listed = JSON.parse(execFileSync("lms", ["ps", "--json"], {
      encoding: "utf8", windowsHide: true, timeout: 10000,
    }));
    const processes = Array.isArray(listed) ? listed : listed.models || listed.processes || [];
    processInfo = processes.find(item => item.identifier === identifier) || {};
  } catch {
    // The SDK run remains valid; process metadata is optional evidence.
  }
  const modelInfo = { ...loadedHandle, ...processInfo };
  const model = await client.llm.model(identifier);
  const runs = [];
  for (let index = 1; index <= baselineRunsRequested; index += 1) {
    const run = await oneRun(model, modelInfo, "BASELINE", "access-baseline", index,
      6800 + index, "", {
        inputAvailabilityMode: "observe",
        auditContractSelected: false,
        auditCompletionMode: "off",
        historyMode: "targeted",
        pressureProfile: "access-baseline",
        researchMaxTokens: finalMaxTokens,
        evaluationTimeoutSeconds,
        captureModelInputs: true,
      });
    run.structureEvaluation = structureEvaluation(run);
    runs.push(run);
    process.stdout.write(`${JSON.stringify({
      phase: run.phase,
      group: run.group,
      compactions: run.compactionCount,
      outcome: run.outcome,
      score: run.answerEvaluation.score,
      maximumScore: run.answerEvaluation.maximumScore,
      elapsedMs: run.elapsedMs,
      error: run.error,
    })}\n`);
  }
  for (let pairIndex = 1; pairIndex <= pairs; pairIndex += 1) {
    const groups = pairIndex % 2 === 0 ? ["BOUNDED", "OFF"] : ["OFF", "BOUNDED"];
    for (const group of groups) {
      const run = await oneRun(model, modelInfo, group, "completion-ab", pairIndex,
        7000 + pairIndex, "", {
          inputAvailabilityMode: "observe",
          auditContractSelected: false,
          auditCompletionMode: group === "BOUNDED" ? "bounded" : "off",
          historyMode: "pressure",
          pressureProfile,
          auditResearchSeconds: researchSeconds,
          auditResearchRounds: researchRounds,
          auditFinalSeconds: finalSeconds,
          auditFinalMaxTokens: finalMaxTokens,
          researchMaxTokens,
          evaluationTimeoutSeconds,
          captureModelInputs: true,
        });
      run.structureEvaluation = structureEvaluation(run);
      runs.push(run);
      process.stdout.write(`${JSON.stringify({
        pairIndex,
        group,
        pressureExposure: run.pressureExposure,
        compactions: run.compactionCount,
        predictionRounds: run.predictionRounds,
        modelCalls: run.modelActs.length,
        finalModelCalls: run.boundedFinalModelCalls,
        finalToolCalls: run.boundedFinalToolCalls,
        outcome: run.outcome,
        score: run.answerEvaluation.score,
        structure: run.structureEvaluation,
        elapsedMs: run.elapsedMs,
        error: run.error,
      })}\n`);
    }
  }

  const repositoryRoot = path.resolve(__dirname, "../..");
  const packageMetadata = require("../package.json");
  const manifestMetadata = require("../manifest.json");
  const sdkMetadata = require("../node_modules/@lmstudio/sdk/package.json");
  const hashedFiles = [
    path.resolve(__dirname, "../dist/prediction-loop.js"),
    path.resolve(__dirname, "eval-audit-pressure.cjs"),
    path.resolve(__dirname, "audit-pressure-fixture.cjs"),
    __filename,
  ];
  const baselineRuns = runs.filter(run => run.group === "BASELINE");
  const pressureRuns = runs.filter(run => run.group !== "BASELINE");
  const selectedProfileConfig = pressureProfileConfig(pressureProfile);
  const loadedContextLength = Number(modelInfo.contextLength || 0);
  const maximumConfiguredRemaining = loadedContextLength
    ? loadedContextLength - selectedProfileConfig.maxOutputReserve - selectedProfileConfig.safetyMarginTokens
    : null;
  const thresholdForcesEveryRound = maximumConfiguredRemaining === null ? null
    : selectedProfileConfig.softRemainingTokens > maximumConfiguredRemaining;
  const experimentValidity = evaluateExperimentValidity(runs, {
    baselineRunsRequested,
    requestedPairs: pairs,
    pressureProfile,
    thresholdForcesEveryRound,
  });
  const report = {
    experiment: "evidence-access-baseline-and-bounded-audit-completion-off-on-A-B",
    generatedAt: new Date().toISOString(),
    sourceScope: "repository dist exercised directly; installed plugin and active chats were not modified",
    sourceHead: execFileSync("git", ["rev-parse", "HEAD"], {
      cwd: repositoryRoot, encoding: "utf8", windowsHide: true, timeout: 10000,
    }).trim(),
    sourceWorktreeDirty: Boolean(execFileSync("git", ["status", "--porcelain", "--",
      "lmstudio-context-compactor-plugin"], {
      cwd: repositoryRoot, encoding: "utf8", windowsHide: true, timeout: 10000,
    }).trim()),
    packageVersion: packageMetadata.version,
    manifestRevision: manifestMetadata.revision,
    sdkVersion: sdkMetadata.version,
    sourceBundleSha256: sha256(hashedFiles.map(file => (
      `${path.basename(file)}:${sha256(fs.readFileSync(file))}`
    )).join("\n")),
    fixture: {
      fileCount: fixture.files.length,
      projectIdentity: fixture.PROJECT_IDENTITY,
      fileVersions: fixture.files.map(file => ({
        path: file.path, sha256: file.sha256, totalLines: file.lines.length,
      })),
      auditQuestionSha256: sha256(fixture.AUDIT_QUESTION),
    },
    model: {
      identifier: model.identifier,
      path: modelInfo.path || null,
      quantization: modelInfo.quantization || null,
      loadedContextLength: modelInfo.contextLength || null,
      maxContextLength: modelInfo.maxContextLength || null,
      temperature: 0,
      researchMaxTokens,
      seedPolicy: "same seed within each comparison pair",
    },
    controlledConditions: {
      inputAvailabilityMode: "observe",
      auditContractSelected: false,
      separateDocumentInput: false,
      researchSeconds,
      researchRounds,
      finalSeconds,
      finalMaxTokens,
      evaluationTimeoutSeconds,
      pressureProfile,
      pressureProfileConfig: selectedProfileConfig,
      maximumConfiguredRemaining,
      pressureThresholdForcesEveryRound: thresholdForcesEveryRound,
      modelInputSnapshots: "captured because this fixture is synthetic; do not enable for private production transcripts",
      onlyChangedVariableWithinPressurePair: "auditCompletionMode: off versus bounded; both use the same per-call output limit",
    },
    accessBaseline: {
      purpose: "Confirm report behavior when all exact evidence is current, no compaction is requested, and output opportunity is normal.",
      requestedRuns: baselineRunsRequested,
      summary: summarize(baselineRuns, "BASELINE"),
    },
    comparison: {
      requestedPairs: pairs,
      recordedPairs: Math.min(
        pressureRuns.filter(run => run.group === "OFF").length,
        pressureRuns.filter(run => run.group === "BOUNDED").length,
      ),
      structurallyValidPairs: Math.min(
        pressureRuns.filter(run => run.group === "OFF" && run.structureEvaluation.passed).length,
        pressureRuns.filter(run => run.group === "BOUNDED" && run.structureEvaluation.passed).length,
      ),
      off: summarize(pressureRuns, "OFF"),
      bounded: summarize(pressureRuns, "BOUNDED"),
    },
    experimentValidity,
    runs,
    interpretationBoundary: [
      "The access baseline is separate from the pressure pair and is not counted as an A/B member.",
      "Within the pressure A/B, metadata injection and the audit contract are disabled, per-call output limits are equal, and only bounded completion policy changes.",
      "The controlled profile requires real compaction exposure without configuring a soft threshold above the model's maximum possible remaining context.",
      "The forced profile is a non-representative stress condition and is labeled as such.",
      "Exact semantic Chat inputs are saved only because every source in this fixture is synthetic.",
      "One pair is a runtime wiring check, not an improvement-rate estimate.",
      "A structurally valid finalization does not by itself prove better report quality.",
      "Failed, timed-out, truncated, and no-answer runs remain in the report.",
      "Design validity is separate from completed-report quality comparability; a timeout is retained as an outcome and cannot satisfy the latter.",
      "The fixture is synthetic and does not establish the direct cause of the original 865-line transcript.",
    ],
  };
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({ output, experimentValidity, comparison: report.comparison })}\n`);
  if (!experimentValidity.completedReportQualityComparable
    || runs.some(run => !run.structureEvaluation.passed || run.error)) process.exitCode = 1;
}

if (require.main === module) {
  main().catch(error => {
    console.error(error?.stack || error);
    process.exitCode = 1;
  });
}

module.exports = { evaluateExperimentValidity, structureEvaluation };

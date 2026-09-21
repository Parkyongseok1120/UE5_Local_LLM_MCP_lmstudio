#!/usr/bin/env node
"use strict";

const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { Chat, ChatMessage, LMStudioClient, rawFunctionTool } = require("@lmstudio/sdk");
const { createPredictionLoopHandler } = require("../dist/prediction-loop.js");
const evalCore = require("./availability-eval-core.cjs");
const fixture = require("./audit-pressure-fixture.cjs");

const TOOL_SCHEMA = {
  type: "object",
  properties: {
    path: { type: "string" },
    startLine: { type: "integer", minimum: 1 },
    endLine: { type: "integer", minimum: 1 },
  },
  required: ["path", "startLine", "endLine"],
  additionalProperties: false,
};

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

function appendToolExchange(history, id, args, payload) {
  history.append(ChatMessage.from({ role: "assistant", content: [{
    type: "toolCallRequest",
    toolCallRequest: { id, type: "function", name: "read_file_range", arguments: args },
  }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{
    type: "toolCallResult", toolCallId: id, content: JSON.stringify(payload),
  }] }));
}

function buildInitialHistory(contractText = "") {
  const history = Chat.empty();
  const system = contractText
    ? `${fixture.SYSTEM_PROMPT}\n\nUSER-SELECTED AUDIT TASK CONTRACT:\n${contractText}`
    : fixture.SYSTEM_PROMPT;
  history.append("system", system);
  history.append("user", "Collect exact evidence for the synthetic settlement audit before my follow-up.");
  const reads = [
    ["Assets/03.Scripts/Tycoon/GuestManager.cs", 1, 220],
    ["Assets/03.Scripts/Tycoon/GuestManager.cs", 837, 1056],
    ["Assets/03.Scripts/Tycoon/DailySales.cs", 1, 180],
    ["Assets/03.Scripts/Player/PlayerDataSO.cs", 1, 220],
    ["Assets/03.Scripts/UI/UICashPanel.cs", 1, 200],
    ["Assets/ProjectLifeScope.prefab", 1, 220],
    ["Assets/03.Scripts/Tycoon/SettlementFlow.cs", 1, 180],
    ["Evidence/RuntimeObservation.md", 1, 90],
  ];
  reads.forEach(([filePath, startLine, endLine], index) => appendToolExchange(
    history,
    `prefetch-${index + 1}`,
    { path: filePath, startLine, endLine },
    fixture.readPayload(filePath, startLine, endLine),
  ));
  appendToolExchange(history, "prefetch-failed", {
    path: "Assets/03.Scripts/UI/MissingCashPanel.cs", startLine: 1, endLine: 200,
  }, fixture.readPayload("Assets/03.Scripts/UI/MissingCashPanel.cs", 1, 200));
  history.append("user", fixture.AUDIT_QUESTION);
  appendToolExchange(history, "current-game-event", {
    path: "Assets/03.Scripts/Event/GameEvent.cs", startLine: 1, endLine: 150,
  }, fixture.readPayload("Assets/03.Scripts/Event/GameEvent.cs", 1, 150));
  return history;
}

function controller(history, tokenSource, tool, mode, recorder, configOverrides = {}) {
  const blocks = [];
  const debugValues = [];
  const statuses = [];
  const config = {
    projectEngine: "auto",
    projectIdentity: fixture.PROJECT_IDENTITY,
    observeOnly: false,
    showDebugInfo: true,
    softRemainingTokens: 38000,
    hardRemainingTokens: 5000,
    maxOutputReserve: 2200,
    safetyMarginTokens: 800,
    assumedContextLength: 66816,
    recentCompleteTurns: 0,
    compactAboveMessageCount: 12,
    maxCheckpointChars: 16000,
    maxToolResultChars: 1400,
    pastReasoningTokens: 0,
    toolStagnationAction: "warn",
    toolStagnationRounds: 5,
    generationRepetitionAction: "warn",
    generationRepeatCount: 3,
    inputAvailabilityMode: mode,
    auditCompletionMode: "off",
    auditResearchSeconds: 100,
    auditResearchRounds: 12,
    auditFinalSeconds: 70,
    auditFinalMaxTokens: 4096,
    reviewProgress: false,
    separateAttachments: false,
    ...configOverrides,
  };
  const session = { tools: [tool], [Symbol.dispose]() {} };
  return {
    abortSignal: AbortSignal.timeout(240000),
    guardAbort() { if (this.abortSignal.aborted) throw this.abortSignal.reason; },
    getPluginConfig() { return { get: key => config[key] }; },
    getWorkingDirectory() { return ""; },
    async pullHistory() { return history; },
    async tokenSource() { return tokenSource; },
    async startToolUseSession() { return session; },
    async requestConfirmToolCall() { return { type: "allow" }; },
    debug(value) {
      debugValues.push(value);
      if (value?.event === "direct_context_measurement") recorder.latestMeasurement = value;
    },
    createStatus(initial) {
      const status = { initial, texts: [], states: [], removed: false };
      statuses.push(status);
      return {
        setText(text) { status.texts.push(text); },
        setState(state) { status.states.push(state); },
        remove() { status.removed = true; },
      };
    },
    createContentBlock(options) {
      const block = { options, text: "", requests: [], results: [] };
      blocks.push(block);
      return {
        appendText(text) { block.text += text; },
        appendToolRequest(value) { block.requests.push(value); },
        appendToolResult(value) { block.results.push(value); },
        setStyle(style) { block.options.style = style; },
      };
    },
    blocks,
    debugValues,
    statuses,
  };
}

function availabilityEvidence(measurements, modelInputs, initialHistory, calls) {
  const historical = evalCore.oracleObservationsFromMessages(initialHistory).filter(item => item.verified);
  const comparisons = [];
  for (const measurement of measurements) {
    const input = modelInputs.get(measurement.modelInputId);
    const oracleEntries = input ? evalCore.oracleProjection(input, historical) : [];
    const comparison = evalCore.projectionMatchesOracle(
      measurement.inputAvailability?.entries || [], oracleEntries,
    );
    comparisons.push({
      modelInputId: measurement.modelInputId,
      ...comparison,
      currentFullCount: oracleEntries.filter(entry => entry.rawPresence === "full").length,
      currentPartialCount: oracleEntries.filter(entry => entry.rawPresence === "partial").length,
      currentAbsentCount: oracleEntries.filter(entry => entry.rawPresence === "none").length,
      currentUnknownCount: oracleEntries.filter(entry => entry.rawPresence === "unknown").length,
    });
    for (const call of calls.filter(item => item.causalModelInputId === measurement.modelInputId)) {
      const observation = evalCore.oracleObservation(call.payload);
      if (call.executionState === "succeeded" && observation?.verified) historical.push(observation);
    }
  }
  return comparisons;
}

async function oneRun(model, modelInfo, group, phase, pairIndex, seed, contractText, runOptions = {}) {
  const mode = runOptions.inputAvailabilityMode || (group === "B" ? "inject" : "observe");
  const contractSelected = runOptions.auditContractSelected ?? group === "C";
  const auditCompletionMode = runOptions.auditCompletionMode === "bounded" ? "bounded" : "off";
  const history = buildInitialHistory(contractSelected ? contractText : "");
  const recorder = {
    latestMeasurement: null,
    activeModelInputId: null,
    modelInputs: new Map(),
    calls: [],
    modelActs: [],
  };
  const tool = rawFunctionTool({
    name: "read_file_range",
    description: "Read an exact line range from one of the eight synthetic audit files. Returns at most 220 lines.",
    parametersJsonSchema: TOOL_SCHEMA,
    implementation: async args => {
      const payload = fixture.readPayload(args.path, args.startLine, args.endLine);
      recorder.calls.push({
        callKey: `${phase}:${pairIndex}:${group}:${recorder.calls.length + 1}`,
        causalModelInputId: recorder.activeModelInputId,
        executionState: payload.ok ? "succeeded" : "failed",
        args: { path: args.path, startLine: args.startLine, endLine: args.endLine },
        payload,
      });
      return payload;
    },
  });
  // The real MCP supplies this identity. Keep the runtime fixture on the same
  // read-only tool classification path when bounded completion is selected.
  tool.pluginIdentifier = "mcp/unreal-agent";
  const fixedModel = {
    identifier: model.identifier,
    getContextLength: () => model.getContextLength(),
    applyPromptTemplate: (chat, options) => model.applyPromptTemplate(chat, options),
    countTokens: text => model.countTokens(text),
    act: (chat, tools, options) => {
      const modelInputId = recorder.latestMeasurement?.modelInputId || `unmatched:${recorder.modelInputs.size}`;
      recorder.activeModelInputId = modelInputId;
      recorder.modelInputs.set(modelInputId, Chat.from(chat));
      const maxTokens = options.maxTokens ?? Number(runOptions.researchMaxTokens || 1800);
      recorder.modelActs.push({
        sequence: recorder.modelActs.length + 1,
        modelInputId,
        toolCount: tools.length,
        toolNames: tools.map(item => item.name),
        maxTokens,
      });
      return model.act(chat, tools, { ...options, temperature: 0, seed, maxTokens });
    },
  };
  const completionConfig = {
    auditCompletionMode,
    auditResearchSeconds: Number(runOptions.auditResearchSeconds || 100),
    auditResearchRounds: Number(runOptions.auditResearchRounds || 12),
    auditFinalSeconds: Number(runOptions.auditFinalSeconds || 70),
    auditFinalMaxTokens: Number(runOptions.auditFinalMaxTokens || 4096),
  };
  const ctl = controller(history, fixedModel, tool, mode, recorder, completionConfig);
  const started = Date.now();
  let error = null;
  try {
    await createPredictionLoopHandler()(ctl);
  } catch (caught) {
    error = String(caught?.stack || caught);
  }
  const visibleBlocks = ctl.blocks
    .filter(block => block.options?.style?.type !== "thinking")
    .map(block => block.text.trim()).filter(Boolean);
  const finalizations = ctl.debugValues.filter(value => value.event === "bounded_audit_finalization");
  const visibleAnswer = (finalizations.length ? visibleBlocks.at(-1) : visibleBlocks.join("\n")).trim();
  const measurements = ctl.debugValues.filter(value => value.event === "direct_context_measurement");
  const rounds = ctl.debugValues.filter(value => value.event === "direct_round_observation");
  const runtimeToolCalls = rounds.flatMap(round => round.toolTrace?.runtime || []);
  const uniqueRuntimeCalls = new Map(runtimeToolCalls.map(call => [call.callKey, call]));
  const comparisons = availabilityEvidence(measurements, recorder.modelInputs, history, recorder.calls);
  const causalMetrics = evalCore.evaluateCausalCalls({
    initialMessages: history, modelInputs: recorder.modelInputs, calls: recorder.calls,
  });
  const firstAvailability = comparisons[0] || {};
  const rubric = evalCore.auditAnswerMatchesOracle(visibleAnswer);
  const timedOut = ctl.abortSignal.aborted;
  const run = {
    phase,
    pairIndex,
    group,
    mode,
    auditContractSelected: contractSelected,
    auditCompletionMode,
    auditCompletionConfig: completionConfig,
    seed,
    modelIdentifier: model.identifier,
    modelPath: modelInfo.path || null,
    quantization: modelInfo.quantization?.name || modelInfo.quantization || null,
    loadedContextLength: modelInfo.contextLength || null,
    elapsedMs: Date.now() - started,
    timedOut,
    predictionRounds: rounds.length,
    compressedPredictionCount: measurements.filter(value => value.compactionAppliedCount > 0).length,
    compactionCount: measurements.reduce((sum, value) => sum + Number(value.compactionAppliedCount || 0), 0),
    firstInputCurrentFullCount: firstAvailability.currentFullCount ?? null,
    firstInputCurrentPartialCount: firstAvailability.currentPartialCount ?? null,
    firstInputCurrentAbsentCount: firstAvailability.currentAbsentCount ?? null,
    pressureExposure: measurements.some(value => value.compactionAppliedCount > 0)
      && Number(firstAvailability.currentFullCount || 0) > 0
      && Number(firstAvailability.currentAbsentCount || 0) > 0,
    generatedToolCalls: [...uniqueRuntimeCalls.values()].filter(call => (
      call.generationState === "generated" || call.generationState === "finalized"
    )).length,
    dispatchedToolCalls: [...uniqueRuntimeCalls.values()].filter(call => (
      call.executionState === "dispatched"
    )).length,
    implementationEnteredToolCalls: recorder.calls.length,
    callStatusCounts: causalMetrics.statusCounts,
    rangeMetricsByUnit: causalMetrics.byUnit,
    callMetrics: causalMetrics.perCall,
    excludedMetricCallCount: causalMetrics.excludedCallCount,
    answerEvaluation: rubric,
    availabilityProjectionMatchesOracle: comparisons.every(item => item.matches),
    availabilityComparisons: comparisons,
    modelInputs: measurements.map(value => ({
      modelInputId: value.modelInputId,
      finalInputTokens: value.finalInputTokens,
      finalRemainingTokens: value.finalRemainingTokens,
      metadataTokens: value.inputAvailability?.metadataTokens ?? null,
      compactionAppliedCount: value.compactionAppliedCount,
      compactionAppliedModes: value.compactionAppliedModes,
    })),
    visibleAnswer,
    error,
    modelActs: recorder.modelActs,
    boundedFinalizations: finalizations,
    boundedFinalModelCalls: recorder.modelActs.filter(act => act.modelInputId.endsWith(":final-report")).length,
    boundedFinalToolCalls: recorder.calls.filter(call => (
      String(call.causalModelInputId || "").endsWith(":final-report")
    )).length,
    trace: {
      executionId: measurements.at(-1)?.executionId || null,
      modelInputId: measurements.at(-1)?.modelInputId || null,
      roundFinishReasons: rounds.map(round => round.finishReason),
      roundPredictionStats: rounds.map(round => round.predictionStats || null),
    },
  };
  return { ...run, outcome: classifyRunOutcome(run) };
}

function classifyRunOutcome(run) {
  const answer = String(run?.visibleAnswer || "").trim();
  const finishReason = String(run?.trace?.roundFinishReasons?.at(-1) || "unknown");
  const boundedFinalization = run?.boundedFinalizations?.at(-1) || null;
  let executionOutcome;
  if (run?.timedOut) executionOutcome = "timed_out";
  else if (boundedFinalization?.finishReason === "final_timeout") executionOutcome = "timed_out";
  else if (finishReason === "userStopped") executionOutcome = "canceled";
  else if (run?.error) executionOutcome = "failed";
  else if (boundedFinalization?.deliveryState === "no_answer") executionOutcome = "no_answer";
  else if (boundedFinalization?.deliveryState === "truncated") executionOutcome = "truncated";
  else if (boundedFinalization?.deliveryState === "partial") executionOutcome = "failed";
  else if (boundedFinalization?.deliveryState === "complete") executionOutcome = "completed";
  else if (finishReason === "maxPredictedTokensReached") executionOutcome = "truncated";
  else if (!answer) executionOutcome = "no_answer";
  else executionOutcome = "completed";
  return {
    executionOutcome,
    reportPresent: Boolean(answer),
    reportCompleted: executionOutcome === "completed",
    deliveryState: executionOutcome === "completed" ? "complete"
      : !answer ? "no_answer"
        : executionOutcome === "truncated" ? "truncated" : "partial",
    finalFinishReason: finishReason,
  };
}

function summarize(runs, group) {
  const selected = runs.filter(run => run.group === group).map(run => ({
    ...run,
    outcome: run.outcome || classifyRunOutcome(run),
  }));
  const outcomeCounts = Object.fromEntries([
    "completed", "truncated", "no_answer", "failed", "canceled", "timed_out",
  ].map(outcome => [outcome, selected.filter(run => run.outcome.executionOutcome === outcome).length]));
  const successfulRawReturns = selected.reduce((sum, run) => (
    sum + Number(run.callStatusCounts?.succeeded || 0)
  ), 0);
  const measuredLineRuns = selected.filter(run => Number(run.rangeMetricsByUnit?.line?.returnedUnits || 0) > 0);
  return {
    n: selected.length,
    pressureExposureRuns: selected.filter(run => run.pressureExposure).length,
    outcomeCounts,
    successfulRuns: outcomeCounts.completed,
    errors: outcomeCounts.failed,
    timeouts: outcomeCounts.timed_out,
    totalCompactions: selected.reduce((sum, run) => sum + run.compactionCount, 0),
    generatedToolCalls: selected.reduce((sum, run) => sum + run.generatedToolCalls, 0),
    dispatchedToolCalls: selected.reduce((sum, run) => sum + Number(run.dispatchedToolCalls || 0), 0),
    implementationEnteredToolCalls: selected.reduce((sum, run) => (
      sum + Number(run.implementationEnteredToolCalls ?? run.generatedToolCalls ?? 0)
    ), 0),
    successfulRawReturns,
    historicalReacquisitionLines: selected.reduce((sum, run) => (
      sum + Number(run.rangeMetricsByUnit.line?.historicalReacquisitionUnits || 0)
    ), 0),
    historicalReacquisitionEvaluableRuns: measuredLineRuns.length,
    historicalReacquisitionEvaluation: measuredLineRuns.length
      ? "measured" : "not_evaluable_no_successful_line_return",
    groundedAnswers: selected.filter(run => run.answerEvaluation.pass).length,
    answerScores: selected.map(run => run.answerEvaluation.score),
    oracleProjectionMatches: selected.filter(run => run.availabilityProjectionMatchesOracle).length,
    elapsedMs: selected.map(run => run.elapsedMs),
  };
}

async function main() {
  const identifier = valueAfter("--model", "swift-qwen3.8-27b");
  const mainPairs = integerAfter("--main-pairs", 5);
  const contractPairs = integerAfter("--contract-pairs", 1);
  const acOnly = process.argv.includes("--ac-only");
  const output = path.resolve(valueAfter("--output", path.join(process.cwd(), "audit-pressure-ab-ac.json")));
  const contractPath = path.resolve(__dirname, "../eval/AUDIT_TASK_CONTRACT.md");
  const contractText = fs.readFileSync(contractPath, "utf8");
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
    // The SDK run remains valid; unavailable process metadata is explicitly null below.
  }
  const modelInfo = { ...loadedHandle, ...processInfo };
  const model = await client.llm.model(identifier);
  const runs = [];
  if (!acOnly) {
    for (const group of ["A", "B"]) {
      const run = await oneRun(model, modelInfo, group, "pilot", 1, 4101, contractText);
      runs.push(run);
      process.stdout.write(`${JSON.stringify({ phase: run.phase, group, pressureExposure: run.pressureExposure,
        compactions: run.compactionCount, calls: run.generatedToolCalls, score: run.answerEvaluation.score,
        outcome: run.outcome, elapsedMs: run.elapsedMs, error: run.error })}\n`);
    }
  }
  const pilotExposureSatisfied = acOnly ? null : runs
    .filter(run => run.phase === "pilot")
    .every(run => run.pressureExposure);
  const pilotSuccessfulRuns = runs
    .filter(run => run.phase === "pilot" && run.outcome.executionOutcome === "completed").length;
  if (!acOnly && pilotExposureSatisfied) {
    for (let pairIndex = 1; pairIndex <= mainPairs; pairIndex += 1) {
      const groups = pairIndex % 2 === 0 ? ["B", "A"] : ["A", "B"];
      for (const group of groups) {
        const run = await oneRun(model, modelInfo, group, "main-ab", pairIndex, 5000 + pairIndex, contractText);
        runs.push(run);
        process.stdout.write(`${JSON.stringify({ phase: run.phase, pairIndex, group,
          pressureExposure: run.pressureExposure, compactions: run.compactionCount,
          calls: run.generatedToolCalls, score: run.answerEvaluation.score,
          outcome: run.outcome, elapsedMs: run.elapsedMs, error: run.error })}\n`);
      }
    }
  }
  if (acOnly || pilotExposureSatisfied) {
    for (let pairIndex = 1; pairIndex <= contractPairs; pairIndex += 1) {
      for (const group of pairIndex % 2 === 0 ? ["C", "A"] : ["A", "C"]) {
        const run = await oneRun(model, modelInfo, group, "exploratory-ac", pairIndex,
          6000 + pairIndex, contractText);
        runs.push(run);
        process.stdout.write(`${JSON.stringify({ phase: run.phase, pairIndex, group,
          pressureExposure: run.pressureExposure, compactions: run.compactionCount,
          calls: run.generatedToolCalls, score: run.answerEvaluation.score,
          outcome: run.outcome, elapsedMs: run.elapsedMs, error: run.error })}\n`);
      }
    }
  }

  const repositoryRoot = path.resolve(__dirname, "../..");
  const packageMetadata = require("../package.json");
  const manifestMetadata = require("../manifest.json");
  const sdkMetadata = JSON.parse(fs.readFileSync(path.resolve(
    __dirname, "../node_modules/@lmstudio/sdk/package.json"), "utf8"));
  const sourceHead = execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: repositoryRoot, encoding: "utf8", windowsHide: true, timeout: 10000,
  }).trim();
  const sourceWorktreeDirty = Boolean(execFileSync("git", ["status", "--porcelain", "--",
    "lmstudio-context-compactor-plugin"], {
    cwd: repositoryRoot, encoding: "utf8", windowsHide: true, timeout: 10000,
  }).trim());
  const hashedFiles = [
    path.resolve(__dirname, "../dist/prediction-loop.js"),
    path.resolve(__dirname, "../dist/input-availability.js"),
    path.resolve(__dirname, "availability-eval-core.cjs"),
    path.resolve(__dirname, "audit-pressure-fixture.cjs"),
    contractPath,
  ];
  const sourceBundleSha256 = sha256(hashedFiles.map(file => (
    `${path.basename(file)}:${sha256(fs.readFileSync(file))}`
  )).join("\n"));
  const mainRuns = runs.filter(run => run.phase === "main-ab");
  const contractRuns = runs.filter(run => run.phase === "exploratory-ac");
  const report = {
    experiment: "compressed-audit-metadata-A-B-and-user-contract-A-C",
    executionMode: acOnly ? "A-C only (explicit CLI selection)" : "pilot, main A-B, exploratory A-C",
    generatedAt: new Date().toISOString(),
    sourceScope: "repository dist exercised directly; installed plugin and active chats were not modified",
    sourceHead,
    sourceWorktreeDirty,
    packageVersion: packageMetadata.version,
    manifestRevision: manifestMetadata.revision,
    sdkVersion: sdkMetadata.version,
    sourceBundleSha256,
    fixture: {
      fileCount: fixture.files.length,
      projectIdentity: fixture.PROJECT_IDENTITY,
      fileVersions: fixture.files.map(file => ({ path: file.path, sha256: file.sha256,
        totalLines: file.lines.length })),
      systemPromptSha256: sha256(fixture.SYSTEM_PROMPT),
      auditQuestionSha256: sha256(fixture.AUDIT_QUESTION),
      toolSchemaSha256: sha256(JSON.stringify(TOOL_SCHEMA)),
      deliberatelyFailedInitialRead: "Assets/03.Scripts/UI/MissingCashPanel.cs",
      retainedCurrentRaw: "Assets/03.Scripts/Event/GameEvent.cs:1-150",
    },
    model: {
      identifier: model.identifier,
      path: modelInfo.path || null,
      quantization: modelInfo.quantization || null,
      loadedContextLength: modelInfo.contextLength || null,
      maxContextLength: modelInfo.maxContextLength || null,
      temperature: 0,
      maxTokens: 1800,
      seedPolicy: "same seed within each comparison pair",
    },
    groups: {
      A: "Observe metadata mode; base audit prompt",
      B: "Inject metadata mode; otherwise identical to A",
      C: "Observe metadata mode; user-selected AUDIT_TASK_CONTRACT appended to system prompt",
    },
    comparisons: {
      metadataAB: {
        pilotExposureSatisfied,
        pilotSuccessfulRuns,
        requestedMainPairs: mainPairs,
        recordedMainPairs: Math.min(
          mainRuns.filter(run => run.group === "A").length,
          mainRuns.filter(run => run.group === "B").length,
        ),
        completedReportPairs: Math.min(
          mainRuns.filter(run => run.group === "A" && run.outcome.executionOutcome === "completed").length,
          mainRuns.filter(run => run.group === "B" && run.outcome.executionOutcome === "completed").length,
        ),
        A: summarize(mainRuns, "A"),
        B: summarize(mainRuns, "B"),
      },
      auditInstructionsAC: {
        exploratory: true,
        requestedPairs: contractPairs,
        recordedPairs: Math.min(
          contractRuns.filter(run => run.group === "A").length,
          contractRuns.filter(run => run.group === "C").length,
        ),
        completedReportPairs: Math.min(
          contractRuns.filter(run => run.group === "A" && run.outcome.executionOutcome === "completed").length,
          contractRuns.filter(run => run.group === "C" && run.outcome.executionOutcome === "completed").length,
        ),
        A: summarize(contractRuns, "A"),
        C: summarize(contractRuns, "C"),
      },
    },
    pilot: { A: summarize(runs.filter(run => run.phase === "pilot"), "A"),
      B: summarize(runs.filter(run => run.phase === "pilot"), "B") },
    runs,
    interpretationBoundary: [
      "The fixture is synthetic and does not reconstruct missing raw payloads from the original 865-line transcript.",
      "A successful reread is measured against its causal model input; it is not automatically labeled unnecessary.",
      "Structural pressure exposure and final-answer behavior are separate outcomes.",
      "The answer rubric is deterministic but still marked human-review-required.",
      "The audit contract is selected only for group C by this evaluation script; product code does not inject it.",
      "Failed calls, timeouts, and no-compaction runs remain in runs and summary counts.",
    ],
  };
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({ output, pilotExposureSatisfied,
    comparisons: report.comparisons })}\n`);
  if (runs.some(run => run.outcome.executionOutcome !== "completed")) process.exitCode = 1;
}

if (require.main === module) {
  main().catch(error => {
    console.error(error?.stack || error);
    process.exitCode = 1;
  });
}

module.exports = { buildInitialHistory, classifyRunOutcome, oneRun, summarize };

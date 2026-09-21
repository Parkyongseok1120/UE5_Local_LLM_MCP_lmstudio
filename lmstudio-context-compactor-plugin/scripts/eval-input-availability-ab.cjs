#!/usr/bin/env node
"use strict";

// Explicit live-model experiment. It exercises the repository's built
// prediction-loop handler without installing or replacing the user's plugin.
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { Chat, ChatMessage, LMStudioClient, rawFunctionTool } = require("@lmstudio/sdk");
const { createPredictionLoopHandler } = require("../dist/prediction-loop.js");
const evalCore = require("./availability-eval-core.cjs");

const SOURCE_LINES = [
  'public const string SettlementOwner = "DailySales";',
  "public const bool DuplicateGuard = true;",
];
const SOURCE = SOURCE_LINES.join("\n");
const VERSION = crypto.createHash("sha256").update(SOURCE).digest("hex");
const SYSTEM_PROMPT = [
  "You are reviewing a two-line synthetic code fixture.",
  "Tool use remains your decision. Do not claim that review is complete merely because input-presence metadata exists.",
  "Answer the user's exact question concisely and preserve literal identifier/value spelling.",
].join(" ");
const USER_QUESTION = [
  "Using the exact file version and raw range available to you now, report only these two literal facts:",
  "SettlementOwner and DuplicateGuard.",
  "You may call read_file if you decide another read is necessary.",
].join(" ");
const TOOL_SCHEMA = {
  type: "object",
  properties: { path: { type: "string", const: "Assets/SyntheticSettlement.cs" } },
  required: ["path"],
  additionalProperties: false,
};

const {
  auditAnswerMatchesOracle,
  answerMatchesOracle,
  calculateReadMetrics,
  evaluateCausalCalls,
  oracleObservation,
  oracleObservationsFromMessages,
  oracleProjection,
  projectionMatchesOracle,
} = evalCore;

function valueAfter(flag, fallback = "") {
  const index = process.argv.indexOf(flag);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

function integerAfter(flag, fallback) {
  const value = Number(valueAfter(flag, fallback));
  return Number.isSafeInteger(value) && value >= 0 ? value : fallback;
}

function initialHistory() {
  const request = { id: "fixture-read-1", type: "function", name: "read_file",
    arguments: { path: "Assets/SyntheticSettlement.cs" } };
  const payload = {
    ok: true,
    kind: "workspace_file_observation",
    projectIdentity: "synthetic-project-v1",
    path: "Assets/SyntheticSettlement.cs",
    sha256: VERSION,
    startLine: 1,
    endLine: 2,
    returnedLineCount: 2,
    totalLines: 2,
    content: SOURCE,
  };
  const history = Chat.empty();
  history.append("system", SYSTEM_PROMPT);
  history.append("user", "Inspect Assets/SyntheticSettlement.cs so I can ask a follow-up.");
  history.append(ChatMessage.from({ role: "assistant", content: [
    { type: "toolCallRequest", toolCallRequest: request },
  ] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult",
    toolCallId: request.id, content: JSON.stringify(payload) }] }));
  history.append("user", USER_QUESTION);
  return { history, payload };
}

function controller(history, tokenSource, tool, mode, recorder, configOverrides = {}) {
  const blocks = [];
  const debugValues = [];
  const statuses = [];
  const config = {
    projectEngine: "auto",
    projectIdentity: "",
    observeOnly: false,
    showDebugInfo: true,
    softRemainingTokens: 14000,
    hardRemainingTokens: 8000,
    // This is the explicit evaluation cap. The product now passes the same
    // value as maxTokens; the wrapper must not silently replace it.
    maxOutputReserve: 1200,
    safetyMarginTokens: 1024,
    assumedContextLength: 66816,
    recentCompleteTurns: 2,
    compactAboveMessageCount: 24,
    maxCheckpointChars: 22000,
    maxToolResultChars: 1200,
    pastReasoningTokens: 0,
    toolStagnationAction: "warn",
    toolStagnationRounds: 3,
    generationRepetitionAction: "warn",
    generationRepeatCount: 3,
    inputAvailabilityMode: mode,
    outputRecoveryMode: "off",
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

async function oneRun(model, modelInfo, group, phase, pairIndex, seed) {
  const mode = group === "A" ? "observe" : "inject";
  const { history, payload } = initialHistory();
  const recorder = { latestMeasurement: null, activeModelInputId: null, modelInputs: new Map(), calls: [] };
  const tool = rawFunctionTool({
    name: "read_file",
    description: "Read the exact two-line synthetic fixture. Calling it is always allowed but is never automatic.",
    parametersJsonSchema: TOOL_SCHEMA,
    implementation: async (args) => {
      recorder.calls.push({
        callKey: `smoke:${phase}:${pairIndex}:${group}:${recorder.calls.length}`,
        causalModelInputId: recorder.activeModelInputId,
        executionState: "succeeded",
        argsFingerprint: crypto.createHash("sha256").update(JSON.stringify(args)).digest("hex"),
        payload,
      });
      return payload;
    },
  });
  const fixedModel = {
    identifier: model.identifier,
    getContextLength: () => model.getContextLength(),
    applyPromptTemplate: (chat, options) => model.applyPromptTemplate(chat, options),
    countTokens: text => model.countTokens(text),
    act: (chat, tools, options) => {
      const modelInputId = recorder.latestMeasurement?.modelInputId || `unmatched:${recorder.modelInputs.size}`;
      recorder.activeModelInputId = modelInputId;
      recorder.modelInputs.set(modelInputId, Chat.from(chat));
      return model.act(chat, tools, {
        ...options,
        temperature: 0,
        seed,
      });
    },
  };
  const ctl = controller(history, fixedModel, tool, mode, recorder);
  const started = Date.now();
  let error = null;
  try {
    await createPredictionLoopHandler()(ctl);
  } catch (caught) {
    error = String(caught?.stack || caught);
  }
  const visibleAnswer = ctl.blocks
    .filter(block => block.options?.style?.type !== "thinking")
    .map(block => block.text).join("").trim();
  const measurements = ctl.debugValues.filter(value => value.event === "direct_context_measurement");
  const rounds = ctl.debugValues.filter(value => value.event === "direct_round_observation");
  const causalMetrics = evaluateCausalCalls({
    initialMessages: history,
    modelInputs: recorder.modelInputs,
    calls: recorder.calls,
  });
  const lineMetrics = causalMetrics.byUnit.line || {
    returnedUnits: 0,
    inputOverlapUnits: 0,
    historicalReacquisitionUnits: 0,
    newEvidenceUnits: 0,
    overlapRate: null,
  };
  const historical = oracleObservationsFromMessages(history).filter(item => item.verified);
  const availabilityComparisons = [];
  for (const measurement of measurements) {
    const input = recorder.modelInputs.get(measurement.modelInputId);
    const oracleEntries = input ? oracleProjection(input, historical) : [];
    const comparison = projectionMatchesOracle(
      measurement.inputAvailability?.entries || [], oracleEntries,
    );
    availabilityComparisons.push({
      modelInputId: measurement.modelInputId,
      ...comparison,
    });
    for (const call of recorder.calls.filter(item => item.causalModelInputId === measurement.modelInputId)) {
      const observation = oracleObservation(call.payload);
      if (call.executionState === "succeeded" && observation?.verified) historical.push(observation);
    }
  }
  const answerEvaluation = answerMatchesOracle(visibleAnswer);
  return {
    phase,
    pairIndex,
    group,
    mode,
    seed,
    modelIdentifier: model.identifier,
    modelPath: modelInfo.path || null,
    quantization: modelInfo.quantization?.name || null,
    loadedContextLength: modelInfo.contextLength || null,
    elapsedMs: Date.now() - started,
    predictionRounds: rounds.length,
    compressedPredictionCount: measurements.filter(value => value.compactionAppliedCount > 0).length,
    compactionCount: measurements.reduce((sum, value) => sum + Number(value.compactionAppliedCount || 0), 0),
    actualSuccessfulReads: causalMetrics.statusCounts.succeeded,
    returnedLines: lineMetrics.returnedUnits,
    inputOverlapLines: lineMetrics.inputOverlapUnits,
    inInputReturnOverlap: lineMetrics.overlapRate,
    historicalReacquisitionLines: lineMetrics.historicalReacquisitionUnits,
    newEvidenceLines: lineMetrics.newEvidenceUnits,
    rangeMetricsByUnit: causalMetrics.byUnit,
    callMetrics: causalMetrics.perCall,
    callStatusCounts: causalMetrics.statusCounts,
    excludedMetricCallCount: causalMetrics.excludedCallCount,
    metadataTokens: measurements.reduce((sum, value) => (
      sum + Number(value.inputAvailability?.metadataTokens || 0)
    ), 0),
    finalInputTokens: measurements.at(-1)?.finalInputTokens ?? null,
    modelInputs: measurements.map(value => ({
      modelInputId: value.modelInputId,
      finalInputTokens: value.finalInputTokens,
      metadataTokens: value.inputAvailability?.metadataTokens ?? null,
      compactionAppliedCount: value.compactionAppliedCount,
      compactionAppliedModes: value.compactionAppliedModes,
    })),
    groundedFinalAnswer: answerEvaluation.pass,
    answerEvaluation,
    availabilityProjectionMatchesOracle: availabilityComparisons.every(item => item.matches),
    availabilityComparisons,
    visibleAnswer,
    error,
    trace: {
      executionId: measurements.at(-1)?.executionId || null,
      modelInputId: measurements.at(-1)?.modelInputId || null,
      hostInputVerification: measurements.at(-1)?.inputAvailability?.hostInputVerification || null,
      roundFinishReasons: rounds.map(round => round.finishReason),
    },
  };
}

async function main() {
  const identifier = valueAfter("--model", "swift-qwen3.8-27b");
  const pairs = integerAfter("--pairs", 5);
  const pilotPairs = integerAfter("--pilot-pairs", 1);
  const output = path.resolve(valueAfter("--output", path.join(process.cwd(), "input-availability-ab.json")));
  const client = new LMStudioClient();
  const loaded = await client.llm.listLoaded();
  const loadedHandle = loaded.find(item => item.identifier === identifier);
  if (!loadedHandle) throw new Error(`Loaded model not found: ${identifier}`);
  let processInfo = {};
  try {
    const processes = JSON.parse(execFileSync("lms", ["ps", "--json"], {
      encoding: "utf8", windowsHide: true, timeout: 10000,
    }));
    processInfo = processes.find(item => item.identifier === identifier) || {};
  } catch {
    // SDK execution can continue. Missing CLI process metadata stays unknown.
  }
  const modelInfo = { ...loadedHandle, ...processInfo };
  const model = await client.llm.model(identifier);
  const bundlePaths = ["prediction-loop.js", "input-availability.js"]
    .map(name => path.resolve(__dirname, "../dist", name));
  const packageMetadata = require("../package.json");
  const manifestMetadata = require("../manifest.json");
  const sdkMetadata = JSON.parse(fs.readFileSync(path.resolve(
    __dirname, "../node_modules/@lmstudio/sdk/package.json"), "utf8"));
  const repositoryRoot = path.resolve(__dirname, "../..");
  const sourceHead = execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: repositoryRoot, encoding: "utf8", windowsHide: true, timeout: 10000,
  }).trim();
  const sourceWorktreeDirty = Boolean(execFileSync("git", ["status", "--porcelain", "--",
    "lmstudio-context-compactor-plugin"], {
    cwd: repositoryRoot, encoding: "utf8", windowsHide: true, timeout: 10000,
  }).trim());
  const runs = [];
  for (const [phase, count] of [["pilot", pilotPairs], ["main", pairs]]) {
    for (let pairIndex = 1; pairIndex <= count; pairIndex += 1) {
      const seed = (phase === "pilot" ? 1000 : 2000) + pairIndex;
      for (const group of pairIndex % 2 === 0 ? ["B", "A"] : ["A", "B"]) {
        const run = await oneRun(model, modelInfo, group, phase, pairIndex, seed);
        runs.push(run);
        process.stdout.write(`${JSON.stringify({ phase, pairIndex, group,
          reads: run.actualSuccessfulReads, grounded: run.groundedFinalAnswer,
          elapsedMs: run.elapsedMs, error: run.error })}\n`);
      }
    }
  }
  const mainRuns = runs.filter(run => run.phase === "main");
  const byGroup = Object.fromEntries(["A", "B"].map(group => {
    const selected = mainRuns.filter(run => run.group === group);
    return [group, {
      n: selected.length,
      totalSuccessfulReads: selected.reduce((sum, run) => sum + run.actualSuccessfulReads, 0),
      groundedFinalAnswers: selected.filter(run => run.groundedFinalAnswer).length,
      availabilityOracleMatches: selected.filter(run => run.availabilityProjectionMatchesOracle).length,
      totalCompactions: selected.reduce((sum, run) => sum + run.compactionCount, 0),
      errors: selected.filter(run => run.error).length,
      totalElapsedMs: selected.reduce((sum, run) => sum + run.elapsedMs, 0),
      metadataTokens: selected.map(run => run.metadataTokens),
    }];
  }));
  const report = {
    experiment: "current-input-availability-A-B",
    generatedAt: new Date().toISOString(),
    sourceScope: "repository dist exercised directly; installed plugin was not modified",
    sourceHead,
    sourceWorktreeDirty,
    packageVersion: packageMetadata.version,
    manifestRevision: manifestMetadata.revision,
    sdkVersion: sdkMetadata.version,
    sourceBundleSha256: crypto.createHash("sha256")
      .update(bundlePaths.map(bundlePath => fs.readFileSync(bundlePath)).join("\n--module-boundary--\n"))
      .digest("hex"),
    fixtureVersion: VERSION,
    systemPromptSha256: crypto.createHash("sha256").update(SYSTEM_PROMPT).digest("hex"),
    userQuestionSha256: crypto.createHash("sha256").update(USER_QUESTION).digest("hex"),
    toolSchemaSha256: crypto.createHash("sha256").update(JSON.stringify(TOOL_SCHEMA)).digest("hex"),
    model: {
      identifier: model.identifier,
      path: modelInfo.path || null,
      quantization: modelInfo.quantization || null,
      loadedContextLength: modelInfo.contextLength || null,
      maxContextLength: modelInfo.maxContextLength || null,
      temperature: 0,
      maxTokens: 1200,
      seedPolicy: "same seed within each A/B pair",
    },
    groups: {
      A: "current behavior plus non-injecting observation",
      B: "A plus verified current-input availability metadata",
    },
    pilotPairs,
    mainPairs: pairs,
    byGroup,
    runs,
    interpretationBoundary: [
      "Synthetic live-model reproduction; it does not reconstruct the original 623-line session.",
      "An overlapping reread is measured, not automatically classified as unnecessary.",
      "Host delivery beyond the final SDK chat boundary remains unknown.",
    ],
  };
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, `${JSON.stringify(report, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({ output, byGroup })}\n`);
  if (runs.some(run => run.error)) process.exitCode = 1;
}

if (require.main === module) {
  main().catch(error => {
    console.error(error?.stack || error);
    process.exitCode = 1;
  });
}

module.exports = {
  auditAnswerMatchesOracle,
  answerMatchesOracle,
  calculateReadMetrics,
  evaluateCausalCalls,
  oracleObservation,
  oracleObservationsFromMessages,
  oracleProjection,
  projectionMatchesOracle,
};

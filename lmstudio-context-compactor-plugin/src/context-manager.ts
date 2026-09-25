import {
  Chat,
  ChatMessage,
  type LLMTool,
  type PredictionLoopHandlerController
} from "@lmstudio/sdk";
import { BudgetBroker } from "./budget-broker";
import { boundPastReasoning, resolveGenerationBudget, selectMeasuredCandidate } from "./context-budget";
import { core, inputAvailability, modelNotes, workingContextModule } from "./context-ports";
import { telemetryFingerprint } from "./evidence-telemetry";
import { numeric } from "./execution-config";
import { type CheckpointResult, type ContextMeasurement, type ContinuityNote, type DirectConfig, type NormalizedMessage } from "./execution-contracts";
import { visibleAssistantOutput } from "./raw-tool-intent";
import { hasReadCapability, type RemoteToolLike } from "./tool-capability-registry";

type MeasurementCost = { measurementCalls: number; measurementMs: number; templateCalls: number;
  tokenCountCalls: number; contextLengthCalls: number; cacheHits: number };

export function normalizeMessages(messages: Array<ChatMessage>): Array<NormalizedMessage> {
  return messages.map((message) => ({
    role: message.getRole(),
    text: message.getText(),
    hasFiles: message.hasFiles(),
    toolRequests: message.getToolCallRequests(),
    toolResults: message.getToolCallResults(),
  }));
}

export function normalizeHistory(history: Chat): Array<NormalizedMessage> {
  return normalizeMessages(history.getMessagesArray());
}

export function toolDefinitionSurface(tools: Array<RemoteToolLike>): Array<Record<string, unknown>> {
  return tools.map(tool => ({
    type: "function",
    function: {
      name: tool.name,
      ...(tool.description ? { description: tool.description } : {}),
      ...(tool.parametersJsonSchema ? { parameters: tool.parametersJsonSchema } : {}),
    },
  }));
}

export async function measureContext(
  tokenSource: unknown,
  history: Chat,
  config: DirectConfig,
  tools: Array<RemoteToolLike & { description?: string; parametersJsonSchema?: unknown }> = [],
  options: { outputReserve?: number; cost?: MeasurementCost } = {},
): Promise<ContextMeasurement> {
  const started = Date.now(), cost = options.cost;
  if (cost) cost.measurementCalls++;
  const source = tokenSource as {
    getContextLength?: () => Promise<number>;
    applyPromptTemplate?: (chat: Chat, opts?: { toolDefinitions?: Array<LLMTool> }) => Promise<string>;
    countTokens?: (text: string) => Promise<number>;
  };
  const toolDefinitions: Array<LLMTool> = toolDefinitionSurface(tools) as Array<LLMTool>;
  const toolSchemaJson = toolDefinitions.length > 0 ? JSON.stringify(toolDefinitions) : "";
  const toolSchemaChars = toolSchemaJson.length;
  const toolSchemaFingerprint = telemetryFingerprint(toolDefinitions);
  let contextLength = config.assumedContextLength;
  let contextLengthSource: ContextMeasurement["contextLengthSource"] = "configured_fallback";
  let inputTokens = Math.ceil(
    (history.toString().length + toolSchemaChars) / 4,
  );
  let promptMeasurementSource: ContextMeasurement["promptMeasurementSource"] = "character_estimate";
  let templatedInputChars: number | null = null;
  let toolSchemaTokens: number | null = toolSchemaChars > 0 ? Math.ceil(toolSchemaChars / 4) : 0;
  let toolSchemaTokenMeasurement: ContextMeasurement["toolSchemaTokenMeasurement"] = toolSchemaChars > 0
    ? "estimate" : "none";
  let templatedInputFingerprint: string | null = null;
  let exact = false;
  try {
    if (typeof source.getContextLength === "function") {
      if (cost) cost.contextLengthCalls++;
      const loadedContextLength = await source.getContextLength();
      if (!Number.isSafeInteger(loadedContextLength) || loadedContextLength < 2048 || loadedContextLength > 4_000_000)
        throw new Error("invalid_runtime_context_length");
      contextLength = loadedContextLength;
      contextLengthSource = "model";
    }
    if (typeof source.applyPromptTemplate === "function" && typeof source.countTokens === "function") {
      if (cost) cost.templateCalls++;
      const prompt = await source.applyPromptTemplate(history, { toolDefinitions });
      templatedInputChars = String(prompt).length;
      templatedInputFingerprint = telemetryFingerprint(String(prompt));
      if (cost) cost.tokenCountCalls++;
      const counted = await source.countTokens(prompt);
      if (!Number.isSafeInteger(counted) || counted < 0) throw new Error("invalid_token_measurement");
      inputTokens = counted;
      promptMeasurementSource = "templated_model_count";
      exact = true;
      if (toolSchemaChars > 0) {
        try {
          if (cost) cost.tokenCountCalls++;
          toolSchemaTokens = numeric(
            await source.countTokens(toolSchemaJson), toolSchemaTokens, 0, 4_000_000,
          );
          toolSchemaTokenMeasurement = "exact";
        } catch {
          // Full prompt measurement remains exact; standalone schema cost is estimated.
        }
      }
    }
  } catch {
    // A selected generator or experimental model handle may not expose token
    // measurement. The character estimate is diagnostic only: it cannot
    // certify a safe model call for an unknown tokenizer/template.
  }
  const outputReserve = numeric(options.outputReserve, config.maxOutputReserve, 0, 131072);
  const remainingTokens = contextLength - inputTokens - outputReserve - config.safetyMarginTokens;
  if (cost) cost.measurementMs += Date.now() - started;
  return {
    contextLength, contextLengthSource, promptMeasurementSource, templatedInputChars,
    inputTokens,
    toolSchemaChars,
    toolSchemaTokens,
    toolSchemaTokenMeasurement,
    toolSchemaFingerprint,
    templatedInputFingerprint,
    outputReserve,
    remainingTokens,
    exact,
    messageCount: history.length,
    fit: remainingTokens >= 0 ? (exact ? true : null) : false,
  };
}

export function buildCompactedHistory(
  history: Chat,
  recentCompleteTurns: number,
  config: DirectConfig,
  checkpointOptions: Record<string, unknown> = {},
): { history: Chat; checkpoint: CheckpointResult } {
  const messages = history.getMessagesArray();
  const normalized = normalizeHistory(history);
  const checkpoint = core.buildCheckpoint(normalized, {
    recentCompleteTurns,
    maxCheckpointChars: config.maxCheckpointChars,
    maxToolResultChars: config.maxToolResultChars,
    ...checkpointOptions,
  });
  if (checkpoint.omittedMessageCount <= 0) return { history, checkpoint };
  const retained = new Set(checkpoint.retainedIndexes);
  const compacted = Chat.empty();
  const systemText = [
    ...normalized.filter(message => message.role === "system")
      .map(message => core.invariantSystemText(message)).filter(Boolean),
    checkpoint.checkpoint,
  ].filter(Boolean).join("\n\n");
  if (systemText) compacted.append("system", systemText);
  if (checkpoint.assistantCheckpoint) compacted.append("assistant", checkpoint.assistantCheckpoint);
  for (let index = 0;index < messages.length;index += 1) {
    if (!messages[index].isSystemPrompt() && retained.has(index)) compacted.append(messages[index]);
  }
  return { history: compacted, checkpoint };
}

export type CompactedHistory = ReturnType<typeof buildCompactedHistory>;

export type SoftCompactionSelection = {
  candidate: CompactedHistory;
  targetRemainingTokens: number;
  remainingTokensAfter: number | null;
  maxCurrentTurnMessages: number | null;
  fit: boolean | null;
};

export function targetRemainingForInput(measurement: ContextMeasurement, config: DirectConfig, hasReadTools = false): number {
  if (config.contextManagementMode === "legacy" || !measurement.exact) return 0;
  const water = new BudgetBroker(config).watermarks(measurement, 0, hasReadTools);
  return Math.max(0, water.hardInputCeiling - water.configuredLowWaterTokens);
}

export function shouldCompactContext(measurement: ContextMeasurement, config: DirectConfig, hasReadTools = false): boolean {
  if (config.observeOnly) return false;
  return core.shouldCompact(measurement, config)
    || (config.contextManagementMode !== "legacy" && measurement.exact
      && measurement.inputTokens > new BudgetBroker(config).watermarks(measurement, 0, hasReadTools).effectiveHighWaterTokens);
}

export async function selectSoftCompaction(
  history: Chat,
  config: DirectConfig,
  checkpointOptions: Record<string, unknown>,
  measureCandidate: (history: Chat) => Promise<ContextMeasurement>,
  minimumTargetRemainingTokens = 0,
): Promise<SoftCompactionSelection> {
  const targetRemainingTokens = Math.max(minimumTargetRemainingTokens, config.softRemainingTokens
    + Math.max(1024, Math.trunc(Math.max(0,
      config.softRemainingTokens - config.hardRemainingTokens) / 2)));
  const recent = buildCompactedHistory(
    history, config.recentCompleteTurns, config, checkpointOptions,
  );
  if (recent.history !== history) {
    const measured = await measureCandidate(recent.history);
    if (measured.remainingTokens >= targetRemainingTokens) {
      return {
        candidate: recent, targetRemainingTokens,
        remainingTokensAfter: measured.remainingTokens, maxCurrentTurnMessages: null, fit: true
      };
    }
  }

  const selected = await selectMeasuredCandidate(history.length, targetRemainingTokens,
    cap => buildCompactedHistory(history, 0, config, { ...checkpointOptions, maxCurrentTurnMessages: cap }),
    async candidate => candidate.history === history ? null : (await measureCandidate(candidate.history)).remainingTokens);
  return { ...selected, targetRemainingTokens };

}

export function composeModelHistory(
  history: Chat,
  note: ContinuityNote | null,
  config: DirectConfig,
  systemInstructions: Array<string> = [],
  enableNoteProtocol = Boolean(note),
) {
  const instructions = systemInstructions.map((value) => String(value || "").trim()).filter(Boolean);
  const messages = history.getMessagesArray();
  const systemIndexes = messages.map((message, index) => message.isSystemPrompt() ? index : -1)
    .filter((index) => index >= 0);
  const systemLayoutIsCompatible = systemIndexes.length === 0
    || (systemIndexes.length === 1 && systemIndexes[0] === 0);
  if (!enableNoteProtocol && instructions.length === 0 && systemLayoutIsCompatible) {
    return { history, overhead: 0 };
  }
  const noteText = note ? modelNotes.renderAssistantNote(note) : "";
  const instruction = config.reviewProgress ? modelNotes.NOTE_INSTRUCTION.replace(
    "No other keys. refs may contain",
    "The optional reviewClaims array uses [{path, sha256, reviewScope, statement, refs}]; reviewClaims require an observed file hash and tool-call refs and are assistant claims, never verified completion. No other keys. refs may contain") : modelNotes.NOTE_INSTRUCTION;
  const canIncludeInstruction = enableNoteProtocol
    && config.maxCheckpointChars >= 2000 + instruction.length;
  const canIncludeNote = canIncludeInstruction
    && config.maxCheckpointChars >= 2000 + instruction.length + noteText.length;
  const overhead = canIncludeInstruction ? instruction.length + (canIncludeNote ? noteText.length : 0) : 0;
  const composed = Chat.empty();
  const combinedSystemText = [
    ...messages.filter((message) => message.isSystemPrompt())
      .map((message) => message.getText().trim()).filter(Boolean),
    ...instructions,
    ...(canIncludeInstruction ? [instruction] : []),
  ].join("\n\n");
  if (combinedSystemText) composed.append("system", combinedSystemText);
  if (canIncludeNote && noteText) composed.append("assistant", noteText);
  for (const message of messages) {
    if (!message.isSystemPrompt()) composed.append(message);
  }
  return { history: composed, overhead };
}

export function modelHistoryMessage(message: ChatMessage): ChatMessage {
  if (!message.isAssistantMessage() || !message.getText()) return message;
  const extracted = modelNotes.splitVisibleAnswer(message.getText());
  if (!extracted.hasFooter || extracted.visibleText === message.getText()) return message;
  const copy = ChatMessage.from(message);
  // Keep the SDK's raw reasoning/non-reasoning serialization for the next
  // tool round. Only the private continuity footer is removed from model input.
  copy.replaceText(extracted.visibleText);
  return copy;
}

export type InputAssemblyOptions = {
  tokenSource: unknown; config: DirectConfig; roundTools: Array<RemoteToolLike>;
  roundScopeInstructions: Array<string>; getRoundOutputReserve: () => number;
  modelInputId: string; noteEnabled: boolean; getNote: () => ContinuityNote | null;
  historicalAvailabilityLedger: Array<Record<string, unknown>>;
  preProjectionHistory?: Chat; postProjectionHistory?: Chat;
};
export function createInputAssembler(options: InputAssemblyOptions) {
  const { tokenSource, config, roundTools, roundScopeInstructions, getRoundOutputReserve,
    modelInputId, noteEnabled, getNote, historicalAvailabilityLedger } = options;
  // Cache only within this round/model handle, keyed by the entire SDK message
  // and schema surface. Typed files bypass serialization and therefore caching.
  const cache = new Map<string, ContextMeasurement>();
  const cost: MeasurementCost = { measurementCalls: 0, measurementMs: 0,
    templateCalls: 0, tokenCountCalls: 0, contextLengthCalls: 0, cacheHits: 0 };
  const measure = async (history: Chat, tools: Array<RemoteToolLike>, reserve: number) => {
    let key: string | null = null;
    try { key = telemetryFingerprint([workingContextModule.serialize(history), toolDefinitionSurface(tools), reserve]); }
    catch { /* A typed file is never flattened into a text cache key. */ }
    const cached = key ? cache.get(key) : undefined;
    if (cached) { cost.cacheHits++; return cached; }
    const measured = await measureContext(tokenSource, history, config, tools, { outputReserve: reserve, cost });
    if (key && measured.exact) {
      if (cache.size >= 96) cache.delete(cache.keys().next().value!);
      cache.set(key, measured);
    }
    return measured;
  };
  return async (sourceHistory: Chat, profile: {
    tools?: Array<RemoteToolLike>;
    instructions?: Array<string>;
    outputReserve?: number;
    modelInputId?: string;
  } = {}) => {
    const profileTools = profile.tools || roundTools;
    const profileInstructions = profile.instructions || roundScopeInstructions;
    const profileOutputReserve = profile.outputReserve ?? getRoundOutputReserve();
    const profileModelInputId = profile.modelInputId || modelInputId;
    const measureProfileInput = (history: Chat) => measure(history, profileTools, profileOutputReserve);
    const baseComposition = composeModelHistory(
      sourceHistory, noteEnabled ? getNote() : null, config, profileInstructions, noteEnabled,
    );
    const baseMeasurement = await measureProfileInput(baseComposition.history);
    const historyMeasurement = config.contextManagementMode !== "legacy" && options.preProjectionHistory
      ? await measure(options.preProjectionHistory, [], profileOutputReserve) : null;
    const projectionMeasurement = config.contextManagementMode !== "legacy" && options.postProjectionHistory
      ? await measure(options.postProjectionHistory, [], profileOutputReserve) : null;
    const compactionMeasurement = config.contextManagementMode === "legacy" ? null
      : await measure(sourceHistory, [], profileOutputReserve);
    const stages = async (input: Chat, measured: ContextMeasurement) => {
      const metadataMeasurement = config.contextManagementMode === "legacy" ? null
        : await measure(input, [], profileOutputReserve);
      return {
        postHistoryTokens: historyMeasurement?.exact ? historyMeasurement.inputTokens : null,
        postProjectionTokens: projectionMeasurement?.exact ? projectionMeasurement.inputTokens : null,
        postCompactionTokens: compactionMeasurement?.exact ? compactionMeasurement.inputTokens : null,
        postMetadataTokens: metadataMeasurement?.exact ? metadataMeasurement.inputTokens : null,
        postToolSchemaTokens: measured.exact ? measured.inputTokens : null,
        finalExactInputTokens: measured.exact ? measured.inputTokens : null,
        stageFingerprints: { history: historyMeasurement?.templatedInputFingerprint ?? null,
          projection: projectionMeasurement?.templatedInputFingerprint ?? null,
          compaction: compactionMeasurement?.templatedInputFingerprint ?? null,
          metadata: metadataMeasurement?.templatedInputFingerprint ?? null,
          final: measured.templatedInputFingerprint },
        measurementCost: { ...cost, scope: "round_input_assembler", cacheScope: "same_round_model_handle" }
};
    };
    let projection = config.inputAvailabilityMode === "off" ? null
      : inputAvailability.projectInputAvailability(
        normalizeHistory(baseComposition.history), historicalAvailabilityLedger, { modelInputId: profileModelInputId },
      );
    if (config.inputAvailabilityMode !== "inject" || !projection || projection.entries.length === 0) {
      return {
        stages: await stages(baseComposition.history, baseMeasurement),
        composition: baseComposition,
        history: baseComposition.history,
        measurement: baseMeasurement,
        projection,
        metadataChars: 0,
        metadataTokens: 0,
      };
    }
    let metadata = inputAvailability.renderInputAvailabilityMetadata(projection);
    let composition = composeModelHistory(
      sourceHistory, noteEnabled ? getNote() : null, config,
      [...profileInstructions, metadata], noteEnabled,
    );
    let measurement = await measureProfileInput(composition.history);
    const finalProjection = inputAvailability.projectInputAvailability(
      normalizeHistory(composition.history), historicalAvailabilityLedger, { modelInputId: profileModelInputId },
    );
    const finalMetadata = inputAvailability.renderInputAvailabilityMetadata(finalProjection);
    // One bounded reassembly is sufficient: metadata is derived only from
    // tool-result bodies, and adding the metadata does not create one.
    if (finalMetadata !== metadata) {
      metadata = finalMetadata;
      composition = composeModelHistory(
        sourceHistory, noteEnabled ? getNote() : null, config,
        [...profileInstructions, metadata], noteEnabled,
      );
      measurement = await measureProfileInput(composition.history);
    }
    projection = inputAvailability.projectInputAvailability(
      normalizeHistory(composition.history), historicalAvailabilityLedger, { modelInputId: profileModelInputId },
    );
    return {
      stages: await stages(composition.history, measurement),
      composition,
      history: composition.history,
      measurement,
      projection,
      metadataChars: metadata.length,
      metadataTokens: baseMeasurement.exact && measurement.exact
        ? Math.max(0, measurement.inputTokens - baseMeasurement.inputTokens) : null,
    };
  };
}

type AssembledInput = Awaited<ReturnType<ReturnType<typeof createInputAssembler>>>;

/** Owns the final input post-condition. Every candidate is measured after all
 * continuity/availability metadata and the exact exposed schema are attached. */
export class ContextManager {
  constructor(readonly config: DirectConfig, readonly broker: BudgetBroker) { }
  commit(store: InstanceType<typeof workingContextModule.WorkingContext> | null, visible: Chat,
    candidate: Chat, measured: ContextMeasurement, fingerprint: Record<string, unknown>, eligible: boolean) {
    if (!store || !eligible || !measured.exact || measured.remainingTokens < 0) return { committed: false, reason: null };
    try {
      const committed = store.commit(visible, candidate, measured,
        workingContextModule.hash(workingContextModule.serialize(visible)), workingContextModule.hash(fingerprint));
      return { committed, reason: committed ? null : store.lastCommitReason || "commit_validation_rejected" };
    } catch (error) {
      return {
committed: false, reason: error instanceof Error && error.message === "typed_files_not_serializable"
          ? "unsupported_typed_content" : "fingerprint_unavailable"
};
    }
  }
  async enforceLowWater(history: Chat, assemble: (history: Chat) => Promise<AssembledInput>,
    options: { force?: boolean; hasReadTools: boolean; minimumReadTokens?: number }) {
    const before = await assemble(history);
    if (this.config.observeOnly || this.config.contextManagementMode === "legacy" || !before.measurement.exact
      || before.measurement.contextLengthSource !== "model") {
      return {
history, assembled: before, changed: false, success: false, checkpoint: null,
        floorMeasurement: null, telemetry: null,
        canRun: this.config.observeOnly || this.config.contextManagementMode === "legacy",
        disposition: !before.measurement.exact ? "exact_measurement_unavailable"
          : before.measurement.contextLengthSource !== "model" ? "runtime_context_length_unavailable" : "compatibility_mode"
};
    }
    const floorCandidate = buildCompactedHistory(history, 0, this.config,
      { maxCurrentTurnMessages: 0, checkpointPolicy: "mandatory" });
    const floorInput = floorCandidate.history === history ? before : await assemble(floorCandidate.history);
    // A checkpoint that expands an already minimal input is not its mandatory floor.
    const floor = floorInput.measurement.exact && floorInput.measurement.inputTokens < before.measurement.inputTokens ? floorInput : before;
    const water = this.broker.watermarks(before.measurement, floor.measurement.inputTokens, options.hasReadTools, options.minimumReadTokens);
    const measurements: Array<{ candidate: string; finalExactInputTokens: number; accepted: boolean }> = [];
    let selected = { history, assembled: before, checkpoint: null as CheckpointResult | null, name: "unchanged" };
    const pressured = options.force || before.measurement.inputTokens > water.effectiveHighWaterTokens
      || water.preDispatchCompaction;
    if (pressured && before.measurement.inputTokens > water.effectiveLowWaterTokens) {
      const caps = [...new Set([128, 64, 32, 16, 8, 4, 2].filter(cap => cap <= history.length))];
      const candidates = [
        ...caps.map(cap => ({ name: `complete_exchange_cap_${cap}`, options: {
          maxCurrentTurnMessages: cap, checkpointPolicy: "bounded", maxCheckpointChars: this.config.maxCheckpointChars } })),
        ...[1, 0.5, 0.25, 0.125].map(ratio => ({ name: `checkpoint_budget_${ratio}`, options: {
          maxCurrentTurnMessages: 0, checkpointPolicy: "bounded",
          maxCheckpointChars: Math.max(2000, Math.floor(this.config.maxCheckpointChars * ratio)) } })),
        { name: "mandatory_floor", options: { maxCurrentTurnMessages: 0, checkpointPolicy: "mandatory" } },
      ];
      // Complete exchanges are indivisible. Never assume token sizes are monotone.
      for (const choice of candidates) {
        const candidate = choice.name === "mandatory_floor" ? floorCandidate
          : buildCompactedHistory(history, 0, this.config, choice.options);
        const input = candidate.history === history ? before
          : choice.name === "mandatory_floor" ? floorInput : await assemble(candidate.history);
        const accepted = input.measurement.exact && input.measurement.inputTokens <= water.effectiveLowWaterTokens
          && input.measurement.inputTokens <= water.hardInputCeiling
          && this.broker.watermarks(input.measurement, floor.measurement.inputTokens,
            options.hasReadTools, options.minimumReadTokens).nextActionFit;
        measurements.push({ candidate: choice.name, finalExactInputTokens: input.measurement.inputTokens, accepted });
        if (accepted) {
          selected = {
history: candidate.history, assembled: input, checkpoint: candidate.checkpoint,
            name: choice.name
}; break;
        }
      }
    }
    const final = selected.assembled.measurement;
    const success = final.exact && final.inputTokens <= water.effectiveLowWaterTokens
      && final.inputTokens <= water.hardInputCeiling;
    const finalWater = this.broker.watermarks(final, floor.measurement.inputTokens, options.hasReadTools, options.minimumReadTokens);
    const hardFit = final.exact && final.inputTokens <= water.hardInputCeiling;
    const canRun = hardFit && finalWater.nextActionFit && (!pressured || success);
    const disposition = !hardFit ? "blocked_hard_limit" : !finalWater.nextActionFit ? "blocked_next_action"
      : pressured && !success ? "blocked_low_target" : water.targetUnreachable ? "mandatory_floor_exception"
        : pressured ? "compacted_to_low" : "normal_growth";
    return {
      history: selected.history, assembled: selected.assembled, checkpoint: selected.checkpoint,
      changed: selected.history !== history, success, canRun, disposition, floorMeasurement: floor.measurement,
      telemetry: {
...water, tokenMetric: "exact_templated_model_input", beforeExactInputTokens: before.measurement.inputTokens,
        candidateMeasurements: measurements, selectedCandidate: selected.name,
        ...selected.assembled.stages, finalExactInputTokens: final.inputTokens, targetDelta: final.inputTokens - water.effectiveLowWaterTokens,
        compactionSucceeded: success, compactionRequested: Boolean(pressured),
        lowTargetMet: success, mandatoryFloorException: water.targetUnreachable, hardFit,
        nextActionFit: finalWater.nextActionFit, disposition, canRun,
        metadataTokens: selected.assembled.metadataTokens, toolSchemaTokens: final.toolSchemaTokens,
        reclaimRatio: before.measurement.inputTokens ? (before.measurement.inputTokens - final.inputTokens) / before.measurement.inputTokens : 0
}
    };
  }
}

export async function prepareWorkingInput(options: {
  tokenSource: unknown; config: DirectConfig; workingHistory: Chat; activeNote: ContinuityNote | null;
  noteEnabled: boolean; roundScopeInstructions: Array<string>; roundTools: Array<RemoteToolLike>;
  roundOutputReserve: number; beforeInput: ReturnType<typeof composeModelHistory>;
  measureRoundInput: (history: Chat) => Promise<ContextMeasurement>;
  ctl: PredictionLoopHandlerController; executionId: string; roundIndex: number;
}) {
  let { workingHistory } = options;
  const { tokenSource, config, activeNote, noteEnabled, roundScopeInstructions, roundTools,
    roundOutputReserve, beforeInput, measureRoundInput, ctl, executionId, roundIndex } = options;
  let before = await measureRoundInput(beforeInput.history);
  let modelHistory = workingHistory;
  let compacted = false;
  let compactionAppliedCount = 0;
  const compactionAppliedModes: Array<string> = [];
  let compactionCheckpoint: CheckpointResult | null = null;
  let compactionRetention: Record<string, unknown> | null = null;
  if (shouldCompactContext(before, config, roundTools.some(hasReadCapability))) {
    const counter = tokenSource as { countTokens?: (text: string) => Promise<number> };
    if (config.pastReasoningTokens > 0 && counter.countTokens) {
      workingHistory = await boundPastReasoning(workingHistory, config.pastReasoningTokens, text => counter.countTokens!(text));
      modelHistory = workingHistory;
      before = await measureContext(tokenSource,
        composeModelHistory(workingHistory, noteEnabled ? activeNote : null, config, roundScopeInstructions, noteEnabled).history,
        config, roundTools, { outputReserve: roundOutputReserve });
    }
  }
  if (config.contextManagementMode !== "legacy") return {
    before, modelHistory, workingHistory, compacted, compactionAppliedCount, compactionAppliedModes,
    compactionCheckpoint, compactionRetention,
  };
  if (shouldCompactContext(before, config, roundTools.some(hasReadCapability))) {
    const hard = before.remainingTokens <= config.hardRemainingTokens;
    const checkpointOptions = {
      maxCheckpointChars: config.maxCheckpointChars - beforeInput.overhead,
    };
    let candidate: CompactedHistory;
    if (!hard && before.exact) {
      const selected = await selectSoftCompaction(
        workingHistory, config, checkpointOptions,
        async (candidateHistory) => measureRoundInput(composeModelHistory(
          candidateHistory, noteEnabled ? activeNote : null,
          config, roundScopeInstructions, noteEnabled,
        ).history),
        targetRemainingForInput(before, config, roundTools.some(hasReadCapability)),
      );
      candidate = selected.candidate;
      compactionRetention = {
        mode: "soft_token_budget",
        targetRemainingTokens: selected.targetRemainingTokens,
        remainingTokensAfter: selected.remainingTokensAfter,
        maxCurrentTurnMessages: selected.maxCurrentTurnMessages,
      };
    } else {
      candidate = buildCompactedHistory(
        workingHistory,
        hard ? 0 : config.recentCompleteTurns,
        config,
        { ...checkpointOptions, ...(hard ? { maxCurrentTurnMessages: 2 } : {}) },
      );
    }
    if (!hard && !before.exact) {
      let needsBoundedCurrentTurn = candidate.history === workingHistory;
      if (!needsBoundedCurrentTurn) {
        const measuredCandidate = composeModelHistory(
          candidate.history, noteEnabled ? activeNote : null, config, roundScopeInstructions, noteEnabled,
        ).history;
        const afterFirst = await measureRoundInput(measuredCandidate);
        needsBoundedCurrentTurn = shouldCompactContext(afterFirst, config, roundTools.some(hasReadCapability));
      }
      if (needsBoundedCurrentTurn) {
        candidate = buildCompactedHistory(
          workingHistory,
          0,
          config,
          { ...checkpointOptions, maxCurrentTurnMessages: 2 },
        );
      }
      compactionRetention = {
        mode: "inexact_message_fallback",
        maxCurrentTurnMessages: needsBoundedCurrentTurn ? 2 : null
      };
    } else if (hard) {
      compactionRetention = {
        mode: "hard_latest_exchange",
        maxCurrentTurnMessages: 2
      };
    }
    if (candidate.history !== workingHistory) {
      const candidateComposition = composeModelHistory(
        candidate.history, noteEnabled ? activeNote : null, config, roundScopeInstructions, noteEnabled,
      );
      const candidateMeasurement = await measureRoundInput(candidateComposition.history);
      const measurableBenefit = !(before.exact && candidateMeasurement.exact
        && candidateMeasurement.inputTokens >= before.inputTokens);
      if (measurableBenefit) {
        modelHistory = candidate.history;
        compacted = modelHistory !== workingHistory;
        compactionCheckpoint = candidate.checkpoint;
        compactionAppliedCount += 1;
        compactionAppliedModes.push(String(compactionRetention?.mode || "regular"));
      } else if (config.showDebugInfo) ctl.debug({
        event: "compaction_candidate_rejected",
        executionId,
        roundIndex,
        reason: "no_measured_input_reduction",
        beforeInputTokens: before.inputTokens,
        candidateInputTokens: candidateMeasurement.inputTokens,
        exact: true,
      });
    }
  }
  workingHistory = modelHistory;

  return {
before, modelHistory, workingHistory, compacted, compactionAppliedCount, compactionAppliedModes,
    compactionCheckpoint, compactionRetention
};
}

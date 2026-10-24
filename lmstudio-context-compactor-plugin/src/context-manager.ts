import {
  Chat,
  ChatMessage,
  type LLMTool,
  type PredictionLoopHandlerController
} from "@lmstudio/sdk";
import { BudgetBroker } from "./budget-broker";
import { boundPastReasoning, resolveGenerationBudget, selectMeasuredCandidate } from "./context-budget";
import { core, inputAvailability, modelNotes, workingContextModule, REASONING_SEPARATOR } from "./context-ports";
import { stripInternalDataEcho } from "./internal-data-output";
import { telemetryFingerprint } from "./evidence-telemetry";
import { numeric } from "./execution-config";
import { type CheckpointResult, type ContextMeasurement, type ContinuityNote, type DirectConfig, type NormalizedMessage } from "./execution-contracts";
import { visibleAssistantOutput } from "./raw-tool-intent";
import { hasReadCapability, type RemoteToolLike } from "./tool-capability-registry";
import { renderTaskProgress } from "./continuity-memory.js";

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
  if (checkpoint.changeDataCheckpoint) compacted.append("assistant", checkpoint.changeDataCheckpoint);
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
  referenceData?: string,
  runtimeFacts: Array<string> = [],
  shownBlocks?: ShownDataBlocks,
) {
  const instructions = systemInstructions.map((value) => String(value || "").trim()).filter(Boolean);
  const messages = history.getMessagesArray();
  const taskData = config.observeOnly ? "" : renderTaskProgress(messages);
  const facts = runtimeFacts.map(value => String(value || "").trim()).filter(Boolean);
  const systemIndexes = messages.map((message, index) => message.isSystemPrompt() ? index : -1)
    .filter((index) => index >= 0);
  const systemLayoutIsCompatible = systemIndexes.length === 0
    || (systemIndexes.length === 1 && systemIndexes[0] === 0);
  if (!enableNoteProtocol && instructions.length === 0 && !referenceData && !taskData && !facts.length
    && systemLayoutIsCompatible) {
    return { history, overhead: 0, privateDataParts: [] as string[] };
  }
  const noteText = note ? modelNotes.renderAssistantNote(note, messages) : "";
  // Real ids of recent completed calls; kept in the per-round data slot so the cached system prefix stays stable.
  const refsText = enableNoteProtocol ? modelNotes.renderAvailableRefs(messages) : "";
  const instruction = config.reviewProgress ? modelNotes.NOTE_INSTRUCTION.replace(
    "No other keys. refs may contain",
    "The optional reviewClaims array uses [{path, sha256, reviewScope, statement, refs}]; reviewClaims require an observed file hash and tool-call refs and are assistant claims, never verified completion. No other keys. refs may contain") : modelNotes.NOTE_INSTRUCTION;
  const canIncludeInstruction = enableNoteProtocol
    && config.maxCheckpointChars >= 2000 + instruction.length;
  const canIncludeNote = canIncludeInstruction
    && config.maxCheckpointChars >= 2000 + instruction.length + noteText.length;
  const canIncludeRefs = canIncludeInstruction && Boolean(refsText)
    && config.maxCheckpointChars >= 2000 + instruction.length + (canIncludeNote ? noteText.length : 0) + refsText.length;
  const overhead = (canIncludeInstruction ? instruction.length + (canIncludeNote ? noteText.length : 0) : 0)
    + (canIncludeRefs ? refsText.length : 0) + taskData.length;
  const composed = Chat.empty();
  const combinedSystemText = [
    ...messages.filter((message) => message.isSystemPrompt())
      .map((message) => message.getText().trim()).filter(Boolean),
    ...instructions,
    ...(canIncludeInstruction ? [instruction] : []),
  ].join("\n\n");
  if (combinedSystemText) composed.append("system", combinedSystemText);
  // Data that changes between rounds stays in assistant data slots (never in a
  // user or system message), placed before the latest user message or, once the
  // current turn has complete tool exchanges, before the newest one. It is
  // never last: chat templates render a trailing assistant message as an
  // unfinished answer the model would continue. With shownBlocks (one execution's
  // blocks already sent, keyed by their anchor) every earlier block stays where
  // it was and only the parts not yet shown are added at the newest anchor, so
  // each input extends the previous one: measured on a hybrid-attention model,
  // moving or rewriting earlier data made the server reprocess every exchange.
  // Without shownBlocks the full current data sits at the newest anchor.
  // Model input only; never persisted.
  const runtimeParts = [...(canIncludeNote && noteText ? [noteText] : []), ...(canIncludeRefs ? [refsText] : []),
    ...(referenceData ? [referenceData] : []),
    ...(taskData ? [taskData] : []), ...facts];
  const latestUser = messages.reduce((found, message, index) => message.isUserMessage() ? index : found, -1);
  const anchors = new Map<number, string>();
  messages.forEach((message, index) => {
    if (index === latestUser) anchors.set(index, "user");
    else if (latestUser >= 0 && index > latestUser && message.getToolCallRequests().length)
      anchors.set(index, `request:${String(message.getToolCallRequests()[0].id || index)}`);
  });
  const slot = runtimeSlotIndex(messages, latestUser);
  const slotAnchor = anchors.get(slot);
  const blocks = new Map<number, readonly string[]>();
  let dataBlock: { anchor: string; parts: string[] } | undefined;
  if (shownBlocks && slotAnchor) {
    for (const [index, anchor] of anchors) { const shown = shownBlocks.get(anchor); if (shown) blocks.set(index, shown); }
    // A reasoning retry can produce a new observation without a new tool
    // exchange/anchor. Keep everything already shown, then append only the
    // missing parts; otherwise that observation is silently lost on the retry.
    const fresh = unshownParts(runtimeParts, [...blocks.values()].flat());
    if (fresh.length) {
      const parts = [...(blocks.get(slot) || []), ...fresh];
      blocks.set(slot, parts); dataBlock = { anchor: slotAnchor, parts };
    }
  } else blocks.set(slot, runtimeParts);
  if (slot < 0) for (const part of runtimeParts) composed.append("assistant", part);
  messages.forEach((message, index) => {
    for (const part of blocks.get(index) || []) composed.append("assistant", part);
    if (!message.isSystemPrompt()) composed.append(message);
  });
  const privateDataParts = [...blocks.values()].flat().filter(part =>
    part.startsWith("[Available tool-call refs]") || part.startsWith("[Derived reference data"));
  return { history: composed, overhead, privateDataParts, ...(dataBlock ? { dataBlock } : {}) };
}

/** An execution's data blocks already sent to the model, keyed by anchor. */
export type ShownDataBlocks = ReadonlyMap<string, readonly string[]>;

/** Parts not already visible in an earlier block. A refs part keeps its header
 * and only the rows not shown yet; other parts are kept unless shown verbatim. */
function unshownParts(parts: ReadonlyArray<string>, shown: ReadonlyArray<string>) {
  const shownLines = new Set(shown.flatMap(part => part.split("\n")));
  return parts.flatMap(part => {
    if (!part.startsWith("[Available tool-call refs]")) return shown.includes(part) ? [] : [part];
    const [header, ...rows] = part.split("\n");
    const fresh = rows.filter(row => !shownLines.has(row));
    return fresh.length ? [[header, ...fresh].join("\n")] : [];
  });
}

/** Where the per-round data slot goes: before the newest tool-request message
 * of the latest user turn when every request in that turn has its result, so
 * no request/result pair is split and the history still ends with results;
 * otherwise the latest user message (or -1 before any user message). */
function runtimeSlotIndex(messages: ReadonlyArray<ChatMessage>, latestUser: number) {
  if (latestUser < 0) return -1;
  const requested = new Set<string>(), answered = new Set<string>();
  let newestRequest = -1;
  messages.forEach((message, index) => {
    if (index <= latestUser || message.isSystemPrompt()) return;
    const requests = message.getToolCallRequests();
    if (requests.length) newestRequest = index;
    for (const request of requests) requested.add(String(request.id || ""));
    for (const result of message.getToolCallResults()) answered.add(String(result.toolCallId || ""));
  });
  const complete = newestRequest >= 0 && [...requested].every(id => id && answered.has(id))
    && messages.slice(newestRequest + 1).some(message => message.getToolCallResults().length > 0);
  return complete ? newestRequest : latestUser;
}

export function modelHistoryMessage(message: ChatMessage, internalDataParts: readonly string[] = []): ChatMessage {
  if (!message.isAssistantMessage() || !message.getText()) return message;
  const extracted = modelNotes.splitVisibleAnswer(message.getText());
  const separator = extracted.visibleText.lastIndexOf(REASONING_SEPARATOR);
  const split = separator < 0 ? 0 : separator + REASONING_SEPARATOR.length;
  const text = extracted.visibleText.slice(0, split)
    + stripInternalDataEcho(extracted.visibleText.slice(split), internalDataParts);
  if (text === message.getText()) return message;
  const copy = ChatMessage.from(message);
  // Keep the SDK's raw reasoning/non-reasoning serialization for the next
  // tool round. Remove the private footer and automatic data echoes from prose.
  copy.replaceText(text);
  return copy;
}

export type InputAssemblyOptions = {
  tokenSource: unknown; config: DirectConfig; roundTools: Array<RemoteToolLike>;
  roundScopeInstructions: Array<string>; getRoundOutputReserve: () => number;
  modelInputId: string; noteEnabled: boolean; getNote: () => ContinuityNote | null;
  historicalAvailabilityLedger: Array<Record<string, unknown>>;
  getRuntimeFacts?: (history: Chat) => Array<string>;
  /** Execution-scoped exact-measurement cache owned by ContextManager. */
  measurementCache?: Map<string, ContextMeasurement>;
  preProjectionHistory?: Chat; postProjectionHistory?: Chat;
  /** Execution-scoped data blocks already sent; read only here. */
  shownDataBlocks?: ShownDataBlocks;
};
export function createInputAssembler(options: InputAssemblyOptions) {
  const { tokenSource, config, roundTools, roundScopeInstructions, getRoundOutputReserve,
    modelInputId, noteEnabled, getNote, historicalAvailabilityLedger } = options;
  // Cache within one execution/model handle (or this round when no owner
  // cache is supplied), keyed by the entire SDK message and schema surface.
  // Typed files bypass serialization and therefore caching.
  const cache = options.measurementCache || new Map<string, ContextMeasurement>();
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
    referenceData?: string;
  } = {}) => {
    const profileTools = profile.tools || roundTools;
    const profileInstructions = profile.instructions || roundScopeInstructions;
    const profileOutputReserve = profile.outputReserve ?? getRoundOutputReserve();
    const profileModelInputId = profile.modelInputId || modelInputId;
    const measureProfileInput = (history: Chat) => measure(history, profileTools, profileOutputReserve);
    const runtimeFacts = options.getRuntimeFacts?.(sourceHistory) || [];
    const baseComposition = composeModelHistory(
      sourceHistory, noteEnabled ? getNote() : null, config, profileInstructions, noteEnabled, profile.referenceData, runtimeFacts, options.shownDataBlocks,
    );
    const baseMeasurement = await measureProfileInput(baseComposition.history);
    // Per-stage measurements are telemetry only. They are taken lazily for the
    // selected input instead of for every candidate a search assembles.
    const stages = async (input: Chat, measured: ContextMeasurement) => {
      const historyMeasurement = config.contextManagementMode !== "legacy" && options.preProjectionHistory
        ? await measure(options.preProjectionHistory, [], profileOutputReserve) : null;
      const projectionMeasurement = config.contextManagementMode !== "legacy" && options.postProjectionHistory
        ? await measure(options.postProjectionHistory, [], profileOutputReserve) : null;
      const compactionMeasurement = config.contextManagementMode === "legacy" ? null
        : await measure(sourceHistory, [], profileOutputReserve);
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
        measurementCost: { ...cost, scope: "round_input_assembler", cacheScope: options.measurementCache ? "execution_model_handle" : "same_round_model_handle" }
};
    };
    const basicStages = (measured: ContextMeasurement) => ({
      postToolSchemaTokens: measured.exact ? measured.inputTokens : null,
      finalExactInputTokens: measured.exact ? measured.inputTokens : null,
      stageFingerprints: { final: measured.templatedInputFingerprint },
      measurementCost: { ...cost, scope: "round_input_assembler", cacheScope: options.measurementCache ? "execution_model_handle" : "same_round_model_handle" },
    });
    let projection = config.inputAvailabilityMode === "off" ? null
      : inputAvailability.projectInputAvailability(
        normalizeHistory(baseComposition.history), historicalAvailabilityLedger, { modelInputId: profileModelInputId },
      );
    if (config.inputAvailabilityMode !== "inject" || !projection || projection.entries.length === 0) {
      return {
        stages: basicStages(baseMeasurement),
        measureStages: () => stages(baseComposition.history, baseMeasurement),
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
      [...profileInstructions, metadata], noteEnabled, profile.referenceData, runtimeFacts, options.shownDataBlocks,
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
        [...profileInstructions, metadata], noteEnabled, profile.referenceData, runtimeFacts, options.shownDataBlocks,
      );
      measurement = await measureProfileInput(composition.history);
    }
    projection = inputAvailability.projectInputAvailability(
      normalizeHistory(composition.history), historicalAvailabilityLedger, { modelInputId: profileModelInputId },
    );
    return {
      stages: basicStages(measurement),
      measureStages: () => stages(composition.history, measurement),
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
  constructor(readonly config: DirectConfig, readonly broker: BudgetBroker,
    readonly checkpointOptions: Record<string, unknown> = {},
    /** Indexes that every compaction candidate must retain (pending results). */
    readonly requiredIndexes: (history: Chat) => number[] = () => []) { }
  /** Exact measurements are deterministic per execution/model handle. */
  readonly measurementCache = new Map<string, ContextMeasurement>();
  /** reservedTokens: material this round will add after the evidence window
   * (a retained instruction), counted so one rewrite makes room for both. */
  shouldProjectEvidence(measurement: ContextMeasurement, hasReadTools: boolean, minimumReadTokens: number, reservedTokens = 0) {
    if (this.config.observeOnly || this.config.contextManagementMode === "legacy" || !measurement.exact) return false;
    const water = this.broker.watermarks(measurement, 0, hasReadTools, minimumReadTokens);
    return measurement.remainingTokens < reservedTokens
      || measurement.inputTokens + reservedTokens > water.effectiveHighWaterTokens;
  }
  /** Add optional material only after the existing evidence window is ready.
   * Admission reuses the exact assembler and shared broker; it never compacts
   * evidence, changes output limits, or creates a second execution gate.
   * The material is ephemeral (never in the evidence history), so a selection
   * backed by an explicit topic may use the space up to high water, where
   * compaction of that evidence would begin; generic material keeps low water. */
  async addOptionalReferences(base: AssembledInput,
    candidates: ReadonlyArray<{ ids: readonly string[]; instruction: string; referenceData?: string; dataIds?: readonly string[] }>,
    assemble: (instruction: string, referenceData?: string) => Promise<AssembledInput>,
    options: { maxAddedTokens: number; hasReadTools: boolean; minimumReadTokens: number; guardAbort: () => void;
      admissionWater?: "low" | "high" }) {
    const requestedIds = candidates[0]?.ids || [];
    const requestedDataIds = candidates[0]?.dataIds || [];
    const admissionWater = options.admissionWater === "high" ? "high" : "low";
    const ceiling = (water: ReturnType<BudgetBroker["watermarks"]>) =>
      admissionWater === "high" ? water.effectiveHighWaterTokens : water.effectiveLowWaterTokens;
    let reason = "no_candidates";
    let attempts = 0;
    const result = (assembled: AssembledInput, selectedIds: readonly string[] = [], addedTokens = 0, selectedDataIds: readonly string[] = [],
      selectedInstruction?: string) => ({
      assembled, selectedInstruction,
      telemetry: { requestedIds, selectedIds, omittedIds: requestedIds.filter(id => !selectedIds.includes(id)),
        requestedDataIds, selectedDataIds, omittedDataIds: requestedDataIds.filter(id => !selectedDataIds.includes(id)),
        reason, attempts, addedTokens, baseInputTokens: base.measurement.inputTokens,
        finalInputTokens: assembled.measurement.inputTokens, admissionWater },
    });
    if (!candidates.length) return result(base);
    if (!Number.isFinite(options.maxAddedTokens) || options.maxAddedTokens <= 0) {
      reason = "disabled";
      return result(base);
    }
    if (!base.measurement.exact || base.measurement.contextLengthSource !== "model") {
      reason = "measurement_unavailable";
      return result(base);
    }
    const baselineWater = this.broker.watermarks(base.measurement, 0, options.hasReadTools, options.minimumReadTokens);
    if (base.measurement.inputTokens >= ceiling(baselineWater)) {
      reason = "existing_input_priority";
      return result(base);
    }
    for (const candidate of candidates) {
      options.guardAbort();
      let input: AssembledInput;
      try {
        attempts++;
        input = await assemble(candidate.instruction, candidate.referenceData);
      } catch {
        options.guardAbort();
        reason = "assembly_failed";
        continue;
      }
      options.guardAbort();
      const measured = input.measurement;
      if (!measured.exact || measured.contextLengthSource !== "model") {
        reason = "measurement_unavailable";
        continue;
      }
      const addedTokens = Math.max(0, measured.inputTokens - base.measurement.inputTokens);
      const water = this.broker.watermarks(measured, 0, options.hasReadTools, options.minimumReadTokens);
      if (addedTokens > options.maxAddedTokens || measured.remainingTokens < 0
        || measured.inputTokens > ceiling(water) || !water.nextActionFit || water.preDispatchCompaction) {
        reason = "budget_omitted";
        continue;
      }
      reason = "included";
      return result(input, candidate.ids, addedTokens, candidate.dataIds, candidate.instruction);
    }
    return result(base);
  }

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
  /** deepen: this round already rewrote the adopted input (an evidence
   * projection), so its prefix is not reusable; reaching the low-water target
   * here saves a rewrite a few rounds later. Unlike force, an unmet target does
   * not block a round whose input already fits under high water. */
  async enforceLowWater(history: Chat, assemble: (history: Chat) => Promise<AssembledInput>,
    options: { force?: boolean; deepen?: boolean; hasReadTools: boolean; minimumReadTokens?: number }) {
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
    const requiredIndexes = this.requiredIndexes(history);
    const floorCandidate = buildCompactedHistory(history, 0, this.config,
      { ...this.checkpointOptions, requiredIndexes, maxCurrentTurnMessages: 0, checkpointPolicy: "mandatory" });
    const floorInput = floorCandidate.history === history ? before : await assemble(floorCandidate.history);
    // A checkpoint that expands an already minimal input is not its mandatory floor.
    const floor = floorInput.measurement.exact && floorInput.measurement.inputTokens < before.measurement.inputTokens ? floorInput : before;
    const water = this.broker.watermarks(before.measurement, floor.measurement.inputTokens, options.hasReadTools, options.minimumReadTokens);
    const measurements: Array<{ candidate: string; finalExactInputTokens: number; accepted: boolean }> = [];
    let selected = { history, assembled: before, checkpoint: null as CheckpointResult | null, name: "unchanged" };
    const required = options.force || before.measurement.inputTokens > water.effectiveHighWaterTokens
      || water.preDispatchCompaction;
    const pressured = required || Boolean(options.deepen);
    if (pressured && before.measurement.inputTokens > water.effectiveLowWaterTokens) {
      const caps = [...new Set([128, 64, 32, 16, 8, 4, 2].filter(cap => cap <= history.length))];
      // Most-retaining first: keep the last one or two complete user turns
      // verbatim before collapsing every earlier turn into the checkpoint.
      const candidates: Array<{ name: string; recentTurns?: number; options: Record<string, unknown> }> = [
        ...[2, 1].map(recentTurns => ({ name: `recent_turns_${recentTurns}`, recentTurns, options: {
          checkpointPolicy: "bounded", maxCheckpointChars: this.config.maxCheckpointChars } })),
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
          : buildCompactedHistory(history, choice.recentTurns ?? 0, this.config, { ...this.checkpointOptions, requiredIndexes, ...choice.options });
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
    const canRun = hardFit && finalWater.nextActionFit && (!required || success);
    const disposition = !hardFit ? "blocked_hard_limit" : !finalWater.nextActionFit ? "blocked_next_action"
      : required && !success ? "blocked_low_target" : water.targetUnreachable ? "mandatory_floor_exception"
        : pressured && !success ? "projection_target_unmet" : pressured ? "compacted_to_low" : "normal_growth";
    return {
      history: selected.history, assembled: selected.assembled, checkpoint: selected.checkpoint,
      changed: selected.history !== history, success, canRun, disposition, floorMeasurement: floor.measurement,
      telemetry: {
...water, tokenMetric: "exact_templated_model_input", beforeExactInputTokens: before.measurement.inputTokens,
        candidateMeasurements: measurements, selectedCandidate: selected.name,
        ...(this.config.showDebugInfo && selected.assembled.measureStages
          ? await selected.assembled.measureStages() : selected.assembled.stages), finalExactInputTokens: final.inputTokens, targetDelta: final.inputTokens - water.effectiveLowWaterTokens,
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
  checkpointOptions?: Record<string, unknown>;
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
      ...options.checkpointOptions,
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

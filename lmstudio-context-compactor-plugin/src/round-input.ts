import { Chat, type PredictionLoopHandlerController } from "@lmstudio/sdk";
import { minimumReadResultTokens } from "./budget-broker";
import { boundPastReasoning, resolveGenerationBudget } from "./context-budget";
import { buildCompactedHistory, composeModelHistory, ContextManager, createInputAssembler, type ShownDataBlocks,
  measureContext, prepareWorkingInput } from "./context-manager";
import { updateSemanticContext } from "./semantic-handoff";
import { workingContextModule } from "./context-ports";
import { EvidenceManager } from "./evidence-manager";
import { FIRST_CONSUMER_RAW_GIT_MAX_CHARS, MIN_FINAL_OUTPUT_TOKENS, RESEARCH_RECOVERY_MAX_TOKENS,
  type ContinuityNote, type DirectConfig } from "./execution-contracts";
import { READ_ONLY_CONTEXT_RECOVERY_INSTRUCTION } from "./execution-instructions";
import { readOnlyRecoveryProfile } from "./recovery-coordinator";
import { hasReadCapability, type RemoteToolLike } from "./tool-capability-registry";
import { autoGuidanceCandidates, designGuidanceCandidates, focusedGuidanceCandidates, selectDesignGuidance, type GuidanceCandidate, type GuidanceDocument } from "./design-guidance";
import { selectGuidanceTopics, type GuidanceIntent } from "./guidance-topic-selection";
import type { ToolScope } from "./tool-scope";
import { referenceGuidanceScope } from "./reference-context";

/** Older reasoning a projection round keeps, newest first, when no
 * pastReasoningTokens budget is configured. */
const PROJECTION_ROUND_REASONING_TOKENS = 2048;
/** tokens: the measured cost of the instruction when last admitted;
 * omissionReasons: its candidate's, from when it was selected. A dropped entry
 * without an instruction records only the scope a rescope could not fit. */
type RetainedGuidance = { ids: readonly string[]; instruction?: string; selectionScope: string; tokens?: number;
  omissionReasons?: readonly string[] };
/** Optional reference text admitted earlier in this execution, or the one it dropped. */
export type GuidanceRetention = { kept?: RetainedGuidance; dropped?: RetainedGuidance };
type RoundInputOptions = {
  ctl: PredictionLoopHandlerController; config: DirectConfig;
  tokenSource: Awaited<ReturnType<PredictionLoopHandlerController["tokenSource"]>>;
  contextManager: ContextManager; evidenceManager: EvidenceManager;
  workingContext: InstanceType<typeof workingContextModule.WorkingContext> | null;
  workingHistory: Chat; visibleHistory: Chat; preProjectionHistory: Chat; postProjectionHistory: Chat;
  roundTools: RemoteToolLike[]; modelTools: RemoteToolLike[];
  roundScopeInstructions: string[]; scopeInstructions: string[];
  roundOutputReserve: number; activeNote: ContinuityNote | null; noteEnabled: boolean;
  historicalAvailabilityLedger: Array<Record<string, unknown>>;
  semanticSummaryCooldownUntilRound: number; objectiveFingerprint: string;
  finalizing: boolean; boundedAudit: boolean; researchRecoveryEpisodeStarted: boolean; allowReadRecovery: boolean;
  reasoningRecovery?: boolean;
  /** An output cap above maxOutputReserve that headroom bounds (final reports). */
  headroomOutputCap?: boolean;
  reportContinuation?: string;
  guidanceDocuments?: readonly GuidanceDocument[];
  guidanceIntent?: GuidanceIntent;
  /** Execution-scoped; the caller discards it with the execution. */
  guidanceRetention?: GuidanceRetention;
  guidanceScope?: ToolScope;
  referenceRecovery?: boolean;
  getRuntimeFacts?: (history: Chat) => string[];
  /** Data blocks this execution already sent (owned by the prediction loop). */
  shownDataBlocks?: ShownDataBlocks;
  finalizationTrigger: string | null;
  projectionApplied: boolean; executionId: string; modelInputId: string; roundIndex: number;
};

/** Select and measure one executable input. This module returns measured readiness and an optional recovery candidate;
 * it never changes execution phase, commits evidence, or dispatches research/report predictions. */
export async function prepareRoundInput(options: RoundInputOptions) {
  const { ctl, config, tokenSource, contextManager, evidenceManager, workingContext,
    visibleHistory, preProjectionHistory, postProjectionHistory, roundTools, modelTools,
    roundScopeInstructions, scopeInstructions, noteEnabled, historicalAvailabilityLedger,
    objectiveFingerprint, finalizing, boundedAudit, researchRecoveryEpisodeStarted,
    executionId, modelInputId, roundIndex } = options;
  let { workingHistory, roundOutputReserve, activeNote, semanticSummaryCooldownUntilRound, projectionApplied } = options;
  // projectionApplied also records projections of recovery candidates; this
  // records only a projection of the input this round adopts.
  let workingInputProjected = false;
  // Set once this round rewrites its input, before the rewritten input is assembled.
  let rewritingInput = false;
  // Releasing every pin still left the input over high water: the working set
  // no longer fits, and restoring it again would only repeat this rewrite.
  let projectionExhausted = false;
  let releasedAllPins = false;
  const minimumReadBudget = (tools: RemoteToolLike[]) => minimumReadResultTokens(
    tools.filter(hasReadCapability), Boolean(workingContext?.hasArchivedEvidence));
  let outputCapSource: "configured" | "headroom_clamp" = "configured";
  const beforeInput = composeModelHistory(workingHistory, noteEnabled ? activeNote : null,
    config, roundScopeInstructions, noteEnabled);
  const measureRoundInput = (history: Chat) => measureContext(tokenSource, history, config, roundTools,
    { outputReserve: roundOutputReserve });
  let { before, modelHistory, compacted, compactionAppliedCount, compactionAppliedModes,
    compactionCheckpoint, compactionRetention } = await prepareWorkingInput({ tokenSource, config,
    checkpointOptions: contextManager.checkpointOptions,
    workingHistory, activeNote, noteEnabled, roundScopeInstructions, roundTools, roundOutputReserve,
    beforeInput, measureRoundInput, ctl, executionId, roundIndex });
  workingHistory = modelHistory;
  const assembleModelInput = createInputAssembler({ tokenSource, config, roundTools,
    roundScopeInstructions, getRoundOutputReserve: () => roundOutputReserve, modelInputId,
    noteEnabled, getNote: () => activeNote, historicalAvailabilityLedger, preProjectionHistory, postProjectionHistory,
    getRuntimeFacts: options.getRuntimeFacts, measurementCache: contextManager.measurementCache,
    shownDataBlocks: options.shownDataBlocks, consolidateDataBlocks: () => rewritingInput || compacted });
  const assembleRecoveryCandidate = async (sourceHistory: Chat, tools: RemoteToolLike[], instruction: string,
    candidateModelInputId: string) => {
    const desiredMaxTokens = Math.min(config.maxOutputReserve, RESEARCH_RECOVERY_MAX_TOKENS);
    let reserve = desiredMaxTokens;
    const assemble = (history: Chat) => assembleModelInput(history, {
      tools, instructions: [...scopeInstructions, instruction], outputReserve: reserve, modelInputId: candidateModelInputId });
    let assembled = await assemble(sourceHistory);
    // A retry candidate can encounter pressure after the preceding generation
    // consumed raw evidence. Use the same policy as normal round preparation.
    if (workingContext && contextManager.shouldProjectEvidence(assembled.measurement,
      tools.some(hasReadCapability), minimumReadBudget(tools))) {
      const projected = evidenceManager.project(sourceHistory,
        { executionId, roundIndex, modelInputId: candidateModelInputId,
          proofLevel: "returned_tool_result", preserveUnconsumedRaw: true },
        config.toolResultProjectionChars);
      if (projected.changed) {
        sourceHistory = projected.history;
        projectionApplied = true;
        assembled = await assemble(sourceHistory);
      }
    }
    const budget = resolveGenerationBudget({ desiredMaxTokens, contextLength: assembled.measurement.contextLength,
      inputTokens: assembled.measurement.inputTokens, safetyMarginTokens: config.safetyMarginTokens,
      minimumTokens: MIN_FINAL_OUTPUT_TOKENS });
    if (budget.fit && budget.appliedMaxTokens < desiredMaxTokens) {
      reserve = budget.appliedMaxTokens; assembled = await assemble(sourceHistory);
    }
    let low = await contextManager.enforceLowWater(sourceHistory, assemble, {
      hasReadTools: tools.some(hasReadCapability), minimumReadTokens: minimumReadBudget(tools) });
    // First try narrowing the tool surface without changing source evidence.
    // If a checkpoint is actually needed, archive the source before accepting
    // that destructive reduction. A failed cross-turn restore must not make
    // summary success the only way to preserve historical bodies.
    if (low.changed && workingContext && !config.observeOnly) {
      const projected = evidenceManager.project(sourceHistory,
        { executionId, roundIndex, modelInputId: candidateModelInputId, proofLevel: "returned_tool_result", pinnedWorkingSetMaxChars: 0 },
        config.toolResultProjectionChars);
      if (projected.changed) {
        projectionApplied = true;
        low = await contextManager.enforceLowWater(projected.history, assemble, {
          hasReadTools: tools.some(hasReadCapability), minimumReadTokens: minimumReadBudget(tools) });
        assembled = low.assembled;
      }
    }
    if (low.changed) assembled = low.assembled;
    // Admission belongs to the final measured candidate, not the oversized
    // pre-compaction input that initiated recovery.
    const finalBudget = resolveGenerationBudget({ desiredMaxTokens: reserve,
      contextLength: assembled.measurement.contextLength, inputTokens: assembled.measurement.inputTokens,
      safetyMarginTokens: config.safetyMarginTokens, minimumTokens: MIN_FINAL_OUTPUT_TOKENS });
    return { assembled, budget: finalBudget, desiredMaxTokens, history: low.history,
      fit: finalBudget.fit && assembled.measurement.remainingTokens >= 0 && low.canRun };
  };
  const measureOriginalProfile = async (history: Chat) => {
    ctl.guardAbort();
    const candidate = await assembleModelInput(history, { tools: modelTools, instructions: scopeInstructions,
      outputReserve: config.maxOutputReserve, modelInputId: `${modelInputId}:resume-original` });
    return candidate.measurement.exact && candidate.measurement.remainingTokens >= minimumReadBudget(modelTools)
      && !contextManager.shouldProjectEvidence(candidate.measurement, modelTools.some(hasReadCapability), minimumReadBudget(modelTools));
  };
  let assembledInput = await assembleModelInput(workingHistory);
  if ((options.reasoningRecovery || options.headroomOutputCap) && !config.observeOnly && roundOutputReserve > config.maxOutputReserve) {
    const measured = assembledInput.measurement;
    const budget = resolveGenerationBudget({ desiredMaxTokens: roundOutputReserve, contextLength: measured.contextLength,
      inputTokens: measured.inputTokens, safetyMarginTokens: config.safetyMarginTokens, minimumTokens: MIN_FINAL_OUTPUT_TOKENS });
    const reserve = Math.max(config.maxOutputReserve, Math.min(roundOutputReserve, budget.appliedMaxTokens));
    if (reserve !== roundOutputReserve) {
      roundOutputReserve = reserve; outputCapSource = "headroom_clamp";
      assembledInput = await assembleModelInput(workingHistory);
    }
  }
  // Exposure does not prove review completion. Keep returned bodies while the
  // measured shared budget permits; pressure is owned by ContextManager. A
  // retained instruction counts toward that pressure: dropping it alone changes
  // the system message and costs a full prompt rebuild, so the round that must
  // rewrite the input makes room for it instead.
  const retainedGuidanceTokens = options.guidanceRetention?.kept?.tokens ?? 0;
  const hasReadTools = roundTools.some(hasReadCapability);
  // Set when this round rewrites its input anyway and is close enough to high
  // water that the same rewrite should also reach low water (plan B2).
  let deepenLowWater = false;
  if (workingContext && !finalizing && contextManager.shouldProjectEvidence(assembledInput.measurement,
    hasReadTools, minimumReadBudget(roundTools), retainedGuidanceTokens)) {
    const water = contextManager.broker.watermarks(assembledInput.measurement, 0, hasReadTools, minimumReadBudget(roundTools));
    const beforeInputTokens = assembledInput.measurement.inputTokens;
    const pinnedWorkingSetMaxChars = contextManager.broker.pinnedWorkingSetChars(water);
    let pinnedWorkingSetFinalChars = pinnedWorkingSetMaxChars;
    const project = (pinnedChars: number) => evidenceManager.project(workingHistory,
      { executionId, roundIndex, modelInputId, proofLevel: "returned_tool_result", preserveUnconsumedRaw: true,
        pinnedWorkingSetMaxChars: pinnedChars },
      config.toolResultProjectionChars);
    let projected = project(pinnedWorkingSetMaxChars);
    if (projected.changed) {
      workingHistory = projected.history; rewritingInput = true;
      assembledInput = await assembleModelInput(workingHistory);
    }
    // The working set yields when keeping it leaves the input over high water.
    let pinsReleased = false;
    let changed = projected.changed;
    for (const pinBudget of [Math.floor(pinnedWorkingSetMaxChars / 2), Math.floor(pinnedWorkingSetMaxChars / 4), 0]) {
      if (!contextManager.shouldProjectEvidence(assembledInput.measurement, hasReadTools, minimumReadBudget(roundTools),
        retainedGuidanceTokens)) break;
      const released = project(pinBudget);
      pinnedWorkingSetFinalChars = pinBudget;
      pinsReleased = pinBudget === 0;
      if (released.changed) {
        projected = released; changed = true; rewritingInput = true; workingHistory = released.history;
        assembledInput = await assembleModelInput(workingHistory);
      }
    }
    releasedAllPins = pinsReleased;
    let reasoningBounded = false;
    if (changed) {
      projectionApplied = true; workingInputProjected = true;
      // This rewrite already discards the reusable prefix, so older reasoning
      // goes in the same rewrite: it would otherwise outlive the bodies it
      // described and be quoted as if it were source (2026-10-20 run, r13).
      const counter = tokenSource as { countTokens?: (text: string) => Promise<number> };
      if (counter.countTokens) {
        const bounded = await boundPastReasoning(workingHistory,
          config.pastReasoningTokens > 0 ? config.pastReasoningTokens : PROJECTION_ROUND_REASONING_TOKENS,
          text => counter.countTokens!(text));
        reasoningBounded = bounded !== workingHistory;
        workingHistory = bounded;
      }
      if (reasoningBounded) assembledInput = await assembleModelInput(workingHistory);
    }
    // Reach low water in this rewrite only when the input (with the retained
    // instruction) is still above the midpoint: far below it, cutting whole
    // exchanges saves no rewrite soon and loses the turn's own progress. An
    // unchanged projection that cannot make room would otherwise drop the
    // retained instruction alone, a full prompt rebuild that reclaims nothing
    // (2026-10-25 run, r18 then r19: two rebuilds in a row).
    const settled = assembledInput.measurement.inputTokens + retainedGuidanceTokens;
    deepenLowWater = settled > (water.effectiveLowWaterTokens + water.effectiveHighWaterTokens) / 2;
    if (config.showDebugInfo) ctl.debug({ event: "working_context_projection", executionId, roundIndex,
      changed, reason: "measured_input_pressure", archiveFailed: projected.archiveFailed === true,
      reasoningBounded, beforeInputTokens, inputTokens: assembledInput.measurement.inputTokens,
      pinnedWorkingSetMaxChars, pinnedWorkingSetFinalChars, pinsReleased, deepenLowWater, exact: assembledInput.measurement.exact });
  }
  const baseResult = () => ({ activeNote, semanticSummaryCooldownUntilRound });
  const measurePressure = async () => {
    const profile = readOnlyRecoveryProfile(workingHistory, modelTools);
    const candidate = options.allowReadRecovery && profile.eligible
      ? await assembleRecoveryCandidate(workingHistory, profile.tools, READ_ONLY_CONTEXT_RECOVERY_INSTRUCTION,
        `${executionId}:research-recovery-candidate-1`) : null;
    const measured = assembledInput.measurement;
    if (config.showDebugInfo) ctl.debug({ event: "direct_context_budget_rescue", executionId, modelInputId, roundIndex,
      trigger: "context_budget", compactionAppliedCount, compactionAppliedModes,
      exactMeasurement: measured.exact, contextLength: measured.contextLength, inputTokens: measured.inputTokens,
      requestedOutputReserve: roundOutputReserve, safetyMargin: config.safetyMarginTokens,
      remainingTokens: measured.remainingTokens, fullToolCount: modelTools.length,
      narrowedToolCount: profile.tools.length, narrowedToolNames: profile.tools.map(t => t.name),
      recoveryEligibilityReason: profile.reason, recoveryEpisodeStarted: researchRecoveryEpisodeStarted,
      candidateExactMeasurement: candidate?.assembled.measurement.exact ?? null,
      candidateInputTokens: candidate?.assembled.measurement.inputTokens ?? null,
      candidateRequestedMaxTokens: candidate?.desiredMaxTokens ?? null,
      candidateAppliedMaxTokens: candidate?.budget.appliedMaxTokens ?? null,
      candidateRemainingTokens: candidate?.assembled.measurement.remainingTokens ?? null,
      candidateFit: candidate?.fit ?? false, nextToolCount: candidate?.fit ? profile.tools.length : 0,
      maxFinalAttempts: 1, unconsumedEvidenceProjectedBeforeCandidate: projectionApplied });
    return { kind: "pressure" as const, ...baseResult(), history: workingHistory,
      recoveryCandidate: candidate?.fit ? { history: candidate.history, tools: profile.tools,
        outputReserve: candidate.budget.appliedMaxTokens } : null };
  };

  if (assembledInput.measurement.remainingTokens < 0 && !finalizing && !config.observeOnly
    && !researchRecoveryEpisodeStarted) {
    const rescue = await measurePressure();
    if (rescue.recoveryCandidate) return rescue;
  }
  // Preserve the complete first-consumer exchange. Only measured aggregate
  // pressure permits a bounded archive view of its body.
  if (assembledInput.measurement.remainingTokens < 0 && workingContext && !finalizing && !config.observeOnly) {
    for (const preserve of [FIRST_CONSUMER_RAW_GIT_MAX_CHARS, 0]) {
      const projected = evidenceManager.project(workingHistory,
        { executionId, roundIndex, modelInputId, proofLevel: "returned_tool_result", preserveUnconsumedRawMaxChars: preserve,
          pinnedWorkingSetMaxChars: 0 },
        config.toolResultProjectionChars);
      if (projected.changed) {
        workingHistory = projected.history; projectionApplied = true; workingInputProjected = true; rewritingInput = true;
        deepenLowWater = true;
        assembledInput = await assembleModelInput(workingHistory);
      }
      if (config.showDebugInfo) ctl.debug({ event: preserve ? "working_context_first_consumer_batch" : "working_context_projection",
        executionId, roundIndex, changed: projected.changed, reason: preserve ? projected.reason : "aggregate_prompt_budget_projection",
        archiveFailed: projected.archiveFailed === true, candidateInputTokens: assembledInput.measurement.inputTokens,
        candidateRemainingTokens: assembledInput.measurement.remainingTokens, candidateExact: assembledInput.measurement.exact,
        fit: assembledInput.measurement.remainingTokens >= 0, rawThresholdChars: preserve, archive: workingContext.archive.stats });
      if (assembledInput.measurement.remainingTokens >= 0) break;
    }
  }
  // The legacy path retains its compatibility policy. Hybrid has one exact
  // candidate search below, rather than regular + emergency + LOW searches.
  if (config.contextManagementMode === "legacy" && assembledInput.measurement.remainingTokens < 0 && !config.observeOnly) {
    const emergency = buildCompactedHistory(workingHistory, 0, config, {
      ...contextManager.checkpointOptions,
      maxCheckpointChars: config.maxCheckpointChars - assembledInput.composition.overhead, maxCurrentTurnMessages: 2 });
    if (emergency.history !== workingHistory) {
      workingHistory = emergency.history; compacted = true; compactionCheckpoint = emergency.checkpoint;
      compactionRetention = { mode: "final_budget_emergency", maxCurrentTurnMessages: 2 };
      compactionAppliedCount++; compactionAppliedModes.push("final_budget_emergency");
      assembledInput = await assembleModelInput(workingHistory);
    }
  }
  if (finalizing && !config.observeOnly) {
    const measured = assembledInput.measurement;
    const budget = resolveGenerationBudget({ desiredMaxTokens: roundOutputReserve, contextLength: measured.contextLength,
      inputTokens: measured.inputTokens, safetyMarginTokens: config.safetyMarginTokens, minimumTokens: MIN_FINAL_OUTPUT_TOKENS });
    if (budget.fit && budget.appliedMaxTokens < roundOutputReserve) {
      roundOutputReserve = budget.appliedMaxTokens; outputCapSource = "headroom_clamp";
      assembledInput = await assembleModelInput(workingHistory);
    }
  }
  // A round that rewrites its adopted input near high water reaches the
  // low-water target in the same rewrite (plan B2; see deepenLowWater).
  let lowWater = await contextManager.enforceLowWater(workingHistory, assembleModelInput, {
    force: compacted, deepen: deepenLowWater, hasReadTools, minimumReadTokens: minimumReadBudget(roundTools) });
  const acceptLow = () => {
    if (!lowWater.changed) return;
    if (workingContext && lowWater.checkpoint && !config.observeOnly) {
      const archivedProse = workingContext.archiveConversationProse(workingHistory, lowWater.checkpoint.retainedIndexes);
      if (config.showDebugInfo && archivedProse) ctl.debug({ event: "conversation_prose_archived", executionId,
        modelInputId, roundIndex, archivedMessages: archivedProse });
    }
    workingHistory = lowWater.history; assembledInput = lowWater.assembled;
    compactionCheckpoint = lowWater.checkpoint; compacted = lowWater.success;
    compactionAppliedCount++; compactionAppliedModes.push("final_exact_low_water");
  };
  acceptLow();
  if (!lowWater.canRun && !finalizing && !config.observeOnly) return measurePressure();
  if (lowWater.canRun && !options.reasoningRecovery) {
    const priorNote = activeNote;
    // Build only the summary view from the pre-removal evidence. Do not replace
    // or dispatch this unmeasured history as a research input.
    const semanticCheckpoint = compactionCheckpoint || (projectionApplied
      ? buildCompactedHistory(options.preProjectionHistory, 0, config, { maxCurrentTurnMessages: 0 }).checkpoint : null);
    ({ activeNote, semanticSummaryCooldownUntilRound } = await updateSemanticContext({ tokenSource, config, workingContext,
      compactionCheckpoint: semanticCheckpoint, compacted, projectionApplied, visibleHistory, activeNote, semanticSummaryCooldownUntilRound,
      boundedAudit, finalizing, roundIndex, executionId, objectiveFingerprint, ctl }));
    // A generated note changes the actual input. Reassemble under the same
    // contract; the summary completion itself never authorizes dispatch.
    if (activeNote !== priorNote) {
      lowWater = await contextManager.enforceLowWater(workingHistory, assembleModelInput, {
        force: compacted, deepen: deepenLowWater, hasReadTools, minimumReadTokens: minimumReadBudget(roundTools) });
      assembledInput = lowWater.assembled; acceptLow();
    }
  }
  if (config.showDebugInfo && lowWater.telemetry) ctl.debug({ event: "context_low_water", executionId,
    modelInputId, roundIndex, ...lowWater.telemetry });
  // Optional references never enter workingHistory or the persisted checkpoint.
  // Always derive a fresh, measured candidate from the same evidence history.
  const focused = config.designGuidanceDelivery === "focused" || config.designGuidanceMode === "auto";
  // A recovery round adds no new focused material, but the instruction this
  // execution already admitted stays: dropping it would rewrite the system
  // message and take away the engine contracts the failed round reasoned with.
  const recoveryReuse = focused && options.referenceRecovery ? options.guidanceRetention?.kept : undefined;
  if (lowWater.canRun && !config.observeOnly && !finalizing && config.designGuidanceMode !== "off"
    && config.designGuidanceMaxTokens > 0 && (!focused || !options.referenceRecovery || recoveryReuse)) {
    let candidates: GuidanceCandidate[] = [];
    let selection: ReturnType<typeof selectGuidanceTopics> | undefined;
    let selectionScope = "unscoped";
    // A malformed optional catalogue/selector omits guidance. Cancellation and
    // baseline measurement still belong to their existing execution owners.
    ctl.guardAbort();
    try {
      if (recoveryReuse) selectionScope = recoveryReuse.selectionScope;
      else if (focused && options.guidanceScope) {
        const snapshot = evidenceManager.referenceSnapshot(workingHistory);
        const guidanceScope = referenceGuidanceScope(options.guidanceScope, snapshot);
        selectionScope = JSON.stringify([guidanceScope.engine, guidanceScope.source, snapshot.applicability]);
        if (config.designGuidanceMode === "auto") {
          selection = selectGuidanceTopics(options.guidanceIntent || { primary: "core", reason: "objective_completeness_unknown" }, snapshot);
          candidates = selection.omitted ? [] : autoGuidanceCandidates(selection.primary, selection.supplement, guidanceScope, snapshot);
          // A new topic from returned evidence may select once more, like a new scope.
          selectionScope = JSON.stringify([selectionScope, selection.primary, selection.supplement || null]);
        } else candidates = focusedGuidanceCandidates(config.designGuidanceMode, guidanceScope, snapshot);
      } else if (!focused) {
        const snapshot = evidenceManager.referenceSnapshot(workingHistory);
        const guidanceScope = options.guidanceScope ? referenceGuidanceScope(options.guidanceScope, snapshot) : undefined;
        if (guidanceScope) selectionScope = JSON.stringify([guidanceScope.engine, guidanceScope.source, snapshot.applicability]);
        candidates = designGuidanceCandidates(guidanceScope
          ? selectDesignGuidance(config.designGuidanceMode, guidanceScope, undefined, snapshot.applicability)
          : options.guidanceDocuments || []);
      }
    } catch { ctl.guardAbort(); }
    // A topic from the request, a returned observation or the configured mode
    // backs the material; the bare core fallback stays generic.
    const topicBacked = config.designGuidanceMode === "auto"
      ? Boolean(selection && (selection.primary !== "core" || selection.supplement))
      : config.designGuidanceMode !== "core";
    // The admitted instruction joins the system message, so changing it
    // between rounds discards the server's reusable prefix. Within one
    // execution the first admitted instruction is re-admitted unchanged while
    // it fits (only the per-round reference data, kept outside the system
    // message, follows the current observations). A dropped instruction stays
    // out until a round that already rewrote this input (projection or
    // compaction) or one back under low water: re-admitting it there costs no
    // prefix the round still had, and the gap to high water keeps it from
    // alternating. Newly returned engine/version evidence or a topic it adds (the selection
    // scope) is the one input that may select again. Catalogue/generator output and runtime
    // identity are assumed fixed for the execution.
    // Retained material is reused only for the scope it was selected for, kept
    // or dropped alike; a changed scope selects from the current candidates at
    // once, and a dropped entry never outlives its scope.
    const retention = options.guidanceRetention;
    const retained = retention?.kept || retention?.dropped;
    const rescoped = Boolean(retained && retained.selectionScope !== selectionScope);
    if (retention && rescoped) { retention.kept = undefined; retention.dropped = undefined; }
    const dropped = retention?.dropped;
    const readmitCause = dropped
      ? workingInputProjected || compacted || lowWater.changed ? "input_reshaped"
        // Configured, not effective, low water: a mandatory-floor exception
        // raises the effective one to high water, which would remove the gap.
        : assembledInput.measurement.inputTokens <= (lowWater.telemetry?.configuredLowWaterTokens ?? -1) ? "low_water" : undefined
      : undefined;
    // A dropped scope with no admitted instruction (a rescope that did not fit)
    // selects again from the current candidates when it may be readmitted.
    const reuse = retention?.kept || (readmitCause && dropped?.instruction !== undefined ? dropped : undefined);
    const offered = reuse ? [...new Map([...candidates, {}].map(({ referenceData, dataIds }: Partial<GuidanceCandidate>) =>
      [referenceData, { ids: reuse.ids, instruction: reuse.instruction!, referenceData, dataIds }])).values()]
      : dropped && !readmitCause ? [] : candidates;
    const guidance = await contextManager.addOptionalReferences(assembledInput, offered,
      (instruction, referenceData) => assembleModelInput(workingHistory, { instructions: [...roundScopeInstructions, instruction], referenceData }),
      { maxAddedTokens: config.designGuidanceMaxTokens, hasReadTools: roundTools.some(hasReadCapability),
        minimumReadTokens: minimumReadBudget(roundTools), guardAbort: () => ctl.guardAbort(),
        admissionWater: reuse || topicBacked ? "high" : "low" });
    assembledInput = guidance.assembled;
    const telemetry: typeof guidance.telemetry & { stickyCause?: string } = { ...guidance.telemetry };
    const included = telemetry.reason === "included";
    // The omission reasons describe the instruction actually offered.
    const omissionReasons = reuse ? reuse.omissionReasons || [] : candidates[0]?.omissionReasons || [];
    const scopeCause = (cause: string) => rescoped ? `scope_changed${cause ? `:${cause}` : ""}` : cause;
    if (retention && dropped && !readmitCause) Object.assign(telemetry, { reason: "sticky_dropped", stickyCause: "dropped_earlier" });
    else if (retention && dropped && included) {
      Object.assign(telemetry, { reason: "sticky_readmitted", stickyCause: readmitCause });
      retention.kept = { ids: telemetry.selectedIds, instruction: guidance.selectedInstruction!, selectionScope,
        tokens: telemetry.addedTokens, omissionReasons };
      retention.dropped = undefined;
    } else if (retention && dropped) Object.assign(telemetry, { reason: "sticky_dropped", stickyCause: `${readmitCause}:${telemetry.reason}` });
    else if (retention && reuse && !included) {
      Object.assign(telemetry, { reason: "sticky_dropped", stickyCause: telemetry.reason });
      retention.dropped = retention.kept; retention.kept = undefined;
    } else if (reuse) {
      telemetry.reason = "sticky_kept";
      if (retention?.kept) retention.kept.tokens = telemetry.addedTokens;
    } else if (retention && included && guidance.selectedInstruction !== undefined) {
      if (rescoped) telemetry.stickyCause = "scope_changed";
      retention.kept = { ids: telemetry.selectedIds, instruction: guidance.selectedInstruction, selectionScope,
        tokens: telemetry.addedTokens, omissionReasons };
    } else if (retention && rescoped) {
      // A rescope that did not fit leaves only its scope: the old instruction
      // is invalid there, and the current candidates select again when the
      // input may take them back (low water or a rewritten round).
      Object.assign(telemetry, { reason: "sticky_dropped", stickyCause: scopeCause(telemetry.reason) });
      retention.dropped = { ids: [], selectionScope };
    }
    if (config.showDebugInfo) ctl.debug({ event: "design_guidance_input", executionId, modelInputId, roundIndex,
      mode: config.designGuidanceMode, delivery: config.designGuidanceDelivery,
      configuredMode: config.designGuidanceMode, configuredDelivery: config.designGuidanceDelivery,
      effectiveDelivery: focused ? "focused" : "documents", objectiveFingerprint,
      omissionReasons,
      outcome: guidance.telemetry.reason === "included" ? "included" : "omitted",
      ...(selection ? { primaryTopic: selection.primary, supplementTopic: selection.supplement,
        selectionReasonCodes: [...selection.reasons, ...omissionReasons],
        selectionObservationIds: selection.observationIds } : {}), ...telemetry });
  } else if (config.showDebugInfo) ctl.debug({ event: "design_guidance_input", executionId, modelInputId, roundIndex,
    configuredMode: config.designGuidanceMode, configuredDelivery: config.designGuidanceDelivery,
    effectiveDelivery: focused ? "focused" : "documents", outcome: "not_attempted",
    reason: config.observeOnly ? "observe_only" : config.designGuidanceMode === "off" ? "disabled"
      : config.designGuidanceMaxTokens <= 0 ? "zero_allowance" : finalizing ? "finalization"
        : options.referenceRecovery ? "recovery_policy" : "input_unavailable",
    addedTokens: 0, attempts: 0, selectedIds: [], selectedDataIds: [],
    finalInputTokens: assembledInput.measurement.inputTokens });
  const finalMeasurement = assembledInput.measurement;
  projectionExhausted = releasedAllPins && contextManager.shouldProjectEvidence(finalMeasurement, hasReadTools,
    minimumReadBudget(roundTools));
  const pressure = Object.freeze({ workingSetExhausted: projectionExhausted, measuredStage: "final" as const });
  if (options.reportContinuation && (!lowWater.canRun || finalMeasurement.remainingTokens < 0
    || !assembledInput.history.getMessagesArray().some(message => message.isAssistantMessage()
      && message.getText().includes(options.reportContinuation!)))) {
    if (config.showDebugInfo) ctl.debug({ event: "report_continuation_blocked", executionId, modelInputId,
      reason: "continuation_context_budget", prefixPreserved: assembledInput.history.getMessagesArray()
        .some(message => message.getText().includes(options.reportContinuation!)), modelCallSkipped: true });
    return { kind: "continuation_blocked" as const, activeNote, semanticSummaryCooldownUntilRound };
  }
  if (!lowWater.canRun || (!config.observeOnly && finalMeasurement.remainingTokens < 0)) {
    if (!finalizing && !config.observeOnly) return measurePressure();
    ctl.debug({ event: "context_execution_blocked", executionId, modelInputId,
      disposition: lowWater.disposition, exact: finalMeasurement.exact });
    if (finalMeasurement.remainingTokens < 0) {
      if (config.showDebugInfo) ctl.debug({ event: "direct_context_budget_rejected", executionId, modelInputId, roundIndex,
        exactMeasurement: finalMeasurement.exact, fit: false, modelCallSkipped: true,
        contextLength: finalMeasurement.contextLength, inputTokens: finalMeasurement.inputTokens,
        outputReserve: roundOutputReserve, remainingTokens: finalMeasurement.remainingTokens,
        finalizationTrigger: options.finalizationTrigger,
        minimumFinalOutputTokens: MIN_FINAL_OUTPUT_TOKENS });
      throw new Error(`CONTEXT_BUDGET_EXCEEDED: final model input exceeds the context budget by ${-finalMeasurement.remainingTokens} tokens`);
    }
    throw new Error(`CONTEXT_EXECUTION_BLOCKED: ${lowWater.disposition}`);
  }
  if (config.contextManagementMode !== "legacy") compacted = compacted && lowWater.success;
  return { kind: "ready" as const, ...baseResult(), before, workingHistory, modelHistory: workingHistory,
    modelComposition: assembledInput.composition, modelInput: assembledInput.history, finalMeasurement, assembledInput,
    compacted, compactionAppliedCount, compactionAppliedModes, compactionCheckpoint, compactionRetention,
    lowWater, projectionApplied, projectionExhausted, pressure, roundOutputReserve, outputCapSource, assembleRecoveryCandidate, measureOriginalProfile,
    mandatoryFloorMeasurement: lowWater.floorMeasurement,
    mandatoryFloorExceedsTarget: Boolean(lowWater.floorMeasurement?.exact
      && lowWater.floorMeasurement.inputTokens > config.workingInputTargetTokens) };
}

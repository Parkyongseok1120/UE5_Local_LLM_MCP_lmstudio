import { Chat, type PredictionLoopHandlerController } from "@lmstudio/sdk";
import { minimumReadResultTokens } from "./budget-broker";
import { resolveGenerationBudget } from "./context-budget";
import { buildCompactedHistory, composeModelHistory, ContextManager, createInputAssembler,
  measureContext, prepareWorkingInput } from "./context-manager";
import { updateSemanticContext } from "./semantic-handoff";
import { workingContextModule } from "./context-ports";
import { EvidenceManager } from "./evidence-manager";
import { FIRST_CONSUMER_RAW_GIT_MAX_CHARS, MIN_FINAL_OUTPUT_TOKENS, RESEARCH_RECOVERY_MAX_TOKENS,
  type ContinuityNote, type DirectConfig } from "./execution-contracts";
import { READ_ONLY_CONTEXT_RECOVERY_INSTRUCTION } from "./execution-instructions";
import { readOnlyRecoveryProfile } from "./recovery-coordinator";
import { hasReadCapability, type RemoteToolLike } from "./tool-capability-registry";

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
  finalizing: boolean; boundedAudit: boolean; researchRecoveryEpisodeStarted: boolean;
  finalizationTrigger: string | null;
  projectionApplied: boolean; executionId: string; modelInputId: string; roundIndex: number;
};

/** Select and measure one executable input. This module returns a disposition;
 * it never changes execution phase, commits evidence, or dispatches research/report predictions. */
export async function prepareRoundInput(options: RoundInputOptions) {
  const { ctl, config, tokenSource, contextManager, evidenceManager, workingContext,
    visibleHistory, preProjectionHistory, postProjectionHistory, roundTools, modelTools,
    roundScopeInstructions, scopeInstructions, noteEnabled, historicalAvailabilityLedger,
    objectiveFingerprint, finalizing, boundedAudit, researchRecoveryEpisodeStarted,
    executionId, modelInputId, roundIndex } = options;
  let { workingHistory, roundOutputReserve, activeNote, semanticSummaryCooldownUntilRound, projectionApplied } = options;
  const minimumReadBudget = (tools: RemoteToolLike[]) => minimumReadResultTokens(
    tools.filter(hasReadCapability), Boolean(workingContext?.hasArchivedEvidence));
  let outputCapSource: "configured" | "headroom_clamp" = "configured";
  const beforeInput = composeModelHistory(workingHistory, noteEnabled ? activeNote : null,
    config, roundScopeInstructions, noteEnabled);
  const measureRoundInput = (history: Chat) => measureContext(tokenSource, history, config, roundTools,
    { outputReserve: roundOutputReserve });
  let { before, modelHistory, compacted, compactionAppliedCount, compactionAppliedModes,
    compactionCheckpoint, compactionRetention } = await prepareWorkingInput({ tokenSource, config,
    workingHistory, activeNote, noteEnabled, roundScopeInstructions, roundTools, roundOutputReserve,
    beforeInput, measureRoundInput, ctl, executionId, roundIndex });
  workingHistory = modelHistory;
  const assembleModelInput = createInputAssembler({ tokenSource, config, roundTools,
    roundScopeInstructions, getRoundOutputReserve: () => roundOutputReserve, modelInputId,
    noteEnabled, getNote: () => activeNote, historicalAvailabilityLedger, preProjectionHistory, postProjectionHistory });
  const assembleRecoveryCandidate = async (sourceHistory: Chat, tools: RemoteToolLike[], instruction: string,
    candidateModelInputId: string) => {
    const desiredMaxTokens = Math.min(config.maxOutputReserve, RESEARCH_RECOVERY_MAX_TOKENS);
    let reserve = desiredMaxTokens;
    const assemble = (history: Chat) => assembleModelInput(history, {
      tools, instructions: [...scopeInstructions, instruction], outputReserve: reserve, modelInputId: candidateModelInputId });
    let assembled = await assemble(sourceHistory);
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
        { executionId, roundIndex, modelInputId: candidateModelInputId, proofLevel: "returned_tool_result" },
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
  let assembledInput = await assembleModelInput(workingHistory);
  const baseResult = () => ({ activeNote, semanticSummaryCooldownUntilRound });
  const recoveryDisposition = async () => {
    const profile = readOnlyRecoveryProfile(workingHistory, modelTools);
    const candidate = !researchRecoveryEpisodeStarted && profile.eligible
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
    return candidate?.fit ? { kind: "recover" as const, ...baseResult(),
      history: candidate.history, tools: profile.tools, outputReserve: candidate.budget.appliedMaxTokens }
      : { kind: "finalize" as const, ...baseResult(), history: workingHistory };
  };

  if (assembledInput.measurement.remainingTokens < 0 && !finalizing && !config.observeOnly
    && !researchRecoveryEpisodeStarted) {
    const rescue = await recoveryDisposition();
    if (rescue.kind === "recover") return rescue;
  }
  // Preserve the complete first-consumer exchange. Only measured aggregate
  // pressure permits a bounded archive view of its body.
  if (assembledInput.measurement.remainingTokens < 0 && workingContext && !finalizing && !config.observeOnly) {
    for (const preserve of [FIRST_CONSUMER_RAW_GIT_MAX_CHARS, 0]) {
      const projected = evidenceManager.project(workingHistory,
        { executionId, roundIndex, modelInputId, proofLevel: "returned_tool_result", preserveUnconsumedRawMaxChars: preserve },
        config.toolResultProjectionChars);
      if (projected.changed) {
        workingHistory = projected.history; projectionApplied = true;
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
  let lowWater = await contextManager.enforceLowWater(workingHistory, assembleModelInput, {
    force: compacted, hasReadTools: roundTools.some(hasReadCapability),
    minimumReadTokens: minimumReadBudget(roundTools) });
  const acceptLow = () => {
    if (!lowWater.changed) return;
    workingHistory = lowWater.history; assembledInput = lowWater.assembled;
    compactionCheckpoint = lowWater.checkpoint; compacted = lowWater.success;
    compactionAppliedCount++; compactionAppliedModes.push("final_exact_low_water");
  };
  acceptLow();
  if (!lowWater.canRun && !finalizing && !config.observeOnly) return recoveryDisposition();
  if (lowWater.canRun) {
    const priorNote = activeNote;
    ({ activeNote, semanticSummaryCooldownUntilRound } = await updateSemanticContext({ tokenSource, config, workingContext,
      compactionCheckpoint, compacted, projectionApplied, visibleHistory, activeNote, semanticSummaryCooldownUntilRound,
      boundedAudit, finalizing, roundIndex, executionId, objectiveFingerprint, ctl }));
    // A generated note changes the actual input. Reassemble under the same
    // contract; the summary completion itself never authorizes dispatch.
    if (activeNote !== priorNote) {
      lowWater = await contextManager.enforceLowWater(workingHistory, assembleModelInput, {
        force: compacted, hasReadTools: roundTools.some(hasReadCapability),
        minimumReadTokens: minimumReadBudget(roundTools) });
      assembledInput = lowWater.assembled; acceptLow();
    }
  }
  if (config.showDebugInfo && lowWater.telemetry) ctl.debug({ event: "context_low_water", executionId,
    modelInputId, roundIndex, ...lowWater.telemetry });
  const finalMeasurement = assembledInput.measurement;
  if (!lowWater.canRun || (!config.observeOnly && finalMeasurement.remainingTokens < 0)) {
    if (!finalizing && !config.observeOnly) return recoveryDisposition();
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
    lowWater, projectionApplied, roundOutputReserve, outputCapSource, assembleRecoveryCandidate,
    mandatoryFloorMeasurement: lowWater.floorMeasurement,
    mandatoryFloorExceedsTarget: Boolean(lowWater.floorMeasurement?.exact
      && lowWater.floorMeasurement.inputTokens > config.workingInputTargetTokens) };
}

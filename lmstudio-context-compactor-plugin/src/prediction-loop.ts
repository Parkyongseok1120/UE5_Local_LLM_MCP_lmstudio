import {
  Chat,
  ChatMessage,
  type LMStudioClient,
  type PredictionLoopHandler,
  type ToolCallRequest
} from "@lmstudio/sdk";
import crypto from "node:crypto";
import { attachmentBoundary } from "./attachment-boundary";
import { createAttachmentContext } from "./attachment-tools";
import { BudgetBroker, minimumReadResultTokens } from "./budget-broker";
import { resolveGenerationBudget } from "./context-budget";
import { buildCompactedHistory, composeModelHistory, ContextManager, createInputAssembler, measureContext, modelHistoryMessage, normalizeHistory, normalizeMessages, prepareWorkingInput, selectSoftCompaction, toolDefinitionSurface, updateSemanticContext } from "./context-manager";
import { core, inputAvailability, modelNotes, workingContextModule } from "./context-ports";
import { canRecoverOutputLimit, classifyFinalDelivery, classifyOutputLimitStage, DeliveryController } from "./delivery-controller";
import { completedRequestFingerprints, EvidenceManager, observeRecoveryProgress, seedRecoveryProgress } from "./evidence-manager";
import { assistantNoteTelemetry, messageEvidenceTelemetry, serializedCheckpointCounts, telemetryFingerprint, ToolRoundStagnationDetector } from "./evidence-telemetry";
import { readConfig } from "./execution-config";
import { FIRST_CONSUMER_RAW_GIT_MAX_CHARS, MIN_FINAL_OUTPUT_TOKENS, RESEARCH_RECOVERY_MAX_TOKENS, type ContinuityNote } from "./execution-contracts";
import { BOUNDED_AUDIT_FINAL_INSTRUCTION, CONTEXT_BUDGET_FINAL_INSTRUCTION, FRESH_TOOL_PLANNING_RETRY_INSTRUCTION, OUTPUT_RECOVERY_FINAL_INSTRUCTION, READ_ONLY_BATCH_INSTRUCTION, READ_ONLY_CONTEXT_RECOVERY_INSTRUCTION, RESEARCH_RECOVERY_FINAL_INSTRUCTION } from "./execution-instructions";
import { ExecutionState, RoundTransaction } from "./execution-state";
import { GenerationRepetitionDetector, PredictionStreamRenderer } from "./prediction-stream";
import { createMessageEmitter, createRoundActivityTracker, createToolGenerationTracker, selectedSourceIsThisPlugin } from "./prediction-ui";
import { classifyRawToolIntent, containsUnresolvedToolIntent, hasUnexecutedRawToolSyntax, visibleAssistantOutput, visibleTextFromMessages } from "./raw-tool-intent";
import { freshToolPlanningRetryDecision, planReadRecovery, readOnlyRecoveryProfile, RecoveryCoordinator } from "./recovery-coordinator";
import { runOneToolRound } from "./round-loop";
import { runtimeInstallationIdentity } from "./runtime-identity";
import { classifyToolCallBoundary, createToolGuard } from "./tool-boundary";
import { hasReadCapability, isObservationOnlyToolCall, localObservationTools, ToolCapabilityRegistry, type RemoteToolLike } from "./tool-capability-registry";
import {
  bindProjectArguments,
  detectMentionedProject,
  filterToolsForScope,
  renderToolScopeInstruction,
  resolveToolScope,
  type ScopedTool
} from "./tool-scope";
import { workingContextBoundary } from "./working-context-boundary";

export function createPredictionLoopHandler(
  noteStore: InstanceType<typeof modelNotes.ContinuityNoteStore> = new modelNotes.ContinuityNoteStore(),
  client?: LMStudioClient,
  contextBoundaryStore: Pick<typeof workingContextBoundary, "restore"> = workingContextBoundary,
  workingContextOptions: Record<string, unknown> = {},
): PredictionLoopHandler {
  return async (ctl) => {
    ctl.guardAbort();
    const config = readConfig(ctl);
    const pulledHistory = await ctl.pullHistory();
    const executionId = crypto.randomUUID();
    const attachments = config.observeOnly ? { modelHistory: pulledHistory, attachmentHistory: pulledHistory }
      : attachmentBoundary.restore(pulledHistory, client);
    const contextBoundary = config.contextManagementMode === "legacy" || config.observeOnly
      ? { modelHistory: attachments.modelHistory, scope: null, reason: "disabled" }
      : contextBoundaryStore.restore(attachments.modelHistory);
    const cleanAttachmentHistory = config.contextManagementMode === "legacy" || config.observeOnly
      ? attachments.attachmentHistory
      : contextBoundaryStore.restore(attachments.attachmentHistory).modelHistory;
    const originalHistory = contextBoundary.modelHistory;
    if (config.showDebugInfo && config.contextManagementMode !== "legacy" && !config.observeOnly) ctl.debug({
      event: "working_context_boundary_restore",
      status: contextBoundary.reason || "unknown",
      reused: Boolean(contextBoundary.scope),
    });
    const tokenSource = await ctl.tokenSource();
    if (selectedSourceIsThisPlugin(tokenSource)) {
      throw new Error("Select the actual Qwen/LLM in LM Studio. The context compactor is middleware, not a chat model.");
    }

    let workingDirectory = "";
    try { workingDirectory = ctl.getWorkingDirectory(); } catch { /* No stable chat workspace is available. */ }
    const noteWorkingDirectory = config.observeOnly ? "" : workingDirectory;
    const toolSession = await ctl.startToolUseSession();
    const mentionedProject = detectMentionedProject(originalHistory.getMessagesArray());
    const scope = resolveToolScope(
      config.projectEngine,
      config.projectIdentity,
      workingDirectory,
      toolSession.tools as Array<ScopedTool>,
      mentionedProject,
    );
    const attachmentContext = config.observeOnly
      ? { tools: [], instruction: "", attachmentCount: 0 }
      : createAttachmentContext(cleanAttachmentHistory, client);
    const scopedRemoteTools = config.observeOnly
      ? toolSession.tools as Array<ScopedTool>
      : filterToolsForScope(toolSession.tools as Array<ScopedTool>, scope);
    const workingContext = config.contextManagementMode === "legacy" || config.observeOnly ? null
      : new workingContextModule.WorkingContext({
        conversation: contextBoundary.scope?.conversation || executionId,
        lineage: contextBoundary.scope?.conversation || executionId,
        workspace: workingDirectory || `unverified-workspace:${executionId}`,
        repository: scope.projectIdentity || workingDirectory || `unverified-repository:${executionId}`,
      }, {
        ...workingContextOptions,
        durable: Boolean(contextBoundary.scope && workingDirectory),
        lineage: contextBoundary.scope?.lineage || null,
        parentLineage: contextBoundary.scope?.parentLineage || null,
      });
    const localTools = [...attachmentContext.tools, ...(workingContext ? [workingContext.tool()] : [])];
    for (const tool of localTools) localObservationTools.add(tool);
    const registeredModelTools = [...toolSession.tools as Array<ScopedTool>, ...localTools] as Array<RemoteToolLike>;
    const capabilityRegistry = new ToolCapabilityRegistry([...scopedRemoteTools, ...localTools] as Array<RemoteToolLike>);
    const allModelTools = capabilityRegistry.tools;
    const evidenceManager = new EvidenceManager(workingContext, allModelTools);
    const modelTools = config.auditCompletionMode === "bounded"
      ? capabilityRegistry.readProfile()
      : allModelTools;
    if (config.showDebugInfo) ctl.debug({
      event: "direct_runtime_identity",
      executionId,
      ...runtimeInstallationIdentity(),
      modelIdentifier: String((tokenSource as { identifier?: unknown }).identifier || "unknown"),
      toolRegistryFingerprint: telemetryFingerprint(registeredModelTools.map(tool => ({
        name: tool.name,
        pluginIdentifier: tool.pluginIdentifier || null,
        schema: tool.parametersJsonSchema || null,
      }))),
      fullToolCount: modelTools.length,
      contextManagementMode: config.contextManagementMode,
      configuredWorkingInputTargetTokens: config.workingInputTargetTokens,
      configuredWorkingInputTriggerTokens: config.workingInputTriggerTokens,
      configuredSoftRemainingTokens: config.softRemainingTokens,
      configuredHardRemainingTokens: config.hardRemainingTokens,
      configuredMaxOutputReserve: config.maxOutputReserve,
      configuredSafetyMarginTokens: config.safetyMarginTokens,
      configuredFallbackContextLength: config.assumedContextLength,
    });
    const scopeInstructions = config.observeOnly ? [] : [
      ...(scope.availableUnityTools + scope.availableUnrealTools > 0
        ? [renderToolScopeInstruction(scope)] : []),
      ...(attachmentContext.instruction ? [attachmentContext.instruction] : []),
      ...(config.contextManagementMode !== "legacy"
        && modelTools.some(hasReadCapability)
        ? [READ_ONLY_BATCH_INSTRUCTION] : []),
    ];
    const emitter = createMessageEmitter(ctl, modelTools);
    const visibleHistory = Chat.from(originalHistory);
    const historicalAvailabilityLedger: Array<Record<string, unknown>> = [];
    if (config.inputAvailabilityMode !== "off") {
      const normalizedOriginal = normalizeHistory(originalHistory);
      historicalAvailabilityLedger.push(...inputAvailability.currentRawObservations(normalizedOriginal));
      const bootstrap = core.buildCheckpoint(normalizedOriginal, {
        recentCompleteTurns: config.recentCompleteTurns,
        maxCheckpointChars: config.maxCheckpointChars,
        maxToolResultChars: config.maxToolResultChars,
      });
      historicalAvailabilityLedger.push(...inputAvailability.historicalAvailabilityFromMemory(bootstrap.memory));
    }
    const restoredWindow = workingContext?.restore(originalHistory);
    const restoredArchiveRefs = workingContext?.summaryRefs() || new Set<string>();
    const objectiveFingerprint = modelNotes.objectiveFingerprint(originalHistory.getMessagesArray());
    const originalMessages = originalHistory.getMessagesArray();
    const priorHistoryKey = originalMessages.at(-1)?.isUserMessage()
      ? modelNotes.historyKey(originalMessages.slice(0, -1), noteWorkingDirectory) : "";
    const priorNote = priorHistoryKey ? noteStore.read(priorHistoryKey) : null;
    let activeNote = priorNote?.scope.objectiveFingerprint === objectiveFingerprint
      ? modelNotes.reconcileStoredNote(priorNote, originalMessages, restoredArchiveRefs) : null;
    // A newly attached rubric may change what "review C" means even when the
    // request text and source hashes are identical. Never carry review claims
    // across this unverified criterion boundary.
    if (activeNote && cleanAttachmentHistory.getMessagesArray().at(-1)?.hasFiles()) {
      delete activeNote.reviewClaims;
    }
    if (!activeNote && workingContext?.note) {
      activeNote = modelNotes.reconcileStoredNote(
        workingContext.note as ContinuityNote, originalMessages, restoredArchiveRefs,
      );
    }
    const restoredWindowApplied = restoredWindow?.reason === "restored_remeasure_required";
    let workingHistory = restoredWindow?.history || originalHistory;
    if (workingContext && config.showDebugInfo) ctl.debug({
      event: "working_context_restore",
      executionId,
      mode: config.contextManagementMode,
      status: restoredWindow?.reason || "session_only",
      durable: Boolean(contextBoundary.scope && workingDirectory),
      conversationScopeVerified: Boolean(contextBoundary.scope),
    });
    let roundIndex = 0;
    const boundedAudit = config.auditCompletionMode === "bounded";
    const researchStartedAt = Date.now();
    const researchDeadlineAt = researchStartedAt + config.auditResearchSeconds * 1000;
    const execution = new ExecutionState();
    const budgetBroker = new BudgetBroker(config);
    const contextManager = new ContextManager(config, budgetBroker);
    const deliveryController = new DeliveryController();
    const recoveryCoordinator = new RecoveryCoordinator();
    let toolPlanningRetryAttempts = 0;
    let toolPlanningRetryBlockedFingerprints = new Set<string>();
    let toolPlanningRetryInstruction = FRESH_TOOL_PLANNING_RETRY_INSTRUCTION;
    let researchRecoveryEpisodeStarted = false;
    let researchRecoveryToolNames = new Set<string>();
    let researchRecoveryOutputReserve = Math.min(config.maxOutputReserve, RESEARCH_RECOVERY_MAX_TOKENS);

    const seedResearchRecoveryTracking = (history: Chat) => evidenceManager.seed(history);
    let researchRecoveryReason = "";
    let semanticSummaryCooldownUntilRound = -1;
    const executionCost = {
      modelCalls: 0, promptTokens: 0, predictedTokens: 0,
      elapsedMs: 0, promptProcessingMs: 0, generationElapsedMs: 0, unknownPromptProcessingCalls: 0,
      unknownUsageCalls: 0
    };
    let activeActivity: ReturnType<typeof createRoundActivityTracker> | null = null;
    const toolStagnation = new ToolRoundStagnationDetector();
    try {
      while (true) {
        ctl.guardAbort();
        if (boundedAudit && !execution.finalizing && Date.now() >= researchDeadlineAt) {
          execution.finishResearch("research_time_limit");
          continue;
        }
        const finalizing = execution.finalizing;
        const researchRecoveryRound = !finalizing && execution.recovering;
        const toolPlanningRetryRound = researchRecoveryRound && execution.replanning;
        if (finalizing) {
          const deliveryAttempt = deliveryController.begin(boundedAudit, toolPlanningRetryAttempts);
          const finalizationAttemptLimit = deliveryAttempt.limit;
          if (!deliveryAttempt.allowed) {
            if (config.showDebugInfo) ctl.debug({
              event: "terminal_delivery_exhausted",
              executionId,
              finalizationTrigger: execution.finalizationTrigger,
              finalizationAttempts: deliveryController.attempts,
              finalizationAttemptLimit,
              recoveryAttempts: toolPlanningRetryAttempts,
              recoveryEpisodeStarted: researchRecoveryEpisodeStarted,
            });
            ctl.createStatus({
              status: "error",
              text: "제한된 조사 복구 이후 최종 보고 기회를 모두 사용해 추가 생성을 중단했습니다."
            });
            break;
          }
        }
        const preProjectionHistory = Chat.from(workingHistory);
        let projectionApplied = false;
        if (workingContext && !finalizing) {
          const projected = evidenceManager.project(workingHistory, {
            executionId, roundIndex, modelInputId: `${executionId}:prediction-${roundIndex + 1}`,
            proofLevel: "returned_tool_result",
            preserveUnconsumedRawMaxChars: FIRST_CONSUMER_RAW_GIT_MAX_CHARS,
          }, config.toolResultProjectionChars);
          if (projected.changed) {
            workingHistory = projected.history;
            projectionApplied = true;
          }
          if (config.showDebugInfo && (projected.changed || projected.archiveFailed)) ctl.debug({
            event: "working_context_projection",
            executionId,
            roundIndex,
            changed: projected.changed,
            archiveFailed: projected.archiveFailed === true,
            reason: projected.reason,
            archive: workingContext.archive.stats,
          });
        }
        const roundTools = finalizing ? [] : researchRecoveryRound
          ? modelTools.filter(tool => researchRecoveryToolNames.has(tool.name))
          : modelTools;
        const actToolSurfaceFingerprint = telemetryFingerprint(toolDefinitionSurface(roundTools));
        let roundOutputReserve = finalizing
          ? execution.finalizationTrigger === "output_recovery"
            ? Math.min(config.maxOutputReserve, config.outputRecoveryMaxTokens)
            : execution.finalizationTrigger === "context_budget" && !boundedAudit
              ? config.maxOutputReserve
              : Math.min(config.maxOutputReserve, config.auditFinalMaxTokens)
          : researchRecoveryRound ? researchRecoveryOutputReserve : config.maxOutputReserve;
        const requestedOutputCap = roundOutputReserve;
        let outputCapSource: "configured" | "headroom_clamp" = "configured";
        const roundScopeInstructions = finalizing
          ? [...scopeInstructions, execution.finalizationTrigger === "output_recovery"
            ? OUTPUT_RECOVERY_FINAL_INSTRUCTION
            : execution.finalizationTrigger === "context_budget"
              ? CONTEXT_BUDGET_FINAL_INSTRUCTION
              : ["research_recovery_complete", "research_recovery_exhausted"].includes(execution.finalizationTrigger || "")
                ? RESEARCH_RECOVERY_FINAL_INSTRUCTION : BOUNDED_AUDIT_FINAL_INSTRUCTION]
          : [...scopeInstructions, ...(researchRecoveryRound
            ? [toolPlanningRetryRound ? toolPlanningRetryInstruction : READ_ONLY_CONTEXT_RECOVERY_INSTRUCTION]
            : [])];
        const modelInputId = finalizing
          ? execution.finalizationTrigger === "output_recovery"
            ? `${executionId}:output-recovery-${deliveryController.attempts}`
            : `${executionId}:final-report-${deliveryController.attempts}`
          : toolPlanningRetryRound
            ? `${executionId}:tool-planning-retry-${toolPlanningRetryAttempts}`
            : researchRecoveryRound
              ? `${executionId}:research-recovery-${recoveryCoordinator.toolRounds + 1}`
              : `${executionId}:prediction-${roundIndex + 1}`;
        activeActivity = createRoundActivityTracker(ctl, roundIndex);
        const noteEnabled = Boolean(noteWorkingDirectory && objectiveFingerprint && !config.observeOnly);
        if (noteEnabled && activeNote) {
          activeNote = modelNotes.reconcileStoredNote(
            activeNote, visibleHistory.getMessagesArray(), workingContext?.summaryRefs(),
          );
        }
        const postProjectionHistory = Chat.from(workingHistory);
        const beforeInput = composeModelHistory(
          workingHistory, noteEnabled ? activeNote : null, config, roundScopeInstructions, noteEnabled,
        );
        const measureRoundInput = (history: Chat) => measureContext(
          tokenSource, history, config, roundTools, { outputReserve: roundOutputReserve },
        );
        let { before, modelHistory, compacted, compactionAppliedCount, compactionAppliedModes,
          compactionCheckpoint, compactionRetention } = await prepareWorkingInput({
tokenSource, config,
            workingHistory, activeNote, noteEnabled, roundScopeInstructions, roundTools, roundOutputReserve,
            beforeInput, measureRoundInput, ctl, executionId, roundIndex
});
        workingHistory = modelHistory;
        ({ activeNote, semanticSummaryCooldownUntilRound } = await updateSemanticContext({
tokenSource, config,
          workingContext, compactionCheckpoint, compacted, projectionApplied, visibleHistory, activeNote,
          semanticSummaryCooldownUntilRound, boundedAudit, finalizing, roundIndex, executionId, objectiveFingerprint, ctl
}));
        const assembleModelInput = createInputAssembler({
          tokenSource, config, roundTools,
          roundScopeInstructions, getRoundOutputReserve: () => roundOutputReserve, modelInputId,
          noteEnabled, getNote: () => activeNote, historicalAvailabilityLedger,
          preProjectionHistory, postProjectionHistory
        });
        const assembleRecoveryCandidate = async (
          sourceHistory: Chat,
          tools: Array<RemoteToolLike>,
          instruction: string,
          candidateModelInputId: string,
        ) => {
          const desiredMaxTokens = Math.min(config.maxOutputReserve, RESEARCH_RECOVERY_MAX_TOKENS);
          let assembled = await assembleModelInput(sourceHistory, {
            tools,
            instructions: [...scopeInstructions, instruction],
            outputReserve: desiredMaxTokens,
            modelInputId: candidateModelInputId,
          });
          const budget = resolveGenerationBudget({
            desiredMaxTokens,
            contextLength: assembled.measurement.contextLength,
            inputTokens: assembled.measurement.inputTokens,
            safetyMarginTokens: config.safetyMarginTokens,
            minimumTokens: MIN_FINAL_OUTPUT_TOKENS,
          });
          if (budget.fit && budget.appliedMaxTokens < desiredMaxTokens) {
            assembled = await assembleModelInput(sourceHistory, {
              tools,
              instructions: [...scopeInstructions, instruction],
              outputReserve: budget.appliedMaxTokens,
              modelInputId: candidateModelInputId,
            });
          }
          return {
            assembled, budget, desiredMaxTokens,
            fit: budget.fit && assembled.measurement.remainingTokens >= 0
          };
        };
        let assembledInput = await assembleModelInput(workingHistory);
        let modelComposition = assembledInput.composition;
        let modelInput = assembledInput.history;
        let finalMeasurement = assembledInput.measurement;
        if (finalMeasurement.remainingTokens < 0 && !finalizing && !config.observeOnly
          && !researchRecoveryEpisodeStarted) {
          const recoveryProfile = readOnlyRecoveryProfile(workingHistory, modelTools);
          const recoveryCandidate = recoveryProfile.eligible
            ? await assembleRecoveryCandidate(
              workingHistory,
              recoveryProfile.tools,
              READ_ONLY_CONTEXT_RECOVERY_INSTRUCTION,
              `${executionId}:research-recovery-candidate-1`,
            ) : null;
          if (recoveryCandidate?.fit) {
            if (config.showDebugInfo) ctl.debug({
              event: "direct_context_budget_rescue",
              executionId,
              modelInputId,
              roundIndex,
              trigger: "context_budget",
              compactionAppliedCount,
              compactionAppliedModes,
              exactMeasurement: finalMeasurement.exact,
              contextLength: finalMeasurement.contextLength,
              inputTokens: finalMeasurement.inputTokens,
              requestedOutputReserve: roundOutputReserve,
              safetyMargin: config.safetyMarginTokens,
              remainingTokens: finalMeasurement.remainingTokens,
              fullToolCount: modelTools.length,
              narrowedToolCount: recoveryProfile.tools.length,
              narrowedToolNames: recoveryProfile.tools.map(tool => tool.name),
              recoveryEligibilityReason: recoveryProfile.reason,
              recoveryEpisodeStarted: false,
              candidateExactMeasurement: recoveryCandidate.assembled.measurement.exact,
              candidateInputTokens: recoveryCandidate.assembled.measurement.inputTokens,
              candidateRequestedMaxTokens: recoveryCandidate.desiredMaxTokens,
              candidateAppliedMaxTokens: recoveryCandidate.budget.appliedMaxTokens,
              candidateRemainingTokens: recoveryCandidate.assembled.measurement.remainingTokens,
              candidateFit: true,
              nextToolCount: recoveryProfile.tools.length,
              maxFinalAttempts: 1,
              unconsumedEvidenceProjectedBeforeCandidate: false,
            });
            researchRecoveryEpisodeStarted = true;
            execution.startRecovery("context_budget");
            researchRecoveryToolNames = new Set(recoveryProfile.tools.map(tool => tool.name));
            researchRecoveryOutputReserve = recoveryCandidate.budget.appliedMaxTokens;
            recoveryCoordinator.toolRounds = 0;
            recoveryCoordinator.paginationRounds = 0;
            recoveryCoordinator.noProgressRounds = 0;
            seedResearchRecoveryTracking(workingHistory);
            researchRecoveryReason = "context_budget";
            activeActivity.complete();
            activeActivity = null;
            continue;
          }
        }
        if (finalMeasurement.remainingTokens < 0 && workingContext && !finalizing && !config.observeOnly) {
          const projectionObservation = (request: ToolCallRequest) => {
            const tool = allModelTools.find(candidate => candidate.name === request.name);
            return Boolean(tool && isObservationOnlyToolCall(tool, request));
          };
          // First measure a complete first-consumer batch while retaining every
          // unconsumed Git page that is individually within the existing raw
          // threshold. This is deliberately a candidate: only the measured
          // whole batch is accepted. If it does not fit, the second pass may
          // project those results, but it must use the body-first view above.
          const batchCandidate = workingContext.project(workingHistory, projectionObservation,
            {
              executionId, roundIndex, modelInputId, proofLevel: "returned_tool_result",
              preserveUnconsumedRawMaxChars: FIRST_CONSUMER_RAW_GIT_MAX_CHARS
            },
            config.toolResultProjectionChars);
          if (batchCandidate.changed) {
            workingHistory = batchCandidate.history;
            modelHistory = batchCandidate.history;
            projectionApplied = true;
            assembledInput = await assembleModelInput(workingHistory);
            modelComposition = assembledInput.composition;
            modelInput = assembledInput.history;
            finalMeasurement = assembledInput.measurement;
          }
          const batchCandidateMeasurement = finalMeasurement;
          if (config.showDebugInfo) ctl.debug({
            event: "working_context_first_consumer_batch",
            executionId,
            roundIndex,
            changed: batchCandidate.changed,
            reason: batchCandidate.reason,
            candidateInputTokens: batchCandidateMeasurement.inputTokens,
            candidateRemainingTokens: batchCandidateMeasurement.remainingTokens,
            candidateExact: batchCandidateMeasurement.exact,
            fit: batchCandidateMeasurement.remainingTokens >= 0,
            rawThresholdChars: FIRST_CONSUMER_RAW_GIT_MAX_CHARS,
            archive: workingContext.archive.stats,
          });
          if (batchCandidateMeasurement.remainingTokens < 0) {
            const budgetProjection = workingContext.project(workingHistory, projectionObservation,
              {
                executionId, roundIndex, modelInputId, proofLevel: "returned_tool_result",
                preserveUnconsumedRawMaxChars: 0
              }, config.toolResultProjectionChars);
            if (budgetProjection.changed) {
              workingHistory = budgetProjection.history;
              modelHistory = budgetProjection.history;
              projectionApplied = true;
              assembledInput = await assembleModelInput(workingHistory);
              modelComposition = assembledInput.composition;
              modelInput = assembledInput.history;
              finalMeasurement = assembledInput.measurement;
            }
            if (config.showDebugInfo) ctl.debug({
              event: "working_context_projection",
              executionId,
              roundIndex,
              changed: budgetProjection.changed,
              archiveFailed: budgetProjection.archiveFailed === true,
              reason: "aggregate_prompt_budget_projection",
              finalInputTokens: finalMeasurement.inputTokens,
              finalRemainingTokens: finalMeasurement.remainingTokens,
              archive: workingContext.archive.stats,
            });
          }
        }
        if (finalMeasurement.remainingTokens < 0 && !config.observeOnly) {
          const emergency = buildCompactedHistory(workingHistory, 0, config, {
            maxCheckpointChars: config.maxCheckpointChars - modelComposition.overhead,
            maxCurrentTurnMessages: 2,
          });
          if (emergency.history !== workingHistory) {
            workingHistory = emergency.history;
            modelHistory = emergency.history;
            compacted = true;
            compactionCheckpoint = emergency.checkpoint;
            compactionRetention = { mode: "final_budget_emergency", maxCurrentTurnMessages: 2 };
            compactionAppliedCount += 1;
            compactionAppliedModes.push("final_budget_emergency");
            assembledInput = await assembleModelInput(workingHistory);
            modelComposition = assembledInput.composition;
            modelInput = assembledInput.history;
            finalMeasurement = assembledInput.measurement;
          }
        }

        if (finalizing && !config.observeOnly) {
          const resolvedBudget = resolveGenerationBudget({
            desiredMaxTokens: roundOutputReserve,
            contextLength: finalMeasurement.contextLength,
            inputTokens: finalMeasurement.inputTokens,
            safetyMarginTokens: config.safetyMarginTokens,
            minimumTokens: MIN_FINAL_OUTPUT_TOKENS,
          });
          if (resolvedBudget.fit && resolvedBudget.appliedMaxTokens < roundOutputReserve) {
            roundOutputReserve = resolvedBudget.appliedMaxTokens;
            outputCapSource = "headroom_clamp";
            finalMeasurement = await measureRoundInput(modelInput);
          }
        }

        if (finalMeasurement.remainingTokens < 0 && !config.observeOnly) {
          if (!finalizing) {
            const recoveryProfile = readOnlyRecoveryProfile(workingHistory, modelTools);
            const recoveryCandidate = !researchRecoveryEpisodeStarted && recoveryProfile.eligible
              ? await assembleRecoveryCandidate(
                workingHistory,
                recoveryProfile.tools,
                READ_ONLY_CONTEXT_RECOVERY_INSTRUCTION,
                `${executionId}:research-recovery-candidate-1`,
              ) : null;
            if (config.showDebugInfo) ctl.debug({
              event: "direct_context_budget_rescue",
              executionId,
              modelInputId,
              roundIndex,
              trigger: "context_budget",
              compactionAppliedCount,
              compactionAppliedModes,
              exactMeasurement: finalMeasurement.exact,
              contextLength: finalMeasurement.contextLength,
              inputTokens: finalMeasurement.inputTokens,
              requestedOutputReserve: roundOutputReserve,
              safetyMargin: config.safetyMarginTokens,
              remainingTokens: finalMeasurement.remainingTokens,
              fullToolCount: modelTools.length,
              narrowedToolCount: recoveryProfile.tools.length,
              narrowedToolNames: recoveryProfile.tools.map(tool => tool.name),
              recoveryEligibilityReason: recoveryProfile.reason,
              recoveryEpisodeStarted: researchRecoveryEpisodeStarted,
              candidateExactMeasurement: recoveryCandidate?.assembled.measurement.exact ?? null,
              candidateInputTokens: recoveryCandidate?.assembled.measurement.inputTokens ?? null,
              candidateRequestedMaxTokens: recoveryCandidate?.desiredMaxTokens ?? null,
              candidateAppliedMaxTokens: recoveryCandidate?.budget.appliedMaxTokens ?? null,
              candidateRemainingTokens: recoveryCandidate?.assembled.measurement.remainingTokens ?? null,
              candidateFit: recoveryCandidate?.fit ?? false,
              nextToolCount: recoveryCandidate?.fit ? recoveryProfile.tools.length : 0,
              maxFinalAttempts: 1,
            });
            if (recoveryCandidate?.fit) {
              researchRecoveryEpisodeStarted = true;
              execution.startRecovery("context_budget");
              researchRecoveryToolNames = new Set(recoveryProfile.tools.map(tool => tool.name));
              researchRecoveryOutputReserve = recoveryCandidate.budget.appliedMaxTokens;
              recoveryCoordinator.toolRounds = 0;
              recoveryCoordinator.paginationRounds = 0;
              recoveryCoordinator.noProgressRounds = 0;
              seedResearchRecoveryTracking(workingHistory);
              researchRecoveryReason = "context_budget";
              activeActivity.complete();
              activeActivity = null;
              continue;
            }
            activeActivity.complete();
            activeActivity = null;
            execution.finishResearch("context_budget");
            continue;
          }
          if (config.showDebugInfo) ctl.debug({
            event: "direct_context_budget_rejected",
            executionId,
            modelInputId,
            roundIndex,
            compactionAppliedCount,
            compactionAppliedModes,
            exactMeasurement: finalMeasurement.exact,
            fit: false,
            contextLength: finalMeasurement.contextLength,
            inputTokens: finalMeasurement.inputTokens,
            outputReserve: roundOutputReserve,
            safetyMargin: config.safetyMarginTokens,
            remainingTokens: finalMeasurement.remainingTokens,
            availableOutputTokens: Math.trunc(
              finalMeasurement.contextLength - finalMeasurement.inputTokens - config.safetyMarginTokens,
            ),
            minimumFinalOutputTokens: MIN_FINAL_OUTPUT_TOKENS,
            finalizationTrigger: execution.finalizationTrigger,
            modelCallSkipped: true,
          });
          ctl.createStatus({
            status: "error",
            text: "도구 없는 최종 보고 입력에도 최소 출력 공간이 남지 않아 모델 호출을 중단했습니다.",
          });
          throw new Error(`CONTEXT_BUDGET_EXCEEDED: final model input exceeds the context budget by ${-finalMeasurement.remainingTokens} tokens`);
        }

        const lowWater = await contextManager.enforceLowWater(workingHistory, assembleModelInput, {
          force: compacted || projectionApplied,
          hasReadTools: roundTools.some(hasReadCapability),
          minimumReadTokens: minimumReadResultTokens(roundTools.filter(hasReadCapability)),
        });
        if (lowWater.changed) {
          workingHistory = lowWater.history;
          modelHistory = workingHistory;
          assembledInput = lowWater.assembled;
          modelComposition = assembledInput.composition;
          modelInput = assembledInput.history;
          finalMeasurement = assembledInput.measurement;
          compactionCheckpoint = lowWater.checkpoint;
          compacted = lowWater.success;
          compactionAppliedCount += 1;
          compactionAppliedModes.push("final_exact_low_water");
        }
        const mandatoryFloorMeasurement = lowWater.floorMeasurement;
        if (config.contextManagementMode !== "legacy") compacted = compacted && lowWater.success;
        const mandatoryFloorExceedsTarget = Boolean(mandatoryFloorMeasurement?.exact
          && mandatoryFloorMeasurement.inputTokens > config.workingInputTargetTokens);
        if (config.showDebugInfo && lowWater.telemetry) ctl.debug({
          event: "context_low_water", executionId,
          modelInputId, roundIndex, ...lowWater.telemetry
        });
        if (!lowWater.canRun) {
          ctl.debug({ event: "context_execution_blocked", executionId, modelInputId,
            disposition: lowWater.disposition, exact: finalMeasurement.exact });
          if (!finalizing && lowWater.disposition === "blocked_next_action") {
            execution.finishResearch("context_budget");
            continue;
          }
          throw new Error(`CONTEXT_EXECUTION_BLOCKED: ${lowWater.disposition}`);
        }
        // Validate first-consumer evidence before any durable window mutation.
        evidenceManager.captureExposure(modelInput, modelInputId, false);
        const committedWindow = contextManager.commit(workingContext, visibleHistory, workingHistory, finalMeasurement,
          {
model: String((tokenSource as { identifier?: unknown }).identifier || "unknown"), contextLength: finalMeasurement.contextLength,
            tools: roundTools.map(tool => ({ name: tool.name, schema: tool.parametersJsonSchema })), target: config.workingInputTargetTokens
},
          (projectionApplied || compacted || restoredWindowApplied) && lowWater.canRun);
        const workingWindowCommitted = committedWindow.committed;
        const windowCommitSkippedReason = committedWindow.reason;

        const retainedIndexes = compactionCheckpoint?.retainedIndexes || [];
        const retainedIndexLimit = 64;
        const inputEvidence = messageEvidenceTelemetry(modelInput.getMessagesArray());
        const serializedCounts = compactionCheckpoint
          ? serializedCheckpointCounts(compactionCheckpoint.checkpoint)
          : serializedCheckpointCounts("");
        if (config.showDebugInfo) ctl.debug({
          event: "direct_context_measurement",
          executionId,
          modelInputId,
          roundIndex,
          compacted,
          compactionAppliedCount,
          compactionAppliedModes,
          observeOnly: config.observeOnly,
          exactMeasurement: before.exact,
          contextLengthSource: before.contextLengthSource,
          promptMeasurementSource: before.promptMeasurementSource,
          templatedInputChars: before.templatedInputChars,
          messageCount: before.messageCount,
          inputTokens: before.inputTokens,
          remainingTokens: before.remainingTokens,
          toolSchemaChars: before.toolSchemaChars,
          toolSchemaTokens: before.toolSchemaTokens,
          toolSchemaTokenMeasurement: before.toolSchemaTokenMeasurement,
          finalExactMeasurement: finalMeasurement.exact,
          finalContextLengthSource: finalMeasurement.contextLengthSource,
          finalPromptMeasurementSource: finalMeasurement.promptMeasurementSource,
          finalTemplatedInputChars: finalMeasurement.templatedInputChars,
          finalInputTokens: finalMeasurement.inputTokens,
          finalToolSchemaChars: finalMeasurement.toolSchemaChars,
          finalToolSchemaTokens: finalMeasurement.toolSchemaTokens,
          finalToolSchemaTokenMeasurement: finalMeasurement.toolSchemaTokenMeasurement,
          toolSchemaFingerprint: finalMeasurement.toolSchemaFingerprint,
          actToolSurfaceFingerprint,
          toolSurfaceMatchesMeasured: finalMeasurement.toolSchemaFingerprint === actToolSurfaceFingerprint,
          templatedInputFingerprint: finalMeasurement.templatedInputFingerprint,
          outputReserve: roundOutputReserve,
          requestedMaxTokens: requestedOutputCap,
          appliedMaxTokens: roundOutputReserve,
          capSource: outputCapSource,
          budgetMode: "explicit",
          finalRemainingTokens: finalMeasurement.remainingTokens,
          finalFit: finalMeasurement.fit,
          finalMessageCount: finalMeasurement.messageCount,
          postCompactionInputTokens: compacted || projectionApplied ? finalMeasurement.inputTokens : null,
          postCompactionRemainingTokens: compacted || projectionApplied ? finalMeasurement.remainingTokens : null,
          runtimeContext: {
            effectiveContextTokens: "unknown",
            cachedPrefixTokens: "unknown",
            source: "sdk_stats_unavailable",
          },
          workingContext: workingContext ? {
            mode: config.contextManagementMode,
            inputTargetTokens: config.workingInputTargetTokens,
            inputTriggerTokens: config.workingInputTriggerTokens,
            targetMet: finalMeasurement.exact
              ? finalMeasurement.inputTokens <= config.workingInputTargetTokens : null,
            mandatoryFloorTokens: mandatoryFloorMeasurement?.exact
              ? mandatoryFloorMeasurement.inputTokens : null,
            mandatoryFloorExceedsTarget,
            projectionApplied,
            restoredWindowApplied,
            windowCommitted: workingWindowCommitted,
            windowCommitSkippedReason,
            archive: workingContext.archive.stats,
            semanticCost: workingContext.cost,
            exact: finalMeasurement.exact,
          } : { mode: "legacy" },
          assistantNote: assistantNoteTelemetry(activeNote, noteEnabled),
          projectEngine: scope.engine,
          projectScopeSource: scope.source,
          projectIdentity: scope.projectIdentity || undefined,
          availableUnityTools: scope.availableUnityTools,
          availableUnrealTools: scope.availableUnrealTools,
          visibleToolCount: roundTools.length,
          visibleToolNames: roundTools.map(tool => tool.name),
          fullToolCount: modelTools.length,
          recoveryProfileActive: researchRecoveryRound,
          recoveryReason: researchRecoveryReason || undefined,
          recoveryAttempts: toolPlanningRetryAttempts,
          recoveryToolRounds: recoveryCoordinator.toolRounds,
          finalizationPending: execution.finalizing,
          finalizationTrigger: execution.finalizationTrigger,
          finalizationAttempts: deliveryController.attempts,
          auditCompletionPhase: finalizing
            ? execution.finalizationTrigger === "output_recovery" ? "output_recovery" : "finalize_once"
            : boundedAudit ? "research" : "off",
          auditFinalizationTrigger: finalizing ? execution.finalizationTrigger : undefined,
          attachmentCount: attachmentContext.attachmentCount,
          inputAvailability: assembledInput.projection ? {
            mode: config.inputAvailabilityMode,
            verificationBoundary: assembledInput.projection.verificationBoundary,
            hostInputVerification: assembledInput.projection.hostInputVerification,
            entries: assembledInput.projection.entries,
            omittedEntryCount: assembledInput.projection.omittedEntryCount,
            entryListComplete: assembledInput.projection.entryListComplete,
            metrics: assembledInput.projection.metrics,
            metadataChars: assembledInput.metadataChars,
            metadataTokens: assembledInput.metadataTokens,
            metadataTokenMeasurement: assembledInput.metadataTokens === null ? "unknown" : "prompt_delta",
          } : { mode: "off" },
          compactionDetails: compacted && compactionCheckpoint ? {
            omittedMessageCount: compactionCheckpoint.omittedMessageCount,
            retainedMessageCount: retainedIndexes.length,
            retainedMessageIndexes: retainedIndexes.slice(-retainedIndexLimit),
            retainedMessageIndexesTruncated: retainedIndexes.length > retainedIndexLimit,
            checkpointChars: compactionCheckpoint.checkpoint.length,
            assistantCheckpointChars: compactionCheckpoint.assistantCheckpoint.length,
            ...serializedCounts,
            serialization: compactionCheckpoint.serializationDiagnostics,
            inputToolResultCount: inputEvidence.results.length,
            inputBatchReservation: inputEvidence.batchReservation,
            inputLatestToolResultFingerprint: inputEvidence.results.at(-1)?.resultFingerprint,
            inputLatestSemanticResultFingerprint: inputEvidence.results.at(-1)?.semanticResultFingerprint,
            retention: compactionRetention,
          } : undefined,
        });
        ctl.guardAbort();

        // SDK call IDs are guaranteed unique only within one .act invocation.
        // Clear correlation state after the prior round's captured messages have
        // been emitted, while keeping guard/finalized registration idempotent.
        emitter.beginRound();
        const stream = new PredictionStreamRenderer(ctl, modelNotes.splitVisibleAnswer, noteEnabled);
        const generationRepetition = new GenerationRepetitionDetector(config.generationRepeatCount);
        let generationRepetitionReported = false;
        const toolGeneration = createToolGenerationTracker(ctl);
        const runtimeToolTrace = new Map<number, Record<string, unknown>>();
        const traceCall = (callId: number, values: Record<string, unknown>) => {
          runtimeToolTrace.set(callId, {
            ...(runtimeToolTrace.get(callId) || {}),
            callKey: `${executionId}:${roundIndex}:sdk-${callId}`,
            causalModelInputId: modelInputId,
            sdkCallId: callId,
            ...values,
          });
        };
        activeActivity.waitingForPrompt();
        const displayedMessages = new Set<ChatMessage>();
        const liveMessages = new Map<ChatMessage, { visibleMessage: ChatMessage; textStreamed: boolean }>();
        const phaseTimeoutSignal = finalizing
          ? execution.finalizationTrigger === "context_budget" && !boundedAudit
            ? null
            : AbortSignal.timeout(Math.max(1, (execution.finalizationTrigger === "output_recovery"
              ? config.outputRecoverySeconds : config.auditFinalSeconds) * 1000))
          : boundedAudit
            ? AbortSignal.timeout(Math.max(1, researchDeadlineAt - Date.now()))
            : null;
        const roundSignal = phaseTimeoutSignal
          ? AbortSignal.any([ctl.abortSignal, phaseTimeoutSignal])
          : ctl.abortSignal;
        const historyBeforeRound = Chat.from(workingHistory);
        const batchReservation = config.contextManagementMode === "legacy" || config.observeOnly
          ? null : budgetBroker.beginBatch(finalMeasurement, roundTools.some(hasReadCapability));
        const reservationIds = new Map<string, string>();
        const roundModelStartedAt = Date.now();
        const captured = await runOneToolRound(
          tokenSource,
          modelInput,
          roundTools,
          roundSignal,
          {
            onPromptProcessingProgress: activeActivity.progress,
            onFirstToken: activeActivity.firstToken,
            onPredictionFragment: (fragment) => {
              activeActivity?.firstToken();
              stream.onFragment(fragment);
            },
            abortAfterPredictionFragment: config.observeOnly || config.generationRepetitionAction === "off"
              ? undefined
              : (fragment) => {
                if (!generationRepetition.observe(fragment) || generationRepetitionReported) return undefined;
                generationRepetitionReported = true;
                const pause = config.generationRepetitionAction === "pause";
                ctl.createStatus({
                  status: pause ? "canceled" : "done",
                  text: pause
                    ? "생성 중 반복 블록을 감지해 이 응답을 일시 중지했습니다."
                    : "생성 중 반복 블록을 감지했습니다. 응답은 계속 진행합니다.",
                });
                if (config.showDebugInfo) ctl.debug({
                  event: "within_generation_repetition",
                  roundIndex,
                  action: config.generationRepetitionAction,
                  repeatCount: config.generationRepeatCount,
                });
                if (!pause) return undefined;
                const reason = new Error("Generation paused after repeated text blocks");
                reason.name = "ContextCompactorGenerationRepetition";
                return reason;
              },
            onToolCallRequestStart: (modelRoundIndex, callId) => {
              activeActivity?.firstToken();
              toolGeneration.start(modelRoundIndex, callId);
              traceCall(callId, { sdkPredictionRound: modelRoundIndex, generationState: "started" });
            },
            onToolCallRequestNameReceived: (modelRoundIndex, callId, name) => {
              toolGeneration.name(modelRoundIndex, callId, name);
              traceCall(callId, { toolName: name });
            },
            onToolCallRequestArgumentFragmentGenerated: (modelRoundIndex, callId, content) => {
              toolGeneration.argument(modelRoundIndex, callId, content);
              const prior = runtimeToolTrace.get(callId);
              traceCall(callId, { argumentChars: Number(prior?.argumentChars || 0) + content.length });
            },
            onToolCallRequestEnd: (modelRoundIndex, callId) => {
              toolGeneration.end(modelRoundIndex, callId);
              traceCall(callId, { generationState: "generated" });
            },
            onToolCallRequestFailure: (modelRoundIndex, callId) => {
              toolGeneration.failure(modelRoundIndex, callId);
              traceCall(callId, { generationState: "failed", executionState: "not_executed" });
            },
            onToolCallRequestFinalized: (_roundIndex, callId, info) => {
              emitter.registerRequest(callId, info.toolCallRequest);
              emitter.emitRequest(callId, info.toolCallRequest);
              toolGeneration.finalized(callId);
              traceCall(callId, {
                generationState: "finalized",
                providerRequestId: info.toolCallRequest.id || undefined,
                toolName: info.toolCallRequest.name,
                proposedArgumentsFingerprint: telemetryFingerprint(info.toolCallRequest.arguments || {}),
              });
            },
            onMessageCaptured: (message) => {
              activeActivity?.firstToken();
              for (const result of message.getToolCallResults()) {
                const id = reservationIds.get(String(result.toolCallId));
                if (id) batchReservation?.receive(id, Buffer.byteLength(typeof result.content === "string"
                  ? result.content : JSON.stringify(result.content)));
              }
              let visibleMessage = message;
              let textStreamed = false;
              if (message.isAssistantMessage() && message.getText()) {
                const streamed = stream.consumeAssistant(message.getText());
                const fallback = visibleAssistantOutput(message.getText());
                const visibleText = streamed.streamed ? streamed.visibleText : fallback.visibleText;
                textStreamed = streamed.streamed;
                if (visibleText !== message.getText()) {
                  visibleMessage = ChatMessage.from(message);
                  visibleMessage.replaceText(visibleText);
                }
              }
              liveMessages.set(message, { visibleMessage, textStreamed });
              emitter.emit(visibleMessage, textStreamed);
              displayedMessages.add(message);
            },
            guardToolCall: createToolGuard({
ctl, emitter, roundTools, config, scope, toolPlanningRetryRound,
              toolPlanningRetryBlockedFingerprints, batchReservation, reservationIds, toolGeneration, traceCall
}),
          },
          { maxTokens: roundOutputReserve },
        );
        const roundModelElapsedMs = Date.now() - roundModelStartedAt;
        evidenceManager.captureReturned(captured.messages, executionId);
        for (const result of captured.messages.flatMap(message => message.getToolCallResults())) {
          const id = reservationIds.get(String(result.toolCallId));
          if (id) batchReservation?.settle(id, "returned");
        }
        batchReservation?.close();
        if (config.showDebugInfo && batchReservation) ctl.debug({
          event: "batch_budget", modelInputId,
          ...batchReservation.snapshot(), actualResultMetric: "utf8_bytes", actualResultBytes: messageEvidenceTelemetry(captured.messages).batchReservation.actualResultBytes
        });
        executionCost.modelCalls += 1;
        executionCost.elapsedMs += roundModelElapsedMs;
        if (captured.timing.promptProcessingMs === null) executionCost.unknownPromptProcessingCalls += 1;
        else {
          executionCost.promptProcessingMs += captured.timing.promptProcessingMs;
          executionCost.generationElapsedMs += Math.max(0, roundModelElapsedMs - captured.timing.promptProcessingMs);
        }
        const promptTokens = Number(captured.predictionStats?.promptTokensCount);
        const predictedTokens = Number(captured.predictionStats?.predictedTokensCount);
        if (Number.isFinite(promptTokens)) executionCost.promptTokens += promptTokens;
        if (Number.isFinite(predictedTokens)) executionCost.predictedTokens += predictedTokens;
        if (!Number.isFinite(promptTokens) || !Number.isFinite(predictedTokens)) {
          executionCost.unknownUsageCalls += 1;
        }
        if (captured.predictionCompleted && captured.failure === undefined) {
          evidenceManager.captureExposure(modelInput, modelInputId, true);
        }
        activeActivity.complete();
        activeActivity = null;
        const transaction = new RoundTransaction(captured, roundSignal.aborted);
        let noteLifecycle = activeNote ? "injected_existing" : "absent";
        for (const message of captured.messages) {
          const live = liveMessages.get(message);
          let visibleMessage = live?.visibleMessage || message;
          let historyMessage = message;
          let textAlreadyStreamed = live?.textStreamed || false;
          if (message.isAssistantMessage() && message.getText()) {
            const extracted = modelNotes.splitVisibleAnswer(message.getText());
            historyMessage = modelHistoryMessage(message);
            if (hasUnexecutedRawToolSyntax(visibleAssistantOutput(historyMessage.getText()).visibleText)) {
              const sanitized = ChatMessage.from(historyMessage);
              sanitized.replaceText("[Unexecuted raw tool-call text omitted from subsequent model input; no tool was dispatched.]");
              historyMessage = sanitized;
            }
            if (!live) {
              const streamed = stream.consumeAssistant(message.getText());
              const fallback = visibleAssistantOutput(message.getText());
              textAlreadyStreamed = streamed.streamed;
              visibleMessage = ChatMessage.from(message);
              visibleMessage.replaceText(streamed.streamed ? streamed.visibleText : fallback.visibleText);
            }
            if (extracted.hasFooter) {
              if (transaction.planningCommitted && noteEnabled && extracted.note) {
                activeNote = modelNotes.attachScope(
                  extracted.note, objectiveFingerprint, visibleHistory.getMessagesArray(),
                  workingContext?.summaryRefs(),
                );
                if (activeNote && activeNote.decisions.length + activeNote.rejectedHypotheses.length
                  + activeNote.openQuestions.length + (activeNote.reviewClaims?.length || 0) === 0) {
                  activeNote = null;
                  noteLifecycle = "rejected_empty";
                } else noteLifecycle = activeNote ? "accepted" : "rejected_invalid";
              } else {
                noteLifecycle = noteEnabled ? "rejected_invalid" : "ignored_disabled";
              }
            }
          }
          const durableMessage = transaction.durable(historyMessage);
          if (durableMessage) workingHistory.append(durableMessage);
          visibleHistory.append(visibleMessage);
          if (!displayedMessages.has(message)) emitter.emit(visibleMessage, textAlreadyStreamed);
        }
        const outputLimitStage = classifyOutputLimitStage(captured, finalizing, toolGeneration);
        if (toolPlanningRetryRound) execution.finishReplan();
        const outputEvidence = messageEvidenceTelemetry(captured.messages);
        const rawIntentText = visibleTextFromMessages(captured.messages);
        const finalRawToolIntent = containsUnresolvedToolIntent(rawIntentText, captured.messages);
        const rawIntent = classifyRawToolIntent(rawIntentText, allModelTools, registeredModelTools);
        const { catalogueCorrectionAllowed, archiveOnlyRecoveryAllowed, retryTools, retryInstruction }
          = planReadRecovery(historyBeforeRound, workingHistory, rawIntent, modelTools);
        const runtimeDispatchCount = [...runtimeToolTrace.values()]
          // Conservative retry barrier: authorization may already have caused
          // execution. This legacy count is a guard proxy, not provider proof.
          .filter(value => value.guardAllowed === true).length;
        const guardAllowedCount = [...runtimeToolTrace.values()]
          .filter(value => value.validationState === "allowed").length;
        const guardDeniedCount = [...runtimeToolTrace.values()]
          .filter(value => String(value.validationState || "").startsWith("denied")
            || value.approvalState === "denied").length;
        const structuredToolRequestCount = outputEvidence.calls.length;
        const actualResultCount = outputEvidence.results.length;
        const toolCallBoundary = classifyToolCallBoundary({
          rawToolIntent: finalRawToolIntent,
          outputLimitStage,
          structuredRequestCount: structuredToolRequestCount,
          sdkStartedCount: captured.predictionUsage.sdkToolRequestStartedCount,
          sdkFinalizedCount: captured.predictionUsage.sdkToolRequestFinalizedCount,
          sdkFailureCount: captured.predictionUsage.sdkToolRequestFailureCount,
          guardAllowedCount,
          guardDeniedCount,
          dispatchCount: null,
          resultCount: actualResultCount,
        });
        const recoveryProgress = researchRecoveryRound
          ? evidenceManager.observe(captured.messages) : null;
        const planningAllowed = !(finalizing && execution.finalizationTrigger === "output_recovery");
        const shouldMeasureRetryCandidate = !boundedAudit && !config.observeOnly && planningAllowed
          && toolPlanningRetryAttempts < 1 && captured.failure === undefined
          && !ctl.abortSignal.aborted && phaseTimeoutSignal?.aborted !== true
          && captured.finishReason !== "generation_repetition_paused"
          && finalRawToolIntent && structuredToolRequestCount === 0
          && runtimeDispatchCount === 0 && actualResultCount === 0
          && rawIntent.registeredButWithheldNames.length === 0
          && rawIntent.registeredUnsafeNames.length === 0
          && rawIntent.unsafeUnknownNames.length === 0
          && (rawIntent.unknownNames.length === 0 || catalogueCorrectionAllowed)
          && retryTools.length > 0;
        const retryCandidate = shouldMeasureRetryCandidate
          ? await assembleRecoveryCandidate(
            historyBeforeRound,
            retryTools,
            retryInstruction,
            `${executionId}:tool-planning-retry-candidate-${toolPlanningRetryAttempts + 1}`,
          ) : null;
        const retryDecision = freshToolPlanningRetryDecision({
          boundedAudit,
          observeOnly: config.observeOnly,
          planningAllowed,
          attempts: toolPlanningRetryAttempts,
          failure: captured.failure,
          aborted: ctl.abortSignal.aborted,
          phaseTimedOut: phaseTimeoutSignal?.aborted === true,
          finishReason: captured.finishReason,
          finalRawToolIntent,
          candidateFit: retryCandidate?.fit === true,
          candidateToolCount: retryTools.length,
          candidateExact: retryCandidate?.assembled.measurement.exact === true,
          unknownNameCount: rawIntent.unknownNames.length,
          registeredButWithheldCount: rawIntent.registeredButWithheldNames.length,
          registeredUnsafeCount: rawIntent.registeredUnsafeNames.length,
          unsafeUnknownCount: rawIntent.unsafeUnknownNames.length,
          catalogueCorrectionAllowed,
          structuredToolRequestCount,
          runtimeDispatchCount,
          actualResultCount,
        });
        const freshToolPlanningRetryEligible = retryDecision.eligible;
        const scheduleFreshToolPlanningRetry = () => {
          workingHistory = historyBeforeRound;
          toolPlanningRetryAttempts += 1;
          execution.startReplan();
          toolPlanningRetryInstruction = retryInstruction;
          toolPlanningRetryBlockedFingerprints = completedRequestFingerprints(modelInput);
          researchRecoveryEpisodeStarted = true;
          researchRecoveryToolNames = new Set(retryTools.map(tool => tool.name));
          researchRecoveryOutputReserve = retryCandidate?.budget.appliedMaxTokens
            || Math.min(config.maxOutputReserve, RESEARCH_RECOVERY_MAX_TOKENS);
          recoveryCoordinator.toolRounds = 0;
          recoveryCoordinator.paginationRounds = 0;
          recoveryCoordinator.noProgressRounds = 0;
          seedResearchRecoveryTracking(historyBeforeRound);
          researchRecoveryReason = archiveOnlyRecoveryAllowed
            ? "archive_only_catalogue"
            : rawIntent.unknownNames.length > 0 ? "catalogue_correction" : "raw_tool_intent";
          if (config.showDebugInfo) ctl.debug({
            event: "fresh_tool_planning_retry_scheduled",
            executionId,
            sourceModelInputId: modelInputId,
            retryModelInputId: `${executionId}:tool-planning-retry-${toolPlanningRetryAttempts}`,
            attempt: toolPlanningRetryAttempts,
            maxAttempts: 1,
            finishReason: captured.finishReason || "unknown",
            outputLimitStage,
            requestedMaxTokens: requestedOutputCap,
            appliedMaxTokens: roundOutputReserve,
            contextLength: finalMeasurement.contextLength,
            inputTokens: finalMeasurement.inputTokens,
            remainingTokens: finalMeasurement.remainingTokens,
            retryCandidateExactMeasurement: retryCandidate?.assembled.measurement.exact ?? null,
            retryCandidateInputTokens: retryCandidate?.assembled.measurement.inputTokens ?? null,
            retryCandidateRequestedMaxTokens: retryCandidate?.desiredMaxTokens ?? null,
            retryCandidateAppliedMaxTokens: retryCandidate?.budget.appliedMaxTokens ?? null,
            retryCandidateRemainingTokens: retryCandidate?.assembled.measurement.remainingTokens ?? null,
            retryCandidateFit: retryCandidate?.fit ?? false,
            finalizationTrigger: "raw_tool_intent",
            structuredToolRequestCount,
            eligibilityReason: retryDecision.reason,
            streamRawToolIntentCandidate: captured.predictionUsage.rawToolIntentCandidate,
            finalRawToolIntent,
            rawToolIntentNames: rawIntent.names,
            knownReadOnlyRawToolNames: rawIntent.knownReadOnlyNames,
            archiveOnlyRecoveryAllowed,
            unknownRawToolNames: rawIntent.unknownNames,
            registeredButWithheldRawToolNames: rawIntent.registeredButWithheldNames,
            registeredUnsafeRawToolNames: rawIntent.registeredUnsafeNames,
            recoveryToolNames: retryTools.map(tool => tool.name),
            actualDispatchCount: null,
            guardAllowedCount,
            actualResultCount,
          });
        };
        if (config.showDebugInfo) {
          const normalizedCaptured = normalizeMessages(captured.messages);
          ctl.debug({
            event: "direct_round_observation",
            executionId,
            modelInputId,
            roundIndex,
            finishReason: captured.finishReason || (captured.failure === undefined ? "unknown" : "failed"),
            predictionStats: captured.predictionStats,
            predictionUsage: captured.predictionUsage,
            callPurpose: finalizing
              ? execution.finalizationTrigger === "output_recovery" ? "output_recovery" : "final"
              : toolPlanningRetryRound ? "fresh_planning_retry"
                : researchRecoveryRound ? "read_only_research_recovery" : "research",
            roundModelElapsedMs,
            modelPrefillMs: captured.timing.promptProcessingMs,
            modelGenerationElapsedMs: captured.timing.promptProcessingMs === null
              ? null : Math.max(0, roundModelElapsedMs - captured.timing.promptProcessingMs),
            executionCost: {
              ...executionCost,
              summary: workingContext?.cost || null,
              executionElapsedMs: Date.now() - researchStartedAt,
              modelCallsIncludingSummary: executionCost.modelCalls
                + Number(workingContext?.cost.summaryCalls || 0),
              unknownUsageCallsIncludingSummary: executionCost.unknownUsageCalls
                + Number(workingContext?.cost.unknownUsageCalls || 0),
              knownPromptTokensIncludingSummary: executionCost.promptTokens
                + Number(workingContext?.cost.summaryPromptTokens || 0),
              knownPredictedTokensIncludingSummary: executionCost.predictedTokens
                + Number(workingContext?.cost.summaryPredictedTokens || 0),
              elapsedMsIncludingSummary: executionCost.elapsedMs
                + Number(workingContext?.cost.summaryMs || 0),
            },
            outputLimitStage,
            requestedMaxTokens: requestedOutputCap,
            appliedMaxTokens: roundOutputReserve,
            capSource: outputCapSource,
            contextLength: finalMeasurement.contextLength,
            inputTokens: finalMeasurement.inputTokens,
            remainingTokens: finalMeasurement.remainingTokens,
            toolSchemaChars: finalMeasurement.toolSchemaChars,
            toolSchemaTokens: finalMeasurement.toolSchemaTokens,
            toolSchemaTokenMeasurement: finalMeasurement.toolSchemaTokenMeasurement,
            toolSchemaFingerprint: finalMeasurement.toolSchemaFingerprint,
            actToolSurfaceFingerprint,
            toolSurfaceMatchesMeasured: finalMeasurement.toolSchemaFingerprint === actToolSurfaceFingerprint,
            templatedInputFingerprint: finalMeasurement.templatedInputFingerprint,
            finalizationTrigger: finalizing ? execution.finalizationTrigger : undefined,
            finalizationPending: execution.finalizing,
            finalizationAttempts: deliveryController.attempts,
            recoveryEpisodeStarted: researchRecoveryEpisodeStarted,
            recoveryProfileActive: execution.recovering,
            recoveryReason: researchRecoveryReason || undefined,
            recoveryAttempts: toolPlanningRetryAttempts,
            recoveryToolRounds: recoveryCoordinator.toolRounds,
            recoveryPaginationRounds: recoveryCoordinator.paginationRounds,
            recoveryNoProgressRounds: recoveryCoordinator.noProgressRounds,
            recoveryProgress,
            fullToolCount: modelTools.length,
            exposedToolNames: roundTools.map(tool => tool.name),
            structuredToolRequestCount,
            sdkToolEvents: {
              started: captured.predictionUsage.sdkToolRequestStartedCount,
              named: captured.predictionUsage.sdkToolRequestNamedCount,
              ended: captured.predictionUsage.sdkToolRequestEndedCount,
              finalized: captured.predictionUsage.sdkToolRequestFinalizedCount,
              failed: captured.predictionUsage.sdkToolRequestFailureCount,
            },
            guardAllowedCount,
            guardDeniedCount,
            toolCallBoundary,
            rawToolIntentCandidate: captured.predictionUsage.rawToolIntentCandidate,
            finalRawToolIntent,
            rawToolIntentFirstFragment: captured.predictionUsage.rawToolIntentFirstFragment,
            rawToolIntentFirstVisibleChar: captured.predictionUsage.rawToolIntentFirstVisibleChar,
            rawToolIntentNames: rawIntent.names,
            unknownRawToolNames: rawIntent.unknownNames,
            registeredButWithheldRawToolNames: rawIntent.registeredButWithheldNames,
            registeredUnsafeRawToolNames: rawIntent.registeredUnsafeNames,
            retryCandidate: retryCandidate ? {
              exact: retryCandidate.assembled.measurement.exact,
              inputTokens: retryCandidate.assembled.measurement.inputTokens,
              requestedMaxTokens: retryCandidate.desiredMaxTokens,
              appliedMaxTokens: retryCandidate.budget.appliedMaxTokens,
              remainingTokens: retryCandidate.assembled.measurement.remainingTokens,
              fit: retryCandidate.fit,
              toolNames: retryTools.map(tool => tool.name),
            } : null,
            freshToolPlanningRetry: retryDecision,
            actualDispatchCount: null,
            actualResultCount,
            phase: finalizing ? "final_report"
              : researchRecoveryRound ? "read_only_research_recovery" : "research",
            attempt: finalizing ? deliveryController.attempts : roundIndex + 1,
            continueAfterTools: captured.continueAfterTools,
            modelInputToolResultCount: inputEvidence.results.length,
            modelInputLatestResultFingerprint: inputEvidence.results.at(-1)?.resultFingerprint,
            modelInputLatestSemanticResultFingerprint: inputEvidence.results.at(-1)?.semanticResultFingerprint,
            calls: outputEvidence.calls,
            results: outputEvidence.results,
            outputBatchReservation: outputEvidence.batchReservation,
            workingContextExposure: workingContext
              ? [...workingContext.exposure.values()].filter((entry: any) => entry.modelInputId === modelInputId)
              : [],
            toolTrace: {
              runtime: [...runtimeToolTrace.values()],
              captured: inputAvailability.traceToolRound(normalizedCaptured, {
                executionId, modelInputId, roundIndex,
              }),
            },
            modelNoteLifecycle: noteLifecycle,
          });
        }
        if (config.inputAvailabilityMode !== "off") {
          historicalAvailabilityLedger.push(...inputAvailability.currentRawObservations(
            normalizeMessages(captured.messages),
          ));
        }
        if (!config.observeOnly && config.toolStagnationAction !== "off" && captured.continueAfterTools) {
          const stagnation = toolStagnation.observe(captured.messages);
          if (stagnation.count >= config.toolStagnationRounds) {
            const pause = config.toolStagnationAction === "pause";
            ctl.createStatus({
              status: pause ? "canceled" : "done",
              text: pause
                ? `동일한 도구 라운드가 ${stagnation.count}회 반복되어 후속 호출을 일시 중지했습니다.`
                : `동일한 도구 라운드가 ${stagnation.count}회 반복되었습니다. 후속 호출은 계속합니다.`,
            });
            if (config.showDebugInfo) ctl.debug({
              event: "tool_round_stagnation",
              roundIndex,
              action: config.toolStagnationAction,
              repeatCount: stagnation.count,
              semanticFingerprint: stagnation.fingerprint,
            });
            if (pause) captured.continueAfterTools = false;
          }
        }
        if (ctl.abortSignal.aborted) throw ctl.abortSignal.reason || captured.failure;
        const phaseTimedOut = phaseTimeoutSignal?.aborted === true;
        if (finalizing) {
          const evaluated = deliveryController.evaluate(workingHistory, captured, phaseTimedOut,
            execution.finalizationTrigger, recoveryCoordinator.noProgressRounds);
          const finalDelivery = evaluated.delivery;
          const { deliveryState } = finalDelivery;
          const safePartialReport = evaluated.partial;
          if (safePartialReport) {
            const block = ctl.createContentBlock({ roleOverride: "assistant" });
            block.appendText(safePartialReport.text);
            if (config.showDebugInfo) ctl.debug({
              event: "partial_report_delivery",
              executionId,
              modelInputId,
              finalizationTrigger: execution.finalizationTrigger,
              reportDelivered: true,
              taskCompleted: false,
              evidenceCount: safePartialReport.evidenceCount,
              errorCount: safePartialReport.errorCount,
              reason: finalDelivery.rejectionReason || execution.finalizationTrigger || "research_incomplete",
            });
          }
          const reportDelivered = deliveryState === "complete" || Boolean(safePartialReport);
          if (config.showDebugInfo) ctl.debug({
            event: execution.finalizationTrigger === "output_recovery"
              ? "output_recovery"
              : ["research_recovery_complete", "research_recovery_exhausted"].includes(execution.finalizationTrigger || "")
                ? "research_recovery_finalization" : "bounded_audit_finalization",
            executionId,
            modelInputId,
            trigger: execution.finalizationTrigger,
            phase: execution.finalizationTrigger === "output_recovery" ? "output_recovery" : "finalize_once",
            attempt: deliveryController.attempts,
            maxAttempts: boundedAudit ? 1 : toolPlanningRetryAttempts > 0 ? 2 : 1,
            recoveryAttempts: toolPlanningRetryAttempts,
            recoveryToolRounds: recoveryCoordinator.toolRounds,
            deliveryState,
            phaseTimedOut,
            finishReason: phaseTimedOut ? "final_timeout" : captured.finishReason || "failed",
            reportState: finalDelivery.reportState,
            rejectionReason: finalDelivery.rejectionReason,
            completionAccepted: deliveryState === "complete",
            reportDelivered,
            generationCompleted: evaluated.generationCompleted,
            researchTerminated: evaluated.researchTerminated,
            objectiveSatisfied: evaluated.objectiveSatisfied,
            taskCompleted: evaluated.taskCompleted,
            predictionStats: captured.predictionStats,
            predictionUsage: captured.predictionUsage,
            outputLimitStage,
            requestedMaxTokens: requestedOutputCap,
            appliedMaxTokens: roundOutputReserve,
            capSource: outputCapSource,
            toolCount: roundTools.length,
            toolCallBoundary,
            sdkToolEvents: {
              started: captured.predictionUsage.sdkToolRequestStartedCount,
              named: captured.predictionUsage.sdkToolRequestNamedCount,
              ended: captured.predictionUsage.sdkToolRequestEndedCount,
              finalized: captured.predictionUsage.sdkToolRequestFinalizedCount,
              failed: captured.predictionUsage.sdkToolRequestFailureCount,
            },
            guardAllowedCount,
            guardDeniedCount,
            outputBatchReservation: outputEvidence.batchReservation,
            rawToolIntentNames: rawIntent.names,
            unknownRawToolNames: rawIntent.unknownNames,
            retryEligibilityReason: retryDecision.reason,
          });
          if (deliveryState !== "complete" && finalDelivery.reportState === "unresolved_tool_intent"
            && freshToolPlanningRetryEligible) {
            scheduleFreshToolPlanningRetry();
            roundIndex += 1;
            continue;
          }
          if (deliveryState !== "complete") ctl.createStatus({
            status: deliveryState === "no_answer" ? "error" : "canceled",
            text: safePartialReport
              ? "조사 복구가 완료되지 않아 근거가 있는 부분 보고만 전달했습니다. 전체 완료로 처리하지 않았습니다."
              : finalDelivery.reportState === "unresolved_tool_intent"
                ? "최종 단계에서 실행되지 않은 도구 호출 의도만 남아 보고서 완료로 처리하지 않았습니다."
                : deliveryState === "truncated"
                  ? "최종 보고가 출력 한도에 도달해 잘렸습니다. 완료로 처리하지 않았습니다."
                  : deliveryState === "partial"
                    ? "최종 보고가 제한 시간 안에 완료되지 않았습니다. 부분 출력으로 기록했습니다."
                    : "최종 보고가 생성되지 않았습니다. 완료로 처리하지 않았습니다.",
          });
          break;
        }
        if (captured.failure !== undefined && !phaseTimedOut) throw captured.failure;
        if (phaseTimedOut) {
          execution.finishResearch("research_timeout");
          roundIndex += 1;
          continue;
        }
        if (freshToolPlanningRetryEligible) {
          scheduleFreshToolPlanningRetry();
          roundIndex += 1;
          continue;
        }
        if (researchRecoveryRound) {
          const decision = recoveryCoordinator.advance(recoveryProgress, config.auditResearchRounds,
            captured.continueAfterTools, finalRawToolIntent, structuredToolRequestCount,
            actualResultCount, visibleTextFromMessages(captured.messages).trim());
          if (decision.trigger) {
            execution.finishResearch(decision.trigger);
            if (config.showDebugInfo) ctl.debug({
event: decision.noProgress
                ? "read_only_research_recovery_exhausted" : "read_only_research_recovery_completed",
              executionId, modelInputId, recoveryReason: researchRecoveryReason,
              toolRounds: recoveryCoordinator.toolRounds, paginationRounds: recoveryCoordinator.paginationRounds,
              maxPaginationRounds: decision.maxPaginationRounds, noProgressRounds: recoveryCoordinator.noProgressRounds,
              recoveryProgress, nextPhase: "final_report"
});
            roundIndex++; continue;
          }
          if (decision.endRecovery) execution.endRecovery();
          if (decision.continue) { roundIndex++; continue; }
        }
        if (boundedAudit && captured.finishReason === "maxPredictedTokensReached") {
          execution.finishResearch("research_output_limit");
          roundIndex += 1;
          continue;
        }
        if (!boundedAudit && config.outputRecoveryMode === "on"
          && canRecoverOutputLimit(captured, toolGeneration)) {
          // Keep the partial answer visible, but do not feed its cut-off prose
          // back to the model. The recovery request rewrites from the same
          // evidence that was available before the truncated report.
          workingHistory = historyBeforeRound;
          execution.finishResearch("output_recovery");
          if (config.showDebugInfo) ctl.debug({
            event: "output_recovery_scheduled",
            executionId,
            sourceModelInputId: modelInputId,
            recoveryAttempt: 1,
            recoveryModelInputId: `${executionId}:output-recovery-1`,
            outputLimitStage,
            recoveryToolCount: 0,
            partialReportPreservedInGui: true,
          });
          roundIndex += 1;
          continue;
        }
        if (researchRecoveryRound && finalRawToolIntent && !captured.continueAfterTools) {
          execution.finishResearch("research_recovery_exhausted");
          if (config.showDebugInfo) ctl.debug({
            event: "read_only_research_recovery_exhausted",
            executionId,
            modelInputId,
            recoveryReason: researchRecoveryReason,
            recoveryAttempts: toolPlanningRetryAttempts,
            toolRounds: recoveryCoordinator.toolRounds,
            rawToolIntentNames: rawIntent.names,
            nextPhase: "final_report",
          });
          roundIndex += 1;
          continue;
        }
        if (captured.finishReason === "maxPredictedTokensReached" && !captured.continueAfterTools) {
          ctl.createStatus({
            status: "canceled",
            text: outputLimitStage === "tool_arguments"
              ? "도구 호출 인수가 출력 한도에서 끝나 실행하지 않았습니다."
              : "응답이 출력 한도에 도달해 완료로 처리하지 않았습니다.",
          });
        }
        if (toolPlanningRetryRound && captured.predictionUsage.rawToolIntentCandidate
          && !captured.continueAfterTools) {
          ctl.createStatus({
            status: "canceled",
            text: "읽기 전용 도구 계획 재시도에서도 structured 도구 요청이 생성되지 않아 중단했습니다.",
          });
        }
        if (!captured.continueAfterTools) break;
        if (boundedAudit && (
          roundIndex + 1 >= config.auditResearchRounds || Date.now() >= researchDeadlineAt
        )) {
          execution.finishResearch("research_round_limit");
        }
        roundIndex += 1;
      }
      if (activeNote && noteWorkingDirectory) {
        const nextHistoryKey = modelNotes.historyKey(visibleHistory.getMessagesArray(), noteWorkingDirectory);
        const verifiedNote = modelNotes.reconcileStoredNote(
          activeNote, visibleHistory.getMessagesArray(), workingContext?.summaryRefs(),
        );
        const stored = Boolean(nextHistoryKey && verifiedNote && noteStore.write(nextHistoryKey, verifiedNote));
        if (config.showDebugInfo) ctl.debug({
          event: "assistant_note_persistence",
          status: stored ? "stored" : "not_stored",
          reason: stored ? undefined : !nextHistoryKey ? "unstable_history_key"
            : !verifiedNote ? "scope_reconciliation_failed" : "atomic_write_failed",
          itemCount: verifiedNote
            ? verifiedNote.decisions.length + verifiedNote.rejectedHypotheses.length + verifiedNote.openQuestions.length
            : 0,
          reviewClaimCount: verifiedNote?.reviewClaims?.length || 0,
        });
      }
    } finally {
      execution.terminate(ctl.abortSignal.aborted ? "canceled" : "execution_finished");
      if (config.showDebugInfo) ctl.debug({ event: "execution_transitions", phase: execution.phase, transitions: execution.transitions });
      activeActivity?.complete();
      toolSession[Symbol.dispose]();
    }
  };
}

export const handlePredictionLoop = createPredictionLoopHandler();

export const __test = {
  buildCompactedHistory,
  measureContext,
  normalizeHistory,
  readConfig,
  selectedSourceIsThisPlugin,
  resolveToolScope,
  filterToolsForScope,
  bindProjectArguments,
  isObservationOnlyToolCall,
  createRoundActivityTracker,
  telemetryFingerprint,
  messageEvidenceTelemetry,
  serializedCheckpointCounts,
  ToolRoundStagnationDetector,
  GenerationRepetitionDetector,
  resolveGenerationBudget,
  classifyOutputLimitStage,
  canRecoverOutputLimit,
  classifyFinalDelivery,
  containsUnresolvedToolIntent,
  classifyRawToolIntent,
  readOnlyRecoveryProfile,
  completedRequestFingerprints,
  observeRecoveryProgress,
  seedRecoveryProgress,
  freshToolPlanningRetryDecision,
  modelHistoryMessage,
  selectSoftCompaction,
};

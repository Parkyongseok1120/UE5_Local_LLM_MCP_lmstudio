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
import { BudgetBroker, readBudgetFeedback } from "./budget-broker";
import { buildCompactedHistory, ContextManager, measureContext, modelHistoryMessage, normalizeHistory, normalizeMessages, selectSoftCompaction, toolDefinitionSurface } from "./context-manager";
import { resolveGenerationBudget } from "./context-budget";
import { prepareRoundInput, type GuidanceRetention } from "./round-input";
import { core, inputAvailability, modelNotes, workingContextModule, objectiveContinuity, continuityState } from "./context-ports";
import { canRecoverOutputLimit, classifyFinalDelivery, classifyOutputLimitStage, DeliveryController,
  partialSearchScopeNotice, terminalReportNotices, verificationScopeNotice } from "./delivery-controller";
import { completedRequestFingerprints, EvidenceManager, observeRecoveryProgress, seedRecoveryProgress } from "./evidence-manager";
import { assistantNoteTelemetry, messageEvidenceTelemetry, readResultSize, serializedCheckpointCounts, telemetryFingerprint, ToolRoundStagnationDetector } from "./evidence-telemetry";
import { readConfig } from "./execution-config";
import { selectDesignGuidance } from "./design-guidance";
import { selectFollowUpGuidanceIntent } from "./guidance-topic-selection";
import { MIN_FINAL_OUTPUT_TOKENS, RESEARCH_RECOVERY_MAX_TOKENS, type ContinuityNote, type TaskObjective } from "./execution-contracts";
import { MUTATION_NOT_APPLIED_INSTRUCTION, finalizationCause } from "./execution-instructions";
import { classifyObjective } from "./tool-capability-registry";
import { NotePersistence } from "./note-persistence";
import { sealAndRelease } from "./terminal-persistence";
import { PRIOR_TURN_INTERRUPTED_FACT, partialProjectionFact, priorTurnInterrupted, reportNowFact } from "./resume-facts";
import { acceptedValidatePackets, packetQuoteMismatches, quoteCheckFact, returnedSourceLines, validatePackets,
  type QuoteMismatch } from "./quote-anchoring";
import { confirmedProgressStatements, engineClaimFact, gradedSections, packetEngineClaimGaps,
  returnedEngineEvidence, textEngineClaimGaps, unresolvedEngineClaimGaps, unresolvedReportEngineClaimGaps, type EngineClaimGap } from "./engine-claim-anchoring";
import { ReviewEvidenceIndex, ReviewRegister, deliveredReportFact, reportReviewFact, type ReviewRow } from "./review-register";
import { unverifiedWriteApis, writeApiFact } from "./write-api-anchoring";
import { BOUNDED_AUDIT_FINAL_INSTRUCTION, CONTEXT_BUDGET_FINAL_INSTRUCTION, OUTPUT_RECOVERY_FINAL_INSTRUCTION, REPORT_REVIEW_INSTRUCTION, READ_ONLY_BATCH_INSTRUCTION, READ_ONLY_CONTEXT_RECOVERY_INSTRUCTION, REASONING_RECOVERY_INSTRUCTION, REPETITION_RECOVERY_INSTRUCTION, RESEARCH_RECOVERY_FINAL_INSTRUCTION } from "./execution-instructions";
import { ExecutionState, RoundTransaction } from "./execution-state";
import { GenerationRepetitionDetector, PredictionStreamRenderer } from "./prediction-stream";
import { createMessageEmitter, createRoundActivityTracker, createToolGenerationTracker, selectedSourceIsThisPlugin, VisibleHistoryRecorder } from "./prediction-ui";
import { classifyRawToolIntent, containsUnresolvedToolIntent, hasRawToolIntent, hasUnexecutedRawToolSyntax, stripUnexecutedToolProtocol, visibleAssistantOutput, visibleTextFromMessages } from "./raw-tool-intent";
import { freshToolPlanningRetryDecision, recoveryFallbackInstruction, planReadRecovery, readOnlyRecoveryProfile } from "./recovery-coordinator";
import { runOneToolRound } from "./round-loop";
import { decideReasoningRecovery, reasoningRecoveryOutputCap, repetitionFact } from "./reasoning-recovery";
import { runtimeInstallationIdentity } from "./runtime-identity";
import { classifyToolCallBoundary, createToolGuard, readHostToolApprovalPolicy } from "./tool-boundary";
import { hasReadCapability, isObservationOnlyToolCall, localObservationTools, readOnlyReviewRequest, ToolCapabilityRegistry, type RemoteToolLike } from "./tool-capability-registry";
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
  readApprovalPolicy = readHostToolApprovalPolicy,
): PredictionLoopHandler {
  return async (ctl) => {
    const executionId = crypto.randomUUID();
    let terminalOwner = new DeliveryController();
    let objective: TaskObjective | undefined;
    let finalExecution: ExecutionState | null = null;
    let finalModelInputId: string | null = null;
    let executionFailure: unknown;
    let showDebugInfo = true;
    let releaseContext: (() => void) | null = null;
    let releaseSession: (() => void) | null = null;
    const cleanupFailures: string[] = [];
    try {
    ctl.guardAbort();
    const config = readConfig(ctl);
    showDebugInfo = config.showDebugInfo;
    const pulledHistory = await ctl.pullHistory();
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
    // The execution owns this resource from acquisition, including setup
    // failures before the round loop and failures while sealing the window.
    const toolSession = await ctl.startToolUseSession();
    releaseSession = () => toolSession[Symbol.dispose]();
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
    const focusedReferences = !config.observeOnly && config.designGuidanceMode !== "off"
      && config.designGuidanceMaxTokens > 0 && (config.designGuidanceDelivery === "focused" || config.designGuidanceMode === "auto");
    const guidanceMessages = focusedReferences && config.designGuidanceMode === "auto"
      ? normalizeHistory(originalHistory).map((message, index) => ({ ...message, index })) : null;
    const guidanceObjectiveOf = (messages: NonNullable<typeof guidanceMessages>) =>
      objectiveContinuity.guidanceObjective(messages, continuityState.extractPriorContinuityState(messages));
    // The previous request is read through the same objective owner, from the
    // history before the latest user message.
    const latestUserIndex = guidanceMessages ? guidanceMessages.map(m => m.role).lastIndexOf("user") : -1;
    const earlierMessages = guidanceMessages && latestUserIndex > 0 ? guidanceMessages.slice(0, latestUserIndex) : [];
    const guidanceIntent = guidanceMessages ? selectFollowUpGuidanceIntent(guidanceObjectiveOf(guidanceMessages),
      earlierMessages.some(m => m.role === "user") ? guidanceObjectiveOf(earlierMessages) : undefined) : undefined;
    const guidanceDocuments = config.observeOnly || config.designGuidanceMaxTokens <= 0
      || focusedReferences ? [] : selectDesignGuidance(config.designGuidanceMode, scope);
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
    releaseContext = workingContext ? () => {
      if (!workingContext.close()) throw new Error(workingContext.lastRetentionReason || "archive_close_unavailable");
    } : null;
    const localTools = [...attachmentContext.tools, ...(workingContext ? [workingContext.tool()] : [])];
    for (const tool of localTools) localObservationTools.add(tool);
    const registeredModelTools = [...toolSession.tools as Array<ScopedTool>, ...localTools] as Array<RemoteToolLike>;
    const capabilityRegistry = new ToolCapabilityRegistry([...scopedRemoteTools, ...localTools] as Array<RemoteToolLike>);
    const allModelTools = capabilityRegistry.tools;
    const evidenceManager = new EvidenceManager(workingContext, allModelTools);
    const reviewRegister = new ReviewRegister();
    // Returned bodies and searches the register cites as each judgment's evidence.
    const reviewEvidence = new ReviewEvidenceIndex(allModelTools);
    evidenceManager.initializeChangeOrigins(originalHistory);
    // A review-only request withholds the file/project mutation schemas it
    // ruled out; they would be resent every round (2026-10-20 audit: 27.7k
    // schema chars per call).
    const latestUserText = originalHistory.getMessagesArray().filter(message => message.getRole() === "user").at(-1)?.getText() || "";
    objective = classifyObjective(latestUserText);
    const readOnlyReview = !config.observeOnly && readOnlyReviewRequest(latestUserText);
    const modelTools = config.auditCompletionMode === "bounded" ? capabilityRegistry.readProfile()
      : readOnlyReview ? capabilityRegistry.reviewProfile() : allModelTools;
    if (config.showDebugInfo) ctl.debug({
      event: "direct_runtime_identity",
      objectiveKind: objective.kind, mutationState: objective.mutationState,
      executionId,
      ...runtimeInstallationIdentity(),
      modelIdentifier: String((tokenSource as { identifier?: unknown }).identifier || "unknown"),
      toolRegistryFingerprint: telemetryFingerprint(registeredModelTools.map(tool => ({
        name: tool.name,
        pluginIdentifier: tool.pluginIdentifier || null,
        schema: tool.parametersJsonSchema || null,
      }))),
      fullToolCount: modelTools.length,
      toolProfile: config.auditCompletionMode === "bounded" ? "bounded_audit" : readOnlyReview ? "read_only_review" : "full",
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
    const visibleTranscript = new VisibleHistoryRecorder(ctl, originalHistory);
    const emitter = createMessageEmitter(visibleTranscript.controller, modelTools);
    let visibleHistory = visibleTranscript.snapshot();
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
    const historicalReadsRebuilt = workingContext && !config.observeOnly && restoredWindow?.reason !== "restored_remeasure_required"
      ? evidenceManager.rebuildHistoricalReads(originalHistory) : 0;
    evidenceManager.restoreReferences();
    // Version-checked engine guidance needs an engine identity; an earlier turn's
    // observation applies under the same invalidation rules as a live one.
    if (!config.observeOnly && config.designGuidanceMode !== "off" && config.designGuidanceMaxTokens > 0)
      evidenceManager.seedPriorEngineIdentity(originalHistory, scope, executionId);
    const objectiveFingerprint = modelNotes.objectiveFingerprint(originalHistory.getMessagesArray());
    const originalMessages = originalHistory.getMessagesArray();
    const noteScope = { objectiveFingerprint, projectDescriptor: scope.projectIdentity };
    const notes = new NotePersistence(noteStore, workingContext, noteWorkingDirectory, noteScope);
    let activeNote = notes.restore(originalMessages,
      Boolean(cleanAttachmentHistory.getMessagesArray().at(-1)?.hasFiles()));
    const replaceActiveNote = (note: ContinuityNote | null) => {
      activeNote = note;
      notes.mirror(note);
    };
    const restoredWindowApplied = restoredWindow?.reason === "restored_remeasure_required";
    let workingHistory = restoredWindow?.history || originalHistory;
    if (workingContext && config.showDebugInfo) ctl.debug({
      event: "working_context_restore",
      executionId,
      mode: config.contextManagementMode,
      status: restoredWindow?.reason || "session_only",
      historicalReadsRebuilt,
      historicalReadsGrantAccess: false,
      ...(restoredWindow?.detail ? { detail: restoredWindow.detail } : {}),
      boundaryStatus: contextBoundary.reason || "unknown",
      durable: Boolean(contextBoundary.scope && workingDirectory),
      conversationScopeVerified: Boolean(contextBoundary.scope),
    });
    let roundIndex = 0;
    const boundedAudit = config.auditCompletionMode === "bounded";
    const researchStartedAt = Date.now();
    const researchDeadlineAt = researchStartedAt + config.auditResearchSeconds * 1000;
    const execution = finalExecution = new ExecutionState();
    const budgetBroker = new BudgetBroker(config);
    const contextManager = new ContextManager(config, budgetBroker, { changeOrigin: evidenceManager.changeOrigin,
      reviewRegisterItems: () => reviewRegister.rows.length ? reviewRegister.lines() : null },
      history => workingContext?.pendingResultIndexes(history) || [],
      (history, maxChars) => workingContext?.pinnedResultIndexes(history, maxChars) || []);
    const deliveryController = terminalOwner = new DeliveryController(allModelTools, objective);
    const recoveryCoordinator = execution.recovery;

    const seedResearchRecoveryTracking = (history: Chat) => evidenceManager.seed(history);
    evidenceManager.seed(workingHistory);
    let semanticSummaryCooldownUntilRound = -1;
    const executionCost = {
      modelCalls: 0, promptTokens: 0, predictedTokens: 0,
      elapsedMs: 0, promptProcessingMs: 0, generationElapsedMs: 0, unknownPromptProcessingCalls: 0,
      unknownUsageCalls: 0
    };
    let activeActivity: ReturnType<typeof createRoundActivityTracker> | null = null;
    const toolStagnation = new ToolRoundStagnationDetector();
    let stagnationWarnings = 0;
    let lastBudgetFeedback = "";
    let reportContinuation = "";
    let reportReviewAttempted = false;
    let reportReviewBlocked = false;
    // The review round ran and returned no supplement.
    let reportReviewEmpty = false;
    let reviewMissing: ReviewRow[] = [];
    // Runtime observations for the model are execution-scoped input data, not
    // assistant history: they are rendered after the stable prompt prefix and
    // are never sealed into the durable window.
    const runtimeFacts = new Map<"budget" | "stagnation" | "repeated_failure" | "prior_turn" | "quote_check"
      | "engine_claim_check" | "write_api_check" | "report_now" | "review_register" | "report_review" | "delivered_report"
      | "repetition_check", string>();
    // The single reasoning retry is for a repeated reasoning block, not an output limit.
    let repetitionRecoveryActive = false;
    // Consecutive tool rounds that returned no new path, version or range,
    // and how many report_now facts this execution has already given.
    let noNewEvidenceRounds = 0;
    let reportNowTriggers = 0;
    // Data blocks sent in this execution, by anchor. Earlier blocks are resent
    // unchanged so each model input extends the previous one; discarded with
    // the execution.
    const shownDataBlocks = new Map<string, readonly string[]>();
    // Code names in the latest validated packet that no returned source shows.
    let packetQuoteGaps: QuoteMismatch[] = [];
    // P0/P1 claims in the latest packet that rest on an engine fact no
    // returned engine source backs.
    let packetEngineGaps: EngineClaimGap[] = [];
    let progressEngineGaps: EngineClaimGap[] = [];
    // Admitted reference text for this execution only; discarded with it, so
    // the next user turn selects again.
    const guidanceRetention: GuidanceRetention = {};
    // Resume view: what the restored input carries only in part, and whether
    // the previous turn stopped before it reported. Derived, never stored.
    const resumeProjection = partialProjectionFact(workingHistory.getMessagesArray());
    const resumedAfterInterruption = priorTurnInterrupted(originalHistory.getMessagesArray());
    if (!config.observeOnly) {
      if (resumedAfterInterruption) runtimeFacts.set("prior_turn", PRIOR_TURN_INTERRUPTED_FACT);
    }
    if (config.showDebugInfo) ctl.debug({ event: "resume_state", executionId,
      priorTurnInterrupted: resumedAfterInterruption, restoredWindow: restoredWindowApplied,
      toolResults: workingHistory.getMessagesArray().reduce((n, m) => n + m.getToolCallResults().length, 0),
      partialProjections: resumeProjection.count });
    const persistTerminalNote = () => {
      const result = notes.persist(activeNote, visibleTranscript.snapshot().getMessagesArray());
      if (result.status !== "skipped" && config.showDebugInfo) ctl.debug({ event: "assistant_note_persistence", ...result,
        terminal: executionFailure === undefined ? "completed" : ctl.abortSignal.aborted ? "canceled" : "failed" });
    };
    const deliverPartial = (reason: string) => {
      deliveryController.stop(reason);
      if (config.observeOnly || ctl.abortSignal.aborted || deliveryController.hasPartialDelivery) return;
      const partial = deliveryController.partialReport(workingHistory, reason);
      visibleTranscript.controller.createContentBlock({ roleOverride: "assistant" }).appendText(partial.text);
      deliveryController.recordPartialDelivery(reason);
      if (config.showDebugInfo) ctl.debug({ event: "partial_report_delivery", executionId,
        modelInputId: finalModelInputId, reason, reportDelivered: true, taskCompleted: false,
        evidenceCount: partial.evidenceCount, errorCount: partial.errorCount });
    };
    try {
      while (true) {
        ctl.guardAbort();
        if (boundedAudit && !execution.finalizing && Date.now() >= researchDeadlineAt) {
          execution.finishResearch("research_time_limit");
          continue;
        }
        const finalizing = execution.finalizing;
        const reviewingReport = execution.finalizationTrigger === "report_review";
        const continuingReport = execution.finalizationTrigger === "output_recovery" || reviewingReport;
        const reasoningRecoveryRound = execution.reasoningRecovery;
        const researchRecoveryRound = !finalizing && execution.recovering;
        const toolPlanningRetryRound = researchRecoveryRound && execution.replanning;
        if (finalizing) {
          const deliveryAttempt = deliveryController.begin(boundedAudit, recoveryCoordinator.planningAttempts);
          const finalizationAttemptLimit = deliveryAttempt.limit;
          if (!deliveryAttempt.allowed) {
            if (config.showDebugInfo) ctl.debug({
              event: "terminal_delivery_exhausted",
              executionId,
              finalizationTrigger: execution.finalizationTrigger,
              finalizationAttempts: deliveryController.attempts,
              finalizationAttemptLimit,
              recoveryAttempts: recoveryCoordinator.planningAttempts,
              recoveryEpisodeStarted: recoveryCoordinator.episodeStarted,
            });
            ctl.createStatus({
              status: "error",
              text: "제한된 조사 복구 이후 최종 보고 기회를 모두 사용해 추가 생성을 중단했습니다."
            });
            deliverPartial("terminal_delivery_exhausted");
            break;
          }
        }
        // One input identity covers projection, measurement, exposure and
        // completion. Recovery episode counters must not mint a second ID.
        const modelInputId = reasoningRecoveryRound ? `${executionId}:reasoning-recovery-${execution.reasoningRecoveryAttempts}` : finalizing
          ? reviewingReport ? `${executionId}:report-review-1` : execution.finalizationTrigger === "output_recovery"
            ? `${executionId}:output-recovery-${deliveryController.attempts}`
            : `${executionId}:final-report-${deliveryController.attempts}`
          : toolPlanningRetryRound
            ? `${executionId}:tool-planning-retry-${recoveryCoordinator.planningAttempts}`
            : researchRecoveryRound
              ? `${executionId}:research-recovery-${roundIndex + 1}`
              : `${executionId}:prediction-${roundIndex + 1}`;
        finalModelInputId = modelInputId;
        deliveryController.beginRound();
        const preProjectionHistory = Chat.from(workingHistory);
        let projectionApplied = false;
        if (workingContext && !finalizing && config.contextManagementMode === "legacy") {
          const projected = evidenceManager.project(workingHistory, {
            executionId, roundIndex, modelInputId,
            proofLevel: "returned_tool_result",
            preserveUnconsumedRaw: true,
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
          ? modelTools.filter(tool => recoveryCoordinator.allowsTool(tool.name))
          : modelTools;
        const actToolSurfaceFingerprint = telemetryFingerprint(toolDefinitionSurface(roundTools));
        const finalLimits = deliveryController.limits(config, boundedAudit, execution.finalizationTrigger);
        // The reasoning retry asks for twice the cap that reasoning exhausted;
        // input preparation clamps it to the measured headroom, never below
        // the configured cap.
        // A final report reasons before it writes, and its input is already
        // settled: it may take up to twice the cap within the measured
        // headroom (conversation 1792916809646 v1, r62: 7586 of 8192 tokens
        // went to reasoning and the report was cut). Bounded audits, output
        // recovery and a context-budget rescue keep their own caps.
        const reportHeadroomCap = finalizing && !boundedAudit
          && !["output_recovery", "context_budget"].includes(execution.finalizationTrigger || "");
        let roundOutputReserve = finalizing
          ? reportHeadroomCap ? reasoningRecoveryOutputCap(finalLimits.maxTokens) : finalLimits.maxTokens
          : researchRecoveryRound ? recoveryCoordinator.outputReserve
            : reasoningRecoveryRound && !repetitionRecoveryActive ? reasoningRecoveryOutputCap(config.maxOutputReserve) : config.maxOutputReserve;
        const requestedOutputCap = roundOutputReserve;
        let outputCapSource: "configured" | "headroom_clamp" = "configured";
        const roundScopeInstructions = finalizing
          ? [...(objective.requiredEffects.has("mutation") && objective.mutationState !== "applied"
            ? [`${MUTATION_NOT_APPLIED_INSTRUCTION} 요청한 변경은 적용되지 않았습니다: ${finalizationCause(execution.finalizationTrigger)}`] : []),
            ...scopeInstructions, reviewingReport ? REPORT_REVIEW_INSTRUCTION : execution.finalizationTrigger === "output_recovery"
            ? OUTPUT_RECOVERY_FINAL_INSTRUCTION
            : execution.finalizationTrigger === "context_budget"
              ? CONTEXT_BUDGET_FINAL_INSTRUCTION
              : ["research_recovery_complete", "research_recovery_exhausted", "research_no_new_evidence", "research_repetition"].includes(execution.finalizationTrigger || "")
                ? RESEARCH_RECOVERY_FINAL_INSTRUCTION : BOUNDED_AUDIT_FINAL_INSTRUCTION]
          : [...scopeInstructions, ...(reasoningRecoveryRound ? [repetitionRecoveryActive ? REPETITION_RECOVERY_INSTRUCTION : REASONING_RECOVERY_INSTRUCTION] : []), ...(researchRecoveryRound
            ? [toolPlanningRetryRound ? recoveryCoordinator.instruction : READ_ONLY_CONTEXT_RECOVERY_INSTRUCTION]
            : [])];
        activeActivity = createRoundActivityTracker(ctl, roundIndex);
        const noteEnabled = Boolean(noteWorkingDirectory && objectiveFingerprint && !config.observeOnly);
        if (noteEnabled && activeNote) {
          replaceActiveNote(modelNotes.reconcileStoredNote(
            activeNote, visibleHistory.getMessagesArray(), workingContext?.verifiedNoteRefs(activeNote),
            noteScope,
          ));
        }
        const postProjectionHistory = Chat.from(workingHistory);
        const prepared = await prepareRoundInput({
          ctl, config, tokenSource, contextManager, evidenceManager, workingContext,
          workingHistory, visibleHistory, preProjectionHistory, postProjectionHistory,
          roundTools, modelTools, roundScopeInstructions, scopeInstructions, roundOutputReserve,
          activeNote, noteEnabled, historicalAvailabilityLedger, semanticSummaryCooldownUntilRound,
          objectiveFingerprint, finalizing, boundedAudit, researchRecoveryEpisodeStarted: recoveryCoordinator.episodeStarted,
          allowReadRecovery: execution.canStartReadRecovery,
          reasoningRecovery: reasoningRecoveryRound,
          // The same headroom clamp bounds a final report's enlarged cap.
          headroomOutputCap: reportHeadroomCap,
          guidanceDocuments,
          guidanceIntent,
          guidanceRetention,
          guidanceScope: config.designGuidanceMode !== "off" ? scope : undefined,
          referenceRecovery: researchRecoveryRound || reasoningRecoveryRound,
          // The projection view is derived from the input being assembled, so
          // the round that compacts a body already lists it.
          getRuntimeFacts: (history: Chat) => {
            const projection = config.observeOnly ? null : partialProjectionFact(history.getMessagesArray()).fact;
            // Delivered vs visible vs verified, per file, once a body has left the input.
            const ledger = config.observeOnly ? null : evidenceManager.readLedgerFact(history, runtimeFacts.has("report_now"));
            return [...(projection ? [projection] : []), ...(ledger ? [ledger] : []), ...runtimeFacts.values()];
          },
          shownDataBlocks,
          finalizationTrigger: execution.finalizationTrigger,
          reportContinuation,
          projectionApplied, executionId, modelInputId, roundIndex,
        });
        ({ activeNote, semanticSummaryCooldownUntilRound } = prepared);
        replaceActiveNote(activeNote);
        if (prepared.kind === "continuation_blocked") {
          reportReviewBlocked = reviewingReport;
          deliveryController.stop("continuation_context_budget");
          ctl.createStatus({ status: "canceled", text: "앞서 작성한 답변을 보존했습니다. 이어쓰기에 필요한 입력 공간이 부족해 자동 생성을 중단했습니다." });
          break;
        }
        if (prepared.kind === "pressure") {
          ctl.guardAbort();
          if (continuingReport) {
            reportReviewBlocked = reviewingReport;
            deliveryController.stop("continuation_context_budget");
            ctl.createStatus({ status: "canceled", text: "앞서 작성한 답변을 보존했습니다. 이어쓰기에 필요한 입력 공간이 부족해 자동 생성을 중단했습니다." });
            break;
          }
          const candidate = prepared.recoveryCandidate;
          const action = execution.resolveInputPressure(Boolean(candidate));
          activeActivity.complete(); activeActivity = null;
          if (action === "recover" && candidate) {
            workingHistory = candidate.history;
            execution.startRecovery("context_budget", { toolNames: candidate.tools.map(tool => tool.name),
              outputReserve: candidate.outputReserve });
            seedResearchRecoveryTracking(workingHistory);
            continue;
          }
          workingHistory = prepared.history;
          if (action === "stop") {
            ctl.createStatus({ status: "canceled", text: "생각 단계 재시도에 필요한 입력이 컨텍스트 예산에 맞지 않아 중단했습니다. 추가 도구는 실행하지 않았습니다." });
            if (config.showDebugInfo) ctl.debug({ event: "reasoning_recovery_blocked", executionId, modelInputId, reason: "context_budget" });
            deliverPartial("reasoning_recovery_context_budget");
            break;
          }
          execution.finishResearch("context_budget");
          continue;
        }
        ({ workingHistory, roundOutputReserve, outputCapSource, projectionApplied } = prepared);
        // Closed judgments carry their evidence: when even the released working
        // set does not fit, this round's results go to the report, not to
        // another restore cycle.
        const workingSetExhausted = prepared.projectionExhausted === true && reviewRegister.rows.some(row => row.bundle);
        const { before, modelHistory, compacted, compactionAppliedCount, compactionAppliedModes,
          compactionCheckpoint, compactionRetention, assembledInput, modelComposition, modelInput,
          finalMeasurement, lowWater, mandatoryFloorMeasurement, mandatoryFloorExceedsTarget,
          assembleRecoveryCandidate, measureOriginalProfile } = prepared;
        // Validate first-consumer evidence before any durable window mutation.
        // Candidate selection already retains pending results; if an input
        // still omits one, release it to the archive rather than fail the turn.
        try { evidenceManager.captureExposure(modelInput, modelInputId, false); }
        catch (error) {
          if (!workingContext || !(error instanceof Error) || error.message !== "CONTEXT_PENDING_EVIDENCE_OMITTED") throw error;
          const released = workingContext.releaseOmittedPending(modelInput);
          if (config.showDebugInfo) ctl.debug({ event: "pending_evidence_released_to_archive", executionId,
            modelInputId, releasedCount: released.length });
          evidenceManager.captureExposure(modelInput, modelInputId, false);
        }
        // This input is final: its new data block, if any, is now part of what was sent.
        if (modelComposition && "dataBlock" in modelComposition && modelComposition.dataBlock) {
          // A consolidated block replaces every earlier one.
          if (modelComposition.dataBlock.replacesShown) shownDataBlocks.clear();
          shownDataBlocks.set(modelComposition.dataBlock.anchor, modelComposition.dataBlock.parts);
        }
        const committedWindow = contextManager.commit(workingContext, visibleHistory, workingHistory, finalMeasurement,
          {
model: String((tokenSource as { identifier?: unknown }).identifier || "unknown"), contextLength: finalMeasurement.contextLength,
            tools: roundTools.map(tool => ({ name: tool.name, schema: tool.parametersJsonSchema })), target: config.workingInputTargetTokens
},
          lowWater.canRun);
        const workingWindowCommitted = committedWindow.committed;
        const windowCommitSkippedReason = committedWindow.reason;

        const retainedIndexes = compactionCheckpoint?.retainedIndexes || [];
        const retainedIndexLimit = 64;
        const inputEvidence = messageEvidenceTelemetry(modelInput.getMessagesArray());
        if (config.showDebugInfo) {
          // What the model input actually carries per tool result (no body text):
          // which results are partial projections, and whether each still has its
          // partial notice and a read handle to page the archived original.
          const projections = inputEvidence.results.filter(result => result.kind === "archived_tool_result_projection");
          ctl.debug({ event: "model_input_evidence_summary", executionId, modelInputId, roundIndex,
            toolResults: inputEvidence.results.length, projections: projections.length,
            partialProjections: projections.filter(result => result.bodyComplete === false).slice(0, 32).map(result => ({
              toolCallId: result.toolCallId, bodyTotalChars: result.bodyTotalChars, bodyOmittedChars: result.bodyOmittedChars,
              noticePresent: result.noticePresent === true,
              readHandlePresent: Boolean((result.archiveRef as { readHandle?: unknown } | undefined)?.readHandle) })) });
        }
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
            runtimeDataChars: modelComposition?.runtimeDataChars ?? 0,
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
          recoveryReason: recoveryCoordinator.reason || undefined,
          recoveryAttempts: recoveryCoordinator.planningAttempts,
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
        const roundController = continuingReport ? visibleTranscript.continuationController() : visibleTranscript.controller;
        const roundEmitter = continuingReport ? createMessageEmitter(roundController, modelTools) : emitter;
        const internalDataParts = modelComposition?.privateDataParts || [];
        const stream = new PredictionStreamRenderer(roundController, modelNotes.splitVisibleAnswer, noteEnabled, continuingReport, internalDataParts);
        const continuationStatus = continuingReport ? ctl.createStatus({ status: "loading", text: "답변 이어 작성 중" }) : null;
        const generationRepetition = new GenerationRepetitionDetector(config.generationRepeatCount);
        let generationRepetitionReported = false;
        // Set when this research round's reasoning repeated and was stopped to recover.
        let repetitionAbort: { block: string } | null = null;
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
          ? finalLimits.timeoutMs === null ? null : AbortSignal.timeout(finalLimits.timeoutMs)
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
            captureUnparsedToolRequest: !config.observeOnly,
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
                // A research round looping in reasoning is stopped and retried
                // (conversation 1792934995823, r7: one 1,330-char block about
                // recalled engine source, 13 times in 162 s). A report or a
                // repeated visible block keeps the warn behavior.
                const recover = config.generationRepetitionAction === "recover" && !finalizing
                  && fragment.reasoningType === "reasoning";
                const pause = config.generationRepetitionAction === "pause" || recover;
                if (recover) repetitionAbort = { block: generationRepetition.repeatedBlock };
                ctl.createStatus({
                  status: pause ? "canceled" : "done",
                  text: recover
                    ? "생각 단계에서 같은 블록이 반복되어 이 생성을 중단했습니다."
                    : pause
                    ? "생성 중 반복 블록을 감지해 이 응답을 일시 중지했습니다."
                    : "생성 중 반복 블록을 감지했습니다. 응답은 계속 진행합니다.",
                });
                if (config.showDebugInfo) ctl.debug({
                  event: "within_generation_repetition",
                  roundIndex,
                  action: recover ? "recover" : config.generationRepetitionAction === "recover" ? "warn" : config.generationRepetitionAction,
                  repeatCount: config.generationRepeatCount,
                  blockChars: generationRepetition.repeatedBlock.length,
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
                toolGeneration.completed(result.toolCallId, result.content);
                const id = reservationIds.get(String(result.toolCallId));
                if (id) traceCall(Number(id), { readResultSize: readResultSize(result.content) });
                if (id) batchReservation?.receive(id, Buffer.byteLength(typeof result.content === "string"
                  ? result.content : JSON.stringify(result.content)));
              }
              let visibleMessage = message;
              let textStreamed = false;
              if (message.isAssistantMessage() && message.getText()) {
                const streamed = stream.consumeAssistant(message.getText());
                const fallback = visibleAssistantOutput(message.getText(), internalDataParts);
                const visibleText = streamed.streamed ? streamed.visibleText : stripUnexecutedToolProtocol(fallback.visibleText);
                textStreamed = streamed.streamed;
                if (visibleText !== message.getText()) {
                  visibleMessage = ChatMessage.from(message);
                  visibleMessage.replaceText(visibleText);
                }
              }
              liveMessages.set(message, { visibleMessage, textStreamed });
              roundEmitter.emit(visibleMessage, textStreamed, true);
              displayedMessages.add(message);
            },
            guardToolCall: createToolGuard({
ctl, emitter, roundTools, config, scope, toolPlanningRetryRound,
              toolPlanningRetryBlockedFingerprints: recoveryCoordinator.blockedFingerprints, batchReservation, reservationIds, toolGeneration, traceCall, readApprovalPolicy,
              inputRepeatReceipts: evidenceManager.repeatReceipts(modelInput),
              knownResultBytes: evidenceManager.knownResultBytes,
}),
          },
          { maxTokens: roundOutputReserve },
        ).then(result => {
          for (const returned of result.messages.flatMap(message => message.getToolCallResults())) {
            const id = reservationIds.get(String(returned.toolCallId));
            if (id) batchReservation?.settle(id, "returned");
          }
          return result;
        }).finally(() => batchReservation?.close());
        // UI termination must not erase the round's unfinished-generation
        // observation used by output classification and recovery eligibility.
        stream.finishAll();
        continuationStatus?.remove();
        // Completion/recovery must assess the same prose the user receives.
        // Preserve captured originals for diagnostics and tool evidence.
        const visibleCaptured = { ...captured,
          messages: captured.messages.map(message => modelHistoryMessage(message, internalDataParts)) };
        deliveryController.observe(visibleCaptured, phaseTimeoutSignal?.aborted === true);
        if (!hasUnexecutedRawToolSyntax(visibleTranscript.lastAnswerText))
          deliveryController.retainReportPrefix(visibleTranscript.lastAnswerText);
        const unfinishedGeneration = toolGeneration.hasUnfinished();
        const generationAtRoundEnd = { hasUnfinished: () => unfinishedGeneration };
        toolGeneration.finishPending();
        const roundModelElapsedMs = Date.now() - roundModelStartedAt;
        // Same-hash re-reads of delivered ranges enter the working history as
        // UNCHANGED facts; the user's transcript keeps every returned body.
        const screening = config.observeOnly
          ? { messages: captured.messages, replaced: 0, redundant: 0, duplicatedChars: 0, repages: 0, newDeliveries: 0,
            readResults: 0, failedReads: 0, otherResults: 0, focusedReads: 0, repeatRestores: 0, pending: [], suppressedCallIds: [] }
          : evidenceManager.screenReturnedReads(captured.messages, roundIndex);
        const historyReplacements = new Map(captured.messages.map((message, index) => [message, screening.messages[index]]));
        evidenceManager.captureReturned(screening.messages, executionId, modelInputId);
        evidenceManager.recordReturnedReads(screening);
        if (config.showDebugInfo && (screening.readResults || screening.replaced)) ctl.debug({ event: "read_ledger", executionId,
          modelInputId, roundIndex, readResults: screening.readResults, newDeliveries: screening.newDeliveries,
          redundantRereads: screening.redundant, duplicatedChars: screening.duplicatedChars, archiveRepages: screening.repages,
          restoredReads: screening.focusedReads, repeatRestores: screening.repeatRestores,
          failedReads: screening.failedReads, bodiesReplaced: screening.replaced, replacedCallIds: screening.suppressedCallIds,
          ledger: evidenceManager.ledger.stats(),
          events: evidenceManager.ledger.events.filter(event => event.round === roundIndex + 1).slice(0, 16) });
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
          let historyMessage = historyReplacements.get(message) || message;
          let textAlreadyStreamed = live?.textStreamed || false;
          if (message.isAssistantMessage() && message.getText()) {
            const extracted = modelNotes.splitVisibleAnswer(message.getText());
            historyMessage = modelHistoryMessage(message, internalDataParts);
            if (hasUnexecutedRawToolSyntax(visibleAssistantOutput(historyMessage.getText()).visibleText)) {
              const sanitized = ChatMessage.from(historyMessage);
              sanitized.replaceText("[Unexecuted raw tool-call text omitted from subsequent model input; no tool was dispatched.]");
              historyMessage = sanitized;
            }
            if (!live) {
              const streamed = stream.consumeAssistant(message.getText());
              const fallback = visibleAssistantOutput(message.getText(), internalDataParts);
              textAlreadyStreamed = streamed.streamed;
              visibleMessage = ChatMessage.from(message);
              visibleMessage.replaceText(streamed.streamed ? streamed.visibleText : stripUnexecutedToolProtocol(fallback.visibleText));
            }
            if (extracted.hasFooter) {
              if (transaction.planningCommitted && noteEnabled && extracted.note) {
                const update = modelNotes.resolveNoteUpdate(
                  extracted.note, objectiveFingerprint, visibleHistory.getMessagesArray(),
                  workingContext?.verifiedNoteRefs(extracted.note),
                );
                if (update.kind === "accepted_update" || update.kind === "explicit_clear") {
                  replaceActiveNote(update.note || null);
                }
                noteLifecycle = update.kind;
              } else {
                // A well-formed note on a round that commits no planning (a
                // final report) is not applied, but it is not invalid either.
                noteLifecycle = !noteEnabled ? "ignored_disabled"
                  : extracted.note && !transaction.planningCommitted ? "ignored_uncommitted_round" : "rejected_invalid";
              }
            }
          }
          const durableMessage = transaction.durable(historyMessage);
          if (durableMessage) workingHistory.append(durableMessage);
          if (!displayedMessages.has(message)) roundEmitter.emit(visibleMessage, textAlreadyStreamed);
        }
        roundEmitter.flushDeferred();
        visibleHistory = visibleTranscript.snapshot();
        const interruption = transaction.interruptionMessage(runtimeToolTrace.values());
        if (interruption) workingHistory.append(interruption);
        const budgetFeedback = readBudgetFeedback([...runtimeToolTrace.values()]);
        if (budgetFeedback && budgetFeedback !== lastBudgetFeedback && !config.observeOnly) {
          runtimeFacts.set("budget", budgetFeedback); lastBudgetFeedback = budgetFeedback;
        }
        evidenceManager.observeRoundReferences(workingHistory, captured.messages, scope, executionId,
          !config.observeOnly && config.designGuidanceMode !== "off" && config.designGuidanceMaxTokens > 0);
        const outputLimitStage = classifyOutputLimitStage(visibleCaptured, finalizing, generationAtRoundEnd);
        if (toolPlanningRetryRound) execution.finishReplan();
        const outputEvidence = messageEvidenceTelemetry(captured.messages);
        const rawIntentText = visibleTextFromMessages(captured.messages, internalDataParts);
        const finalRawToolIntent = hasRawToolIntent(rawIntentText, captured.messages);
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
        const recoveryProgress = evidenceManager.observe(screening.messages, modelInputId, screening);
        for (const result of capabilityRegistry.mutationOutcomes(captured.messages)) {
          if (result === "applied") objective.mutationState = "applied";
          else if (objective.mutationState !== "applied") objective.mutationState = "attempted";
        }
        const planningAllowed = !reasoningRecoveryRound && !(finalizing && execution.finalizationTrigger === "output_recovery");
        const shouldMeasureRetryCandidate = !boundedAudit && !config.observeOnly && planningAllowed
          && recoveryCoordinator.planningAttempts < 1 && captured.failure === undefined
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
            `${executionId}:tool-planning-retry-candidate-${recoveryCoordinator.planningAttempts + 1}`,
          ) : null;
        const retryDecision = freshToolPlanningRetryDecision({
          boundedAudit,
          observeOnly: config.observeOnly,
          planningAllowed,
          attempts: recoveryCoordinator.planningAttempts,
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
        const reasoningRecoveryDecision = decideReasoningRecovery({
          config, phase: execution.phase, attempts: execution.reasoningRecoveryAttempts,
          captured, outputLimitStage, canceled: ctl.abortSignal.aborted,
          timedOut: phaseTimeoutSignal?.aborted === true, toolTraceCount: runtimeToolTrace.size,
        });
        const scheduleFreshToolPlanningRetry = () => {
          workingHistory = retryCandidate?.history || historyBeforeRound;
          execution.startReplan({
            instruction: retryInstruction, blockedFingerprints: completedRequestFingerprints(modelInput),
            toolNames: retryTools.map(tool => tool.name),
            outputReserve: retryCandidate?.budget.appliedMaxTokens
              || Math.min(config.maxOutputReserve, RESEARCH_RECOVERY_MAX_TOKENS),
            reason: archiveOnlyRecoveryAllowed ? "archive_only_catalogue"
              : rawIntent.unknownNames.length > 0 ? "catalogue_correction" : "raw_tool_intent",
          });
          seedResearchRecoveryTracking(workingHistory);
          if (config.showDebugInfo) ctl.debug({
            event: "fresh_tool_planning_retry_scheduled",
            executionId,
            sourceModelInputId: modelInputId,
            retryModelInputId: `${executionId}:tool-planning-retry-${recoveryCoordinator.planningAttempts}`,
            attempt: recoveryCoordinator.planningAttempts,
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
            toolRequestFailure: captured.toolRequestFailure,
            predictionUsage: captured.predictionUsage,
            callPurpose: finalizing
              ? reviewingReport ? "report_review" : execution.finalizationTrigger === "output_recovery" ? "output_recovery" : "final"
              : reasoningRecoveryRound ? "reasoning_recovery" : toolPlanningRetryRound ? "fresh_planning_retry"
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
            recoveryEpisodeStarted: recoveryCoordinator.episodeStarted,
            recoveryProfileActive: execution.recovering,
            recoveryReason: recoveryCoordinator.reason || undefined,
            recoveryAttempts: recoveryCoordinator.planningAttempts,
            reasoningRecoveryAttempts: execution.reasoningRecoveryAttempts,
            reasoningRecoveryDecision,
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
        const packets = config.observeOnly ? [] : validatePackets(captured.messages);
        if (packets.length) {
          // The visible recorder keeps every returned body, including ones the
          // working input has since compacted.
          const returnedMessages = [...visibleTranscript.snapshot().getMessagesArray(), ...captured.messages];
          packetQuoteGaps = packetQuoteMismatches(packets.at(-1), returnedSourceLines(returnedMessages));
          const quoteFact = quoteCheckFact(packetQuoteGaps);
          if (quoteFact) runtimeFacts.set("quote_check", quoteFact);
          else runtimeFacts.delete("quote_check");
          packetEngineGaps = packetEngineClaimGaps(packets.at(-1), returnedEngineEvidence(returnedMessages), scope.engine);
          for (const packet of acceptedValidatePackets(captured.messages)) {
            if (!packetQuoteMismatches(packet, returnedSourceLines(returnedMessages)).length
              && !packetEngineClaimGaps(packet, returnedEngineEvidence(returnedMessages), scope.engine).length)
              evidenceManager.recordChecks(packet, roundIndex, true);
          }
          if (config.showDebugInfo) ctl.debug({ event: "quote_anchoring_check", executionId, roundIndex,
            packets: packets.length, mismatches: packetQuoteGaps.map(({ claimPath, location, kind, missingTokens }) =>
              ({ claimPath, location, kind, missingTokens })) });
        }
        if (!config.observeOnly) {
          reviewEvidence.observe(captured.messages);
          reviewRegister.observe(captured.messages, roundIndex + 1, internalDataParts);
          reviewRegister.attach(reviewEvidence);
          const reviewFact = reviewRegister.fact();
          if (reviewFact) runtimeFacts.set("review_register", reviewFact);
          // Progress text shown this execution can state a finding as confirmed
          // without any packet; it is held to the same engine facts.
          const visibleMessages = visibleTranscript.snapshot().getMessagesArray();
          progressEngineGaps = textEngineClaimGaps([...confirmedProgressStatements(visibleMessages, ""), ...reviewRegister.findings()],
            returnedEngineEvidence([...visibleMessages, ...captured.messages]), scope.engine, "progress");
          const engineGaps = [...packetEngineGaps, ...progressEngineGaps];
          const engineFact = engineClaimFact(engineGaps);
          if (engineFact) runtimeFacts.set("engine_claim_check", engineFact);
          else runtimeFacts.delete("engine_claim_check");
          if (config.showDebugInfo && engineGaps.length) ctl.debug({ event: "engine_claim_check", executionId, roundIndex,
            gaps: engineGaps.map(({ claimPath, severity, factId }) => ({ claimPath, severity, factId })) });
          // Engine names an edit adds are compared with returned source before a build reports only the first error.
          if (scope.engine === "unreal") {
            const unverified = unverifiedWriteApis([...visibleMessages, ...captured.messages]);
            const writeFact = writeApiFact(unverified);
            if (writeFact) runtimeFacts.set("write_api_check", writeFact);
            else runtimeFacts.delete("write_api_check");
            if (config.showDebugInfo && unverified.length) ctl.debug({ event: "write_api_check", executionId, roundIndex,
              names: unverified.slice(0, 8).map(api => api.name) });
          }
        }
        let reportNowFinalize = false;
        if (!config.observeOnly && config.toolStagnationAction !== "off" && captured.continueAfterTools) {
          // Historical novelty and restoring evidence needed in the current
          // input are different. Failed reads establish neither unchangedness nor completion.
          // A round that only restored bodies it had already restored once is
          // re-reading, though the restore regains current availability.
          // Archive pages count too: a page inside spans already restored is a
          // repeat even though it repages a delivered version.
          const noNewEvidence = recoveryProgress.noNewEvidence;
          noNewEvidenceRounds = noNewEvidence ? noNewEvidenceRounds + 1 : 0;
          if (!noNewEvidence) { runtimeFacts.delete("report_now"); reportNowTriggers = 0; }
          if (noNewEvidence && noNewEvidenceRounds >= config.noNewEvidenceRounds) {
            noNewEvidenceRounds = 0;
            reportNowTriggers += 1;
            runtimeFacts.set("report_now", reportNowFact(config.noNewEvidenceRounds, evidenceManager.ledger.stats()));
            // The second trigger ends research per the stagnation policy: one
            // tool-free report from the evidence already returned.
            reportNowFinalize = reportNowTriggers >= 2;
            ctl.createStatus({ status: "done", text: reportNowFinalize
              ? "새 근거 없이 이미 읽은 버전만 반복 조회해 조사를 마치고 최종 보고로 전환합니다."
              : "새 근거 없이 이미 읽은 버전만 반복 조회했습니다. 확인된 근거로 최종 보고를 작성하도록 안내합니다." });
            if (config.showDebugInfo) ctl.debug({ event: "report_now", executionId, modelInputId, roundIndex,
              trigger: reportNowTriggers, rounds: config.noNewEvidenceRounds, finalize: reportNowFinalize,
              ledger: evidenceManager.ledger.stats() });
          }
        }
        if (!config.observeOnly && config.toolStagnationAction !== "off" && captured.continueAfterTools) {
          const stagnation = toolStagnation.observe(screening.messages, recoveryProgress);
          const failure = stagnation.repeatedFailure;
          if (failure?.firstNotice) {
            // Advisory only: no pause, no added permissions. The model learns the
            // identical request already failed, and what would change the outcome.
            runtimeFacts.set("repeated_failure", `Runtime evidence observation: ${failure.toolName} failed with `
              + `${failure.errorCode} ${failure.count} times for identical arguments. Repeating it yields no new information; `
              + "change the path, scope or budget the error names, or report the item as unverified.");
            if (config.showDebugInfo) ctl.debug({ event: "tool_repeated_failure", roundIndex, toolName: failure.toolName,
              errorCode: failure.errorCode, count: failure.count });
          }
          if ((stagnation.count >= config.toolStagnationRounds || stagnation.cycleDetected) && stagnation.firstWarning) {
            toolStagnation.acknowledge(stagnation.warningKey);
            stagnationWarnings += 1;
            // A second distinct stagnation in one execution escalates "warn":
            // repeated re-reading after a warning is a loop, not progress.
            const pause = config.toolStagnationAction === "pause" || stagnationWarnings >= 2;
            ctl.createStatus({
              status: pause ? "canceled" : "done",
              text: pause
                ? "동일 근거의 반복 읽기가 감지되어 후속 호출을 일시 중지했습니다. 작업 완료를 뜻하지 않습니다."
                : "동일 근거의 반복 읽기를 감지했습니다. 새 범위와 필요한 재확인 여부를 확인하며 계속합니다.",
            });
            if (config.showDebugInfo) ctl.debug({
              event: "tool_round_stagnation",
              roundIndex,
              action: config.toolStagnationAction,
              repeatCount: stagnation.count,
              semanticFingerprint: stagnation.fingerprint,
              cycleLength: stagnation.cycleLength, reacquisition: stagnation.reacquisition,
            });
            if (stagnation.cycleDetected) runtimeFacts.set("stagnation",
              `Runtime evidence observation: a ${stagnation.cycleLength}-round read cycle repeated with no new source/archive range. `
              + (stagnation.reacquisition ? "Some ranges restored evidence absent from the input; repeated restoration is not new source coverage. " : "The returned observations repeat previously seen evidence. ")
              + "Use the returned evidence; request only a missing range or a necessary fresh version. This observation does not prove review completion.");
            if (pause) {
              captured.continueAfterTools = false;
              deliveryController.stop("tool_stagnation_paused");
              const block = visibleTranscript.controller.createContentBlock({ roleOverride: "assistant" });
              block.appendText("동일 근거를 반복해서 읽는 상태가 감지되어 설정에 따라 중단했습니다. 반환된 근거는 보존했으며, 요청한 검토는 아직 완료되지 않았습니다.");
              deliveryController.recordPartialDelivery("tool_stagnation_paused");
              break;
            }
          }
        }
        if (ctl.abortSignal.aborted) throw ctl.abortSignal.reason || captured.failure;
        const phaseTimedOut = phaseTimeoutSignal?.aborted === true;
        if (captured.toolRequestFailure) {
          const partial = deliveryController.failedToolRequest(workingHistory, captured.messages,
            captured.finishReason === "maxPredictedTokensReached" ? "tool_arguments_output_limit" : "tool_arguments_parse_failure");
          const block = visibleTranscript.controller.createContentBlock({ roleOverride: "assistant" });
          block.appendText(partial.text);
          deliveryController.recordPartialDelivery("tool_request_generation_failed");
          if (config.showDebugInfo) ctl.debug({ event: "partial_report_delivery", executionId, modelInputId,
            reportDelivered: true, taskCompleted: false, generationCompleted: false,
            evidenceCount: partial.evidenceCount, errorCount: partial.errorCount,
            reason: "tool_request_generation_failed", failedRequestExecuted: false });
          ctl.createStatus({ status: "canceled", text: captured.finishReason === "maxPredictedTokensReached"
            ? "도구 인수 JSON이 생성 한도에서 잘려 해당 요청을 실행하지 않았습니다. 완료된 다른 도구 결과는 보존했으며 잘린 요청은 자동 재실행하지 않습니다."
            : "도구 인수 JSON을 해석할 수 없어 해당 요청을 실행하지 않았습니다. 완료된 다른 도구 결과는 보존했으며 잘못된 요청은 자동 재실행하지 않습니다." });
          if (config.showDebugInfo) ctl.debug({ event: "tool_request_generation_failed", executionId, modelInputId,
            finishReason: captured.finishReason || "unknown", stage: "tool_arguments", failedRequestExecuted: false,
            returnedPeerResults: actualResultCount, rawContentChars: captured.toolRequestFailure.rawContentChars });
          break;
        }
        if (finalizing) {
          const evaluated = deliveryController.evaluate(workingHistory, visibleCaptured, phaseTimedOut,
            execution.finalizationTrigger, recoveryCoordinator.noProgressRounds);
          const finalDelivery = evaluated.delivery;
          const { deliveryState } = finalDelivery;
          // A fresh planning retry replaces this round's text; delivering a thin partial report first
          // would show the user a final-looking answer that the retry then contradicts.
          const retryPending = finalDelivery.deliveryState !== "complete"
            && finalDelivery.reportState === "unresolved_tool_intent" && freshToolPlanningRetryEligible;
          const safePartialReport = retryPending ? undefined : evaluated.partial;
          if (safePartialReport) {
            const block = visibleTranscript.controller.createContentBlock({ roleOverride: "assistant" });
            block.appendText(safePartialReport.text);
            deliveryController.recordPartialDelivery(finalDelivery.rejectionReason || execution.finalizationTrigger || "research_incomplete");
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
            event: reviewingReport ? "report_review" : execution.finalizationTrigger === "output_recovery"
              ? "output_recovery"
              : ["research_recovery_complete", "research_recovery_exhausted", "research_no_new_evidence", "research_repetition"].includes(execution.finalizationTrigger || "")
                ? "research_recovery_finalization" : "bounded_audit_finalization",
            executionId,
            modelInputId,
            trigger: execution.finalizationTrigger,
            phase: reviewingReport ? "report_review" : execution.finalizationTrigger === "output_recovery" ? "output_recovery" : "finalize_once",
            attempt: deliveryController.attempts,
            maxAttempts: boundedAudit ? 1 : recoveryCoordinator.planningAttempts > 0 ? 2 : 1,
            recoveryAttempts: recoveryCoordinator.planningAttempts,
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
          if (reviewingReport && !visibleTextFromMessages(captured.messages).trim()) reportReviewEmpty = true;
          if (deliveryState !== "complete") ctl.createStatus({
            status: deliveryState === "no_answer" ? "error" : "canceled",
            text: safePartialReport
              ? "조사 복구가 완료되지 않아 근거가 있는 부분 보고만 전달했습니다. 전체 완료로 처리하지 않았습니다."
              : finalDelivery.reportState === "unresolved_tool_intent"
                ? "최종 단계에서 실행되지 않은 도구 호출 의도만 남아 보고서 완료로 처리하지 않았습니다."
                : deliveryState === "truncated"
                  ? "최종 보고가 출력 한도에 도달해 잘렸습니다. 완료로 처리하지 않았습니다."
                  : phaseTimedOut
                    ? "최종 보고가 제한 시간 안에 완료되지 않았습니다. 부분 출력으로 기록했습니다."
                    : ctl.abortSignal.aborted
                      ? "생성이 중지되었습니다. 지금까지 작성한 답변을 보존했습니다."
                      : captured.failure !== undefined
                        ? "생성 중 오류가 발생했습니다. 지금까지 작성한 답변을 보존했습니다."
                        : continuingReport
                          ? "이어 쓸 본문이 생성되지 않았습니다. 앞서 작성한 답변을 보존했습니다."
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
        if (repetitionAbort && !ctl.abortSignal.aborted) {
          ctl.guardAbort();
          const block = (repetitionAbort as { block: string }).block;
          // The engine contracts the looping text touches: the fact it was
          // trying to recall (r7 above: ue.net.client-rpc-callspace).
          const gaps = textEngineClaimGaps([{ label: "repeated reasoning", severity: "unrated", text: block }],
            returnedEngineEvidence([...visibleTranscript.snapshot().getMessagesArray(), ...captured.messages]), scope.engine, "progress");
          const toolActivity = runtimeToolTrace.size > 0
            || captured.messages.some(message => message.getToolCallRequests().length || message.getToolCallResults().length);
          const retry = !reasoningRecoveryRound && execution.reasoningRecoveryAttempts === 0 && !toolActivity;
          if (config.showDebugInfo) ctl.debug({ event: "repetition_recovery", executionId, modelInputId, roundIndex,
            decision: retry ? "retry" : "report", blockChars: block.length, contracts: gaps.map(gap => gap.factId) });
          if (retry) {
            runtimeFacts.set("repetition_check", repetitionFact(block, engineClaimFact(gaps)));
            repetitionRecoveryActive = true;
            execution.startReasoningRecovery("reasoning_repetition");
            workingHistory = historyBeforeRound;
            ctl.createStatus({ status: "done", text: "반복된 추론을 버리고, 관련 엔진 계약과 확인된 근거로 이 단계를 한 번 다시 시도합니다." });
            roundIndex += 1;
            continue;
          }
          // Repeated again, or no retry is left: report from what was returned.
          runtimeFacts.delete("repetition_check");
          workingHistory = historyBeforeRound;
          execution.finishResearch("research_repetition");
          ctl.createStatus({ status: "done", text: "추론이 다시 반복되어 조사를 마치고, 확인된 근거로 최종 보고를 작성합니다." });
          roundIndex += 1;
          continue;
        }
        if (reasoningRecoveryRound) {
          ctl.guardAbort();
          if (repetitionRecoveryActive) { runtimeFacts.delete("repetition_check"); repetitionRecoveryActive = false; }
          if (captured.continueAfterTools) {
            execution.finishReasoningRecovery();
            roundIndex += 1;
            continue;
          }
          // This is the single retry, not an entry to report or read recovery.
          if (captured.finishReason === "maxPredictedTokensReached" || finalRawToolIntent
            || !captured.predictionCompleted || !visibleTextFromMessages(captured.messages, internalDataParts).trim()) {
            ctl.createStatus({ status: "canceled", text: "생각 단계 재시도에서도 응답을 완료하지 못했습니다. 자동 재시도는 여기서 중단합니다." });
            deliverPartial("reasoning_recovery_exhausted");
          }
          break;
        }
        if (reasoningRecoveryDecision.eligible) {
          ctl.guardAbort();
          execution.startReasoningRecovery();
          // Admission proved this round had no tool activity. Earlier returned
          // evidence remains; incomplete reasoning stays in the GUI only.
          workingHistory = historyBeforeRound;
          ctl.createStatus({ status: "done", text: "생각 단계에서 생성 한도에 도달했습니다. 이번 생성에는 도구 요청이 없어, 확인된 근거로 다음 작업을 한 번 재시도합니다." });
          if (config.showDebugInfo) ctl.debug({ event: "reasoning_recovery_scheduled", executionId,
            sourceModelInputId: modelInputId,
            recoveryModelInputId: `${executionId}:reasoning-recovery-${execution.reasoningRecoveryAttempts}`,
            attempt: execution.reasoningRecoveryAttempts, toolCount: roundTools.length,
            requestedMaxTokens: reasoningRecoveryOutputCap(config.maxOutputReserve), partialReasoningPreservedInGui: true });
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
            actualResultCount, visibleTextFromMessages(captured.messages, internalDataParts).trim(), boundedAudit,
            { originalProfileFits: captured.continueAfterTools && recoveryProgress?.progressed === true
              && recoveryProgress.usableSourceEvidence
              && await measureOriginalProfile(workingHistory), fallbackInstruction: recoveryFallbackInstruction(captured.messages) });
          if (decision.trigger) {
            execution.finishResearch(decision.trigger);
            if (config.showDebugInfo) ctl.debug({
event: decision.trigger === "research_recovery_exhausted"
                ? "read_only_research_recovery_exhausted" : "read_only_research_recovery_completed",
              executionId, modelInputId, recoveryReason: recoveryCoordinator.reason,
              toolRounds: recoveryCoordinator.toolRounds, paginationRounds: recoveryCoordinator.paginationRounds,
              maxPaginationRounds: decision.maxPaginationRounds, noProgressRounds: recoveryCoordinator.noProgressRounds,
              recoveryProgress, nextPhase: "final_report"
});
            roundIndex++; continue;
          }
          if (decision.resumeOriginal && config.showDebugInfo) ctl.debug({ event: "read_recovery_resumed_original",
            executionId, modelInputId, originalToolCount: modelTools.length, reason: "usable_evidence_and_original_profile_fit" });
          if (decision.endRecovery) execution.endRecovery();
          if (decision.continue) { roundIndex++; continue; }
        }
        if (boundedAudit && captured.finishReason === "maxPredictedTokensReached") {
          execution.finishResearch("research_output_limit");
          roundIndex += 1;
          continue;
        }
        if (!config.observeOnly && config.reportReviewMode === "on" && !reportReviewAttempted
          && !captured.continueAfterTools && captured.finishReason === "eosFound" && captured.predictionCompleted
          && structuredToolRequestCount === 0 && actualResultCount === 0 && !finalRawToolIntent) {
          const answer = visibleTranscript.lastAnswerText;
          const gaps = unresolvedReportEngineClaimGaps(answer,
            returnedEngineEvidence(visibleTranscript.snapshot().getMessagesArray()), scope.engine);
          reviewMissing = reviewRegister.missing(answer);
          if (answer.trim() && (reviewMissing.length || gaps.length)) {
            reportReviewAttempted = true;
            runtimeFacts.set("report_review", reportReviewFact(reviewMissing, gaps));
            // The delivered answer is data, not the last message: a trailing
            // complete assistant answer is one the model only continues, and a
            // finished answer continues with EOS (conversation 1792929236226:
            // one token from 53,802 input tokens). The input ends with the last
            // tool results, so the supplement is a new assistant turn.
            runtimeFacts.set("delivered_report", deliveredReportFact(answer));
            reportContinuation = answer;
            deliveryController.retainReportPrefix(answer);
            deliveryController.beginSupplement("report_review", true);
            workingHistory = historyBeforeRound;
            execution.finishResearch("report_review");
            if (config.showDebugInfo) ctl.debug({ event: "report_review_scheduled", executionId, modelInputId,
              missing: reviewMissing.map(row => row.identifier), contracts: gaps.map(gap => gap.factId), toolCount: 0 });
            roundIndex++;
            continue;
          }
        }
        if (!boundedAudit && config.outputRecoveryMode === "on"
          && rawIntentText.trim() && canRecoverOutputLimit(visibleCaptured, generationAtRoundEnd)) {
          // Assistant-authored prose is continuation context, never committed
          // planning or source evidence. Preserve exactly the delivered prefix.
          reportContinuation = visibleTranscript.lastAnswerText;
          deliveryController.retainReportPrefix(reportContinuation);
          deliveryController.beginSupplement("output_recovery", false);
          workingHistory = historyBeforeRound;
          workingHistory.append(ChatMessage.create("assistant", reportContinuation));
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
            partialReportIncludedInInput: true,
            recoveryStrategy: "continue_report",
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
            recoveryReason: recoveryCoordinator.reason,
            recoveryAttempts: recoveryCoordinator.planningAttempts,
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
              : outputLimitStage === "reasoning"
                ? "생각 단계에서 생성 한도에 도달했습니다. 자동 복구가 꺼져 있거나 허용 조건을 충족하지 않아 중단했습니다."
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
        if (hasUnexecutedRawToolSyntax(rawIntentText) && structuredToolRequestCount === 0
          && !captured.continueAfterTools && !boundedAudit && !config.observeOnly) {
          // The research path ends with only unexecuted tool text and no retry left or allowed:
          // hand the user the evidence already gathered instead of the raw text alone.
          if (!toolPlanningRetryRound) ctl.createStatus({ status: "canceled",
            text: "실행되지 않은 도구 호출 텍스트만 남았고 재시도 조건을 충족하지 못해 확인된 근거로 부분 보고를 전달합니다." });
          deliverPartial(toolPlanningRetryRound ? "raw_tool_intent_retry_failed" : "raw_tool_intent_not_retried");
        }
        if (!captured.continueAfterTools) break;
        if ((reportNowFinalize || workingSetExhausted) && !execution.finalizing && !execution.reasoningRecovery && !execution.recovering) {
          if (workingSetExhausted && config.showDebugInfo) ctl.debug({ event: "working_set_exhausted", executionId, modelInputId, roundIndex,
            bundledRows: reviewRegister.rows.filter(row => row.bundle).length });
          execution.finishResearch("research_no_new_evidence");
          roundIndex += 1;
          continue;
        }
        if (boundedAudit && (
          roundIndex + 1 >= config.auditResearchRounds || Date.now() >= researchDeadlineAt
        )) {
          execution.finishResearch("research_round_limit");
        } else if (!boundedAudit && config.maxResearchRounds > 0 && roundIndex + 1 >= config.maxResearchRounds
          && !execution.finalizing && !execution.reasoningRecovery) {
          if (config.showDebugInfo) ctl.debug({ event: "research_round_limit", executionId, modelInputId,
            rounds: roundIndex + 1, maxResearchRounds: config.maxResearchRounds });
          execution.finishResearch("research_round_limit");
        }
        roundIndex += 1;
      }
      const notices = terminalReportNotices(visibleTranscript.snapshot(), visibleTranscript.lastAnswerText, {
        packetQuoteGaps, packetEngineGaps, engine: scope.engine, observeOnly: config.observeOnly,
        reviewedReport: reportReviewAttempted && !reportReviewBlocked && !reportReviewEmpty,
        reportReviewBlocked, reportReviewEmpty, reviewMissing });
      for (const notice of notices) {
        visibleTranscript.controller.createContentBlock({ roleOverride: "assistant" }).appendText(notice.text);
        if (config.showDebugInfo) ctl.debug({ event: notice.event, executionId, ...notice.details });
      }
    } catch (error) {
      executionFailure = error;
      throw error;
    } finally {
      // The note is terminal state like the window: a canceled or failed
      // execution must still hand its accepted judgments to the next turn.
      try { persistTerminalNote(); }
      catch (error) { cleanupFailures.push(String(error)); }
      // No new model work during shutdown. Persist returned evidence and only
      // RoundTransaction-approved planning; the next execution must remeasure.
      try {
        if (workingContext) {
          // Transfer ownership first: a failed close must not be attempted twice.
          releaseContext = null;
          const terminal = sealAndRelease(workingContext, visibleTranscript.freeze(), workingHistory, cleanupFailures);
          if (config.showDebugInfo) ctl.debug({ event: "working_context_seal", executionId, ...terminal });
          if (!terminal.released) throw new Error(terminal.release.reason || "archive_close_unavailable");
        }
      } catch (error) {
        cleanupFailures.push(String(error));
        if (executionFailure === undefined) { deliveryController.stop("context_cleanup_failed"); throw error; }
      } finally { activeActivity?.complete(); }
    }
    } catch (error) {
      executionFailure ??= error;
      if (!finalExecution) terminalOwner.stop("initialization_failed");
      throw executionFailure;
    } finally {
      let cleanupError: unknown;
      try { releaseContext?.(); }
      catch (error) { cleanupFailures.push(String(error)); cleanupError = error; }
      try { releaseSession?.(); }
      catch (error) {
        cleanupFailures.push(String(error));
        if (executionFailure === undefined && cleanupError === undefined) terminalOwner.stop("tool_session_dispose_failed");
        cleanupError ??= error;
      }
      // The external signal owns its cause. A user cancel overrides an earlier
      // phase timeout; an evaluator/host deadline is a timeout, not user cancel.
      const externalTimeout = ctl.abortSignal.aborted && ctl.abortSignal.reason?.name === "TimeoutError";
      const outcome = terminalOwner.finish({ canceled: ctl.abortSignal.aborted && !externalTimeout,
        timedOut: ctl.abortSignal.aborted ? externalTimeout : undefined,
        failure: executionFailure ?? cleanupError });
      const terminalPhase = finalExecution?.phase || "SETUP";
      finalExecution?.terminate(outcome.reason);
      if (showDebugInfo) {
        ctl.debug({ event: "execution_outcome", executionId, modelInputId: finalModelInputId,
          objectiveKind: objective?.kind ?? null, mutationState: objective?.mutationState ?? "none",
          terminalPhase, ...outcome, cleanupFailures });
        if (finalExecution) ctl.debug({ event: "execution_transitions", executionId,
          phase: finalExecution.phase, transitions: finalExecution.transitions });
      }
      if (executionFailure === undefined && cleanupError !== undefined) throw cleanupError;
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

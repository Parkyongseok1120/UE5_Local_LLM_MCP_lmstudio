import { ChatMessage, type Chat } from "@lmstudio/sdk";
import type { FinalizationTrigger } from "./execution-contracts";
import { isCompletedPredictionReason, type CapturedRound } from "./round-loop";
import { renderInterruptedCalls } from "./interrupted-calls.js";
import { RecoveryCoordinator, researchLimitTrigger, researchRoundNotice, type RecoveryPlan } from "./recovery-coordinator";

export type ExecutionPhase = "RESEARCH" | "READ_RECOVERY" | "TOOL_REPLAN"
  | "REASONING_RECOVERY" | "FINAL_DELIVERY" | "OUTPUT_RECOVERY" | "TERMINATED";
export const ALLOWED_TRANSITIONS: Readonly<Record<ExecutionPhase, readonly ExecutionPhase[]>> = {
  RESEARCH: ["READ_RECOVERY", "TOOL_REPLAN", "REASONING_RECOVERY", "FINAL_DELIVERY", "OUTPUT_RECOVERY", "TERMINATED"],
  REASONING_RECOVERY: ["RESEARCH", "FINAL_DELIVERY", "TERMINATED"],
  READ_RECOVERY: ["RESEARCH", "TOOL_REPLAN", "FINAL_DELIVERY", "OUTPUT_RECOVERY", "TERMINATED"],
  TOOL_REPLAN: ["READ_RECOVERY", "FINAL_DELIVERY", "TERMINATED"],
  FINAL_DELIVERY: ["TOOL_REPLAN", "TERMINATED"],
  OUTPUT_RECOVERY: ["TERMINATED"],
  TERMINATED: [],
};
/** Limits that end the research budget itself. Once one ends research, no
 * later recovery or replan may reopen tools (conversation 1793984074550:
 * the round limit was followed by a replan that read two more rounds). */
const RESEARCH_CLOSING_TRIGGERS: ReadonlySet<FinalizationTrigger> = new Set(["research_round_limit", "research_time_limit", "research_timeout"]);
export class ExecutionState {
  readonly recovery = new RecoveryCoordinator();
  private current: ExecutionPhase = "RESEARCH";
  private trigger: FinalizationTrigger | null = null;
  private reasoningAttempts = 0;
  private researchPredictionCount = 0;
  private closedBy: FinalizationTrigger | null = null;
  private roundNoticeShown = false;
  /** Tool-allowed predictions so far in any phase (research, read recovery,
   * replan, reasoning recovery): the unit the research round limit counts. */
  get researchPredictions() { return this.researchPredictionCount; }
  beginResearchPrediction() { this.researchPredictionCount++; }
  /** The research budget ended; the tool surface stays closed for this execution. */
  get toolsClosed() { return this.closedBy !== null; }
  get closedByTrigger() { return this.closedBy; }
  /** The entry check of a round: the limit that ends research now, or the
   * one-time remaining-rounds notice. Final rounds are past this boundary. */
  researchBoundary(limit: number, deadlineAt: number | null) {
    if (this.finalizing) return { trigger: null, notice: null };
    const trigger = researchLimitTrigger(this.researchPredictionCount, limit, deadlineAt);
    const notice = trigger || this.roundNoticeShown ? null : researchRoundNotice(this.researchPredictionCount, limit);
    if (notice) this.roundNoticeShown = true;
    return { trigger, notice };
  }
  readonly transitions: Array<{
from: ExecutionPhase; to: ExecutionPhase; reason: string;
    planningEffect: "commit_completed_only"; evidenceEffect: "retain_returned"
}> = [];
  get phase() { return this.current; }
  get finalizing() { return this.current === "FINAL_DELIVERY" || this.current === "OUTPUT_RECOVERY"; }
  get recovering() { return this.current === "READ_RECOVERY" || this.current === "TOOL_REPLAN"; }
  get replanning() { return this.current === "TOOL_REPLAN"; }
  get reasoningRecovery() { return this.current === "REASONING_RECOVERY"; }
  get reasoningRecoveryAttempts() { return this.reasoningAttempts; }
  get canStartReadRecovery() { return !this.reasoningRecovery && !this.finalizing
    && this.current !== "TERMINATED" && !this.recovery.episodeStarted; }
  resolveInputPressure(recoveryCandidateFits: boolean): "recover" | "finalize" | "stop" {
    if (this.reasoningRecovery || this.finalizing || this.current === "TERMINATED") return "stop";
    return this.canStartReadRecovery && recoveryCandidateFits ? "recover" : "finalize";
  }
  get finalizationTrigger() { return this.trigger; }
  transition(to: ExecutionPhase, reason: string) {
    if (!reason.trim() || !ALLOWED_TRANSITIONS[this.current].includes(to)
      || this.closedBy && ["READ_RECOVERY", "TOOL_REPLAN", "REASONING_RECOVERY", "RESEARCH"].includes(to)) {
      throw new Error(`ILLEGAL_EXECUTION_TRANSITION: ${this.current} -> ${to} (${reason})`);
    }
    if (to === "REASONING_RECOVERY") {
      if (this.reasoningAttempts >= 1) throw new Error("REASONING_RECOVERY_EXHAUSTED");
      this.reasoningAttempts++;
    }
    this.transitions.push({
from: this.current, to, reason,
      planningEffect: "commit_completed_only", evidenceEffect: "retain_returned"
});
    this.current = to;
  }
  finishResearch(trigger: FinalizationTrigger) {
    this.transition(trigger === "output_recovery" ? "OUTPUT_RECOVERY" : "FINAL_DELIVERY", trigger);
    this.trigger = trigger;
    if (RESEARCH_CLOSING_TRIGGERS.has(trigger)) this.closedBy ??= trigger;
  }
  startRecovery(reason: string, plan?: Omit<RecoveryPlan, "reason">) {
    this.transition("READ_RECOVERY", reason); this.trigger = null;
    if (plan) this.recovery.begin({ ...plan, reason });
  }
  startReasoningRecovery(reason = "reasoning_output_limit") { this.transition("REASONING_RECOVERY", reason); }
  finishReasoningRecovery() {
    if (this.reasoningRecovery) this.transition("RESEARCH", "reasoning_retry_tools_returned");
  }
  startReplan(plan?: RecoveryPlan) {
    this.transition("TOOL_REPLAN", "fresh_structured_planning"); this.trigger = null;
    if (plan) this.recovery.begin(plan, true);
  }
  finishReplan() { if (this.replanning) this.transition("READ_RECOVERY", "planning_round_returned"); }
  endRecovery() { if (this.recovering) this.transition("RESEARCH", "recovery_report_returned"); }
  terminate(reason: string) { if (this.current !== "TERMINATED") this.transition("TERMINATED", reason); }
}

/** One round is a transaction: returned evidence survives; incomplete prose does not. */
export class RoundTransaction {
  readonly planningCommitted: boolean;
  readonly returnedIds: Set<string>;
  constructor(readonly captured: CapturedRound, aborted: boolean) {
    this.planningCommitted = captured.predictionCompleted === true && !aborted && captured.failure === undefined
      && isCompletedPredictionReason(captured.finishReason);
    this.returnedIds = new Set(captured.messages.flatMap(m => m.getToolCallResults()).map(r => String(r.toolCallId)));
  }
  durable(message: ChatMessage): ChatMessage | null {
    if (!message.isAssistantMessage() || this.planningCommitted) return message;
    const requests = message.getToolCallRequests().filter(r => this.returnedIds.has(String(r.id)));
    if (!requests.length) return null;
    return ChatMessage.from({ role: "assistant", content: requests.map(toolCallRequest => ({ type: "toolCallRequest", toolCallRequest })) });
  }
  commitEvidence(history: Chat) {
    for (const message of this.captured.messages) {
      const durable = this.durable(message);
      if (durable) history.append(durable);
    }
  }
  interruptionMessage(runtimeTrace: Iterable<Record<string, unknown>>): ChatMessage | null {
    const items = [...runtimeTrace].filter(item => item.providerRequestId
      && !this.returnedIds.has(String(item.providerRequestId)) && item.generationState !== "failed")
      .map(item => ({
        callKey: item.callKey, causalModelInputId: item.causalModelInputId,
        providerRequestId: item.providerRequestId, toolName: item.toolName,
        provider: item.provider, projectIdentity: item.projectIdentity, operationId: item.operationId,
        proposedArgumentsFingerprint: item.proposedArgumentsFingerprint,
        generationState: item.generationState, validationState: item.validationState || "unknown",
        approvalState: item.approvalState || "unknown", guardAllowed: item.guardAllowed === true,
        providerDispatch: item.executionState === "not_executed" ? "not_dispatched" : "unknown",
        resultObservation: "not_observed", effect: item.executionState === "not_executed" ? "not_applied" : "unknown",
      }));
    return items.length ? ChatMessage.create("assistant", renderInterruptedCalls(items)) : null;
  }
}

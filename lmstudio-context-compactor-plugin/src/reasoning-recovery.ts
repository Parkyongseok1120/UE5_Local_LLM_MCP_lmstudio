import type { DirectConfig, OutputLimitStage } from "./execution-contracts";
import type { ExecutionPhase } from "./execution-state";
import type { CapturedRound } from "./round-loop";

/** Admission only. The execution owns attempts; input preparation owns fit;
 * the existing guard still owns every tool authorization. */
export function decideReasoningRecovery(options: {
  config: Pick<DirectConfig, "reasoningRecoveryMode" | "observeOnly" | "auditCompletionMode">;
  phase: ExecutionPhase;
  attempts: number;
  captured: CapturedRound;
  outputLimitStage: OutputLimitStage | undefined;
  canceled: boolean;
  timedOut: boolean;
  toolTraceCount: number;
}): { eligible: boolean; reason: string } {
  const { config, captured, outputLimitStage } = options;
  if (config.reasoningRecoveryMode !== "on") return { eligible: false, reason: "disabled" };
  if (config.observeOnly || config.auditCompletionMode === "bounded") return { eligible: false, reason: "mode_excluded" };
  if (options.phase !== "RESEARCH") return { eligible: false, reason: "phase_excluded" };
  if (options.attempts >= 1) return { eligible: false, reason: "already_retried" };
  if (options.canceled || options.timedOut) return { eligible: false, reason: "canceled_or_timed_out" };
  if (captured.failure !== undefined || captured.continueAfterTools) return { eligible: false, reason: "not_an_unexecuted_generation" };
  if (captured.finishReason !== "maxPredictedTokensReached" || outputLimitStage !== "reasoning") {
    return { eligible: false, reason: "not_reasoning_limit" };
  }
  const usage = captured.predictionUsage;
  // A missing result cannot establish that a provider did not execute. Reject
  // even partial/denied request activity instead of attempting a replay.
  if (options.toolTraceCount !== 0 || usage.toolArgumentChars !== 0
    || usage.sdkToolRequestStartedCount !== 0 || usage.sdkToolRequestNamedCount !== 0
    || usage.sdkToolRequestEndedCount !== 0 || usage.sdkToolRequestFinalizedCount !== 0
    || usage.sdkToolRequestFailureCount !== 0 || usage.rawToolIntentCandidate
    || captured.messages.some(message => message.getToolCallRequests().length || message.getToolCallResults().length)) {
    return { eligible: false, reason: "tool_activity_present" };
  }
  return { eligible: true, reason: "unexecuted_reasoning_limit" };
}

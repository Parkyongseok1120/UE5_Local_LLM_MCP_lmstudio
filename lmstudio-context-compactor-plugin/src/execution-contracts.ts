import {
  type ToolCallRequest
} from "@lmstudio/sdk";
import {
  type ProjectEngineSetting
} from "./tool-scope";
import type { DesignGuidanceMode, DesignGuidanceDelivery } from "./design-guidance";
import { CONTEXT_DEFAULTS } from "./config-defaults";

export type ContinuityNote = {
  scope: { objectiveFingerprint: string; projectIdentity?: string };
  decisions: Array<unknown>;
  rejectedHypotheses: Array<unknown>;
  openQuestions: Array<unknown>;
  reviewClaims?: Array<unknown>;
};

export type NormalizedMessage = {
  role: string;
  text: string;
  hasFiles: boolean;
  toolRequests: Array<ToolCallRequest>;
  toolResults: Array<{ content: string; toolCallId?: string }>;
};

export type CheckpointResult = {
  changeDataCheckpoint?: string;
  checkpoint: string;
  assistantCheckpoint: string;
  retainedIndexes: Array<number>;
  omittedMessageCount: number;
  latestUserVerbatim: string;
  memory?: {
    currentWorkStatus?: { modifiedOrObservedFiles?: Array<Record<string, unknown>> };
  };
  serializationDiagnostics?: Record<string, unknown>;
};

export type InputAvailabilityProjection = {
  modelInputId: string;
  verificationBoundary: "final_sdk_chat";
  hostInputVerification: "unknown" | "verified";
  entries: Array<Record<string, unknown>>;
  omittedEntryCount: number;
  entryListComplete: boolean;
  metrics: Record<string, number>;
};

export type ContextMeasurement = {
  contextLength: number;
  contextLengthSource: "model" | "configured_fallback";
  inputTokens: number;
  promptMeasurementSource: "templated_model_count" | "character_estimate";
  templatedInputChars: number | null;
  toolSchemaChars: number;
  toolSchemaTokens: number | null;
  toolSchemaTokenMeasurement: "none" | "estimate" | "exact";
  toolSchemaFingerprint: string;
  templatedInputFingerprint: string | null;
  outputReserve: number;
  remainingTokens: number;
  exact: boolean;
  messageCount: number;
  fit: boolean | null;
};

export type DirectConfig = {
  projectEngine: ProjectEngineSetting;
  projectIdentity: string;
  designGuidanceMode: DesignGuidanceMode;
  designGuidanceDelivery: DesignGuidanceDelivery;
  designGuidanceMaxTokens: number;
  observeOnly: boolean;
  contextManagementMode: ContextManagementMode;
  workingInputTargetTokens: number;
  workingInputTriggerTokens: number;
  workingInputScaling: WorkingInputScaling;
  toolResultProjectionChars: number;
  semanticHandoff: boolean;
  semanticSummaryMaxTokens: number;
  semanticSummarySeconds: number;
  showDebugInfo: boolean;
  pastReasoningTokens: number;
  reviewProgress: boolean;
  softRemainingTokens: number;
  hardRemainingTokens: number;
  maxOutputReserve: number;
  outputRecoveryMode: OutputRecoveryMode;
  reasoningRecoveryMode: OutputRecoveryMode;
  outputRecoveryMaxTokens: number;
  outputRecoverySeconds: number;
  safetyMarginTokens: number;
  assumedContextLength: number;
  recentCompleteTurns: number;
  compactAboveMessageCount: number;
  maxCheckpointChars: number;
  maxToolResultChars: number;
  toolStagnationAction: StagnationAction;
  toolStagnationRounds: number;
  /** Tool rounds of reads with no new path, version or range before report_now. */
  noNewEvidenceRounds: number;
  maxResearchRounds: number;
  generationRepetitionAction: StagnationAction;
  generationRepeatCount: number;
  inputAvailabilityMode: InputAvailabilityMode;
  auditCompletionMode: AuditCompletionMode;
  auditResearchSeconds: number;
  auditResearchRounds: number;
  auditFinalSeconds: number;
  auditFinalMaxTokens: number;
};

export type StagnationAction = "off" | "warn" | "pause";

export type InputAvailabilityMode = "off" | "observe" | "inject";

export type AuditCompletionMode = "off" | "bounded";

export type OutputRecoveryMode = "off" | "on";

export type ContextManagementMode = "legacy" | "deterministic" | "hybrid";

export const DEFAULT_CONTEXT_MANAGEMENT_MODE: ContextManagementMode = CONTEXT_DEFAULTS.mode;

/** "auto" scales the configured trigger/target up with the loaded context;
 * "fixed" keeps the configured absolute values (pre-1.4.0-RC4 behavior). */
export type WorkingInputScaling = "auto" | "fixed";

// Budget estimates. Every value below is an admission allowance only; the next
// fully templated exact measurement remains the sole authority for dispatch.
/** Conservative UTF-8 bytes per model token for returned JSON/code/CJK text. */
export const UTF8_BYTES_PER_TOKEN_ESTIMATE = 2.5;
/** Room for the structured tool request a research round generates before its
 * results return. The next round's full output reserve is subtracted by that
 * round's own hard ceiling, so it is not reserved twice. */
export const POST_TOOL_REQUEST_TOKENS = 2048;
/** Working space kept above the mandatory floor when compaction can afford it. */
export const MIN_WORKING_SPACE_TOKENS = 6000;
/** Context-proportional watermarks used by workingInputScaling="auto". */
export const SCALED_TRIGGER_RATIO = 0.6;
export const SCALED_TARGET_RATIO = 0.45;

export type FinalizationTrigger = "research_time_limit" | "research_timeout" | "research_output_limit"
  | "output_recovery"
  | "research_round_limit" | "context_budget" | "research_recovery_complete"
  | "research_recovery_exhausted" | "research_no_new_evidence";

export type FinalDeliveryState = "complete" | "truncated" | "partial" | "no_answer";

export type FinalReportState = "report" | "partial_report" | "unresolved_tool_intent" | "no_answer";

/** Delivery completion is not proof that the user's objective is satisfied. */
export type ExecutionOutcome = Readonly<{
  executionOutcome: "completed" | "truncated" | "no_answer" | "failed" | "canceled" | "timed_out";
  reason: string;
  finalFinishReason: string;
  generationCompleted: boolean;
  reportPresent: boolean;
  reportDelivered: boolean;
  reportCompleted: boolean;
  deliveryState: FinalDeliveryState;
  reportState: FinalReportState;
  objectiveSatisfied: boolean | null;
  taskCompleted: boolean;
}>;

export type OutputLimitStage = "reasoning" | "tool_arguments" | "visible_report" | "forced_final" | "unknown";

export const MIN_FINAL_OUTPUT_TOKENS = 256;

export const FIRST_CONSUMER_RAW_GIT_MAX_CHARS = 8192;

export const RESEARCH_RECOVERY_MAX_TOKENS = 4096;

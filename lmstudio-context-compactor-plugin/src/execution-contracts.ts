import {
  type ToolCallRequest
} from "@lmstudio/sdk";
import {
  type ProjectEngineSetting
} from "./tool-scope";

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
  observeOnly: boolean;
  contextManagementMode: ContextManagementMode;
  workingInputTargetTokens: number;
  workingInputTriggerTokens: number;
  toolResultProjectionChars: number;
  semanticSummaryMaxTokens: number;
  semanticSummarySeconds: number;
  showDebugInfo: boolean;
  pastReasoningTokens: number;
  reviewProgress: boolean;
  softRemainingTokens: number;
  hardRemainingTokens: number;
  maxOutputReserve: number;
  outputRecoveryMode: OutputRecoveryMode;
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

export const DEFAULT_CONTEXT_MANAGEMENT_MODE: ContextManagementMode = "hybrid";

export type FinalizationTrigger = "research_time_limit" | "research_timeout" | "research_output_limit"
  | "output_recovery"
  | "research_round_limit" | "context_budget" | "research_recovery_complete"
  | "research_recovery_exhausted";

export type FinalDeliveryState = "complete" | "truncated" | "partial" | "no_answer";

export type FinalReportState = "report" | "partial_report" | "unresolved_tool_intent" | "no_answer";

export type OutputLimitStage = "reasoning" | "tool_arguments" | "visible_report" | "forced_final" | "unknown";

export const MIN_FINAL_OUTPUT_TOKENS = 256;

export const FIRST_CONSUMER_RAW_GIT_MAX_CHARS = 8192;

export const RESEARCH_RECOVERY_MAX_TOKENS = 4096;

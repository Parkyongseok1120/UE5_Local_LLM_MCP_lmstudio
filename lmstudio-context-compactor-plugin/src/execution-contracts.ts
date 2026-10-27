import {
  type ToolCallRequest
} from "@lmstudio/sdk";
import {
  type ProjectEngineSetting
} from "./tool-scope";
import type { DesignGuidanceMode, DesignGuidanceDelivery } from "./design-guidance";
import { CONTEXT_DEFAULTS } from "./config-defaults";

export type ObjectiveKind = "review" | "mutate" | "build" | "explain";
export const RUNTIME_DATA_SHARE = 0.10;
export type RuntimeFactKey = "budget" | "stagnation" | "repeated_failure" | "prior_turn" | "quote_check"
  | "engine_claim_check" | "write_api_check" | "report_now" | "review_register" | "report_review" | "delivered_report"
  | "repetition_check" | "mutation_focus" | "tool_retry" | "partial_projection" | "read_ledger";
export const RUNTIME_FACTS: Readonly<Record<RuntimeFactKey, { priority: 0 | 1 | 2; maxChars: number; lifetime: "until_cleared" | "round" }>> = {
  mutation_focus: { priority: 0, maxChars: 800, lifetime: "until_cleared" },
  report_now: { priority: 0, maxChars: 1500, lifetime: "until_cleared" },
  budget: { priority: 0, maxChars: 2000, lifetime: "until_cleared" },
  report_review: { priority: 0, maxChars: 4000, lifetime: "until_cleared" },
  delivered_report: { priority: 0, maxChars: Infinity, lifetime: "until_cleared" },
  prior_turn: { priority: 0, maxChars: 800, lifetime: "until_cleared" },
  tool_retry: { priority: 0, maxChars: 1500, lifetime: "until_cleared" },
  review_register: { priority: 1, maxChars: 6000, lifetime: "until_cleared" },
  engine_claim_check: { priority: 1, maxChars: 3000, lifetime: "until_cleared" },
  quote_check: { priority: 1, maxChars: 3000, lifetime: "until_cleared" },
  write_api_check: { priority: 1, maxChars: 2000, lifetime: "until_cleared" },
  repetition_check: { priority: 1, maxChars: 3000, lifetime: "until_cleared" },
  read_ledger: { priority: 2, maxChars: 6000, lifetime: "round" },
  partial_projection: { priority: 2, maxChars: 3000, lifetime: "round" },
  stagnation: { priority: 2, maxChars: 1500, lifetime: "until_cleared" },
  repeated_failure: { priority: 2, maxChars: 1500, lifetime: "until_cleared" },
};
export type RuntimeFactEntry = Readonly<{ key: RuntimeFactKey; text: string }>;
export type RewriteCause = "turn_start" | "projection" | "low_water_compaction" | "reasoning_bound"
  | "data_consolidation" | "guidance_rescope" | "guidance_finalization_drop"
  | "phase_transition" | "guidance_applicability_invalidated" | "guidance_budget_drop";
export type MutationState = "none" | "attempted" | "applied" | "blocked";
export interface TaskObjective {
  readonly kind: ObjectiveKind;
  readonly requiredEffects: ReadonlySet<"read" | "mutation" | "long_running">;
  mutationState: MutationState;
}

export interface RoundVerdict {
  readonly progressed: boolean;
  readonly noNewEvidence: boolean;
  readonly failedReads: number;
  readonly budgetDenied: readonly string[];
  readonly mutationOutcomes: readonly ("applied" | "failed")[];
}
export interface PressureSignal {
  readonly workingSetExhausted: boolean;
  readonly measuredStage: "final";
}
export type ContinuationAction = "continue" | "recover_read" | "narrow_to_mutation" | "finalize";

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
  /** Non-enumerable, execution-local template used by prefix telemetry only. */
  readonly templatedPrompt?: string;
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
  reportReviewMode: OutputRecoveryMode;
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
  generationRepetitionAction: GenerationRepetitionAction;
  generationRepeatCount: number;
  inputAvailabilityMode: InputAvailabilityMode;
  auditCompletionMode: AuditCompletionMode;
  auditResearchSeconds: number;
  auditResearchRounds: number;
  auditFinalSeconds: number;
  auditFinalMaxTokens: number;
};

export type StagnationAction = "off" | "warn" | "pause";
/** recover: a research round whose reasoning repeats is stopped and retried once. */
export type GenerationRepetitionAction = StagnationAction | "recover";

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
export const ENVELOPE_TOKENS = 256;
export const UNKNOWN_SIZE_MARGIN_TOKENS = 1024;
/** Room for the structured tool request a research round generates before its
 * results return. The next round's full output reserve is subtracted by that
 * round's own hard ceiling, so it is not reserved twice. */
export const POST_TOOL_REQUEST_TOKENS = 2048;
/** Working space kept above the mandatory floor when compaction can afford it. */
export const MIN_WORKING_SPACE_TOKENS = 6000;
/** Context-proportional watermarks used by workingInputScaling="auto". The
 * trigger is still capped below the hard ceiling by one round's tool results
 * (BudgetBroker safeTrigger). Raised from 0.6/0.45: at an 84K context a
 * 13-file review needed about 20K tokens of bodies next to about 20K of
 * system, schema and guidance, and the old 40K/56K window kept compacting them
 * (2026-10-25 runs). */
export const SCALED_TRIGGER_RATIO = 0.86;
export const SCALED_TARGET_RATIO = 0.65;
/** Share of the low-water input the current turn's working set (the latest
 * reads it is still reasoning about) may keep verbatim through projection. */
export const PINNED_WORKING_SET_SHARE = 0.3;
/** The working-set budget never drops below the edit-only default. */
export const PINNED_WORKING_SET_MIN_CHARS = 6144;

export type FinalizationTrigger = "research_time_limit" | "research_timeout" | "research_output_limit"
  | "output_recovery" | "report_review"
  | "research_round_limit" | "context_budget" | "research_recovery_complete"
  | "research_recovery_exhausted" | "research_no_new_evidence" | "research_repetition"
  | "context_working_set_exhausted" | "mutation_blocked_by_context";

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
  /** A follow-up round that extended an already delivered report. */
  supplement?: { kind: "report_review" | "output_recovery"; state: "delivered" | "empty" | "blocked" | "stopped" };
}>;

export type OutputLimitStage = "reasoning" | "tool_arguments" | "visible_report" | "forced_final" | "unknown";

export const MIN_FINAL_OUTPUT_TOKENS = 256;

export const FIRST_CONSUMER_RAW_GIT_MAX_CHARS = 8192;

export const RESEARCH_RECOVERY_MAX_TOKENS = 4096;

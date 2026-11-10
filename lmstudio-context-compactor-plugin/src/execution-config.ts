import {
  type PredictionLoopHandlerController
} from "@lmstudio/sdk";
import { directConfigSchematics } from "./direct-config";
import { NUMERIC_FIELDS, PERCENT_FIELDS, REPORT_REVIEW_DEFAULT, type NumericField, type PercentField } from "./config-defaults";
import { designGuidanceMode, designGuidanceDelivery } from "./design-guidance";
import { DEFAULT_CONTEXT_MANAGEMENT_MODE, type AuditCompletionMode, type ContextManagementMode, type DirectConfig, type InputAvailabilityMode, type OutputRecoveryMode, type StagnationAction, type GenerationRepetitionAction, type WorkingInputScaling } from "./execution-contracts";
import {
  type ProjectEngineSetting
} from "./tool-scope";

export function numeric(value: unknown, fallback: number, min: number, max: number): number {
  if (value === null || value === undefined || typeof value === "boolean" || typeof value === "string" && !value.trim()) return fallback;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(parsed)));
}

/** A share of the context length, kept to one decimal place. numeric() would cut it to an integer. */
export function percent(value: unknown, fallback: number, min: number, max: number): number {
  if (value === null || value === undefined || typeof value === "boolean" || typeof value === "string" && !value.trim()) return fallback;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < min || parsed > max) return fallback;
  return Math.round(parsed * 10) / 10;
}

export function stagnationAction(value: unknown): StagnationAction {
  return value === "off" || value === "pause" ? value : "warn";
}

/** Within-generation repetition defaults to recover: stop and retry a looping research round. */
export function generationRepetitionAction(value: unknown): GenerationRepetitionAction {
  return value === "off" || value === "pause" || value === "warn" ? value : "recover";
}

export function inputAvailabilityMode(value: unknown): InputAvailabilityMode {
  return value === "off" || value === "inject" ? value : "observe";
}

export function auditCompletionMode(value: unknown): AuditCompletionMode {
  return value === "bounded" ? "bounded" : "off";
}

export function outputRecoveryMode(value: unknown): OutputRecoveryMode {
  return value === "off" ? "off" : "on";
}

export function contextManagementMode(value: unknown): ContextManagementMode {
  return value === "legacy" || value === "deterministic" || value === "hybrid"
    ? value : DEFAULT_CONTEXT_MANAGEMENT_MODE;
}

export function workingInputScaling(value: unknown): WorkingInputScaling {
  return value === "fixed" ? "fixed" : "auto";
}

export function readConfig(ctl: PredictionLoopHandlerController): DirectConfig {
  const config = ctl.getPluginConfig(directConfigSchematics);
  const read = (key: NumericField) => {
    if (key === "outputRecoverySeconds") return 0; // Deprecated rewrite-only timer.
    const spec = NUMERIC_FIELDS[key];
    return numeric(config.get(key), spec.default, spec.min, spec.max);
  };
  const configuredEngine = String(config.get("projectEngine") || "auto");
  const projectEngine: ProjectEngineSetting = ["auto", "unity", "unreal", "mixed"].includes(configuredEngine)
    ? configuredEngine as ProjectEngineSetting : "auto";
  const maxOutputReserve = read("maxOutputReserve");
  const configuredRecoveryMaxTokens = read("outputRecoveryMaxTokens");
  const workingInputTargetTokens = read("workingInputTargetTokens");
  const share = (key: PercentField) => percent(config.get(key), PERCENT_FIELDS[key].default, PERCENT_FIELDS[key].min, PERCENT_FIELDS[key].max);
  const workingContextMode = contextManagementMode(config.get("contextManagementMode"));
  const scaling = workingInputScaling(config.get("workingInputScaling"));
  const contextBudgetRequested = config.get("contextBudgetMode") === "percentage" ? "percentage" : "legacy";
  return {
    projectEngine,
    projectIdentity: String(config.get("projectIdentity") || "").trim().slice(0, 4096),
    designGuidanceMode: designGuidanceMode(config.get("designGuidanceMode")),
    designGuidanceDelivery: designGuidanceDelivery(config.get("designGuidanceDelivery")),
    designGuidanceMaxTokens: read("designGuidanceMaxTokens"),
    observeOnly: config.get("observeOnly") === true,
    contextManagementMode: workingContextMode,
    workingInputTargetTokens,
    workingInputTriggerTokens: Math.max(workingInputTargetTokens,
      read("workingInputTriggerTokens")),
    workingInputScaling: scaling,
    contextBudgetRequested,
    contextBudgetMode: contextBudgetRequested === "percentage" && workingContextMode !== "legacy" ? "percentage"
      : scaling === "fixed" ? "absolute" : "legacy_auto",
    contextTargetPercent: share("contextTargetPercent"),
    contextTriggerPercent: share("contextTriggerPercent"),
    contextOutputPercent: share("contextOutputPercent"),
    contextSafetyPercent: share("contextSafetyPercent"),
    contextOutputMaxTokens: read("contextOutputMaxTokens"),
    toolResultProjectionChars: read("toolResultProjectionChars"),
    semanticHandoff: config.get("semanticHandoff") === true,
    semanticSummaryMaxTokens: read("semanticSummaryMaxTokens"),
    semanticSummarySeconds: read("semanticSummarySeconds"),
    showDebugInfo: config.get("showDebugInfo") !== false,
    reviewProgress: config.get("reviewProgress") === true,
    pastReasoningTokens: read("pastReasoningTokens"),
    softRemainingTokens: read("softRemainingTokens"),
    hardRemainingTokens: read("hardRemainingTokens"),
    maxOutputReserve,
    outputRecoveryMode: outputRecoveryMode(config.get("outputRecoveryMode")),
    reportReviewMode: config.get("reportReviewMode") === "off" ? "off" : REPORT_REVIEW_DEFAULT,
    reasoningRecoveryMode: outputRecoveryMode(config.get("reasoningRecoveryMode")),
    outputRecoveryMaxTokens: configuredRecoveryMaxTokens > 0 ? configuredRecoveryMaxTokens : maxOutputReserve,
    outputRecoveryFollowsReserve: configuredRecoveryMaxTokens === 0,
    outputRecoverySeconds: read("outputRecoverySeconds"),
    safetyMarginTokens: read("safetyMarginTokens"),
    assumedContextLength: read("assumedContextLength"),
    recentCompleteTurns: read("recentCompleteTurns"),
    compactAboveMessageCount: read("compactAboveMessageCount"),
    maxCheckpointChars: read("maxCheckpointChars"),
    maxToolResultChars: read("maxToolResultChars"),
    toolStagnationAction: stagnationAction(config.get("toolStagnationAction")),
    toolStagnationRounds: read("toolStagnationRounds"),
    noNewEvidenceRounds: read("noNewEvidenceRounds"),
    maxResearchRounds: read("maxResearchRounds"),
    generationRepetitionAction: generationRepetitionAction(config.get("generationRepetitionAction")),
    generationRepeatCount: read("generationRepeatCount"),
    inputAvailabilityMode: inputAvailabilityMode(config.get("inputAvailabilityMode")),
    auditCompletionMode: auditCompletionMode(config.get("auditCompletionMode")),
    auditResearchSeconds: read("auditResearchSeconds"),
    auditResearchRounds: read("auditResearchRounds"),
    auditFinalSeconds: read("auditFinalSeconds"),
    auditFinalMaxTokens: read("auditFinalMaxTokens"),
  };
}

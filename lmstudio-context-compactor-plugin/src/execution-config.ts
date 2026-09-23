import {
  type PredictionLoopHandlerController
} from "@lmstudio/sdk";
import { directConfigSchematics } from "./direct-config";
import { DEFAULT_CONTEXT_MANAGEMENT_MODE, type AuditCompletionMode, type ContextManagementMode, type DirectConfig, type InputAvailabilityMode, type OutputRecoveryMode, type StagnationAction } from "./execution-contracts";
import {
  type ProjectEngineSetting
} from "./tool-scope";

export function numeric(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(parsed)));
}

export function stagnationAction(value: unknown): StagnationAction {
  return value === "off" || value === "pause" ? value : "warn";
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

export function readConfig(ctl: PredictionLoopHandlerController): DirectConfig {
  const config = ctl.getPluginConfig(directConfigSchematics);
  const configuredEngine = String(config.get("projectEngine") || "auto");
  const projectEngine: ProjectEngineSetting = ["auto", "unity", "unreal", "mixed"].includes(configuredEngine)
    ? configuredEngine as ProjectEngineSetting : "auto";
  const maxOutputReserve = numeric(config.get("maxOutputReserve"), 8192, 256, 131072);
  const configuredRecoveryMaxTokens = numeric(config.get("outputRecoveryMaxTokens"), 0, 0, 131072);
  const workingInputTargetTokens = numeric(config.get("workingInputTargetTokens"), 18000, 2048, 131072);
  return {
    projectEngine,
    projectIdentity: String(config.get("projectIdentity") || "").trim().slice(0, 4096),
    observeOnly: config.get("observeOnly") === true,
    contextManagementMode: contextManagementMode(config.get("contextManagementMode")),
    workingInputTargetTokens,
    workingInputTriggerTokens: Math.max(workingInputTargetTokens,
      numeric(config.get("workingInputTriggerTokens"), 22000, 2048, 262144)),
    toolResultProjectionChars: numeric(config.get("toolResultProjectionChars"), 512, 128, 8192),
    semanticSummaryMaxTokens: numeric(config.get("semanticSummaryMaxTokens"), 1024, 128, 8192),
    semanticSummarySeconds: numeric(config.get("semanticSummarySeconds"), 30, 1, 300),
    showDebugInfo: config.get("showDebugInfo") !== false,
    reviewProgress: config.get("reviewProgress") === true,
    pastReasoningTokens: numeric(config.get("pastReasoningTokens"), 0, 0, 16384),
    softRemainingTokens: numeric(config.get("softRemainingTokens"), 6000, 0, 1_000_000),
    hardRemainingTokens: numeric(config.get("hardRemainingTokens"), 3000, 0, 1_000_000),
    maxOutputReserve,
    outputRecoveryMode: outputRecoveryMode(config.get("outputRecoveryMode")),
    outputRecoveryMaxTokens: configuredRecoveryMaxTokens > 0 ? configuredRecoveryMaxTokens : maxOutputReserve,
    outputRecoverySeconds: numeric(config.get("outputRecoverySeconds"), 90, 1, 600),
    safetyMarginTokens: numeric(config.get("safetyMarginTokens"), 2048, 0, 131072),
    assumedContextLength: numeric(config.get("assumedContextLength"), 38912, 2048, 4_000_000),
    recentCompleteTurns: numeric(config.get("recentCompleteTurns"), 2, 0, 20),
    compactAboveMessageCount: numeric(config.get("compactAboveMessageCount"), 24, 4, 10000),
    maxCheckpointChars: numeric(config.get("maxCheckpointChars"), 22000, 2000, 100000),
    maxToolResultChars: numeric(config.get("maxToolResultChars"), 1200, 200, 10000),
    toolStagnationAction: stagnationAction(config.get("toolStagnationAction")),
    toolStagnationRounds: numeric(config.get("toolStagnationRounds"), 3, 2, 20),
    generationRepetitionAction: stagnationAction(config.get("generationRepetitionAction")),
    generationRepeatCount: numeric(config.get("generationRepeatCount"), 3, 2, 10),
    inputAvailabilityMode: inputAvailabilityMode(config.get("inputAvailabilityMode")),
    auditCompletionMode: auditCompletionMode(config.get("auditCompletionMode")),
    auditResearchSeconds: numeric(config.get("auditResearchSeconds"), 100, 1, 3600),
    auditResearchRounds: numeric(config.get("auditResearchRounds"), 12, 1, 100),
    auditFinalSeconds: numeric(config.get("auditFinalSeconds"), 70, 1, 600),
    auditFinalMaxTokens: numeric(config.get("auditFinalMaxTokens"), 4096, 256, 32768),
  };
}

/** Saved settings and runtime fallback share these values. Never migrate an
 * explicit Off/0/Documents selection merely because an effective policy differs. */
export const GUIDANCE_DEFAULTS = Object.freeze({ mode: "off", delivery: "documents", maxTokens: 6144 } as const);
export const REPORT_REVIEW_DEFAULT = "on";
export const CONTEXT_DEFAULTS = Object.freeze({ mode: "hybrid", target: 18000, trigger: 22000, scaling: "auto",
  projectionChars: 512, summaryTokens: 1024, summarySeconds: 30, semanticHandoff: false } as const);
/** Single source for every numeric setting: the schema default shown in the
 * UI and the runtime fallback/bounds applied when a saved value is missing or
 * invalid. Add a numeric setting here once; never repeat its literals. */
const field = (value: number, min: number, max: number) => Object.freeze({ default: value, min, max });
export const NUMERIC_FIELDS = Object.freeze({
  designGuidanceMaxTokens: field(GUIDANCE_DEFAULTS.maxTokens, 0, 8192),
  workingInputTargetTokens: field(CONTEXT_DEFAULTS.target, 2048, 131072),
  workingInputTriggerTokens: field(CONTEXT_DEFAULTS.trigger, 2048, 262144),
  toolResultProjectionChars: field(CONTEXT_DEFAULTS.projectionChars, 128, 8192),
  semanticSummaryMaxTokens: field(CONTEXT_DEFAULTS.summaryTokens, 128, 8192),
  semanticSummarySeconds: field(CONTEXT_DEFAULTS.summarySeconds, 1, 300),
  softRemainingTokens: field(6000, 0, 1_000_000),
  hardRemainingTokens: field(3000, 0, 1_000_000),
  maxOutputReserve: field(8192, 256, 131072),
  contextOutputMaxTokens: field(16384, 256, 131072),
  outputRecoveryMaxTokens: field(0, 0, 131072),
  // Retained only for reading old saved configurations; report continuation
  // follows ordinary generation cancellation and does not use this value.
  outputRecoverySeconds: field(0, 0, 600),
  safetyMarginTokens: field(2048, 0, 131072),
  assumedContextLength: field(38912, 2048, 4_000_000),
  recentCompleteTurns: field(2, 0, 20),
  compactAboveMessageCount: field(24, 4, 10000),
  maxCheckpointChars: field(22000, 2000, 100000),
  maxToolResultChars: field(1200, 200, 10000),
  pastReasoningTokens: field(0, 0, 16384),
  maxResearchRounds: field(60, 0, 1000),
  toolStagnationRounds: field(3, 2, 20),
  noNewEvidenceRounds: field(3, 1, 20),
  generationRepeatCount: field(3, 2, 10),
  auditResearchSeconds: field(100, 1, 3600),
  auditResearchRounds: field(12, 1, 100),
  auditFinalSeconds: field(70, 1, 600),
  auditFinalMaxTokens: field(4096, 256, 32768),
});
export type NumericField = keyof typeof NUMERIC_FIELDS;
/** Percentage budget: shares of the loaded context length C. One source for
 * the schema defaults and the runtime bounds. Initial values are proposals to
 * be tuned by measurement: at C=84,224 they land within about 1.4K tokens of
 * what "auto" scaling applied in the recorded runs. */
export const PERCENT_FIELDS = Object.freeze({
  contextTargetPercent: field(55, 10, 90),
  contextTriggerPercent: field(75, 20, 95),
  contextOutputPercent: field(10, 1, 50),
  contextSafetyPercent: field(2, 0.5, 20),
});
export type PercentField = keyof typeof PERCENT_FIELDS;
/** "legacy" keeps the absolute target/trigger with their Auto/Fixed scaling, so
 * installing a new build changes nothing until the user selects percentage. */
export const CONTEXT_BUDGET_DEFAULT = "legacy";
export const { SUMMARY_POLICY } = require("./semantic-handoff-policy.js") as {
  SUMMARY_POLICY: Readonly<{ maxCalls: number; maxElapsedMs: number; failureCooldownRounds: number; retainedEvents: number }>
};

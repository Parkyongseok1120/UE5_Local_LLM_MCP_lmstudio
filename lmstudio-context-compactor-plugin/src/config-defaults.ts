/** Saved settings and runtime fallback share these values. Never migrate an
 * explicit Off/0/Documents selection merely because an effective policy differs. */
export const GUIDANCE_DEFAULTS = Object.freeze({ mode: "off", delivery: "documents", maxTokens: 2048 } as const);
export const CONTEXT_DEFAULTS = Object.freeze({ mode: "hybrid", target: 18000, trigger: 22000,
  projectionChars: 512, summaryTokens: 1024, summarySeconds: 30 } as const);
export const { SUMMARY_POLICY } = require("./semantic-handoff-policy.js") as {
  SUMMARY_POLICY: Readonly<{ maxCalls: number; maxElapsedMs: number; failureCooldownRounds: number; retainedEvents: number }>
};

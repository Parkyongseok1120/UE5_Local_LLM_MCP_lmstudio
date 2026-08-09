import { createConfigSchematics } from "@lmstudio/sdk";

export const configSchematics = createConfigSchematics()
  .field(
    "enabled",
    "boolean",
    { displayName: "Enable context compaction", subtitle: "Compact the model-facing history while retaining the visible LM Studio chat." },
    true,
  )
  .field(
    "observeOnly",
    "boolean",
    { displayName: "Observe only", subtitle: "Measure and persist checkpoints without changing the model-facing history." },
    false,
  )
  .field(
    "bufferUntilPredictionComplete",
    "boolean",
    { displayName: "Atomic output", subtitle: "Buffer output until prediction completion. It is enforced when truncated-output rejection or required checkpoint persistence is enabled." },
    true,
  )
  .field(
    "rejectTruncatedPredictions",
    "boolean",
    { displayName: "Reject truncated output", subtitle: "Discard output stopped by the context or max-token limit instead of presenting a partial result as complete." },
    true,
  )
  .field(
    "requireCheckpointPersistence",
    "boolean",
    { displayName: "Require checkpoint persistence", subtitle: "Fail before generation when the durable checkpoint cannot be written." },
    true,
  )
  .field(
    "strictToolControlPlane",
    "boolean",
    { displayName: "Strict tool control plane", subtitle: "Optional tool-call rejection guard. Off by default so existing LM Studio MCP behavior is preserved." },
    false,
  )
  .field(
    "targetModel",
    "string",
    { displayName: "Underlying model key", subtitle: "Optional when exactly one LLM is loaded; otherwise enter its exact LM Studio model key." },
    "",
  )
  .field("softRemainingTokens", "numeric", { displayName: "Soft threshold", subtitle: "Compact before the next model call below this remaining-token count." }, 14000)
  .field("hardRemainingTokens", "numeric", { displayName: "Hard threshold", subtitle: "Force deterministic checkpoint compaction below this remaining-token count." }, 8000)
  .field("maxOutputReserve", "numeric", { displayName: "Output reserve", subtitle: "Tokens reserved for the next model response." }, 4096)
  .field(
    "architectureMaxOutputReserve",
    "numeric",
    { displayName: "Architecture output reserve", subtitle: "Minimum output budget for structured architecture/design validation calls." },
    8192,
  )
  .field("safetyMarginTokens", "numeric", { displayName: "Safety margin", subtitle: "Extra reserve for token-estimation and prompt-template variance." }, 1024)
  .field("temperature", "numeric", { displayName: "Temperature", subtitle: "Sampling temperature used by the underlying model proxy (0 to 1)." }, 0.1)
  .field("normalToolResultReserve", "numeric", { displayName: "Normal tool reserve", subtitle: "Tokens reserved for ordinary tool results." }, 3000)
  .field("buildToolResultReserve", "numeric", { displayName: "Build tool reserve", subtitle: "Tokens reserved for build and compiler output." }, 8000)
  .field("recentCompleteTurns", "numeric", { displayName: "Recent turns", subtitle: "Complete recent turns retained verbatim after compaction. Goal changes force 0 retained turns." }, 1)
  .field("minimumTurnsBetweenCompactions", "numeric", { displayName: "Minimum turns between compactions", subtitle: "Soft compaction waits for this many new messages; hard compaction never waits." }, 0)
  .field("targetRemainingTokensAfterCompaction", "numeric", { displayName: "Post-compaction target", subtitle: "Reduce the retained tail until this many tokens remain when possible." }, 24000)
  .build();

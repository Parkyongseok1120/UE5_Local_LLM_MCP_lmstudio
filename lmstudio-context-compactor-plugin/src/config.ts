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
  .field("softRemainingTokens", "numeric", { displayName: "Soft threshold", subtitle: "Compact before the next model call below this remaining-token count." }, 10000)
  .field("hardRemainingTokens", "numeric", { displayName: "Hard threshold", subtitle: "Force deterministic checkpoint compaction below this remaining-token count." }, 5000)
  .field("maxOutputReserve", "numeric", { displayName: "Output reserve", subtitle: "Tokens reserved for the next model response." }, 4096)
  .field("temperature", "numeric", { displayName: "Temperature", subtitle: "Sampling temperature used by the underlying model proxy (0 to 1)." }, 0.1)
  .field("normalToolResultReserve", "numeric", { displayName: "Normal tool reserve", subtitle: "Tokens reserved for ordinary tool results." }, 3000)
  .field("buildToolResultReserve", "numeric", { displayName: "Build tool reserve", subtitle: "Tokens reserved for build and compiler output." }, 8000)
  .field("recentCompleteTurns", "numeric", { displayName: "Recent turns", subtitle: "Complete recent turns retained verbatim after compaction." }, 6)
  .field("minimumTurnsBetweenCompactions", "numeric", { displayName: "Minimum turns between compactions", subtitle: "Soft compaction waits for this many new messages; hard compaction never waits." }, 3)
  .field("targetRemainingTokensAfterCompaction", "numeric", { displayName: "Post-compaction target", subtitle: "Reduce the retained tail until this many tokens remain when possible." }, 20000)
  .build();

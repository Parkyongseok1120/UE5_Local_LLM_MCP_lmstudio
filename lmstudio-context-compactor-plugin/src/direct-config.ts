import { createConfigSchematics } from "@lmstudio/sdk";

export const directConfigSchematics = createConfigSchematics()
  .field(
    "enabled",
    "boolean",
    { displayName: "Enable transparent compaction", subtitle: "Explicit opt-in: compact older chat context while the model selected in LM Studio remains the reasoning and tool-use owner." },
    false,
  )
  .field(
    "observeOnly",
    "boolean",
    { displayName: "Observe only", subtitle: "Measure context pressure without changing the model-facing history." },
    false,
  )
  .field("softRemainingTokens", "numeric", { displayName: "Soft threshold", subtitle: "Compact when estimated remaining context falls below this value." }, 14000)
  .field("hardRemainingTokens", "numeric", { displayName: "Hard threshold", subtitle: "At this threshold retain only the current user turn plus factual memory." }, 8000)
  .field("maxOutputReserve", "numeric", { displayName: "Output reserve", subtitle: "Tokens reserved for the selected model's next response." }, 4096)
  .field("safetyMarginTokens", "numeric", { displayName: "Safety margin", subtitle: "Extra allowance for prompt-template and token-estimation variance." }, 1024)
  .field("assumedContextLength", "numeric", { displayName: "Fallback context length", subtitle: "Used only when the selected token source cannot report its context length." }, 32768)
  .field("recentCompleteTurns", "numeric", { displayName: "Recent complete turns", subtitle: "Prior completed user turns retained verbatim after soft compaction." }, 2)
  .field("compactAboveMessageCount", "numeric", { displayName: "Fallback message threshold", subtitle: "Used only when exact token measurement is unavailable." }, 24)
  .field("maxCheckpointChars", "numeric", { displayName: "Memory size", subtitle: "Maximum deterministic factual-memory characters." }, 12000)
  .field("maxToolResultChars", "numeric", { displayName: "Tool summary size", subtitle: "Maximum characters retained for each older tool outcome." }, 1200)
  .build();

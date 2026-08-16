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
    "streamReasoningProgress",
    "boolean",
    { displayName: "Stream reasoning progress", subtitle: "Show model reasoning fragments immediately while final text and tool calls remain atomically buffered." },
    true,
  )
  .field(
    "predictionHeartbeatSeconds",
    "numeric",
    { displayName: "Prediction heartbeat", subtitle: "Emit a reasoning-status heartbeat after this many silent seconds during local-model inference (1-30 seconds)." },
    4,
  )
  .field(
    "predictionWallClockSeconds",
    "numeric",
    { displayName: "Prediction wall-clock limit", subtitle: "Cancel and discard a model prediction after this many total seconds (5-1800 seconds)." },
    180,
  )
  .field(
    "predictionNoProgressSeconds",
    "numeric",
    { displayName: "Prediction no-progress limit", subtitle: "Cancel and discard a prediction after this many seconds without a non-empty token or tool-call callback (5-300 seconds). Heartbeats do not count as progress." },
    45,
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
  .field(
    "reasoningEffort",
    "string",
    { displayName: "Reasoning effort", subtitle: "Server-owned Qwen3.8 effort: low, medium, or xhigh. Applied to every proxied prediction instead of relying on the direct-chat model control." },
    "low",
  )
  .field(
    "modelReadinessTimeoutSeconds",
    "numeric",
    { displayName: "Model readiness timeout", subtitle: "Wait this many seconds for exactly one unloaded/starting LM Studio model to become ready while preserving the task checkpoint." },
    120,
  )
  .field(
    "modelReadinessPollIntervalSeconds",
    "numeric",
    { displayName: "Model readiness poll interval", subtitle: "Seconds between bounded loaded-model readiness checks." },
    2,
  )
  .field("softRemainingTokens", "numeric", { displayName: "Soft threshold", subtitle: "Compact before the next model call below this remaining-token count." }, 14000)
  .field("hardRemainingTokens", "numeric", { displayName: "Hard threshold", subtitle: "Force deterministic checkpoint compaction below this remaining-token count." }, 8000)
  .field("maxOutputReserve", "numeric", { displayName: "Output reserve", subtitle: "Tokens reserved for the next model response." }, 4096)
  .field(
    "architectureMaxOutputReserve",
    "numeric",
    { displayName: "Architecture output reserve", subtitle: "Minimum output budget for structured architecture/design validation calls." },
    6144,
  )
  .field(
    "synthesisMaxOutputReserve",
    "numeric",
    { displayName: "Synthesis output reserve", subtitle: "Output budget for tool-free evidence synthesis/final responses (minimum 8192 tokens)." },
    8192,
  )
  .field(
    "toolCallMaxOutputReserve",
    "numeric",
    { displayName: "Tool-call output reserve", subtitle: "Output budget while tool schemas are available (minimum 6144 tokens)." },
    6144,
  )
  .field(
    "architectureEvidenceReadThreshold",
    "numeric",
    { displayName: "Architecture evidence reads", subtitle: "Minimum unique successful direct-source reads before forcing architecture validation; implementation evidence is also required." },
    4,
  )
  .field(
    "architectureEvidenceHardLimit",
    "numeric",
    { displayName: "Architecture evidence hard limit", subtitle: "Force validation after this many unique reads even when source-file types cannot be classified." },
    8,
  )
  .field(
    "architectureReplanEvidenceReadBudget",
    "numeric",
    { displayName: "Architecture replan evidence reads", subtitle: "Bounded direct-source reads reopened after a rejected full replan before validation is forced again." },
    4,
  )
  .field(
    "preRouteDiscoveryLimit",
    "numeric",
    { displayName: "Pre-route discovery limit", subtitle: "Force one unreal_agent_plan handoff after this many successful discovery calls on a write request without a server-owned task route." },
    6,
  )
  .field(
    "durableInspectionDiscoveryLimit",
    "numeric",
    { displayName: "Durable inspection discovery limit", subtitle: "Force an inspect-only task route after this many project-source discovery calls for complex read-only audits." },
    2,
  )
  .field("safetyMarginTokens", "numeric", { displayName: "Safety margin", subtitle: "Extra reserve for token-estimation and prompt-template variance." }, 1024)
  .field("temperature", "numeric", { displayName: "Temperature", subtitle: "Sampling temperature pinned by the server-owned model proxy (0 to 1)." }, 0.1)
  .field("topPSampling", "numeric", { displayName: "Top P sampling", subtitle: "Nucleus-sampling threshold pinned for every proxied prediction (0 to 1)." }, 0.85)
  .field("topKSampling", "numeric", { displayName: "Top K sampling", subtitle: "Maximum candidate-token set pinned for every proxied prediction (1 to 1000)." }, 20)
  .field("minPSampling", "numeric", { displayName: "Min P sampling", subtitle: "Minimum probability threshold pinned for every proxied prediction (0 to 1; 0 disables filtering)." }, 0)
  .field("normalToolResultReserve", "numeric", { displayName: "Normal tool reserve", subtitle: "Tokens reserved for ordinary tool results." }, 3000)
  .field("buildToolResultReserve", "numeric", { displayName: "Build tool reserve", subtitle: "Tokens reserved for build and compiler output." }, 8000)
  .field("recentCompleteTurns", "numeric", { displayName: "Recent turns", subtitle: "Complete recent turns retained verbatim after compaction. Goal changes force 0 retained turns." }, 1)
  .field(
    "maxCurrentTurnMessages",
    "numeric",
    { displayName: "Current-turn message cap", subtitle: "Soft-compact the active tool/reasoning turn once it exceeds this many non-system messages (0 disables the cap). Complete tool-call/result pairs are retained." },
    12,
  )
  .field("minimumTurnsBetweenCompactions", "numeric", { displayName: "Minimum turns between compactions", subtitle: "Soft compaction waits for this many new messages; hard compaction never waits." }, 0)
  .field("targetRemainingTokensAfterCompaction", "numeric", { displayName: "Post-compaction target", subtitle: "Reduce the retained tail until this many tokens remain when possible." }, 24000)
  .build();

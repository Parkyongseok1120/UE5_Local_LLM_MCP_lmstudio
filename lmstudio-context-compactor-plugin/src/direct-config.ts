import { createConfigSchematics } from "@lmstudio/sdk";

export const directConfigSchematics = createConfigSchematics()
  .field(
    "projectEngine",
    "select",
    {
      displayName: "Project engine",
      subtitle: "Filters project tools before the model sees them. Auto verifies configured paths, paths mentioned in chat, and workspace markers.",
      options: [
        { value: "auto", displayName: "Auto (verified project path)" },
        { value: "unity", displayName: "Unity" },
        { value: "unreal", displayName: "Unreal Engine" },
        { value: "mixed", displayName: "Both (explicit mixed workspace)" },
      ],
    },
    "auto",
  )
  .field(
    "projectIdentity",
    "string",
    {
      displayName: "Project identity",
      subtitle: "Optional exact Unity root, Unreal .uproject path, or exact Unreal project name. Project-capable tools are bound to this value; Unity rejects a different server root.",
      placeholder: "C:\\Projects\\Game\\Game.uproject",
    },
    "",
  )
  .field(
    "observeOnly",
    "boolean",
    { displayName: "Observe only", subtitle: "Measure context pressure without changing the model-facing history." },
    false,
  )
  .field("contextManagementMode", "select", {
    displayName: "Working context",
    subtitle: "Hybrid is the installed default: deterministic scoped evidence archive, bounded result views, measured working window, and at most one tool-free local semantic handoff per accepted compaction. Legacy remains available for an explicit compatibility choice.",
    options: [
      { value: "hybrid", displayName: "Hybrid (default)" },
      { value: "deterministic", displayName: "Deterministic archive/window" },
      { value: "legacy", displayName: "Legacy (compatibility)" },
    ],
  }, "hybrid")
  .field("workingInputTargetTokens", "numeric", { displayName: "Working input target", subtitle: "Full templated input target after compaction, including system and tool definitions. Mandatory input is never removed merely to meet it." }, 18000)
  .field("workingInputTriggerTokens", "numeric", { displayName: "Working input trigger", subtitle: "Start target-based compaction above this full templated input size; kept separate from the post-compaction target." }, 22000)
  .field("toolResultProjectionChars", "numeric", { displayName: "Archived result excerpt", subtitle: "Maximum archived observation excerpt exposed in a model-facing tool-result projection." }, 512)
  .field("semanticSummaryMaxTokens", "numeric", { displayName: "Semantic handoff output", subtitle: "Maximum output tokens for the single tool-free local summary call per accepted compaction." }, 1024)
  .field("semanticSummarySeconds", "numeric", { displayName: "Semantic handoff seconds", subtitle: "Timeout for the optional local summary call. Invalid or interrupted summaries are discarded." }, 30)
  .field(
    "showDebugInfo",
    "boolean",
    { displayName: "Show debug info", subtitle: "Show per-round context measurements in the chat." },
    true,
  )
  .field("softRemainingTokens", "numeric", { displayName: "Soft threshold", subtitle: "Compact when remaining context after the applied output cap and safety margin falls below this value." }, 6000)
  .field("hardRemainingTokens", "numeric", { displayName: "Hard threshold", subtitle: "At this remaining-context threshold, retain the current request, newest unread tool exchange, and factual memory." }, 3000)
  .field("maxOutputReserve", "numeric", { displayName: "Output reserve", subtitle: "Tokens reserved for the selected model's next response and used as its explicit generation cap." }, 8192)
  .field("outputRecoveryMode", "select", {
    displayName: "Output-limit recovery",
    subtitle: "After a normal visible report hits its explicit output cap, allow at most one concise tool-free rewrite from the evidence already collected.",
    options: [
      { value: "on", displayName: "On (one rewrite)" },
      { value: "off", displayName: "Off" },
    ],
  }, "on")
  .field("outputRecoveryMaxTokens", "numeric", { displayName: "Recovery output limit", subtitle: "Maximum predicted tokens for the single normal-mode tool-free rewrite; 0 uses the general output reserve." }, 0)
  .field("outputRecoverySeconds", "numeric", { displayName: "Recovery seconds", subtitle: "Maximum time for the single normal-mode tool-free rewrite." }, 90)
  .field("safetyMarginTokens", "numeric", { displayName: "Safety margin", subtitle: "Extra allowance for prompt-template and token-estimation variance." }, 2048)
  .field("assumedContextLength", "numeric", { displayName: "Fallback context length", subtitle: "38,912-token trial fallback used only when the selected token source cannot report its loaded context length; it never overrides a reported value." }, 38912)
  .field("recentCompleteTurns", "numeric", { displayName: "Recent complete turns", subtitle: "Prior completed user turns retained verbatim after soft compaction." }, 2)
  .field("compactAboveMessageCount", "numeric", { displayName: "Fallback message threshold", subtitle: "Used only when exact token measurement is unavailable." }, 24)
  .field("maxCheckpointChars", "numeric", { displayName: "Memory size", subtitle: "Maximum deterministic factual-memory characters." }, 22000)
  .field("maxToolResultChars", "numeric", { displayName: "Tool summary size", subtitle: "Maximum characters retained for each older tool outcome." }, 1200)
  .field("pastReasoningTokens", "numeric", { displayName: "Past reasoning budget (experimental)", subtitle: "0 keeps current behavior. Under context pressure, retain older SDK-delimited reasoning up to this token budget; preserve the latest exchange." }, 0)
  .field("toolStagnationAction", "select", {
    displayName: "Repeated tool rounds",
    subtitle: "Warn or pause after equivalent tool-call/result rounds. Pagination cursors and changed evidence reset the count.",
    options: [
      { value: "warn", displayName: "Warn" },
      { value: "pause", displayName: "Pause" },
      { value: "off", displayName: "Off" },
    ],
  }, "warn")
  .field("toolStagnationRounds", "numeric", { displayName: "Tool repetition threshold", subtitle: "Equivalent consecutive tool rounds before warning or pausing." }, 3)
  .field("generationRepetitionAction", "select", {
    displayName: "Within-generation repetition",
    subtitle: "Warn or pause when a substantial generated text block repeats consecutively.",
    options: [
      { value: "warn", displayName: "Warn" },
      { value: "pause", displayName: "Pause" },
      { value: "off", displayName: "Off" },
    ],
  }, "warn")
  .field("generationRepeatCount", "numeric", { displayName: "Text repetition threshold", subtitle: "Consecutive copies required; each repeated block must be at least 80 characters." }, 3)
  .field("inputAvailabilityMode", "select", {
    displayName: "Current input availability",
    subtitle: "Observe records final SDK input facts without changing the prompt. Inject adds the same bounded facts for an A/B experiment; it never blocks or selects tools.",
    options: [
      { value: "observe", displayName: "Observe (recommended)" },
      { value: "inject", displayName: "Inject facts (experimental)" },
      { value: "off", displayName: "Off" },
    ],
  }, "observe")
  .field("auditCompletionMode", "select", {
    displayName: "Bounded audit completion",
    subtitle: "Optional read-only audit budget. When its research budget ends, the same model gets at most one tool-free opportunity to report from the evidence already collected.",
    options: [
      { value: "off", displayName: "Off (default)" },
      { value: "bounded", displayName: "Bounded read-only audit (experimental)" },
    ],
  }, "off")
  .field("auditResearchSeconds", "numeric", { displayName: "Audit research seconds", subtitle: "Maximum research time before the optional tool-free report opportunity." }, 100)
  .field("auditResearchRounds", "numeric", { displayName: "Audit research rounds", subtitle: "Maximum completed research prediction rounds before the optional report opportunity." }, 12)
  .field("auditFinalSeconds", "numeric", { displayName: "Audit report seconds", subtitle: "Maximum time for the single tool-free report opportunity." }, 70)
  .field("auditFinalMaxTokens", "numeric", { displayName: "Audit report output limit", subtitle: "Predicted-token limit for the single tool-free report opportunity." }, 4096)
  .field("separateAttachments", "boolean", { displayName: "Separate document input (experimental)", subtitle: "Requires this preprocessor before document RAG. Preserve typed document references instead of injecting the full document." }, false)
  .field("reviewProgress", "boolean", { displayName: "Review continuity notes (experimental)", subtitle: "Allow bounded assistant review claims tied to observed file versions. Claims are not verified completion." }, false)
  .build();

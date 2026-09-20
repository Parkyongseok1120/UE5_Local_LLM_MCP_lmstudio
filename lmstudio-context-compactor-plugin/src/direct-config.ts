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
  .field(
    "showDebugInfo",
    "boolean",
    { displayName: "Show debug info", subtitle: "Show per-round context measurements in the chat." },
    true,
  )
  .field("softRemainingTokens", "numeric", { displayName: "Soft threshold", subtitle: "Compact when estimated remaining context falls below this value." }, 14000)
  .field("hardRemainingTokens", "numeric", { displayName: "Hard threshold", subtitle: "At this threshold retain the current request, the newest unread tool exchange, and factual memory." }, 8000)
  .field("maxOutputReserve", "numeric", { displayName: "Output reserve", subtitle: "Tokens reserved for the selected model's next response." }, 8192)
  .field("safetyMarginTokens", "numeric", { displayName: "Safety margin", subtitle: "Extra allowance for prompt-template and token-estimation variance." }, 1536)
  .field("assumedContextLength", "numeric", { displayName: "Fallback context length", subtitle: "Used only when the selected token source cannot report its context length." }, 65536)
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
  .field("separateAttachments", "boolean", { displayName: "Separate document input (experimental)", subtitle: "Requires this preprocessor before document RAG. Preserve typed document references instead of injecting the full document." }, false)
  .field("reviewProgress", "boolean", { displayName: "Review continuity notes (experimental)", subtitle: "Allow bounded assistant review claims tied to observed file versions. Claims are not verified completion." }, false)
  .build();

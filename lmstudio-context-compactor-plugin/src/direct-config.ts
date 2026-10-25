import { createConfigSchematics } from "@lmstudio/sdk";
import { GUIDANCE_DEFAULTS, CONTEXT_DEFAULTS, NUMERIC_FIELDS, REPORT_REVIEW_DEFAULT } from "./config-defaults";

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
  .field("designGuidanceMode", "select", {
    displayName: "Design references",
    subtitle: "Optional bundled guidance. Adds the common core and selected topic, plus the verified/configured engine. Never gates tools or replaces project evidence; omitted when it does not fit.",
    options: [
      { value: "off", displayName: "Off (default)" },
      { value: "auto", displayName: "Auto (focused, no extra tool calls)" },
      { value: "core", displayName: "Common core" },
      { value: "design", displayName: "SSOT, SOLID and patterns" },
      { value: "debugging", displayName: "Debugging and API contracts" },
      { value: "code-style", displayName: "Formatting and readability" },
      { value: "lifecycle", displayName: "Engine ownership and lifetime" },
      { value: "multiplayer", displayName: "Multiplayer contracts" },
    ],
  }, GUIDANCE_DEFAULTS.mode)
  .field("designGuidanceDelivery", "select", {
    displayName: "Design reference delivery",
    subtitle: "Documents preserves existing delivery. Focused selects complete sections and bounded returned observations. Auto always uses Focused while preserving this saved choice; adds no tool calls or execution gates.",
    options: [
      { value: "documents", displayName: "Documents (compatible default)" },
      { value: "focused", displayName: "Focused sections and observations" },
    ],
  }, GUIDANCE_DEFAULTS.delivery)
  .field("designGuidanceMaxTokens", "numeric", {
    displayName: "Design reference token allowance",
    subtitle: "Maximum measured increase in the final input (default 2048). Existing working-context and tool-result budgets also apply. 0 disables references.",
  }, NUMERIC_FIELDS.designGuidanceMaxTokens.default)
  .field("contextManagementMode", "select", {
    displayName: "Working context",
    subtitle: "Hybrid is the installed default: deterministic scoped evidence archive, bounded result views and a measured working window. The optional semantic handoff below is separate and Off by default. Legacy remains available for an explicit compatibility choice.",
    options: [
      { value: "hybrid", displayName: "Hybrid (default)" },
      { value: "deterministic", displayName: "Deterministic archive/window" },
      { value: "legacy", displayName: "Legacy (compatibility)" },
    ],
  }, CONTEXT_DEFAULTS.mode)
  .field("workingInputTargetTokens", "numeric", { displayName: "Working input target", subtitle: "Full templated input target after compaction, including system and tool definitions. Mandatory input is never removed merely to meet it." }, NUMERIC_FIELDS.workingInputTargetTokens.default)
  .field("workingInputTriggerTokens", "numeric", { displayName: "Working input trigger", subtitle: "Start target-based compaction above this full templated input size; kept separate from the post-compaction target." }, NUMERIC_FIELDS.workingInputTriggerTokens.default)
  .field("workingInputScaling", "select", {
    displayName: "Working input scaling",
    subtitle: "Auto treats the trigger and target as minimums and raises them with the loaded context (86%/65% of the usable input), keeping working space above the mandatory floor. Fixed uses the configured absolute values.",
    options: [
      { value: "auto", displayName: "Auto (scale with context)" },
      { value: "fixed", displayName: "Fixed (absolute values)" },
    ],
  }, CONTEXT_DEFAULTS.scaling)
  .field("toolResultProjectionChars", "numeric", { displayName: "Archived result excerpt", subtitle: "Maximum archived observation excerpt exposed in a model-facing tool-result projection." }, NUMERIC_FIELDS.toolResultProjectionChars.default)
  .field("semanticHandoff", "boolean", { displayName: "Semantic handoff (experimental)", subtitle: "Hybrid only. After an accepted compaction, make one tool-free local summary call. Off by default: recorded runs never accepted a summary and each attempt blocked until its timeout. Deterministic checkpoints keep the prior answer and decisions without it." }, CONTEXT_DEFAULTS.semanticHandoff)
  .field("semanticSummaryMaxTokens", "numeric", { displayName: "Semantic handoff output", subtitle: "Maximum output tokens for the single tool-free local summary call per accepted compaction." }, NUMERIC_FIELDS.semanticSummaryMaxTokens.default)
  .field("semanticSummarySeconds", "numeric", { displayName: "Semantic handoff seconds", subtitle: "Timeout for the optional local summary call. Invalid or interrupted summaries are discarded." }, NUMERIC_FIELDS.semanticSummarySeconds.default)
  .field(
    "showDebugInfo",
    "boolean",
    { displayName: "Show debug info", subtitle: "Show per-round context measurements in the chat." },
    true,
  )
  .field("softRemainingTokens", "numeric", { displayName: "Soft threshold", subtitle: "Compact when remaining context after the applied output cap and safety margin falls below this value." }, NUMERIC_FIELDS.softRemainingTokens.default)
  .field("hardRemainingTokens", "numeric", { displayName: "Hard threshold", subtitle: "At this remaining-context threshold, retain the current request, newest unread tool exchange, and factual memory." }, NUMERIC_FIELDS.hardRemainingTokens.default)
  .field("maxOutputReserve", "numeric", { displayName: "Output reserve", subtitle: "Tokens reserved for the selected model's next response and used as its explicit generation cap." }, NUMERIC_FIELDS.maxOutputReserve.default)
  .field("outputRecoveryMode", "select", {
    displayName: "Output-limit recovery",
    subtitle: "Continue a cut-off visible report once, preserving its existing text. Uses the normal cancellation and output limits without a separate rewrite timer.",
    options: [
      { value: "on", displayName: "On (one continuation)" },
      { value: "off", displayName: "Off" },
    ],
  }, "on")
  .field("reportReviewMode", "select", {
    displayName: "Report review",
    subtitle: "Review omitted working findings and engine-contract gaps once with no tools, appending a supplemental section.",
    options: [{ value: "on", displayName: "On (one review)" }, { value: "off", displayName: "Off" }],
  }, REPORT_REVIEW_DEFAULT)
  .field("outputRecoveryMaxTokens", "numeric", { displayName: "Continuation output limit", subtitle: "Maximum predicted tokens for one tool-free report continuation; 0 uses the general output reserve." }, NUMERIC_FIELDS.outputRecoveryMaxTokens.default)
  .field("reasoningRecoveryMode", "select", {
    displayName: "Reasoning-limit recovery",
    subtitle: "If a normal generation uses its output cap entirely before an answer or tool request, retry once with the same permitted tools and up to twice the output cap (limited by the measured context headroom, never below the cap). No report-rewrite timeout is applied. Missing saved values default to On; bounded audit and observe-only are excluded.",
    options: [
      { value: "on", displayName: "On (one focused retry)" },
      { value: "off", displayName: "Off" },
    ],
  }, "on")
  .field("safetyMarginTokens", "numeric", { displayName: "Safety margin", subtitle: "Extra allowance for prompt-template and token-estimation variance." }, NUMERIC_FIELDS.safetyMarginTokens.default)
  .field("assumedContextLength", "numeric", { displayName: "Fallback context length", subtitle: "38,912-token trial fallback used only when the selected token source cannot report its loaded context length; it never overrides a reported value." }, NUMERIC_FIELDS.assumedContextLength.default)
  .field("recentCompleteTurns", "numeric", { displayName: "Recent complete turns", subtitle: "Prior completed user turns retained verbatim after soft compaction." }, NUMERIC_FIELDS.recentCompleteTurns.default)
  .field("compactAboveMessageCount", "numeric", { displayName: "Fallback message threshold", subtitle: "Used only when exact token measurement is unavailable." }, NUMERIC_FIELDS.compactAboveMessageCount.default)
  .field("maxCheckpointChars", "numeric", { displayName: "Memory size", subtitle: "Maximum deterministic factual-memory characters." }, NUMERIC_FIELDS.maxCheckpointChars.default)
  .field("maxToolResultChars", "numeric", { displayName: "Tool summary size", subtitle: "Maximum characters retained for each older tool outcome." }, NUMERIC_FIELDS.maxToolResultChars.default)
  .field("pastReasoningTokens", "numeric", { displayName: "Past reasoning budget (experimental)", subtitle: "0 keeps current behavior. Under context pressure, retain older SDK-delimited reasoning up to this token budget; preserve the latest exchange." }, NUMERIC_FIELDS.pastReasoningTokens.default)
  .field("toolStagnationAction", "select", {
    displayName: "Repeated tool rounds",
    subtitle: "Warn or pause after equivalent tool-call/result rounds. Pagination cursors and changed evidence reset the count.",
    options: [
      { value: "warn", displayName: "Warn" },
      { value: "pause", displayName: "Pause" },
      { value: "off", displayName: "Off" },
    ],
  }, "warn")
  .field("maxResearchRounds", "numeric", { displayName: "Maximum tool rounds", subtitle: "Tool rounds in one response before a tool-free report from the evidence collected. 0 removes the limit." }, NUMERIC_FIELDS.maxResearchRounds.default)
  .field("toolStagnationRounds", "numeric", { displayName: "Tool repetition threshold", subtitle: "Equivalent consecutive tool rounds before warning or pausing." }, NUMERIC_FIELDS.toolStagnationRounds.default)
  .field("noNewEvidenceRounds", "numeric", { displayName: "No-new-evidence rounds", subtitle: "Consecutive duplicate-read rounds without needed source recovery before a report reminder; a second reminder in the same stalled episode ends research. New progress or a failed read resets the episode." }, NUMERIC_FIELDS.noNewEvidenceRounds.default)
  .field("generationRepetitionAction", "select", {
    displayName: "Within-generation repetition",
    subtitle: "Warn or pause when a substantial generated text block repeats consecutively.",
    options: [
      { value: "warn", displayName: "Warn" },
      { value: "pause", displayName: "Pause" },
      { value: "off", displayName: "Off" },
    ],
  }, "warn")
  .field("generationRepeatCount", "numeric", { displayName: "Text repetition threshold", subtitle: "Consecutive copies required; each repeated block must be at least 80 characters." }, NUMERIC_FIELDS.generationRepeatCount.default)
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
  .field("auditResearchSeconds", "numeric", { displayName: "Audit research seconds", subtitle: "Maximum research time before the optional tool-free report opportunity." }, NUMERIC_FIELDS.auditResearchSeconds.default)
  .field("auditResearchRounds", "numeric", { displayName: "Audit research rounds", subtitle: "Maximum completed research prediction rounds before the optional report opportunity." }, NUMERIC_FIELDS.auditResearchRounds.default)
  .field("auditFinalSeconds", "numeric", { displayName: "Audit report seconds", subtitle: "Maximum time for the single tool-free report opportunity." }, NUMERIC_FIELDS.auditFinalSeconds.default)
  .field("auditFinalMaxTokens", "numeric", { displayName: "Audit report output limit", subtitle: "Predicted-token limit for the single tool-free report opportunity." }, NUMERIC_FIELDS.auditFinalMaxTokens.default)
  .field("separateAttachments", "boolean", { displayName: "Separate document input (experimental)", subtitle: "Requires this preprocessor before document RAG. Preserve typed document references instead of injecting the full document." }, false)
  .field("reviewProgress", "boolean", { displayName: "Review continuity notes (experimental)", subtitle: "Allow bounded assistant review claims tied to observed file versions. Claims are not verified completion." }, false)
  .build();

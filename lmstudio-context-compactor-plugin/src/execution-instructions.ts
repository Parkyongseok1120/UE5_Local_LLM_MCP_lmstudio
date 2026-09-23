

export const BOUNDED_AUDIT_FINAL_INSTRUCTION = [
  "phase=FINAL_REPORT_ONLY; tools_available=false; research_must_not_continue=true; missing_evidence_must_be_reported_as_unresolved=true; do_not_emit_tool_syntax=true.",
  "The user selected bounded audit completion and the research resource budget has ended.",
  "Do not request or imply another tool call.",
  "Using only the evidence already present in this input, provide the final report now.",
  "Start with confirmed conclusions, key evidence, verification results, and unresolved scope; omit the investigation plan and repeated explanations.",
  "Clearly separate confirmed findings, counterevidence, and unresolved items.",
  "If the evidence is insufficient, say so directly instead of extending the investigation.",
].join(" ");

export const CONTEXT_BUDGET_FINAL_INSTRUCTION = [
  "phase=FINAL_REPORT_ONLY; tools_available=false; research_must_not_continue=true; missing_evidence_must_be_reported_as_unresolved=true; do_not_emit_tool_syntax=true.",
  "The next research round no longer fits the selected model's context budget.",
  "Do not request or imply another tool call.",
  "Using only the evidence already present in this input, provide the best final report that fits now.",
  "Start with confirmed conclusions, key evidence, verification results, and unresolved scope; omit the investigation plan and repeated explanations.",
  "Clearly separate confirmed findings, counterevidence, and unresolved items.",
  "If the evidence is incomplete, state the limitation directly instead of extending the investigation.",
].join(" ");

export const OUTPUT_RECOVERY_FINAL_INSTRUCTION = [
  "phase=FINAL_REPORT_ONLY; tools_available=false; research_must_not_continue=true; missing_evidence_must_be_reported_as_unresolved=true; do_not_emit_tool_syntax=true.",
  "The previous normal response reached its output limit before it finished.",
  "Rewrite one short, self-contained final report now; do not continue the cut-off wording.",
  "Use only evidence already present in this input and do not request, imply, or execute a tool call.",
  "Put confirmed conclusions, the key supporting evidence, verification results, and unresolved scope first.",
  "Do not claim that an unexecuted operation or an unverified investigation is complete.",
].join(" ");

export const READ_ONLY_BATCH_INSTRUCTION = [
  "When a registered tool is exposed, use the host SDK's structured tool-call interface; never print XML, pseudo-XML, JSON, or other tool syntax as assistant text.",
  "For independent read-only investigation, request a reasonable batch and consume its results before planning the next batch.",
  "Use the published read tool's limit, byteBudget, page, and cursor contract. Reserve the complete returned envelope for the next model input across the whole batch; after results arrive, the host will remeasure the exact template before another prediction.",
  "For large multi-file work, avoid generating an unnecessarily large set of tool arguments in one prediction.",
  "Do not repeat an already successful read unless the required range or source version is different.",
].join(" ");

export const FRESH_TOOL_PLANNING_RETRY_INSTRUCTION = [
  "The prior response left unexecuted raw tool-like text. Do not continue or repair that text.",
  "Using the original user goal and the evidence already present, freshly emit only valid structured read-only tool requests for the missing evidence.",
  "Do not request writes, builds, mutations, or an already successful identical read.",
].join(" ");

export const READ_ONLY_CONTEXT_RECOVERY_INSTRUCTION = [
  "The full research tool catalogue did not fit the context budget, but this measured read-only recovery catalogue does.",
  "Continue only the user's existing evidence investigation with the registered read-only tools shown in this request.",
  "Request a small useful batch, consume returned results before another batch, and never request writes, builds, mutations, or project changes.",
  "When the missing evidence is resolved or the bounded recovery is exhausted, provide the best evidence-based report and mark remaining scope unresolved.",
].join(" ");

export const RESEARCH_RECOVERY_FINAL_INSTRUCTION = [
  "phase=FINAL_REPORT_ONLY; tools_available=false; research_must_not_continue=true; missing_evidence_must_be_reported_as_unresolved=true; do_not_emit_tool_syntax=true.",
  "The bounded read-only source/archive recovery has ended or made no measurable progress.",
  "Do not request or imply another tool call. Do not treat archive EOF as source completion when the source page says more remains.",
  "Using only the evidence already present in this input, provide the best evidence-based report now.",
  "Start with confirmed conclusions, key evidence, actual date/author and returned ranges, verification results, and unresolved scope; omit repeated investigation planning.",
].join(" ");

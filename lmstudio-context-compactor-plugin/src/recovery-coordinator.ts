import {
  Chat, type ChatMessage
} from "@lmstudio/sdk";
import { toolMemory } from "./context-ports";
import type { RecoveryProgress } from "./evidence-manager";
import type { ContinuationAction, FinalizationTrigger, PressureSignal, RoundVerdict, TaskObjective } from "./execution-contracts";

export type ContinuationInput = {
  verdict: RoundVerdict; objective: TaskObjective; pressure: PressureSignal;
  limits: { reached: boolean };
  state: { reportNowTriggers: number; narrowAttempted: boolean; mutationFocus: boolean };
};
export type ContinuationDecision = { action: ContinuationAction; trigger: FinalizationTrigger | null };
import { FRESH_TOOL_PLANNING_RETRY_INSTRUCTION } from "./execution-instructions";
import { type RawToolIntentClassification } from "./raw-tool-intent";
import { hasRole, isObservationOnlyToolCall, isUnregisteredGitReadIntent, ToolCapabilityRegistry, type RemoteToolLike } from "./tool-capability-registry";

export type RecoveryPlan = {
  reason: string; toolNames: readonly string[]; outputReserve: number;
  instruction?: string; blockedFingerprints?: ReadonlySet<string>;
};

export class RecoveryCoordinator {
  private narrowed = false;
  private focused = false;
  get narrowAttempted() { return this.narrowed; }
  get mutationFocus() { return this.focused; }
  beginMutationFocus() { this.narrowed = true; this.focused = true; }
  endMutationFocus() { this.focused = false; }
  /** Pure layer-A policy. The execution consumes this value once per tool round. */
  decide({ verdict, objective, pressure, limits, state }: ContinuationInput): ContinuationDecision {
    const finalize = (trigger: FinalizationTrigger): ContinuationDecision => ({ action: "finalize", trigger });
    const resume: ContinuationDecision = { action: "continue", trigger: null };
    const mutation = objective.kind === "mutate" || objective.kind === "build";
    const focus = (): ContinuationDecision => state.narrowAttempted
      ? finalize("mutation_blocked_by_context") : { action: "narrow_to_mutation", trigger: null };
    if (limits.reached) return finalize("research_round_limit");
    if (state.mutationFocus) return verdict.mutationOutcomes.length ? resume : finalize("mutation_blocked_by_context");
    if (pressure.workingSetExhausted && objective.kind === "review") return finalize("context_working_set_exhausted");
    if (pressure.workingSetExhausted && mutation) return focus();
    if (verdict.budgetDenied.length) return resume;
    if (state.reportNowTriggers >= 2) return mutation && objective.mutationState === "none"
      ? focus() : finalize("research_no_new_evidence");
    return resume;
  }
  mutationProfile(registry: ToolCapabilityRegistry, targets: readonly string[]) {
    return [...registry.mutationProfile(), ...registry.tools.flatMap(tool => {
      if (hasRole(tool.name,"mutation_focus_validate",tool.pluginIdentifier)) return [tool];
      if (!["read_file", "read_file_range"].includes(tool.name) || !targets.length
        || !isObservationOnlyToolCall(tool, { name: tool.name, arguments: {} })) return [];
      const schema = tool.parametersJsonSchema as { properties?: Record<string, unknown> };
      const narrowed = Object.create(tool) as RemoteToolLike;
      Object.defineProperty(narrowed, "parametersJsonSchema", { enumerable: true, value: {
        ...schema, properties: { ...schema?.properties, path: { type: "string", enum: [...new Set(targets)] } },
      } });
      return [narrowed];
    })];
  }
  private rounds = 0;
  private pages = 0;
  private stagnant = 0;
  private started = false;
  private fallbackUsed = false;
  private replans = 0;
  private plan: RecoveryPlan = { reason: "", toolNames: [], outputReserve: 0 };
  get toolRounds() { return this.rounds; }
  get paginationRounds() { return this.pages; }
  get noProgressRounds() { return this.stagnant; }
  get episodeStarted() { return this.started; }
  get planningAttempts() { return this.replans; }
  get reason() { return this.plan.reason; }
  get outputReserve() { return this.plan.outputReserve; }
  get instruction() { return this.plan.instruction || FRESH_TOOL_PLANNING_RETRY_INSTRUCTION; }
  get blockedFingerprints() { return new Set(this.plan.blockedFingerprints); }
  allowsTool(name: string) { return this.plan.toolNames.includes(name); }
  begin(plan: RecoveryPlan, replanning = false) {
    this.plan = { ...plan, toolNames: [...plan.toolNames], blockedFingerprints: new Set(plan.blockedFingerprints) };
    this.started = true;
    if (replanning) this.replans++;
    this.reset();
  }
  reset() { this.rounds = 0; this.pages = 0; this.stagnant = 0; this.fallbackUsed = false; }
  advance(progress: RecoveryProgress | null, maximum: number, continued: boolean,
    rawIntent: boolean, requestCount: number, resultCount: number, reportText: string, bounded = true, readiness: { originalProfileFits?: boolean; fallbackInstruction?: string; budgetDenied?: boolean } = {}) {
    this.rounds++;
    const progressed = progress?.progressed === true;
    if (progressed) { this.pages++; this.stagnant = 0; } else if (!readiness.budgetDenied) this.stagnant++;
    const resumeOriginal = continued && progressed && readiness.originalProfileFits;
    const fallback = !progressed && !rawIntent && !this.fallbackUsed && Boolean(readiness.fallbackInstruction);
    const noProgress = !progressed && !fallback;
    if (fallback) {
      this.fallbackUsed = true;
      this.plan = { ...this.plan, instruction: `${this.instruction} ${readiness.fallbackInstruction}` };
    }
    const paginationBound = bounded && this.paginationRounds >= Math.max(1, maximum);
    const evidence = Boolean(requestCount || resultCount || progress?.newSuccessfulObservationCount || progress?.errorCount);
    const needsFinal = Boolean(rawIntent || progress?.errorCount || (evidence && !progressed));
    // A budget denial/failure belongs to its request, not to successful peers
    // in the batch. Consume useful results before declaring recovery exhausted.
    const trigger = resumeOriginal ? null : (noProgress && needsFinal) || (noProgress && !continued && !reportText)
      ? "research_recovery_exhausted" as const
      : paginationBound && continued ? "research_recovery_complete" as const : null;
    return {
trigger, resumeOriginal, endRecovery: resumeOriginal || paginationBound || (noProgress && !needsFinal),
      continue: continued || fallback, maxPaginationRounds: bounded ? Math.max(1, maximum) : null, noProgress
};
  }
}

/** Only a typed, actionable control miss allows one alternative. An ordinary
 * duplicate, guard denial or unknown failure is not another recovery budget. */
export function recoveryFallbackInstruction(messages: readonly ChatMessage[]): string | undefined {
  for (const message of messages) for (const result of message.getToolCallResults()) {
    const value = toolMemory.decodeToolResultRecord(result.content).value as Record<string, any> | null;
    if (value?.kind === "historical_evidence_index" && value.ok === true && Array.isArray(value.entries) && value.entries.length === 0)
      return "The archive catalog is empty. Do not repeat it. Use a source path already provided in the task or a registered source search; if none is available, report the missing scope. This is the single alternative attempt.";
    if (["read_handle_unavailable", "version_mismatch", "path_ambiguous"].includes(String(value?.errorCode))
      && (Array.isArray(value?.validHandles) && value.validHandles.length > 0 || typeof value?.readHandle === "string"))
      return "The archive locator was rejected. Use an exact validHandle/readHandle returned by that response once; never guess or reuse the rejected locator. This is the single alternative attempt.";
  }
  return undefined;
}

export type ReadOnlyRecoveryProfile = {
  eligible: boolean;
  reason: string;
  tools: Array<RemoteToolLike>;
};

// This is a capability scope, not a claim about the user's goal or completion.
export type ReadExecutionScope = {
  scope: "registered_read_evidence" | "mixed_or_unknown";
  reason: string; allowedReadNames: ReadonlySet<string>
};
export function readExecutionScope(history: Chat, tools: Array<RemoteToolLike>): ReadExecutionScope {
  const registry = new ToolCapabilityRegistry(tools);
  const messages = history.getMessagesArray();
  const latest = messages.map((m, i) => m.isUserMessage() ? i : -1).filter(i => i >= 0).at(-1) ?? 0;
  const requests = messages.slice(latest + 1).flatMap(m => m.getToolCallRequests());
  for (const request of requests) {
    const tool = registry.resolve(request.name);
    if (!tool || !isObservationOnlyToolCall(tool, request)) return {
scope: "mixed_or_unknown",
      reason: tool ? "current_turn_has_non_observation" : "current_turn_unknown_request", allowedReadNames: new Set()
};
  }
  return {
scope: "registered_read_evidence", reason: "provider_qualified_read_scope",
    allowedReadNames: new Set(registry.readProfile().map(tool => tool.name))
};
}

export function readOnlyRecoveryProfile(history: Chat, visibleTools: Array<RemoteToolLike>): ReadOnlyRecoveryProfile {
  const messages = history.getMessagesArray();
  const scope = readExecutionScope(history, visibleTools);
  if (scope.scope !== "registered_read_evidence") return { eligible: false, reason: scope.reason, tools: [] };

  const latestUserIndex = messages.map((message, index) => message.isUserMessage() ? index : -1)
    .filter(index => index >= 0).at(-1) ?? 0;
  for (const request of messages.slice(latestUserIndex + 1).flatMap(message => message.getToolCallRequests())) {
    const tool = visibleTools.find(candidate => candidate.name === request.name);
    if (!tool) return { eligible: false, reason: "current_turn_unknown_request", tools: [] };
    if (!isObservationOnlyToolCall(tool, request)) {
      return { eligible: false, reason: "current_turn_has_non_observation", tools: [] };
    }
  }
  const tools = new ToolCapabilityRegistry(visibleTools).readProfile();
  return tools.length > 0
    ? { eligible: true, reason: "provider_qualified_read_scope", tools }
    : { eligible: false, reason: "no_registered_read_only_tools", tools: [] };
}

/** The raw text that was not executed usually had the right tool and the wrong
 * argument shape. Restate each named tool's published top-level arguments so the
 * fresh structured request is built from the schema, not from the dropped text. */
export function schemaReminderInstruction(names: ReadonlyArray<string>, tools: ReadonlyArray<RemoteToolLike>): string {
  const lines: Array<string> = [];
  for (const name of [...new Set(names)].slice(0, 6)) {
    const schema = tools.find(tool => tool.name === name)?.parametersJsonSchema;
    if (!schema || typeof schema !== "object") continue;
    const record = schema as { required?: unknown; properties?: unknown };
    const required = Array.isArray(record.required) ? record.required.filter((item): item is string => typeof item === "string") : [];
    const properties = record.properties && typeof record.properties === "object"
      ? Object.keys(record.properties as Record<string, unknown>) : [];
    lines.push(`${name}: required [${required.slice(0, 8).join(", ") || "none"}]; arguments [${properties.slice(0, 12).join(", ")}]`);
  }
  return lines.length
    ? `Schema reminder (pass one JSON object using only these top-level argument names): ${lines.join("; ")}.` : "";
}

export function catalogueCorrectionInstruction(
  classification: RawToolIntentClassification,
  tools: Array<RemoteToolLike>,
): string {
  const unknown = classification.unknownNames.slice(0, 8).join(", ") || "none";
  const registered = tools.map(tool => tool.name).slice(0, 12).join(", ");
  return [
    FRESH_TOOL_PLANNING_RETRY_INSTRUCTION,
    `The prior raw text named unregistered tools (${unknown}); those names and their raw arguments were not executed or converted.`,
    `The measured registered read-only catalogue for this retry is: ${registered}.`,
    "Generate new structured requests only from the published registry and schema. If a single-file Git diff or pinned source read is still required, use the public git_diff_file or git_read_file schema and the original verified scope.",
    "Never execute, alias, or copy raw names such as git_show_file, git_diff, or raw version arguments into a structured request; the model must regenerate valid schema fields itself.",
  ].join(" ");
}

export function archiveOnlyRecoveryInstruction(tools: Array<RemoteToolLike>): string {
  const registered = tools.map(tool => tool.name).slice(0, 12).join(", ");
  return [
    FRESH_TOOL_PLANNING_RETRY_INSTRUCTION,
    "The prior output contained only an archive-reader intent. It was not executed or converted.",
    `Use the measured registered read-only catalogue for the current evidence investigation: ${registered}.`,
    "You may rehydrate the exact archived evidence first, then continue with its registered source reader when the source page has more coverage.",
    "Generate fresh structured requests only from the published registry and JSON schema. Do not execute raw XML or invent aliases.",
  ].join(" ");
}

export type FreshToolPlanningRetryDecision = { eligible: boolean; reason: string };

export function freshToolPlanningRetryDecision(options: {
  boundedAudit: boolean;
  observeOnly: boolean;
  planningAllowed: boolean;
  attempts: number;
  failure: unknown;
  aborted: boolean;
  phaseTimedOut: boolean;
  finishReason?: string;
  finalRawToolIntent: boolean;
  candidateFit: boolean;
  candidateToolCount: number;
  candidateExact: boolean;
  unknownNameCount: number;
  registeredButWithheldCount: number;
  registeredUnsafeCount: number;
  unsafeUnknownCount: number;
  catalogueCorrectionAllowed: boolean;
  structuredToolRequestCount: number;
  runtimeDispatchCount: number;
  actualResultCount: number;
}): FreshToolPlanningRetryDecision {
  if (options.boundedAudit) return { eligible: false, reason: "bounded_mode" };
  if (options.observeOnly) return { eligible: false, reason: "observe_only" };
  if (!options.planningAllowed) return { eligible: false, reason: "final_report_repair_forbidden" };
  if (options.attempts >= 1) return { eligible: false, reason: "already_retried" };
  if (options.failure !== undefined) return { eligible: false, reason: "generation_failure" };
  if (options.aborted) return { eligible: false, reason: "canceled" };
  if (options.phaseTimedOut) return { eligible: false, reason: "timeout" };
  if (options.finishReason === "generation_repetition_paused") {
    return { eligible: false, reason: "explicit_pause" };
  }
  if (!options.finalRawToolIntent) return { eligible: false, reason: "no_final_raw_tool_intent" };
  if (options.structuredToolRequestCount > 0) return { eligible: false, reason: "structured_request_present" };
  if (options.runtimeDispatchCount > 0 || options.actualResultCount > 0) {
    return { eligible: false, reason: "dispatch_or_result_present" };
  }
  if (options.registeredUnsafeCount > 0) return { eligible: false, reason: "registered_but_unsafe" };
  if (options.registeredButWithheldCount > 0) return { eligible: false, reason: "registered_but_not_exposed" };
  if (options.unsafeUnknownCount > 0) return { eligible: false, reason: "unsafe_unknown_tool_name" };
  if (options.unknownNameCount > 0 && !options.catalogueCorrectionAllowed) {
    return { eligible: false, reason: "unregistered_tool_name" };
  }
  if (options.candidateToolCount === 0) return { eligible: false, reason: "no_safe_registered_tools" };
  if (!options.candidateFit) return { eligible: false, reason: "retry_candidate_no_fit" };
  return {
    eligible: true, reason: options.unknownNameCount > 0
      ? "eligible_registered_catalogue_replan"
      : options.candidateExact ? "eligible_read_only_fresh_planning"
        : "eligible_estimated_read_only_fresh_planning"
  };
}

export function planReadRecovery(historyBeforeRound: Chat, workingHistory: Chat,
  rawIntent: RawToolIntentClassification, modelTools: Array<RemoteToolLike>) {
  const exactRawTools = rawIntent.names.length > 0
    && rawIntent.unknownNames.length === 0
    && rawIntent.registeredButWithheldNames.length === 0
    && rawIntent.registeredUnsafeNames.length === 0
    ? modelTools.filter(tool => rawIntent.knownReadOnlyNames.includes(tool.name)) : [];
  const gitRecoveryProfile = readOnlyRecoveryProfile(historyBeforeRound, modelTools);
  const archivedProjectionPresent = workingHistory.getMessagesArray().some(message => (
    message.getToolCallResults().some(result => (
      toolMemory.decodeToolResultRecord(result.content).value?.kind === "archived_tool_result_projection"
    ))
  ));
  const archiveReader = archivedProjectionPresent
    ? modelTools.find(tool => tool.name === "evidence_first_read_context") : undefined;
  const includeArchiveReader = (tools: Array<RemoteToolLike>) => (
    archiveReader && !tools.some(tool => tool.name === archiveReader.name)
      ? [...tools, archiveReader] : tools
  );
  const catalogueCorrectionAllowed = rawIntent.unknownNames.length > 0
    && rawIntent.unknownNames.every(isUnregisteredGitReadIntent)
    && gitRecoveryProfile.eligible;
  const archiveOnlyRecoveryAllowed = rawIntent.names.length > 0
    && rawIntent.unknownNames.length === 0
    && rawIntent.registeredButWithheldNames.length === 0
    && rawIntent.registeredUnsafeNames.length === 0
    && rawIntent.knownReadOnlyNames.length > 0
    && rawIntent.knownReadOnlyNames.every(name => name === "evidence_first_read_context")
    && gitRecoveryProfile.eligible;
  const retryTools = includeArchiveReader(
    gitRecoveryProfile.eligible ? gitRecoveryProfile.tools : exactRawTools,
  );
  const baseInstruction = rawIntent.unknownNames.length > 0
    ? catalogueCorrectionInstruction(rawIntent, retryTools)
    : archiveOnlyRecoveryAllowed
      ? archiveOnlyRecoveryInstruction(retryTools)
      : FRESH_TOOL_PLANNING_RETRY_INSTRUCTION;
  const schemaReminder = schemaReminderInstruction(rawIntent.knownReadOnlyNames, [...modelTools, ...retryTools]);
  const retryInstruction = schemaReminder ? `${baseInstruction} ${schemaReminder}` : baseInstruction;

  return { catalogueCorrectionAllowed, archiveOnlyRecoveryAllowed, retryTools, retryInstruction };
}

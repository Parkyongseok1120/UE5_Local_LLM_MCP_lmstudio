import {
  Chat
} from "@lmstudio/sdk";
import { toolMemory } from "./context-ports";
import type { RecoveryProgress } from "./evidence-manager";
import { FRESH_TOOL_PLANNING_RETRY_INSTRUCTION } from "./execution-instructions";
import { type RawToolIntentClassification } from "./raw-tool-intent";
import { isObservationOnlyToolCall, isUnregisteredGitReadIntent, ToolCapabilityRegistry, type RemoteToolLike } from "./tool-capability-registry";

export class RecoveryCoordinator {
  toolRounds = 0;
  paginationRounds = 0;
  noProgressRounds = 0;
  reset() { this.toolRounds = 0; this.paginationRounds = 0; this.noProgressRounds = 0; }
  advance(progress: RecoveryProgress | null, maximum: number, continued: boolean,
    rawIntent: boolean, requestCount: number, resultCount: number, reportText: string) {
    this.toolRounds++;
    const progressed = progress?.progressed === true;
    if (progressed) { this.paginationRounds++; this.noProgressRounds = 0; } else this.noProgressRounds++;
    const noProgress = this.noProgressRounds >= 1;
    const paginationBound = this.paginationRounds >= Math.max(1, maximum);
    const evidence = Boolean(requestCount || resultCount || progress?.newSuccessfulObservationCount || progress?.errorCount);
    const needsFinal = Boolean(rawIntent || progress?.errorCount || (evidence && !progressed));
    const trigger = (noProgress && needsFinal) || progress?.errorCount || (!continued && !reportText)
      ? "research_recovery_exhausted" as const
      : paginationBound && continued ? "research_recovery_complete" as const : null;
    return {
trigger, endRecovery: paginationBound || (noProgress && !needsFinal),
      continue: continued, maxPaginationRounds: Math.max(1, maximum), noProgress
};
  }
}

export type ReadOnlyRecoveryProfile = {
  eligible: boolean;
  reason: string;
  tools: Array<RemoteToolLike>;
};

export type ObjectiveState = {
  scope: "registered_read_evidence" | "mixed_or_unknown";
  reason: string; allowedReadNames: ReadonlySet<string>
};
export function objectiveState(history: Chat, tools: Array<RemoteToolLike>): ObjectiveState {
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
  const objective = objectiveState(history, visibleTools);
  if (objective.scope !== "registered_read_evidence") return { eligible: false, reason: objective.reason, tools: [] };

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
    rawIntent.unknownNames.length > 0 || archiveOnlyRecoveryAllowed
      ? gitRecoveryProfile.tools : exactRawTools,
  );
  const retryInstruction = rawIntent.unknownNames.length > 0
    ? catalogueCorrectionInstruction(rawIntent, retryTools)
    : archiveOnlyRecoveryAllowed
      ? archiveOnlyRecoveryInstruction(retryTools)
      : FRESH_TOOL_PLANNING_RETRY_INSTRUCTION;

  return { catalogueCorrectionAllowed, archiveOnlyRecoveryAllowed, retryTools, retryInstruction };
}

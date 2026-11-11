import { fileCoverageNotice, quoteAnchoringNotice, type QuoteMismatch } from "./quote-anchoring";
import { confirmedProgressStatements, engineClaimNotice, reportEngineClaimNotice, returnedEngineEvidence,
  textEngineClaimGaps, unresolvedEngineClaimGaps, unresolvedReportEngineClaimGaps, type EngineClaimGap } from "./engine-claim-anchoring";
import { reportReviewNotice, reviewCandidateLines, type ReviewRow } from "./review-register";
import { cleanVerdictScopeGap, REPORT_REVIEW_OBJECTIVES } from "./report-coverage";
import { suggestedCodeNotice } from "./write-api-anchoring";
import { ENGINE_PROFILES, ENGINE_PROVIDERS } from "./engine-profiles.js";
import {
  Chat,
  ChatMessage
} from "@lmstudio/sdk";
import type { DirectConfig, ExecutionOutcome, FinalizationTrigger, ObjectiveKind, TaskObjective } from "./execution-contracts";
import { finalizationCause } from "./execution-instructions";
import { type FinalDeliveryState, type FinalReportState, type OutputLimitStage } from "./execution-contracts";
export const TOOL_RETRY_EXHAUSTED_STATUS = "읽기 전용 도구 계획 재시도에서도 structured 도구 요청이 생성되지 않아 중단했습니다.";
export const RAW_INTENT_PARTIAL_STATUS = "실행되지 않은 도구 호출 텍스트만 남았고 재시도 조건을 충족하지 못해 확인된 근거로 부분 보고를 전달합니다.";
/** The status line of a final attempt that did not complete, most specific cause first. */
export function finalDeliveryStatusText(state: { partialDelivered: boolean; reportState: string; deliveryState: string;
   timedOut: boolean; aborted: boolean; failed: boolean; continuing: boolean; reviewing?: boolean; duplicateOnly?: boolean }): string {
  // A review round that asked for tools instead of writing its supplement: the report itself stands.
  if (state.reviewing && state.reportState === "unresolved_tool_intent")
    return "검토 보완 단계에서는 도구를 쓸 수 없어 보완문을 만들지 못했습니다. 앞서 전달한 보고서는 그대로입니다.";
  if (state.partialDelivered) return "조사 복구가 완료되지 않아 근거가 있는 부분 보고만 전달했습니다. 전체 완료로 처리하지 않았습니다.";
  if (state.reportState === "unresolved_tool_intent") return "최종 단계에서 실행되지 않은 도구 호출 의도만 남아 보고서 완료로 처리하지 않았습니다.";
  if (state.deliveryState === "truncated") return "최종 보고가 출력 한도에 도달해 잘렸습니다. 완료로 처리하지 않았습니다.";
  if (state.timedOut) return "최종 보고가 제한 시간 안에 완료되지 않았습니다. 부분 출력으로 기록했습니다.";
  if (state.aborted) return "생성이 중지되었습니다. 지금까지 작성한 답변을 보존했습니다.";
  if (state.failed) return "생성 중 오류가 발생했습니다. 지금까지 작성한 답변을 보존했습니다.";
  if (state.duplicateOnly) return "앞선 답변과 중복된 출력을 제외한 뒤 새로 전달할 보완 내용이 없습니다. 앞서 작성한 답변을 보존했습니다.";
  return state.continuing ? "새로 전달할 본문이 없습니다. 앞서 작성한 답변을 보존했습니다."
    : "최종 보고가 생성되지 않았습니다. 완료로 처리하지 않았습니다.";
}

export function outputLimitStatusText(stage:OutputLimitStage|undefined):string {
  return stage==="tool_arguments" ? "도구 호출 인수가 출력 한도에서 끝나 실행하지 않았습니다."
    : stage==="reasoning" ? "생각 단계에서 생성 한도에 도달했습니다. 자동 복구가 꺼져 있거나 허용 조건을 충족하지 않아 중단했습니다."
      : "응답이 출력 한도에 도달해 완료로 처리하지 않았습니다.";
}
import { hasRawToolIntent, visibleTextFromMessages } from "./raw-tool-intent";
import { coverageRange, observationRecords, sourceObservationFailed } from "./evidence-manager";
import type { RemoteToolLike } from "./tool-capability-registry";
import { taskProgressView } from "./continuity-memory.js";
const { redact } = require("./evidence-archive.js") as { redact(value: string): string };

export class DeliveryController {
  attempts = 0;
  private observation: ExecutionObservation = { reportText: "" };
  private outcome: ExecutionOutcome | null = null;
  private priorReportPresent = false;
  constructor(private tools: Array<RemoteToolLike> = [], private objective?: TaskObjective) { }
  private candidates: () => readonly ReviewRow[] = () => [];
  /** The execution's facts, bound once after initialization. The owner exists
   * from the first line of the execution, so an initialization failure is
   * finished by the same owner, with no tools, objective or candidates yet. */
  bindExecution(tools: Array<RemoteToolLike>, objective: TaskObjective, candidates: () => readonly ReviewRow[]) {
    this.tools = tools; this.objective = objective; this.candidates = candidates;
  }
  // Only this attempt's answer can become a final report. Prior progress text
  // and an interrupted earlier generation must never count as delivered work.
  beginRound() { this.observation = { reportText: "" }; this.outputDisposition = undefined; }
  retainReportPrefix(text: string) { this.priorReportPresent ||= Boolean(text.trim()); }
  /** The report the user already has and the follow-up round that extends it:
   * a review supplement after a complete report, or the continuation of a cut one. */
  private supplement: { kind: "report_review" | "output_recovery"; reportComplete: boolean } | null = null;
  private outputDisposition: NonNullable<ExecutionOutcome["supplement"]>["outputDisposition"];
  beginSupplement(kind: "report_review" | "output_recovery", reportComplete: boolean) {
    this.outputDisposition = undefined;
    this.supplement = { kind, reportComplete };
  }
  private includePriorReport(outcome: ExecutionOutcome): ExecutionOutcome {
    if (!this.priorReportPresent) return outcome;
    const merged: ExecutionOutcome = { ...outcome, reportPresent: true,
      deliveryState: outcome.deliveryState === "no_answer" ? "partial" : outcome.deliveryState,
      reportState: outcome.reportState === "no_answer" ? "partial_report" : outcome.reportState };
    if (!this.supplement) return merged;
    // The delivered report and its follow-up are recorded apart: an empty
    // supplement does not undeliver the report the user already has
    // (conversation 1792929236226: reportDelivered:false after a complete report).
    // A budget-blocked follow-up never ran and keeps its own reason.
    const blocked = /context_budget/u.test(outcome.reason);
    // A review that only asked for tools wrote no supplement: the same as an empty one.
    const empty = !blocked && (outcome.deliveryState === "no_answer" || outcome.reason === "empty_visible_output"
      || this.supplement.kind === "report_review" && outcome.reportState === "unresolved_tool_intent");
    const stopped = ["timed_out", "canceled"].includes(outcome.executionOutcome);
    const supplement = { kind: this.supplement.kind,
      ...(this.outputDisposition ? { outputDisposition: this.outputDisposition } : {}),
      state: stopped ? "stopped" : blocked ? "blocked" : empty ? "empty" : "delivered" } as const;
    if (!this.supplement.reportComplete || stopped || !(empty || blocked)) return { ...merged, reportDelivered: true, supplement };
    return { ...merged, executionOutcome: "completed", reason: blocked ? outcome.reason : `${this.supplement.kind}_empty`,
      reportDelivered: true, reportCompleted: true, deliveryState: "complete", reportState: "report",
      taskCompleted: outcome.objectiveSatisfied === true, supplement };
  }
  observe(captured: { messages: Array<ChatMessage>; finishReason?: string; failure?: unknown; unexecutedToolIntent?: boolean;
    presentation?: { generatedVisibleChars: number; deliveredChars: number; duplicateSuppressedChars: number } }, timedOut = false) {
    const shown = captured.presentation;
    this.outputDisposition = shown ? shown.deliveredChars > 0 ? "delivered"
      : shown.generatedVisibleChars > 0 && shown.duplicateSuppressedChars >= shown.generatedVisibleChars ? "duplicate_only"
        : shown.generatedVisibleChars > 0 ? "filtered" : "no_generation" : undefined;
    this.observation = { reportText: visibleTextFromMessages(captured.messages).trim(),
      messages: captured.messages, finishReason: captured.finishReason, failure: captured.failure, timedOut,
      unexecutedToolIntent: captured.unexecutedToolIntent };
  }
  recordPartialDelivery(reason: string) {
    this.observation = { ...this.observation, partialReportDelivered: true, reason };
  }
  get hasPartialDelivery() { return this.observation.partialReportDelivered === true; }
  stop(reason: string) { this.observation = { ...this.observation, reason }; }
  partialReport(history: Chat, reason: string, messages: Array<ChatMessage> = [], trigger: string = reason) {
    return this.withMutationNotice(evidenceBackedPartialReport(history, messages, reason, this.tools, this.candidates()), trigger);
  }
  private withMutationNotice<T extends { text: string }>(report: T, trigger: string): T {
    return this.objective?.requiredEffects.has("mutation") && this.objective.mutationState !== "applied"
      ? { ...report, text: `요청한 변경은 적용되지 않았습니다: ${finalizationCause(trigger)}\n\n${report.text}` } : report;
  }
  finish(overrides: Pick<ExecutionObservation, "canceled" | "failure" | "timedOut"> = {}): ExecutionOutcome {
    // Cleanup calls can ask again, but cannot overwrite an accepted outcome.
    return this.outcome ??= Object.freeze(this.includePriorReport(classifyExecutionOutcome({ ...this.observation,
      ...(overrides.canceled ? { canceled: true } : {}),
      ...(overrides.timedOut !== undefined ? { timedOut: overrides.timedOut } : {}),
      ...(overrides.failure !== undefined ? { failure: overrides.failure } : {}) })));
  }
  // Terminal parse failure never repairs or retries an operation. Deliver only
  // returned observations; unfinished model claims are not report evidence.
  // An answer already streamed in the failing round stays the deliverable: the
  // notice states only what ran and what did not (conversation 1794386851846:
  // a 2,799-char plan, then a completed git_status and a cut-off validation).
  failedToolRequest(history: Chat, messages: Array<ChatMessage>, reason: string,
    options: { answerShownChars?: number; toolName?: string } = {}) {
    const request = options.toolName && /^[A-Za-z0-9_.:-]{1,80}$/u.test(options.toolName)
      ? `도구 요청 \`${options.toolName}\`` : "도구 요청";
    if ((options.answerShownChars || 0) >= ANSWERED_ROUND_MIN_CHARS)
      return this.withMutationNotice(answeredRoundToolFailureNotice(messages, this.tools, request, reason), reason);
    const report = this.partialReport(history, reason, messages);
    return { ...report, text: `${report.text}\n- ${request} 인수를 완성하지 못해 해당 요청은 실행되지 않았습니다.\n- 실행되지 않은 요청의 검증 결과는 없으며, 반환 기록만으로 전체 조사나 원인 판단이 완료된 것은 아닙니다.` };
  }
  limits(config: Pick<DirectConfig, "maxOutputReserve" | "auditFinalMaxTokens" | "auditFinalSeconds"
    | "outputRecoveryMaxTokens" | "outputRecoverySeconds">, bounded: boolean, trigger: FinalizationTrigger | null) {
    // A context/read recovery does not opt the user into bounded audit. Keep
    // its report under the ordinary token cap and user cancellation signal.
    if (trigger === "output_recovery") return {
      maxTokens: Math.min(config.maxOutputReserve, config.outputRecoveryMaxTokens),
      timeoutMs: bounded ? Math.max(1, config.auditFinalSeconds * 1000) : null,
    };
    return {
      maxTokens: bounded ? Math.min(config.maxOutputReserve, config.auditFinalMaxTokens) : config.maxOutputReserve,
      timeoutMs: bounded ? Math.max(1, config.auditFinalSeconds * 1000) : null,
    };
  }
  begin(bounded: boolean, replanAttempts: number) {
    const limit = (bounded ? 1 : replanAttempts > 0 ? 2 : 1) + this.grants.size;
    const allowed = this.attempts < limit;
    if (allowed) this.attempts++;
    this.attemptLimit = limit;
    return { allowed, limit };
  }
  attemptLimit = 1;
  private readonly grants = new Set<"raw_tool_intent" | "report_review">();
  private correction: "raw_tool_intent" | null = null;
  /** One more tool-free final attempt, owned here rather than borrowed from
   * the replan count: once for a report that printed tool syntax instead of
   * prose, once for the review of a report that omitted open candidates. The
   * research budget is not reopened, and neither can repeat. */
  grantCorrection(reason: "raw_tool_intent" | "report_review") {
    if (this.grants.has(reason)) return false;
    this.grants.add(reason);
    this.correction = reason === "raw_tool_intent" ? reason : null;
    return true;
  }
  /** The correction the next final attempt is for, if any. */
  get pendingCorrection() { return this.correction; }
  evaluate(history: Chat, captured: Parameters<typeof classifyFinalDelivery>[1] & { messages: Array<ChatMessage> },
    timedOut: boolean, trigger: FinalizationTrigger | null, noProgressRounds: number,
    objectiveSatisfied: boolean | null = null) {
    const text = visibleTextFromMessages(captured.messages).trim();
    const classified = classifyFinalDelivery(text, captured, timedOut, captured.messages);
    if (this.priorReportPresent && classified.deliveryState === "no_answer") {
      classified.deliveryState = "partial";
      classified.reportState = "partial_report";
    }
    const forced = trigger === "research_recovery_exhausted" && noProgressRounds > 0;
    const delivery = forced && classified.deliveryState === "complete"
      ? { ...classified, deliveryState: "partial" as const, reportState: "partial_report" as const, rejectionReason: "research_no_progress" }
      : classified;
    this.observe(captured, timedOut);
    this.observation = { ...this.observation, delivery, objectiveSatisfied };
    // A failed review of a complete report adds no partial report after it.
    const reviewOfCompleteReport = this.supplement?.kind === "report_review" && this.supplement.reportComplete;
    const needsPartial = !reviewOfCompleteReport && delivery.deliveryState !== "complete" && (delivery.reportState === "unresolved_tool_intent"
      || ["research_recovery_complete", "research_recovery_exhausted", "research_no_new_evidence", "research_repetition",
        "context_working_set_exhausted", "mutation_blocked_by_context"].includes(trigger || ""));
    const partial = needsPartial ? this.partialReport(history,
      delivery.rejectionReason || trigger || "research_incomplete", captured.messages, trigger || "research_incomplete") : null;
    const outcome = classifyExecutionOutcome({ ...this.observation, partialReportDelivered: needsPartial });
    return {
      generationCompleted: outcome.generationCompleted,
      reportDelivered: outcome.reportDelivered,
      researchTerminated: trigger !== null,
      objectiveSatisfied,
      taskCompleted: outcome.taskCompleted,
      delivery, partial,
    };
  }
}

const STRUCTURE_ONLY_SCOPE = "packet_structure_and_record_consistency";
// Wording that reads as completed verification. Explicitly negated forms
// ("facts_not_verified", "미검증") are not matches.
const VERIFIED_WORDING = /검증\s*완료|검증(?:을|이)?\s*(?:마쳤|끝났|통과했)|검증(?:을|이|에)?\s*통과|사실\s*확인\s*완료|\((?:fully\s+)?verified\)|\bverified\b|\bvalidation\s+(?:passed|succeeded)\b|\bpassed\s+(?:the\s+)?validation\b/iu;
// Remove only a locally qualified/negated phrase, never another claim in the
// same answer. A disclaimer about B must not hide a verification claim about A.
const QUALIFIED_VERIFICATION = /\bnot\s+(?:(?:yet|fully)\s+)?verified\b|\bverified\s*[:=-]?\s*false\b|\bvalidation\s+(?:has\s+)?not\s+(?:yet\s+)?(?:passed|succeeded)\b|(?:사실\s*확인|검증)\s*완료(?:가|는)?\s*(?:아니\S*|아닙\S*|아님)|검증(?:을|이)?\s*(?:마치지|끝나지|통과하지)\s*(?:않\S*|못\S*)|(?:형식|구조)\s*(?:(?:검사|검증)\s*)?만\s*(?:검증\s*)?(?:완료|통과(?:했\S*)?)/giu;

/** One deterministic footer when the only validation in this turn checked
 * packet structure, yet the answer reads as verified. The answer text itself
 * is never rewritten; the footer states what was actually checked. */
export function verificationScopeNotice(history: Chat, finalText: string): string | null {
  const text = String(finalText || "").replace(QUALIFIED_VERIFICATION, "");
  if (!VERIFIED_WORDING.test(text)) return null;
  const messages = history.getMessagesArray();
  let start = 0;
  messages.forEach((message, index) => { if (message.getRole() === "user") start = index + 1; });
  const names = new Map<string, string>();
  let structureOnly = 0, factual = 0;
  for (const message of messages.slice(start)) {
    for (const request of message.getToolCallRequests()) names.set(String(request.id || ""), request.name);
    for (const result of message.getToolCallResults()) {
      if (!require("./tool-capability-core.js").hasRole(names.get(String(result.toolCallId || "")), "structure_validation")) continue;
      const raw = (result as { content?: unknown }).content;
      const content = typeof raw === "string" ? raw : JSON.stringify(raw ?? "");
      if (/\\?"factualEvidenceVerified\\?"\s*:\s*true/u.test(content)) factual++;
      else if (content.includes(STRUCTURE_ONLY_SCOPE)) structureOnly++;
    }
  }
  if (!structureOnly || factual) return null;
  return "[검증 범위] 이번 답변의 근거 패킷 검사(evidence_first_validate)는 형식과 기록 일관성만 확인했습니다. "
    + "사실 검증이 아니므로, 위의 '검증 완료·통과' 표현은 소스·실행 근거로 다시 확인해야 합니다.";
}

// Wording that states absence or a confirmed defect. Locally negated forms
// ("확인하지 못했", "not confirmed") are removed before matching.
const ABSENCE_OR_CONFIRMED_WORDING = /어디에도[^.\n。]{0,60}?(?:없|않)|존재하지\s*않|설정되지\s*않|호출되지\s*않|대입되지\s*않|\bnever\s+(?:set|called|assigned|reached)\b|\bdoes\s+not\s+exist\b|\bnowhere\b|\bP[0-3]\s*(?:\(?\s*confirmed\b|확정)|확정(?:된)?\s*(?:결함|버그|P[0-3])|\bconfirmed\s+(?:defect|bug)\b/iu;
const QUALIFIED_ABSENCE = /(?:확인|검증)(?:하지|되지)\s*(?:못|않)\S*|\bnot\s+(?:yet\s+)?(?:confirmed|verified)\b|미확인|미검증|의심/giu;

type ResultRecord = Record<string, unknown>;
function decodeToolResult(raw: unknown): ResultRecord | null {
  let value: unknown = raw;
  for (let depth = 0; depth < 3; depth++) {
    if (typeof value === "string") {
      try { value = JSON.parse(value); } catch { return null; }
      continue;
    }
    if (Array.isArray(value) && value.length && typeof (value[0] as { text?: unknown })?.text === "string") {
      value = value.map(block => String((block as { text?: unknown }).text ?? "")).join("");
      continue;
    }
    break;
  }
  return value && typeof value === "object" && !Array.isArray(value) ? value as ResultRecord : null;
}

/** One deterministic footer when the answer states absence or a confirmed
 * defect while a search in this user turn was partial and never completed.
 * A partial search page is not evidence of absence; the answer text itself is
 * never rewritten. */
export function partialSearchScopeNotice(history: Chat, finalText: string): string | null {
  const text = String(finalText || "").replace(QUALIFIED_ABSENCE, "");
  if (!ABSENCE_OR_CONFIRMED_WORDING.test(text)) return null;
  const messages = history.getMessagesArray();
  let start = 0;
  messages.forEach((message, index) => { if (message.getRole() === "user") start = index + 1; });
  const requests = new Map<string, { name: string; args: ResultRecord }>();
  const searches = new Map<string, { label: string; omitted: number | null; complete: boolean }>();
  const limited: string[] = [];
  for (const message of messages.slice(start)) {
    for (const request of message.getToolCallRequests()) {
      requests.set(String(request.id || ""), { name: request.name, args: (request.arguments || {}) as ResultRecord });
    }
    for (const result of message.getToolCallResults()) {
      const request = requests.get(String(result.toolCallId || ""));
      const value = request && decodeToolResult((result as { content?: unknown }).content);
      if (!request || !value) continue;
      const query = String(value.query ?? request.args.query ?? "").slice(0, 80);
      if (request.name === "search_files") {
        const key = JSON.stringify([query, String(request.args.path ?? ""), request.args.regex === true,
          request.args.matchFileNames ?? null]);
        const entry = searches.get(key) || { label: `search_files '${query}'`, omitted: null, complete: false };
        // Unreal pages report coverage/matchesOmitted; Unity pages report
        // items/total and whether the scan stopped early (incomplete).
        const unityPage = value.coverage === undefined && Array.isArray(value.items);
        const complete = unityPage ? value.truncated === false && value.incomplete === false
          : value.coverage === "complete" && value.truncated !== true;
        const partial = unityPage ? value.truncated === true || value.incomplete === true
          : value.coverage === "partial" && (value.truncated === true || Number(value.matchesOmitted) > 0);
        if (complete) entry.complete = true;
        else if (partial) {
          const omitted = unityPage ? Number(value.total) - (value.items as unknown[]).length : Number(value.matchesOmitted);
          if (Number.isFinite(omitted) && omitted > 0) entry.omitted = Math.max(entry.omitted ?? 0, omitted);
        } else continue;
        searches.set(key, entry);
      } else if (request.name === "unreal_symbol_lookup" && value.status === "coverage_limited") {
        const coverage = (value.coverage || {}) as ResultRecord;
        if (coverage.apiAbsenceVerified === false || value.apiAbsenceVerified === false) limited.push(`unreal_symbol_lookup '${query}'`);
      }
    }
  }
  const partial = [...searches.values()].filter(entry => !entry.complete)
    .map(entry => entry.omitted ? `${entry.label}(${entry.omitted}건 생략)` : `${entry.label}(부분 결과)`);
  const items = [...partial, ...limited.map(label => `${label}(범위 제한)`)];
  if (!items.length) return null;
  const shown = items.slice(0, 3).join(", ") + (items.length > 3 ? ` 외 ${items.length - 3}건` : "");
  return `[검색 범위] 이번 답변의 근거에 끝까지 받지 않은 부분 검색이 있습니다: ${shown}. `
    + "부분 결과는 부재의 증거가 아니므로, 위의 '없음·확정' 표현은 남은 결과(nextCursor)나 해당 정의·구현을 직접 읽어 확인해야 합니다.";
}

export type ExecutionObservation = {
  unexecutedToolIntent?: boolean;
  reportText: string;
  messages?: Array<ChatMessage>;
  finishReason?: string;
  failure?: unknown;
  canceled?: boolean;
  timedOut?: boolean;
  reason?: string;
  partialReportDelivered?: boolean;
  objectiveSatisfied?: boolean | null;
  delivery?: { deliveryState: FinalDeliveryState; reportState?: FinalReportState; rejectionReason?: string };
};

/** Shared by the runtime and offline evaluators. Unknown stops fail closed. */
export function classifyExecutionOutcome(observation: ExecutionObservation): ExecutionOutcome {
  const finishReason = observation.finishReason || "unknown";
  const timedOut = observation.timedOut === true || finishReason === "final_timeout";
  const canceled = observation.canceled === true || finishReason === "userStopped";
  const failed = observation.failure !== undefined && observation.failure !== null;
  const classified = classifyFinalDelivery(observation.reportText,
    { finishReason, unexecutedToolIntent: observation.unexecutedToolIntent,
      ...(failed ? { failure: observation.failure } : {}) }, timedOut, observation.messages || []);
  // Policy may reject an otherwise complete report, but cannot make an unknown
  // stop or an empty answer complete (including legacy evaluation records).
  const delivery = observation.delivery?.deliveryState === "complete"
    ? classified : observation.delivery || classified;
  const stopped = timedOut || canceled || failed;
  const reportCompleted = !stopped && delivery.deliveryState === "complete";
  const reportPresent = Boolean(observation.reportText.trim()) || observation.partialReportDelivered === true;
  const deliveryState = observation.partialReportDelivered ? "partial"
    : stopped && delivery.deliveryState === "complete" ? (reportPresent ? "partial" : "no_answer")
      : delivery.deliveryState;
  const outcome = timedOut ? "timed_out" : canceled ? "canceled" : failed ? "failed"
    : reportCompleted && !observation.partialReportDelivered ? "completed"
      : finishReason === "maxPredictedTokensReached" || delivery.deliveryState === "truncated" ? "truncated"
        : delivery.deliveryState === "no_answer" ? "no_answer" : "failed";
  const objectiveSatisfied = observation.objectiveSatisfied ?? null;
  return {
    executionOutcome: outcome,
    reason: timedOut ? "timeout" : canceled ? "canceled"
      : observation.reason || (failed ? "generation_failure" : delivery.rejectionReason || finishReason),
    finalFinishReason: finishReason,
    generationCompleted: !stopped && ["eosFound", "stopStringFound"].includes(finishReason),
    reportPresent, reportDelivered: reportCompleted || observation.partialReportDelivered === true,
    reportCompleted: outcome === "completed", deliveryState,
    reportState: observation.partialReportDelivered ? "partial_report"
      : deliveryState === "complete" ? "report" : deliveryState === "no_answer" ? "no_answer"
      : delivery.reportState === "unresolved_tool_intent" ? "unresolved_tool_intent" : "partial_report",
    objectiveSatisfied, taskCompleted: objectiveSatisfied === true && outcome === "completed",
  };
}

export function classifyOutputLimitStage(
  captured: {
    finishReason?: string;
    toolRequestFailure?: { kind: "unparsed_request" };
    messages: Array<ChatMessage>;
    predictionUsage?: {
      reasoningTokensCount?: number;
      visibleTokensCount?: number;
      visibleChars?: number;
      rawToolIntentCandidate?: boolean;
    };
  },
  finalizing: boolean,
  toolGeneration: { hasUnfinished: () => boolean },
): OutputLimitStage | undefined {
  if (captured.finishReason !== "maxPredictedTokensReached") return undefined;
  if (captured.toolRequestFailure) return "tool_arguments";
  const finalRawToolIntent = hasRawToolIntent(visibleTextFromMessages(captured.messages), captured.messages);
  if ((captured.predictionUsage?.rawToolIntentCandidate === true || finalRawToolIntent)
    && !captured.messages.some(message => message.getToolCallRequests().length > 0)) {
    return "tool_arguments";
  }
  if (finalizing) return "forced_final";
  if (toolGeneration.hasUnfinished()
    || captured.messages.some(message => message.getToolCallRequests().length > 0)) {
    return "tool_arguments";
  }
  if (visibleTextFromMessages(captured.messages).trim()) return "visible_report";
  if (Number(captured.predictionUsage?.reasoningTokensCount || 0) > 0) return "reasoning";
  return "unknown";
}

export function canRecoverOutputLimit(
  captured: {
    finishReason?: string;
    failure?: unknown;
    continueAfterTools: boolean;
    messages: Array<ChatMessage>;
    predictionUsage?: {
      reasoningTokensCount?: number;
      visibleTokensCount?: number;
      visibleChars?: number;
      rawToolIntentCandidate?: boolean;
    };
  },
  toolGeneration: { hasUnfinished: () => boolean },
): boolean {
  if (captured.finishReason !== "maxPredictedTokensReached"
    || captured.failure !== undefined || captured.continueAfterTools) return false;
  if (captured.messages.some(message => message.getToolCallRequests().length > 0
    || message.getToolCallResults().length > 0)) return false;
  const reportText = visibleTextFromMessages(captured.messages);
  return classifyOutputLimitStage(captured, false, toolGeneration) === "visible_report"
    && Boolean(reportText.trim())
    && captured.predictionUsage?.rawToolIntentCandidate !== true
    && !hasRawToolIntent(reportText, captured.messages);
}

export function classifyFinalDelivery(
  reportText: string,
  captured: { failure?: unknown; finishReason?: string; unexecutedToolIntent?: boolean },
  phaseTimedOut: boolean,
  messages: Array<ChatMessage>,
): { deliveryState: FinalDeliveryState; reportState: FinalReportState; rejectionReason?: string } {
  const text = reportText.trim();
  if (captured.unexecutedToolIntent && !text) return { deliveryState: "partial", reportState: "unresolved_tool_intent",
    rejectionReason: "unresolved_tool_intent" };
  if (!text) return { deliveryState: "no_answer", reportState: "no_answer",
    rejectionReason: phaseTimedOut ? "final_timeout"
      : captured.failure !== undefined ? "generation_failure"
        : captured.finishReason === "maxPredictedTokensReached" ? "max_predicted_tokens" : "empty_visible_output" };
  const unresolvedToolIntent = captured.unexecutedToolIntent || hasRawToolIntent(text, messages);
  let deliveryState: FinalDeliveryState;
  let rejectionReason: string | undefined;
  if (captured.finishReason === "maxPredictedTokensReached") {
    deliveryState = "truncated";
    rejectionReason = "max_predicted_tokens";
  } else if (phaseTimedOut) {
    deliveryState = "partial";
    rejectionReason = "final_timeout";
  } else if (captured.failure !== undefined) {
    deliveryState = "partial";
    rejectionReason = "generation_failure";
  } else if (unresolvedToolIntent) {
    deliveryState = "partial";
    rejectionReason = "unresolved_tool_intent";
  } else if (!["eosFound", "stopStringFound"].includes(captured.finishReason || "")) {
    deliveryState = "partial";
    rejectionReason = `unexpected_finish_reason:${captured.finishReason || "unknown"}`;
  } else {
    deliveryState = "complete";
  }
  return {
    deliveryState,
    reportState: unresolvedToolIntent ? "unresolved_tool_intent"
      : deliveryState === "complete" ? "report" : "partial_report",
    ...(rejectionReason ? { rejectionReason } : {}),
  };
}

/** Visible chars that make the failing round's text the answer rather than a
 * progress preface ("파일을 읽겠습니다."). Below it, the evidence report stands in. */
export const ANSWERED_ROUND_MIN_CHARS = 400;

type PartialReport = { text: string; evidenceCount: number; errorCount: number };

/** Status for a round whose streamed answer stays the deliverable: what ran in
 * that round and what did not. No excerpts, working notes or model-input frames. */
export function answeredRoundToolFailureNotice(currentMessages: Array<ChatMessage>, tools: Array<RemoteToolLike>,
  request: string, reason: string): PartialReport {
  const { facts, errors } = returnedObservationFacts(observationRecords(currentMessages, tools,
    { confirmedPairsOnly: tools.length > 0 }));
  const cause = reason === "tool_arguments_output_limit" ? "인수가 출력 생성 한도에서 잘려" : "인수를 해석할 수 없어";
  const lines = [`[응답 상태 · 미완료] 위 내용 뒤에 이어서 생성한 ${request}은(는) ${cause} 실행되지 않았습니다. 자동으로 다시 실행하지 않습니다.`];
  if (facts.length) lines.push(`- 같은 응답에서 완료된 조회: ${facts.join("; ")}`);
  if (errors.length) lines.push(`- 도구 오류: ${errors.slice(0, 4).join(", ")}`);
  lines.push("- 실행되지 않은 요청의 결과는 없습니다. 위 내용 중 그 결과에 기대거나 그 작업을 예고한 부분은 확인되지 않은 상태입니다.");
  return { text: lines.join("\n"), evidenceCount: facts.length, errorCount: errors.length };
}

/** Previous calls without a result, for the user. The continuity frame itself is model input. */
function pendingCallLines(messages: Array<ChatMessage>): string[] {
  const view = taskProgressView(messages) as { interruptedCalls?: Array<{ toolName?: unknown; effect?: unknown }> } | null;
  const calls = Array.isArray(view?.interruptedCalls) ? view.interruptedCalls : [];
  if (!calls.length) return [];
  const unknown = calls.filter(call => call?.effect !== "not_applied").length;
  const names = [...new Set(calls.map(call => typeof call?.toolName === "string" ? call.toolName : "")
    .filter(name => /^[A-Za-z0-9_.:-]{1,80}$/u.test(name)))].slice(0, 6);
  return [`- 결과를 받지 못한 이전 도구 요청 ${calls.length}건${unknown ? ` (적용 여부 불명 ${unknown}건)` : ""}${names.length ? `: ${names.join(", ")}` : ""}`];
}

export function evidenceBackedPartialReport(history: Chat, currentMessages: Array<ChatMessage>, reason: string,
  tools: Array<RemoteToolLike> = [], candidates: readonly ReviewRow[] = []): PartialReport {
  // Favor the most recent returned evidence, not the oldest eight results.
  // Normalize each paired history independently: current messages may already
  // be committed in history. Joining both first would duplicate their call IDs.
  const pairing = { confirmedPairsOnly: tools.length > 0 };
  const { facts, errors, excerpts } = returnedObservationFacts([...observationRecords(history.getMessagesArray(), tools, pairing),
    ...observationRecords(currentMessages, tools, pairing)]);
  // Rows parsed from reasoning are working notes (conversation 1794386851846
  // showed packet-schema deliberation as "findings"); only stated ones are shown.
  const lines = ["부분 조사 보고 (미완료)", ...reviewCandidateLines(candidates.filter(row => row.source === "visible")), "확인된 근거:"];
  if (facts.length) for (const fact of facts) lines.push(`- ${fact}`);
  else lines.push("- 현재 입력에서 구조화된 성공 결과를 확인하지 못했습니다.");
  if (excerpts.length) lines.push("", "반환된 파일 본문 발췌:", ...excerpts, "");
  lines.push("미확인/중단 범위:");
  lines.push(`- ${String(reason || "조사 복구가 진행되지 않음").slice(0, 240)}`);
  if (errors.length) lines.push(`- 도구 오류: ${errors.slice(0, 4).join(", ")}`);
  lines.push(...pendingCallLines(history.getMessagesArray()));
  lines.push("- 이 출력은 안전한 부분 보고이며 전체 조사 완료로 처리하지 않았습니다.");
  return { text: lines.join("\n"), evidenceCount: facts.length, errorCount: errors.length };
}

function returnedObservationFacts(records: ReturnType<typeof observationRecords>) {
  const facts: Array<string> = [];
  const errors: Array<string> = [];
  const seen = new Set<string>();
  const excerpts: Array<string> = [];
  let excerptChars = 0;
  for (const record of [...records].reverse()) {
    const value = record.value;
    if (!value || typeof value !== "object") continue;
    const failed = sourceObservationFailed(value);
    if (failed) {
      const error = String(value.errorCode || value.error || value.status || "unknown_error").slice(0, 120);
      if (!errors.includes(error)) errors.push(error);
      continue;
    }
    const kind = String(value.kind || "");
    let fact = "";
    const nativeFile = record.trustedRead && value.provider === ENGINE_PROFILES.unreal.fileProvider
      && ENGINE_PROFILES.unreal.reportReadTools.includes(String(value.toolName))
      && typeof value.path === "string" && typeof value.content === "string";
    if (kind === "workspace_file_observation" || nativeFile) {
      const status = String(value.resultStatus || value.status || "");
      if (value.pending === true || ["pending", "running", "unknown", "ambiguous"].includes(status)
        || !(value.ok === true || ["observed", "complete"].includes(status))) continue;
      const target = String(value.path || value.sourcePath || "unknown-file").slice(0, 240);
      const returned = coverageRange(value, "source");
      const range = returned?.rangeUnit === "line" ? `lines ${returned.range[0]}–${returned.range[1]}`
        : returned?.rangeUnit === "utf8_byte" ? `bytes [${returned.range[0]}, ${returned.range[1] + 1})`
          : "range unknown";
      fact = `file ${target} ${range} (반환된 관측 범위)`;
      const body = [value.text, value.content, value.body].find(item => typeof item === "string");
      if (!seen.has(fact) && typeof body === "string" && body && excerptChars < 2400) {
        const excerpt = redact(body).slice(0, Math.min(800, 2400 - excerptChars));
        excerptChars += excerpt.length;
        excerpts.push(`${target} — 본문 일부 발췌, 전체 파일 분석 아님:\n${excerpt.split(/\r?\n/u).map(line => `    ${line}`).join("\n")}`);
      }
    } else if (kind === "historical_evidence_range") {
      const id = String(value.evidenceId || "unknown-evidence").slice(0, 80);
      const range = Array.isArray(value.returnedRange) ? JSON.stringify(value.returnedRange) : "unknown-range";
      fact = `archive ${id} range ${range}`;
    } else if (kind === "archived_tool_result_projection") {
      const id = String((value.archiveRef as Record<string, unknown> | undefined)?.evidenceId
        || "unknown-evidence").slice(0, 80);
      const range = Array.isArray(value.projectedBodyRanges)
        ? JSON.stringify(value.projectedBodyRanges) : "unknown-range";
      fact = `archived projection ${id} body range ${range}`;
    } else if (kind === "git_observation") {
      // "status workspace page 1–55" read as noise; name the command and its returned items.
      const action = String(value.action || "status").slice(0, 80);
      const target = typeof value.path === "string" && value.path ? ` ${value.path.slice(0, 160)}` : "";
      const start = value.pageStart, end = value.pageEnd, total = value.total;
      const items = start !== undefined || end !== undefined
        ? ` (항목 ${String(start ?? "?")}–${String(end ?? "?")}${Number.isSafeInteger(total) ? `/${String(total)}` : ""})` : "";
      fact = `git ${action}${target}${items}`;
    } else if (value.action || value.sourceAction) {
      const action = String(value.action || value.sourceAction || "read").slice(0, 80);
      const target = String(value.path || value.sourcePath || "workspace").slice(0, 160);
      const pageStart = value.pageStart ?? value.sourcePageStart;
      const pageEnd = value.pageEnd ?? value.sourcePageEnd;
      const page = pageStart !== undefined || pageEnd !== undefined
        ? ` page ${String(pageStart ?? "?")}–${String(pageEnd ?? "?")}` : "";
      fact = `${action} ${target}${page}`;
    }
    if (fact && !seen.has(fact)) { seen.add(fact); facts.push(fact); }
    if (facts.length >= 8) break;
  }
  return { facts, errors, excerpts };
}

/** Pure terminal presentation. Evidence/claim owners determine the notices;
 * the prediction loop only appends them before its immutable terminal snapshot. */
export function terminalReportNotices(history: Chat, answer: string, options: {
  returnedSources?: import("./context-ports").ReturnedSources;
  packetQuoteGaps: QuoteMismatch[]; packetEngineGaps: EngineClaimGap[]; engine: string; observeOnly: boolean;
   reviewedReport: boolean; reportReviewBlocked: boolean; reportReviewEmpty: boolean; reportReviewDuplicateOnly?: boolean; reviewMissing: ReviewRow[];
  objectiveKind?: ObjectiveKind;
}) {
  const notices: Array<{event: string; text: string; details?: Record<string, unknown>}> = [];
  const add = (event: string, text: string | null, details?: Record<string, unknown>) => { if (text) notices.push({event,text,details}); };
  add("verification_scope_notice", verificationScopeNotice(history, answer));
  add("partial_search_scope_notice", partialSearchScopeNotice(history, answer));
  add("quote_anchoring_notice", answer ? quoteAnchoringNotice(options.packetQuoteGaps) : null, {mismatches:options.packetQuoteGaps.length});
  const remaining = options.reviewedReport ? unresolvedEngineClaimGaps(options.packetEngineGaps, answer) : options.packetEngineGaps;
  if (answer && !options.reviewedReport) add("engine_claim_notice", engineClaimNotice(options.packetEngineGaps), {gaps:options.packetEngineGaps.length});
  if (answer && !options.observeOnly) {
    const source = options.returnedSources || {files:[],contractIds:new Set<string>(),scope:"execution" as const};
    const visible = history.getMessagesArray(), evidence = returnedEngineEvidence(source);
    const gaps = unresolvedReportEngineClaimGaps(answer, evidence, options.engine).filter(gap => !remaining.some(item => item.factId === gap.factId));
    if (options.reviewedReport) gaps.push(...remaining);
    add("report_engine_claim_notice", reportEngineClaimNotice(gaps,
      unresolvedEngineClaimGaps(textEngineClaimGaps(confirmedProgressStatements(visible, answer), evidence, options.engine, "progress"), answer)), {gaps:gaps.length});
    add("suggested_code_scope_notice", suggestedCodeNotice(visible, answer, options.engine, source));
    add("file_coverage_notice", fileCoverageNotice(source, answer));
    // The same gap the review was given; it stays when the supplement did not resolve it.
    if (options.objectiveKind && REPORT_REVIEW_OBJECTIVES.has(options.objectiveKind))
      add("coverage_scope_notice", cleanVerdictScopeGap(answer, source)?.notice ?? null);
    if (options.reportReviewBlocked || options.reportReviewEmpty)
      add("report_review_notice", reportReviewNotice(options.reviewMissing, options.reportReviewDuplicateOnly ? "duplicate"
        : options.reportReviewEmpty ? "empty" : "budget"));
  }
  return notices;
}

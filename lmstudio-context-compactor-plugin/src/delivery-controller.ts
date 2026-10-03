import {
  Chat,
  ChatMessage
} from "@lmstudio/sdk";
import type { DirectConfig, FinalizationTrigger } from "./execution-contracts";
import { type FinalDeliveryState, type FinalReportState, type OutputLimitStage } from "./execution-contracts";
import { containsUnresolvedToolIntent, visibleTextFromMessages } from "./raw-tool-intent";
import { coverageRange, observationRecords, sourceObservationFailed } from "./evidence-manager";
import type { RemoteToolLike } from "./tool-capability-registry";
const { redact } = require("./evidence-archive.js") as { redact(value: string): string };

export class DeliveryController {
  attempts = 0;
  constructor(private readonly tools: Array<RemoteToolLike> = []) { }
  // Terminal parse failure never repairs or retries an operation. Deliver only
  // returned observations; unfinished model claims are not report evidence.
  failedToolRequest(history: Chat, messages: Array<ChatMessage>, reason: string) {
    const report = evidenceBackedPartialReport(history, messages, reason, this.tools);
    return { ...report, text: `${report.text}\n- 도구 요청 인수를 완성하지 못해 해당 요청은 실행되지 않았습니다.\n- 실행되지 않은 요청의 검증 결과는 없으며, 반환 기록만으로 전체 조사나 원인 판단이 완료된 것은 아닙니다.` };
  }
  limits(config: Pick<DirectConfig, "maxOutputReserve" | "auditFinalMaxTokens" | "auditFinalSeconds"
    | "outputRecoveryMaxTokens" | "outputRecoverySeconds">, bounded: boolean, trigger: FinalizationTrigger | null) {
    // A context/read recovery does not opt the user into bounded audit. Keep
    // its report under the ordinary token cap and user cancellation signal.
    if (trigger === "output_recovery") return {
      maxTokens: Math.min(config.maxOutputReserve, config.outputRecoveryMaxTokens),
      timeoutMs: Math.max(1, config.outputRecoverySeconds * 1000),
    };
    return {
      maxTokens: bounded ? Math.min(config.maxOutputReserve, config.auditFinalMaxTokens) : config.maxOutputReserve,
      timeoutMs: bounded ? Math.max(1, config.auditFinalSeconds * 1000) : null,
    };
  }
  begin(bounded: boolean, replanAttempts: number) {
    const limit = bounded ? 1 : replanAttempts > 0 ? 2 : 1;
    const allowed = this.attempts < limit;
    if (allowed) this.attempts++;
    return { allowed, limit };
  }
  evaluate(history: Chat, captured: Parameters<typeof classifyFinalDelivery>[1] & { messages: Array<ChatMessage> },
    timedOut: boolean, trigger: FinalizationTrigger | null, noProgressRounds: number,
    objectiveSatisfied: boolean | null = null) {
    const text = visibleTextFromMessages(captured.messages).trim();
    const classified = classifyFinalDelivery(text, captured, timedOut, captured.messages);
    const forced = trigger === "research_recovery_exhausted" && noProgressRounds > 0;
    const delivery = forced && classified.deliveryState === "complete"
      ? { ...classified, deliveryState: "partial" as const, reportState: "partial_report" as const, rejectionReason: "research_no_progress" }
      : classified;
    const needsPartial = delivery.deliveryState !== "complete" && (delivery.reportState === "unresolved_tool_intent"
      || ["research_recovery_complete", "research_recovery_exhausted"].includes(trigger || ""));
    return {
      generationCompleted: ["eosFound", "stopStringFound"].includes(captured.finishReason || "")
        && captured.failure === undefined && !timedOut,
      reportDelivered: delivery.deliveryState === "complete" || needsPartial,
      researchTerminated: trigger !== null,
      objectiveSatisfied,
      taskCompleted: objectiveSatisfied === true && delivery.deliveryState === "complete",
delivery, partial: needsPartial ? evidenceBackedPartialReport(history, captured.messages,
        delivery.rejectionReason || trigger || "research_incomplete", this.tools) : null
};
  }
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
  const finalRawToolIntent = containsUnresolvedToolIntent(visibleTextFromMessages(captured.messages), captured.messages);
  if ((captured.predictionUsage?.rawToolIntentCandidate === true || finalRawToolIntent)
    && !captured.messages.some(message => message.getToolCallRequests().length > 0)) {
    return "tool_arguments";
  }
  if (finalizing) return "forced_final";
  if (toolGeneration.hasUnfinished()
    || captured.messages.some(message => message.getToolCallRequests().length > 0)) {
    return "tool_arguments";
  }
  const visibleTokens = Number(captured.predictionUsage?.visibleTokensCount || 0);
  if (visibleTokens > 0 || visibleTextFromMessages(captured.messages)) return "visible_report";
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
    && (Boolean(reportText) || Number(captured.predictionUsage?.visibleChars || 0) > 0)
    && captured.predictionUsage?.rawToolIntentCandidate !== true
    && !containsUnresolvedToolIntent(reportText, captured.messages);
}

export function classifyFinalDelivery(
  reportText: string,
  captured: { failure?: unknown; finishReason?: string },
  phaseTimedOut: boolean,
  messages: Array<ChatMessage>,
): { deliveryState: FinalDeliveryState; reportState: FinalReportState; rejectionReason?: string } {
  const text = reportText.trim();
  if (!text) return { deliveryState: "no_answer", reportState: "no_answer",
    rejectionReason: phaseTimedOut ? "final_timeout"
      : captured.failure !== undefined ? "generation_failure"
        : captured.finishReason === "maxPredictedTokensReached" ? "max_predicted_tokens" : "empty_visible_output" };
  const unresolvedToolIntent = containsUnresolvedToolIntent(text, messages);
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

export function evidenceBackedPartialReport(history: Chat, currentMessages: Array<ChatMessage>, reason: string,
  tools: Array<RemoteToolLike> = []): {
  text: string;
  evidenceCount: number;
  errorCount: number;
} {
  const facts: Array<string> = [];
  const errors: Array<string> = [];
  const seen = new Set<string>();
  const excerpts: Array<string> = [];
  let excerptChars = 0;
  // Favor the most recent returned evidence, not the oldest eight results.
  // Normalize each paired history independently: current messages may already
  // be committed in history. Joining both first would duplicate their call IDs.
  const pairing = { confirmedPairsOnly: tools.length > 0 };
  const records = [...observationRecords(history.getMessagesArray(), tools, pairing),
    ...observationRecords(currentMessages, tools, pairing)];
  for (const record of records.reverse()) {
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
    const nativeFile = record.trustedRead && value.provider === "mcp/unreal-agent"
      && ["read_file", "read_file_range"].includes(String(value.toolName))
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
    } else if (kind === "git_observation" || value.action || value.sourceAction) {
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
  const lines = ["부분 조사 보고 (미완료)", "확인된 근거:"];
  if (facts.length) for (const fact of facts) lines.push(`- ${fact}`);
  else lines.push("- 현재 입력에서 구조화된 성공 결과를 확인하지 못했습니다.");
  if (excerpts.length) lines.push("", "반환된 파일 본문 발췌:", ...excerpts, "");
  lines.push("미확인/중단 범위:");
  lines.push(`- ${String(reason || "조사 복구가 진행되지 않음").slice(0, 240)}`);
  if (errors.length) lines.push(`- 도구 오류: ${errors.slice(0, 4).join(", ")}`);
  lines.push("- 이 출력은 안전한 부분 보고이며 전체 조사 완료로 처리하지 않았습니다.");
  return { text: lines.join("\n"), evidenceCount: facts.length, errorCount: errors.length };
}

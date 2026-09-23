import {
  Chat,
  ChatMessage
} from "@lmstudio/sdk";
import { toolMemory } from "./context-ports";
import type { FinalizationTrigger } from "./execution-contracts";
import { type FinalDeliveryState, type FinalReportState, type OutputLimitStage } from "./execution-contracts";
import { containsUnresolvedToolIntent, visibleTextFromMessages } from "./raw-tool-intent";

export class DeliveryController {
  attempts = 0;
  begin(bounded: boolean, replanAttempts: number) {
    const limit = bounded ? 1 : replanAttempts > 0 ? 2 : 1;
    const allowed = this.attempts < limit;
    if (allowed) this.attempts++;
    return { allowed, limit };
  }
  evaluate(history: Chat, captured: Parameters<typeof classifyFinalDelivery>[1] & { messages: Array<ChatMessage> },
    timedOut: boolean, trigger: FinalizationTrigger | null, noProgressRounds: number) {
    const text = visibleTextFromMessages(captured.messages).trim();
    const classified = classifyFinalDelivery(text, captured, timedOut, captured.messages);
    const forced = trigger === "research_recovery_exhausted" && noProgressRounds > 0;
    const delivery = forced && classified.deliveryState === "complete"
      ? { ...classified, deliveryState: "partial" as const, reportState: "partial_report" as const, rejectionReason: "research_no_progress" }
      : classified;
    const needsPartial = delivery.deliveryState !== "complete" && (delivery.reportState === "unresolved_tool_intent"
      || ["research_recovery_complete", "research_recovery_exhausted"].includes(trigger || ""));
    return {
delivery, partial: needsPartial ? evidenceBackedPartialReport(history, captured.messages,
        delivery.rejectionReason || trigger || "research_incomplete") : null
};
  }
}

export function classifyOutputLimitStage(
  captured: {
    finishReason?: string;
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
  if (!text) return { deliveryState: "no_answer", reportState: "no_answer" };
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

export function evidenceBackedPartialReport(history: Chat, currentMessages: Array<ChatMessage>, reason: string): {
  text: string;
  evidenceCount: number;
  errorCount: number;
} {
  const facts: Array<string> = [];
  const errors: Array<string> = [];
  const seen = new Set<string>();
  for (const message of [...history.getMessagesArray(), ...currentMessages]) {
    for (const result of message.getToolCallResults()) {
      const value = toolMemory.decodeToolResultRecord(result.content).value;
      if (!value || typeof value !== "object") continue;
      const status = String(value.status || "").toLowerCase();
      const failed = value.ok === false || Boolean(value.errorCode)
        || ["error", "failed", "timeout", "timed_out", "canceled", "cancelled", "denied"].includes(status);
      if (failed) {
        const error = String(value.errorCode || value.status || "unknown_error").slice(0, 120);
        if (!errors.includes(error)) errors.push(error);
        continue;
      }
      const kind = String(value.kind || "");
      let fact = "";
      if (kind === "historical_evidence_range") {
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
    if (facts.length >= 8) break;
  }
  const lines = ["부분 조사 보고 (미완료)", "확인된 근거:"];
  if (facts.length) for (const fact of facts) lines.push(`- ${fact}`);
  else lines.push("- 현재 입력에서 구조화된 성공 결과를 확인하지 못했습니다.");
  lines.push("미확인/중단 범위:");
  lines.push(`- ${String(reason || "조사 복구가 진행되지 않음").slice(0, 240)}`);
  if (errors.length) lines.push(`- 도구 오류: ${errors.slice(0, 4).join(", ")}`);
  lines.push("- 이 출력은 안전한 부분 보고이며 전체 조사 완료로 처리하지 않았습니다.");
  return { text: lines.join("\n"), evidenceCount: facts.length, errorCount: errors.length };
}

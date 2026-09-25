import { ChatMessage, type Chat } from "@lmstudio/sdk";
import type { FinalizationTrigger } from "./execution-contracts";
import type { CapturedRound } from "./round-loop";

export type ExecutionPhase = "RESEARCH" | "READ_RECOVERY" | "TOOL_REPLAN"
  | "FINAL_DELIVERY" | "OUTPUT_RECOVERY" | "TERMINATED";
export const ALLOWED_TRANSITIONS: Readonly<Record<ExecutionPhase, readonly ExecutionPhase[]>> = {
  RESEARCH: ["READ_RECOVERY", "TOOL_REPLAN", "FINAL_DELIVERY", "OUTPUT_RECOVERY", "TERMINATED"],
  READ_RECOVERY: ["RESEARCH", "TOOL_REPLAN", "FINAL_DELIVERY", "OUTPUT_RECOVERY", "TERMINATED"],
  TOOL_REPLAN: ["READ_RECOVERY", "FINAL_DELIVERY", "TERMINATED"],
  FINAL_DELIVERY: ["TOOL_REPLAN", "TERMINATED"],
  OUTPUT_RECOVERY: ["TERMINATED"],
  TERMINATED: [],
};
export class ExecutionState {
  private current: ExecutionPhase = "RESEARCH";
  private trigger: FinalizationTrigger | null = null;
  readonly transitions: Array<{
from: ExecutionPhase; to: ExecutionPhase; reason: string;
    planningEffect: "commit_completed_only"; evidenceEffect: "retain_returned"
}> = [];
  get phase() { return this.current; }
  get finalizing() { return this.current === "FINAL_DELIVERY" || this.current === "OUTPUT_RECOVERY"; }
  get recovering() { return this.current === "READ_RECOVERY" || this.current === "TOOL_REPLAN"; }
  get replanning() { return this.current === "TOOL_REPLAN"; }
  get finalizationTrigger() { return this.trigger; }
  transition(to: ExecutionPhase, reason: string) {
    if (!reason.trim() || !ALLOWED_TRANSITIONS[this.current].includes(to)) {
      throw new Error(`ILLEGAL_EXECUTION_TRANSITION: ${this.current} -> ${to} (${reason})`);
    }
    this.transitions.push({
from: this.current, to, reason,
      planningEffect: "commit_completed_only", evidenceEffect: "retain_returned"
});
    this.current = to;
  }
  finishResearch(trigger: FinalizationTrigger) {
    this.transition(trigger === "output_recovery" ? "OUTPUT_RECOVERY" : "FINAL_DELIVERY", trigger);
    this.trigger = trigger;
  }
  startRecovery(reason: string) { this.transition("READ_RECOVERY", reason); this.trigger = null; }
  startReplan() { this.transition("TOOL_REPLAN", "fresh_structured_planning"); this.trigger = null; }
  finishReplan() { if (this.replanning) this.transition("READ_RECOVERY", "planning_round_returned"); }
  endRecovery() { if (this.recovering) this.transition("RESEARCH", "recovery_report_returned"); }
  terminate(reason: string) { if (this.current !== "TERMINATED") this.transition("TERMINATED", reason); }
}

/** One round is a transaction: returned evidence survives; incomplete prose does not. */
export class RoundTransaction {
  readonly planningCommitted: boolean;
  readonly returnedIds: Set<string>;
  constructor(readonly captured: CapturedRound, aborted: boolean) {
    this.planningCommitted = captured.predictionCompleted === true && !aborted && captured.failure === undefined
      && ["eosFound", "stopStringFound"].includes(captured.finishReason || "");
    this.returnedIds = new Set(captured.messages.flatMap(m => m.getToolCallResults()).map(r => String(r.toolCallId)));
  }
  durable(message: ChatMessage): ChatMessage | null {
    if (!message.isAssistantMessage() || this.planningCommitted) return message;
    const requests = message.getToolCallRequests().filter(r => this.returnedIds.has(String(r.id)));
    if (!requests.length) return null;
    return ChatMessage.from({ role: "assistant", content: requests.map(toolCallRequest => ({ type: "toolCallRequest", toolCallRequest })) });
  }
  commitEvidence(history: Chat) {
    for (const message of this.captured.messages) {
      const durable = this.durable(message);
      if (durable) history.append(durable);
    }
  }
}

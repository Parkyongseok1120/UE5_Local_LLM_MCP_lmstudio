import type { TaskObjective } from "./execution-contracts";
import { reportsNoChangeNeeded } from "./tool-capability-registry";

export const EXECUTION_RETURN_FACT = "Runtime execution check: the user approved changes and this turn has applied none. "
  + "Apply them now with the mutation tools. If they are already in place, say so with the file evidence; "
  + "if one decision is missing, ask only that one question.";
export const EXECUTION_RETURN_STATUS = "승인된 수정이 아직 적용되지 않아 수정 도구로 한 번 더 진행합니다.";

type Host = { createStatus(state: { status: "done"; text: string }): unknown; debug(value: Record<string, unknown>): void };
type Facts = { set(key: "execution_return", text: string): void };

/** Conversation 1794469194476: "진행해" ran as mutate, the model answered with
 * example code and a confirmation question, and the ordinary end-of-answer
 * path closed the turn with nothing applied. Such an answer gets one return
 * to the tools, for an approval only (a proposal or plan the user accepted):
 * a direct request such as "작성해" can rightly be answered in text. Not for a
 * build request (a build is its own result), an answer that reports the change
 * already in place, a stopped or failed round, or tools that cannot write. */
export class ExecutionReturn {
  private used = false;
  constructor(private readonly host: Host, private readonly debug: boolean, private readonly writable: () => boolean) {}
  due(objective: TaskObjective, captured: { finishReason?: string; failure?: unknown }, answer: string, facts: Facts,
    aborted: boolean): boolean {
    if (this.used || aborted || captured.failure !== undefined) return false;
    if (objective.kind !== "mutate" || objective.mutationState !== "none" || !objective.approvedItems) return false;
    if (captured.finishReason !== "eosFound" && captured.finishReason !== "stopStringFound") return false;
    if (reportsNoChangeNeeded(answer) || !this.writable()) return false;
    this.used = true;
    facts.set("execution_return", EXECUTION_RETURN_FACT);
    this.host.createStatus({ status: "done", text: EXECUTION_RETURN_STATUS });
    if (this.debug) this.host.debug({ event: "execution_return_scheduled", approvedItems: objective.approvedItems?.length ?? 0 });
    return true;
  }
}

import type { ChatMessage } from "@lmstudio/sdk";
import { toolMemory } from "./context-ports";

// Runtime evidence observations derived from the chat the model is about to
// read. They are views of state the chat and the archive already own: nothing
// here is stored, and nothing selects or blocks a tool.

const MAX_PROJECTION_LINES = 6;

type ProjectionView = { toolName: string; path: string; shown: number; total: number; readHandle: string };

function partialProjections(messages: ChatMessage[]): ProjectionView[] {
  const views: ProjectionView[] = [];
  for (const message of messages) {
    for (const result of message.getToolCallResults()) {
      const value = toolMemory.decodeToolResultRecord((result as { content?: unknown }).content).value as
        Record<string, any> | null;
      if (value?.kind !== "archived_tool_result_projection" || value.bodyComplete !== false) continue;
      const readHandle = value.archiveRef?.readHandle;
      if (typeof readHandle !== "string" || !readHandle) continue;
      const total = Number(value.bodyTotalChars);
      const omitted = Number(value.bodyOmittedChars);
      views.push({ toolName: String(value.toolName || "tool"), path: String(value.sourceIdentity?.path || "").slice(0, 160),
        shown: Number.isFinite(total) && Number.isFinite(omitted) ? Math.max(0, total - omitted) : 0,
        total: Number.isFinite(total) ? total : 0, readHandle });
    }
  }
  return views;
}

/** One short list of the results this input carries only in part. Each
 * result's own notice stays; this gathers them where the model reads its
 * runtime facts, so "read earlier" is not mistaken for "present now". */
export function partialProjectionFact(messages: ChatMessage[]): { fact: string | null; count: number } {
  const views = partialProjections(messages);
  if (!views.length) return { fact: null, count: 0 };
  const lines = views.slice(-MAX_PROJECTION_LINES).map(view =>
    `- ${view.toolName} ${view.path || "(path not recorded)"}: chars 0-${view.shown} of ${view.total}; readHandle=${view.readHandle}`);
  const more = views.length > MAX_PROJECTION_LINES ? [`- ${views.length - MAX_PROJECTION_LINES} more partial results not listed`] : [];
  return { count: views.length, fact: [
    `Runtime evidence observation: ${views.length} earlier tool result(s) are present in this input only in part (compacted).`,
    ...lines, ...more,
    "Partial here does not mean the source was never read, and it does not require restoring every omitted body. "
      + "Use the evidence already present when it answers the current question. When a specific claim needs omitted text, "
      + "page only that scope with evidence_first_read_context(action=read, readHandle, view=body), or read the focused "
      + "current source range if the handle is unavailable. Do not claim whole-source review or absence from a partial body.",
  ].join("\n") };
}

/** The previous assistant turn ended after tool results with no answer text:
 * the retained history does not establish why no answer is present. */
export function priorTurnInterrupted(messages: ChatMessage[]): boolean {
  let latestUser = -1;
  messages.forEach((message, index) => { if (message.getRole() === "user") latestUser = index; });
  let previousUser = -1;
  for (let index = latestUser - 1; index >= 0; index--) if (messages[index].getRole() === "user") { previousUser = index; break; }
  if (latestUser < 0 || previousUser < 0) return false;
  const turn = messages.slice(previousUser + 1, latestUser).filter(message => !message.isSystemPrompt());
  const last = turn.at(-1);
  return Boolean(last && last.getRole() === "tool" && turn.some(message => message.getToolCallRequests().length > 0));
}

export const PRIOR_TURN_INTERRUPTED_FACT = "Runtime evidence observation: the previous assistant turn ended after tool "
  + "results without a final answer in the retained history (cause not recorded). Its intermediate statements are drafts, not verified findings; "
  + "re-check each against source evidence before reporting it.";

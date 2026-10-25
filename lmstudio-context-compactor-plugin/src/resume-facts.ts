import type { ChatMessage } from "@lmstudio/sdk";
import { toolMemory } from "./context-ports";

// Runtime evidence observations derived from the chat the model is about to
// read. They are views of state the chat and the archive already own: nothing
// here is stored, and nothing selects or blocks a tool.

const MAX_PROJECTION_FILES = 10;
const MAX_RANGES_PER_FILE = 4;
const MAX_PROJECTION_FACT_CHARS = 2400;

type ProjectionView = { toolName: string; path: string; shown: number; total: number; readHandle: string;
  lines: [number, number] | null; outline: string[] };
const MAX_OUTLINE_SPANS = 4;

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
      const start = Number(value.sourceFacts?.startLine), end = Number(value.sourceFacts?.endLine);
      views.push({ toolName: String(value.toolName || "tool"), path: String(value.sourceIdentity?.path || "").slice(0, 160),
        shown: Number.isFinite(total) && Number.isFinite(omitted) ? Math.max(0, total - omitted) : 0,
        total: Number.isFinite(total) ? total : 0, readHandle,
        lines: Number.isInteger(start) && Number.isInteger(end) && start > 0 && end >= start ? [start, end] : null,
        outline: Array.isArray(value.sourceOutline?.entries)
          ? value.sourceOutline.entries.filter((entry: unknown) => typeof entry === "string").slice(0, MAX_OUTLINE_SPANS) : [] });
    }
  }
  return views;
}

function projectionLine(path: string, views: ProjectionView[]): string {
  const ranges = views.slice(-MAX_RANGES_PER_FILE).map(view => (view.lines ? `lines ${view.lines[0]}-${view.lines[1]}` : view.toolName)
    + ` (${view.shown} of ${view.total} chars shown; readHandle=${view.readHandle}`
    + (view.outline.length ? `; spans: ${view.outline.map(entry => entry.slice(0, 60)).join(" | ")}` : "") + ")");
  const more = views.length > MAX_RANGES_PER_FILE ? `; ${views.length - MAX_RANGES_PER_FILE} older partial result(s)` : "";
  return `- ${path || "(path not recorded)"}: ${ranges.join("; ")}${more}`;
}

/** One short list of the results this input carries only in part, by file.
 * Each result's own notice stays; this gathers them where the model reads its
 * runtime facts, so "read earlier" is not mistaken for "present now". It is
 * derived from the input being assembled, so the round that compacts a body
 * already carries it. */
export function partialProjectionFact(messages: ChatMessage[]): { fact: string | null; count: number } {
  const views = partialProjections(messages);
  if (!views.length) return { fact: null, count: 0 };
  const byPath = new Map<string, ProjectionView[]>();
  for (const view of views) byPath.set(view.path, [...(byPath.get(view.path) || []), view]);
  const files = [...byPath].slice(-MAX_PROJECTION_FILES);
  const lines: string[] = [];
  let used = 0;
  for (const [path, entries] of files.reverse()) {
    const line = projectionLine(path, entries);
    if (used + line.length > MAX_PROJECTION_FACT_CHARS) break;
    lines.unshift(line); used += line.length;
  }
  const hiddenFiles = byPath.size - lines.length;
  const more = hiddenFiles > 0 ? [`- ${hiddenFiles} more file(s) with partial results not listed`] : [];
  return { count: views.length, fact: [
    `Runtime evidence observation: ${views.length} earlier tool result(s) are present in this input only in part (compacted), across ${byPath.size} source(s).`,
    ...lines, ...more, PARTIAL_PROJECTION_GUIDANCE,
  ].join("\n") };
}

/** The closing sentence of the partial-projection fact (the output filter
 * recognizes an echo of it by its opening). */
export const PARTIAL_PROJECTION_GUIDANCE = "Partial here does not mean the source was never read, and it does not require restoring every omitted body. "
  + "Use the evidence already present when it answers the current question. The omitted text of these ranges, "
  + "including exact calls, arguments, conditions and UPROPERTY/UFUNCTION specifiers, is not in this input; "
  + "code quoted in your earlier notes or validation packets is not the source. Before quoting such code or "
  + "stating that something is absent there, page only that scope with evidence_first_read_context(action=read, "
  + "readHandle, view=body, startLine/endLine of the span, or symbol=its name), not the whole body from offset 0; "
  + "or read the focused current source range if the handle is unavailable. "
  + "Do not claim whole-source review or absence from a partial body.";

/** The runtime fact after K tool rounds that only re-delivered versions the
 * read ledger already held: write the report from what was returned. */
export function reportNowFact(rounds: number, stats: Record<string, number>): string {
  return `Runtime evidence observation: report_now. The last ${rounds} tool round(s) returned no new file, version or range `
    + `(${stats.redundantRereads || 0} same-sha re-read(s), ${stats.archiveRepages || 0} archive re-page(s) of delivered lines so far). `
    + "The ledger lists last-observed source versions and recorded assistant claims, not fresh disk verification or completed audit checks. "
    + "Write a report from the available evidence, marking unverified items as unresolved. A focused source check or recovery of evidence missing from this input remains allowed when needed for a claim.";
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

import type { Chat } from "@lmstudio/sdk";
const { decodeToolResultRecord } = require("./compaction-tool-memory.js");
const { invariantSystemText } = require("./direct-compaction-core.js");

/** What one composed system message was joined from. Kept as references; the
 * breakdown below is computed only when telemetry asks for it. */
export type SystemSources = { hostTexts: readonly string[]; instructions: readonly string[];
  noteProtocol: string; runtimeDataRule: string };

/** Telemetry only. The system message by the owner of each part (UTF-16 code
 * units; the parts and their separators add up to `totalChars`). The host
 * part is split into the text that never changes and the checkpoint a
 * compaction appended to it. Instruction heads are harness-authored first
 * lines; host text is reported by length only. */
export function systemComposition(sources: SystemSources) {
  const hostChars = sources.hostTexts.reduce((sum, text) => sum + text.length, 0);
  const hostInvariantChars = sources.hostTexts.reduce((sum, text) =>
    sum + String(invariantSystemText({ role: "system", text }) || "").length, 0);
  const instructionChars = sources.instructions.reduce((sum, text) => sum + text.length, 0);
  const parts = [...sources.hostTexts, ...sources.instructions, sources.noteProtocol, sources.runtimeDataRule].filter(Boolean);
  const totalChars = parts.join("\n\n").length;
  return { unit: "utf16_code_units" as const, totalChars, hostInvariantChars, checkpointChars: hostChars - hostInvariantChars,
    instructionChars, instructionCount: sources.instructions.length,
    noteProtocolChars: sources.noteProtocol.length, runtimeDataRuleChars: sources.runtimeDataRule.length,
    separatorChars: totalChars - hostChars - instructionChars - sources.noteProtocol.length - sources.runtimeDataRule.length,
    largestInstructions: sources.instructions.map(text => ({ head: text.split("\n", 1)[0].slice(0, 60), chars: text.length }))
      .sort((a, b) => b.chars - a.chars).slice(0, 5) };
}

/** Telemetry only. UTF-16 code units of one assembled model input, by the
 * part that carries them. The categories partition the input; `subsets` are
 * already counted inside a category and are never added to the total. Token
 * counts are separate measurements: a character share is not a token share. */
export function inputComposition(history: Chat, subsets: { runtimeDataChars: number }) {
  const chars = { systemChars: 0, userChars: 0, assistantTextChars: 0, toolRequestChars: 0,
    rawToolResultChars: 0, projectedToolResultChars: 0 };
  let rawToolResultCount = 0, projectedToolResultCount = 0;
  for (const message of history.getMessagesArray()) {
    const text = message.getText();
    if (message.isSystemPrompt()) chars.systemChars += text.length;
    else if (message.isUserMessage()) chars.userChars += text.length;
    else if (message.isAssistantMessage()) chars.assistantTextChars += text.length;
    for (const request of message.getToolCallRequests()) chars.toolRequestChars += JSON.stringify(request).length;
    for (const result of message.getToolCallResults()) {
      const content = typeof result.content === "string" ? result.content : JSON.stringify(result.content);
      if (decodeToolResultRecord(content).value?.kind === "archived_tool_result_projection") {
        chars.projectedToolResultChars += content.length; projectedToolResultCount++;
      } else { chars.rawToolResultChars += content.length; rawToolResultCount++; }
    }
  }
  const totalChars = Object.values(chars).reduce((sum, value) => sum + value, 0);
  return { unit: "utf16_code_units" as const, ...chars, totalChars, rawToolResultCount, projectedToolResultCount, subsets };
}

export type ProjectionStats = { projectedCount: number; originalChars: number; projectedChars: number;
  skipped: Record<"pinned" | "pending" | "small" | "archiveFailed" | "preservedRaw" | "noSavings", number> };

/** One round's projection passes. A later pass skips results an earlier pass
 * already projected, so projected counts add without double counting. A
 * result kept by one pass is reconsidered by the next, so skip counts are the
 * last pass's: what the round finally left unprojected, and why. */
export function sumProjectionStats(passes: ReadonlyArray<ProjectionStats | undefined>) {
  const ran = passes.filter((pass): pass is ProjectionStats => Boolean(pass));
  const total = { projectedCount: 0, originalChars: 0, projectedChars: 0 };
  for (const pass of ran) {
    total.projectedCount += pass.projectedCount; total.originalChars += pass.originalChars; total.projectedChars += pass.projectedChars;
  }
  return { ...total, savedChars: total.originalChars - total.projectedChars, skipped: ran.at(-1)?.skipped ?? null };
}

import type { Chat } from "@lmstudio/sdk";
const { decodeToolResultRecord } = require("./compaction-tool-memory.js");

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

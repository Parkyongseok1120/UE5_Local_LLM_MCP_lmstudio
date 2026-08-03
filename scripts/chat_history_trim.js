"use strict";

/**
 * Tool/user-safe chat history trimming for LM Studio Qwen/Jinja templates.
 *
 * Root cause of applyPromptTemplate 400:
 *   Jinja: raise_exception('No user query found in messages.')
 * when a naive slice(-N) drops every role=user message and leaves only
 * system + assistant/tool pairs.
 */

function contentText(message) {
  if (!message) return "";
  if (typeof message.content === "string") return message.content;
  if (!Array.isArray(message.content)) return "";
  return message.content
    .filter((p) => p && p.type === "text")
    .map((p) => String(p.text || ""))
    .join("");
}

function findLatestUserIndex(history) {
  for (let i = (history || []).length - 1; i >= 0; i -= 1) {
    if (history[i]?.role === "user" && contentText(history[i]).trim()) return i;
  }
  return -1;
}

function hasUserMessage(history) {
  return findLatestUserIndex(history) >= 0;
}

/**
 * Align start so the tail does not begin on an orphan tool result.
 */
function alignToolPairStart(messages, start) {
  let i = Math.max(0, Number(start) || 0);
  while (i < messages.length && messages[i]?.role === "tool") i += 1;
  return i;
}

/**
 * Trim long chat histories without dropping the latest user query or
 * breaking assistant↔tool pairs at the cut point.
 */
function trimChatHistory(history, options = {}) {
  const maxMessages = Math.max(4, Number(options.maxMessages ?? 18));
  const keepTail = Math.max(4, Number(options.keepTail ?? 12));
  if (!Array.isArray(history) || history.length <= maxMessages) {
    return { history, trimmed: false, reason: "under_limit" };
  }

  const system = history.find((m) => m?.role === "system") || null;
  const latestUser = findLatestUserIndex(history);
  if (latestUser < 0) {
    // Cannot safely trim: Jinja requires a user query. Leave unchanged.
    return { history, trimmed: false, reason: "no_user_present" };
  }

  const suffix = history.slice(latestUser); // includes latest user + in-flight tools
  const before = [];
  for (let i = 0; i < latestUser; i += 1) {
    const msg = history[i];
    if (!msg || msg === system || msg.role === "system") continue;
    before.push(msg);
  }

  const suffixBudget = Math.min(suffix.length, keepTail);
  const olderBudget = Math.max(0, keepTail - suffixBudget);
  let olderStart = Math.max(0, before.length - olderBudget);
  olderStart = alignToolPairStart(before, olderStart);
  const olderTail = before.slice(olderStart);

  const next = [];
  if (system) next.push(system);
  next.push(...olderTail, ...suffix);

  // Final invariant: must still contain a user message.
  if (!hasUserMessage(next)) {
    return {
      history: system ? [system, ...suffix] : suffix.slice(),
      trimmed: true,
      reason: "fallback_suffix_only",
    };
  }

  return {
    history: next,
    trimmed: true,
    reason: "user_preserving_tail",
    latestUserIndex: latestUser,
    beforeLen: before.length,
    olderKept: olderTail.length,
    suffixLen: suffix.length,
  };
}

module.exports = {
  contentText,
  findLatestUserIndex,
  hasUserMessage,
  alignToolPairStart,
  trimChatHistory,
};

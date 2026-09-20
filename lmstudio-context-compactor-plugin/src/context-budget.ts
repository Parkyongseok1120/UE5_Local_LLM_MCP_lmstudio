import { Chat, ChatMessage } from "@lmstudio/sdk";

// Candidate sizes need not be monotone: complete exchanges and checkpoints vary.
// Measure a bounded set explicitly, rather than assuming binary-search ordering.
export async function selectMeasuredCandidate<T>(
  maxMessages: number, targetRemainingTokens: number,
  build: (cap: number) => T,
  remaining: (candidate: T) => Promise<number | null>,
) {
  const maximum = Math.max(2, Math.min(256, maxMessages));
  const caps = [...new Set([2, 4, 8, 16, 32, 64, 128, maximum].filter(n => n <= maximum))].sort((a, b) => b - a);
  let fallback: { candidate: T; remainingTokensAfter: number | null; maxCurrentTurnMessages: number } | null = null;
  for (const cap of caps) {
    const candidate = build(cap), available = await remaining(candidate);
    const item = { candidate, remainingTokensAfter: available, maxCurrentTurnMessages: cap };
    if (available !== null && available >= targetRemainingTokens) return item;
    if (!fallback || (available ?? -Infinity) > (fallback.remainingTokensAfter ?? -Infinity)) fallback = item;
  }
  return fallback!;
}

export const REASONING_SEPARATOR = "__LM_STUDIO_INTERNAL_LSEP_SYNTHETIC_REASONING_END_f4e9a8d2c6b14d0c9e5f3a7b8c1d2e6a__";

// Only completed older assistant messages, only the SDK's structural boundary.
// Never rewrite the latest exchange, infer summaries, or touch call/result parts.
export async function boundPastReasoning(history: Chat, budget: number, count: (text: string) => Promise<number>) {
  if (budget <= 0) return history;
  const messages = history.getMessagesArray();
  let latest = -1;
  for (let i = messages.length - 1; i >= 0; i--) if (messages[i].getRole() === "assistant") { latest = i; break; }
  let remaining = budget, changed = false;
  const copies = [...messages];
  for (let i = latest; i >= 0; i--) {
    const message = messages[i];
    if (message.getRole() !== "assistant") continue;
    const raw = message.getText(), marker = raw.indexOf(REASONING_SEPARATOR);
    if (marker < 0 || raw.indexOf(REASONING_SEPARATOR, marker + REASONING_SEPARATOR.length) >= 0) continue;
    let tokens: number;
    try { tokens = await count(raw.slice(0, marker)); } catch { return history; }
    if (!Number.isFinite(tokens) || tokens < 0) return history;
    if (i === latest || tokens <= remaining) { remaining = Math.max(0, remaining - tokens); continue; }
    const copy = ChatMessage.from(message);
    copy.replaceText(raw.slice(marker + REASONING_SEPARATOR.length)); copies[i] = copy; changed = true;
  }
  if (!changed) return history;
  const result = Chat.empty(); for (const message of copies) result.append(message); return result;
}

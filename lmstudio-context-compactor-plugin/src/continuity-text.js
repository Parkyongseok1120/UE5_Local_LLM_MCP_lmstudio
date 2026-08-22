"use strict";

function clip(value, maxChars, { trim = true } = {}) {
  const raw = String(value ?? "");
  const text = trim ? raw.trim() : raw;
  if (text.length <= maxChars) return text;
  return `${text.slice(0, Math.max(0, maxChars - 18))} …[truncated]`;
}

function sentenceCandidates(text) {
  return String(text || "")
    .split(/(?<=[.!?。！？])\s+|\r?\n+/u)
    .map((line) => line.replace(/^[-*\d.)\s]+/, "").trim())
    .filter(Boolean);
}

function looksElliptical(text) {
  const value = String(text || "").trim();
  if (!value || value.length > 120) return false;
  return /^(?:(?:yes|yeah|yep|ok(?:ay)?|sure|right|네|예|응|어|좋아|그래)|(?:(?:yes|yeah|yep|ok(?:ay)?|sure|right|네|예|응|어|좋아|그래)[,\s]*)?(?:continue|go\s+on|proceed|do\s+it|keep\s+going|계속\s*(?:해|하세요|진행해)?|진행(?:해|하세요|해줘)?|그거\s*(?:해|해줘)|해줘|하자))[\s.!?。！？]*$/iu.test(value);
}

function normalizedTextKey(value) {
  return String(value || "").trim().replace(/\s+/g, " ").toLocaleLowerCase();
}

function recentConversationTail(messages, previousTail = [], options = {}) {
  const maxItems = Math.max(4, Math.min(8, Math.trunc(Number(options.maxItems) || 8)));
  const maxTextChars = Math.max(200, Math.min(4000, Math.trunc(Number(options.maxTextChars) || 2000)));
  const combined = [];
  for (const item of previousTail || []) {
    if (!item || !["user", "assistant"].includes(String(item.role || ""))) continue;
    combined.push({
      role: String(item.role),
      text: clip(item.text, maxTextChars, { trim: false }),
      source: "prior_checkpoint",
    });
  }
  for (const message of messages) {
    if (!message || !["user", "assistant"].includes(message.role) || !message.text) continue;
    combined.push({
      role: message.role,
      text: clip(message.text, maxTextChars, { trim: false }),
      messageIndex: message.index,
      source: "current_history",
    });
  }
  const deduped = [];
  for (const item of combined) {
    const previous = deduped.at(-1);
    if (previous && previous.role === item.role && previous.text === item.text) continue;
    deduped.push(item);
  }
  return deduped.slice(-maxItems);
}

module.exports = {
  clip,
  looksElliptical,
  normalizedTextKey,
  recentConversationTail,
  sentenceCandidates,
};

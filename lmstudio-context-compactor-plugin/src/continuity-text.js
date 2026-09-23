"use strict";

const {
  sanitizeDerivedOperationalText,
  sanitizeUserAuthoredText,
} = require("./durable-memory-sanitizer.js");

const REASONING_SEPARATOR =
  "__LM_STUDIO_INTERNAL_LSEP_SYNTHETIC_REASONING_END_f4e9a8d2c6b14d0c9e5f3a7b8c1d2e6a__";

function visibleAssistantText(value) {
  const raw = String(value ?? "");
  const marker = raw.indexOf(REASONING_SEPARATOR);
  // Only the SDK's unambiguous single structural boundary is trusted. Plain
  // assistant text that resembles a marker remains ordinary visible text.
  if (marker < 0 || raw.indexOf(REASONING_SEPARATOR, marker + REASONING_SEPARATOR.length) >= 0) {
    return raw;
  }
  return raw.slice(marker + REASONING_SEPARATOR.length);
}

function sanitizeConversationText(role, value) {
  return role === "user"
    ? sanitizeUserAuthoredText(value)
    : sanitizeDerivedOperationalText(visibleAssistantText(value));
}

function clip(value, maxChars, { trim = true } = {}) {
  const raw = String(value ?? "");
  const text = trim ? raw.trim() : raw;
  if (text.length <= maxChars) return text;
  return `${text.slice(0, Math.max(0, maxChars - 18))} …[truncated]`;
}

function clipHeadTail(value, maxChars, { trim = true } = {}) {
  const raw = String(value ?? "");
  const text = trim ? raw.trim() : raw;
  if (text.length <= maxChars) return text;
  const marker = " …[middle truncated]… ";
  if (maxChars <= marker.length + 2) return clip(text, maxChars, { trim: false });
  const available = maxChars - marker.length;
  const headLength = Math.ceil(available / 2);
  return `${text.slice(0, headLength)}${marker}${text.slice(-(available - headLength))}`;
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
      text: clip(sanitizeConversationText(String(item.role), item.text), maxTextChars, { trim: false }),
      source: "prior_checkpoint",
    });
  }
  for (const message of messages) {
    if (!message || !["user", "assistant"].includes(message.role) || !message.text) continue;
    combined.push({
      role: message.role,
      text: clip(sanitizeConversationText(message.role, message.text), maxTextChars, { trim: false }),
      messageIndex: message.index,
      source: "current_history",
    });
  }
  const deduped = [];
  for (const item of combined) {
    // A serialized tail may have a shorter excerpt than the current message.
    // Keep the current, richer occurrence instead of retaining every successive
    // truncation as if it were new conversation evidence.
    if (item.source === "current_history") {
      for (let index = deduped.length - 1; index >= 0; index--) {
        const prior = deduped[index];
        const prefix = prior.text.replace(/\s*…\[truncated\]$/u, "");
        if (prior.source === "prior_checkpoint" && prior.role === item.role
          && (prior.text === item.text || (prefix.length >= 200 && item.text.startsWith(prefix)))) deduped.splice(index, 1);
      }
    }
    const previous = deduped.at(-1);
    if (previous && previous.role === item.role && previous.text === item.text) continue;
    deduped.push(item);
  }
  return deduped.slice(-maxItems);
}

module.exports = {
  REASONING_SEPARATOR,
  clip,
  clipHeadTail,
  looksElliptical,
  normalizedTextKey,
  recentConversationTail,
  sentenceCandidates,
  visibleAssistantText,
};

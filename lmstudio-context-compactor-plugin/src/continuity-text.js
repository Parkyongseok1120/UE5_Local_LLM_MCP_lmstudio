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

// Sole owner of "does this short user turn continue the prior objective?".
// A continuation is a short message built only from continuation verbs,
// acknowledgements, politeness and generic work referents. Any other content
// word (a path, symbol, new verb such as "실패하는 원인 찾아줘") makes it a
// substantive request, so a new objective is never hidden behind "계속".
const CONTINUATION_VERB_SUFFIX = "(?:해|해서|하여|해줘|해주세요|해줄래|해주라|해요|하세요|하자|할게|해봐|해라|합시다|합니다|하고|하기|중)?";
const CONTINUATION_KEYWORD_TOKEN = new RegExp(
  `^(?:(?:계속|이어서|이어|마저|재개|다시|진행|계속해서)${CONTINUATION_VERB_SUFFIX}|continue|continuing|resume|proceed|ahead|going|carry)$`, "u");
const CONTINUATION_FILLER_TOKEN = /^(?:yes|yeah|yep|ok|okay|sure|right|please|pls|go|on|keep|do|it|the|work|task|where|you|left|off|from|with|and|then|now|네|예|응|어|음|좋아|좋아요|그래|그래요|오케이|ㅇㅇ|ㅇㅋ|이제|그럼|그러면|자|좀|그대로|아까|이전|방금|하던|하던거|하던걸|중단된|멈춘|끊긴|남은|나머지|그거|그걸|그것|그것을|거|걸|것|작업|작업을|일|조사|분석|검토|수정|점검|부분|내용|단계|을|를|도|해|해줘|해주세요|주세요|줘|하세요|하자|해봐|해요|부탁해|부탁해요|부탁합니다|진행해|진행해줘)$/u;
const ACKNOWLEDGEMENT_ONLY = /^(?:yes|yeah|yep|ok(?:ay)?|sure|right|네|예|응|어|좋아|그래|오케이|ㅇㅇ|ㅇㅋ)$/u;

function classifyContinuation(text) {
  const value = String(text || "").trim();
  if (!value || value.length > 80) return { continuation: false, reason: value ? "too_long" : "empty" };
  if (/[?？]/u.test(value)) return { continuation: false, reason: "question" };
  const tokens = value.toLocaleLowerCase()
    .replace(/[.!。！~…,，、]+/gu, " ")
    .split(/\s+/u).filter(Boolean);
  if (!tokens.length) return { continuation: false, reason: "empty" };
  if (tokens.length === 1 && ACKNOWLEDGEMENT_ONLY.test(tokens[0])) return { continuation: true, reason: "acknowledgement" };
  let keyword = false;
  for (const token of tokens) {
    if (CONTINUATION_KEYWORD_TOKEN.test(token)) { keyword = true; continue; }
    if (!CONTINUATION_FILLER_TOKEN.test(token)) return { continuation: false, reason: "substantive_token" };
  }
  // "해줘"/"그거 해줘"/"do it" keep the prior objective even without a keyword.
  const genericAction = tokens.some(token => /^(?:해|해줘|해주세요|하자|해봐|do|it)$/u.test(token))
    || /(?:^|\s)go on(?:\s|$)/u.test(tokens.join(" "));
  return keyword || genericAction
    ? { continuation: true, reason: keyword ? "continuation_keyword" : "generic_action" }
    : { continuation: false, reason: "no_continuation_verb" };
}

function looksElliptical(text) {
  return classifyContinuation(text).continuation;
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
  classifyContinuation,
  clip,
  clipHeadTail,
  looksElliptical,
  normalizedTextKey,
  recentConversationTail,
  sentenceCandidates,
  visibleAssistantText,
};

"use strict";

/**
 * File-version receipts are live, runtime-local mutation capabilities. They are
 * useful in the prediction round that receives them, but they are never facts
 * that a durable continuity checkpoint may replay.
 */

const RAW_FILE_RECEIPT_PATTERN = /fvr1_[A-Za-z0-9_-]+/giu;
const RAW_FILE_RECEIPT_TEST = /fvr1_[A-Za-z0-9_-]+/iu;

const EPHEMERAL_KEY_TOKENS = new Set([
  "fileversionreceipt",
  "mutationreceipt",
  "receiptexpiresat",
  "receiptowner",
  "registryobservationversion",
  "snapshotexpiresat",
  "snapshotowner",
  "snapshotreceipt",
  "snapshotversion",
]);

const ENGLISH_RECEIPT_SOURCE = String.raw`(?:fileVersionReceipt|file[- ]?version receipt|receipt|ephemeral file capability omitted)`;
const RECEIPT_TERM_PATTERN = new RegExp(
  String.raw`(?:${ENGLISH_RECEIPT_SOURCE}|리시트|영수증)`,
  "iu",
);
const ACTIONABLE_RECEIPT_PATTERN = new RegExp(
  String.raw`\b(?:use|using|reuse|retry(?:\s+with)?|apply|pass|continue(?:\s+with)?|proceed(?:\s+with)?|keep(?:\s+using)?|supply|submit|provide|set|send|attach|include|carry|forward|feed)\b[^;.!?。！？\r\n]{0,240}\b${ENGLISH_RECEIPT_SOURCE}\b|\b${ENGLISH_RECEIPT_SOURCE}\b\s+(?:(?:must|should|will|can|to)\s+)(?:be\s+)?(?:used|reused|retried|applied|passed|supplied|submitted|provided|sent|attached|included|forwarded)\b|(?:사용|재사용|재시도|적용|전달|수정|교체|제공|제출|설정|첨부)(?:하|해)[^;.!?。！？\r\n]{0,160}(?:receipt|리시트|영수증)|다시\s*시도(?:하|해)[^;.!?。！？\r\n]{0,160}(?:receipt|리시트|영수증)|(?:receipt|리시트|영수증)[^;.!?。！？\r\n]{0,160}(?:(?:사용|재사용|재시도|적용|전달|수정|교체|제공|제출|설정|첨부)(?:하|해)|다시\s*시도(?:하|해)|(?:계속\s*)?진행(?:하|해))`,
  "iu",
);
const RECEIPT_ASSIGNMENT_PATTERN = new RegExp(
  String.raw`(?:\b(?:the\s+)?(?:current|previous|latest|valid|this|that)\s+(?:valid\s+)?${ENGLISH_RECEIPT_SOURCE}\b|\b(?:the\s+)?${ENGLISH_RECEIPT_SOURCE}\b\s+(?:for|of)\s+(?:this|that|the)\s+file\b)(?:\s*(?:is\b|=|:)[^;.!?。！？\r\n]{0,160}|\s+(?:(?:remains?|stays?)\s+(?:valid|usable)|still\s+works?|continues?\s+to\s+work)\b|\s*$)|(?:현재|이전|최신|유효한?|이|해당)\s*(?:파일\s*)?(?:receipt|리시트|영수증)(?:\s*(?:은|는|=|:)[^;.!?。！？\r\n]{0,160}|\s*$)`,
  "iu",
);
const RECEIPT_CLAUSE_BOUNDARY = /([;,:.!?。！？、，—–\r\n]+|\b(?:and|but|then|or)\b|(?:하지\s*말고|말고|하고|하거나|또는))/giu;
const MUTATION_CONTEXT_PATTERN = /\b(?:next|mutation|edit|patch|replace|write|mutate|modify|replace_in_file|write_file|apply_edit_bundle)\b|(?:다음|수정|편집|패치|교체|쓰기|변경|도구\s*호출)/iu;

function receiptActionIsNonExecutable(segment, match) {
  const before = segment.slice(0, match.index);
  const after = segment.slice(match.index + match[0].length);
  if (/\b(?:is|are|was|were|becomes?|remains?|stays?)\s+(?:not\s+(?:allowed|valid|usable)|no\s+longer\s+(?:valid|usable)|invalid|expired|blocked|rejected|denied)\b/iu.test(match[0])) {
    return true;
  }
  if (/(?:\bnever|\bdo\s+not|\bdon't|\bmust\s+not|\bshould\s+not|\bcannot|\bcan't)[^,;]{0,24}$/iu.test(before)) {
    return true;
  }
  if (/^\s*(?:(?:is|are|was|were|must\s+be|should\s+be)\s+)?(?:not\s+(?:allowed|valid)|no\s+longer\s+(?:valid|usable)|forbidden|blocked|rejected|denied|invalid|expired)\b/iu.test(after)) {
    return true;
  }
  if (/^\s*(?:지\s*마|지\s*않|면\s*안\s*돼|금지|방지|거부)/u.test(after)) return true;
  if (/(?:^|\s)(?:explain|diagnose|analy[sz]e|test|verify|determine|investigate|check|audit|review|prove)\s+(?:(?:how|why|whether|if|that|to)\s+)?[^,;]*$/iu.test(before)) {
    return true;
  }
  return /(?:설명|분석|진단|테스트|검증|확인|조사|감사|검토|증명)[^,;]*$/u.test(before);
}

function rewriteMetaReceiptAction(segment, match) {
  const before = segment.slice(0, match.index);
  const after = segment.slice(match.index + match[0].length);
  const instructionPrefix = /\b(?:instructions?|guidance|wording|attempts?)\s+to\s*$/iu.exec(before);
  if (instructionPrefix && /\b(?:block|remove|omit|prevent|reject|neutralize|strip)\b/iu.test(before)) {
    return `${before.slice(0, instructionPrefix.index)}receipt-reuse instructions${after}`;
  }
  if (/\b(?:block|remove|omit|prevent|reject|neutralize|strip)\s*$/iu.test(before)) {
    return `${before}receipt-reuse instructions${after}`;
  }
  const quotedSafetySuffix = /^["'”’]?\s*(?:(?:must|should|can)\s+)?(?:(?:never|not)\b|(?:(?:is|are|was|were|be)\s+)?(?:rejected|blocked|removed|omitted|forbidden|stripped)\b)/iu.test(after)
    || /^["'”’]?[^.!?。！？\r\n]{0,80}(?:하지\s*마|지\s*않|금지|차단|거부|제거|생략)/u.test(after);
  const openQuote = /["'“‘][^"'“”‘’]*$/u.exec(before);
  if (openQuote && quotedSafetySuffix) {
    return `${before.slice(0, openQuote.index + 1)}receipt-reuse wording${after}`;
  }
  if (instructionPrefix && quotedSafetySuffix) {
    return `${before.slice(0, instructionPrefix.index)}receipt-reuse instructions${after}`;
  }
  return null;
}

function normalizeKeyToken(value) {
  return String(value || "").replace(/[^A-Za-z0-9]/g, "").toLowerCase();
}

function isEphemeralCapabilityKey(key, value) {
  const normalized = normalizeKeyToken(key);
  if (EPHEMERAL_KEY_TOKENS.has(normalized) || normalized.includes("fileversionreceipt")) return true;
  if (RAW_FILE_RECEIPT_TEST.test(String(key || ""))) return true;
  return normalized === "receipt" && RAW_FILE_RECEIPT_TEST.test(String(value || ""));
}

function canonicalizeJsonUnicodeEscapes(value) {
  return String(value ?? "")
    // An encoded backslash can reveal another JSON escape after one decode.
    // Removing it first prevents arbitrary multi-stage capability recovery.
    .replace(/\\u005c/giu, "[encoded escape omitted]")
    .replace(/\\u00([0-7][0-9a-f])/giu, (_match, hex) => (
      String.fromCharCode(Number.parseInt(hex, 16))
    ));
}

function sanitizeDurableText(value) {
  const sanitized = canonicalizeJsonUnicodeEscapes(value)
    .replace(RAW_FILE_RECEIPT_PATTERN, "[ephemeral file capability omitted]");
  const neutralized = sanitized.split(RECEIPT_CLAUSE_BOUNDARY).map((segment, index) => {
    if (index % 2 === 1 || !RECEIPT_TERM_PATTERN.test(segment)) return segment;
    const actionable = ACTIONABLE_RECEIPT_PATTERN.exec(segment);
    const assignment = RECEIPT_ASSIGNMENT_PATTERN.exec(segment);
    const contextualReceipt = MUTATION_CONTEXT_PATTERN.test(segment)
      ? RECEIPT_TERM_PATTERN.exec(segment)
      : null;
    const match = [actionable, assignment, contextualReceipt]
      .filter(Boolean)
      .sort((left, right) => left.index - right.index)[0];
    if (!match) return segment;
    const metaRewrite = rewriteMetaReceiptAction(segment, match);
    if (metaRewrite !== null) return metaRewrite;
    if (receiptActionIsNonExecutable(segment, match)) return segment;
    const prefix = segment.slice(0, match.index).replace(/(?:\b(?:and|then|now|next|will|must|should|can|please)\s*)+$/iu, "");
    return `${prefix}fresh file snapshot required before mutation`;
  }).join("");
  return neutralized
    .replace(/fileVersionReceipt/giu, "ephemeral file-mutation capability")
    .replace(/snapshotVersion/giu, "registry observation counter");
}

function sanitizeDurableValue(value, depth = 0) {
  if (depth > 24) return "[depth limited]";
  if (typeof value === "string") {
    const trimmed = value.trim();
    if ((trimmed.startsWith("{") && trimmed.endsWith("}"))
      || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed && typeof parsed === "object") {
          return JSON.stringify(sanitizeDurableValue(parsed, depth + 1));
        }
      } catch {
        // Clipped or prose-like JSON falls through to token/prose sanitization.
      }
    }
    return sanitizeDurableText(value);
  }
  if (Array.isArray(value)) {
    return value.slice(0, 80).map((item) => sanitizeDurableValue(item, depth + 1));
  }
  if (!value || typeof value !== "object") return value;

  const sanitized = {};
  for (const [key, item] of Object.entries(value)) {
    if (item === undefined || isEphemeralCapabilityKey(key, item)) continue;
    const safeKey = sanitizeDurableText(key);
    sanitized[safeKey] = sanitizeDurableValue(item, depth + 1);
  }
  return sanitized;
}

module.exports = {
  isEphemeralCapabilityKey,
  sanitizeDurableText,
  sanitizeDurableValue,
};

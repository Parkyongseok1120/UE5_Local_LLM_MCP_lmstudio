"use strict";

/**
 * File-version receipts are live, runtime-local mutation capabilities. They are
 * useful in the prediction round that receives them, but they are never facts
 * that a durable continuity checkpoint may replay.
 */

function encodedReceiptPrefixCharacter(literal, hex) {
  return String.raw`(?:${literal}|\\u00${hex}|\\u005c(?:u005c){0,2}u00${hex}|(?:\\u005c){1,3}\\u00${hex})`;
}

const BASE64URL_HEX_SOURCE = String.raw`(?:2d|3[0-9]|4[1-9a-f]|5[0-9a]|5f|6[1-9a-f]|7[0-9a])`;
const ENCODED_RECEIPT_TOKEN_CHAR = String.raw`(?:[A-Za-z0-9_-]|\\u00${BASE64URL_HEX_SOURCE}|\\u005c(?:u005c){0,2}u00${BASE64URL_HEX_SOURCE}|(?:\\u005c){1,3}\\u00${BASE64URL_HEX_SOURCE})`;
const RAW_FILE_RECEIPT_SOURCE = [
  encodedReceiptPrefixCharacter("f", "66"),
  encodedReceiptPrefixCharacter("v", "76"),
  encodedReceiptPrefixCharacter("r", "72"),
  encodedReceiptPrefixCharacter("1", "31"),
  encodedReceiptPrefixCharacter("_", "5f"),
  `${ENCODED_RECEIPT_TOKEN_CHAR}+`,
].join("");
const RAW_FILE_RECEIPT_PATTERN = new RegExp(RAW_FILE_RECEIPT_SOURCE, "giu");
const RAW_FILE_RECEIPT_TEST = new RegExp(RAW_FILE_RECEIPT_SOURCE, "iu");
const OMITTED_FILE_CAPABILITY = "[ephemeral file capability omitted]";

const SANITIZER_POLICIES = Object.freeze({
  OPERATIONAL_CAPABILITY: "operational_capability",
  RAW_CAPABILITY_ONLY: "raw_capability_only",
});

// Provenance is selected by the continuity caller, never inferred from prose.
// Both policies strip structural capabilities; only derived assistant/tool text
// receives the defense-in-depth operational wording rewrite.

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
const DERIVED_OPERATIONAL_FIELD_TOKENS = new Set([
  "errorcode",
  "likelyerrors",
  "message",
  "status",
  "summary",
]);

const ENGLISH_BARE_RECEIPT_BOUNDARY = String.raw`(?=$|[.,;:!?。！？"'”’)]|\s*$|\s+(?:is|remains|stays|still|must|should|will|can|now|next|again|instead|after|before)\b|\s+for\s+(?:the\s+)?(?:next\s+)?(?:edit|mutation|patch|replace|write|file|header|implementation|source|path)\b|\s+to\s+(?:replace_in_file|write_file|apply_edit_bundle|edit|mutate|modify|patch|replace|write|continue|retry)\b|\s+(?:in|with)\s+(?:the\s+)?next\s+(?:prediction|round|edit|mutation)\b)`;
const KOREAN_BARE_RECEIPT_BOUNDARY = String.raw`(?=$|[.,;:!?。！？"'”’)]|(?:로|으로|을|를|은|는|이|가|와|과|에|에게|부터|까지))`;
const EXPLICIT_CAPABILITY_RECEIPT_SOURCE = String.raw`(?:fileVersionReceipt|file[- ]?version receipt${ENGLISH_BARE_RECEIPT_BOUNDARY}|ephemeral file capability omitted|(?:(?:the\s+)?receipt\s+(?:for|of)\s+(?:this|that|the)\s+file)|(?:(?:the\s+)?receipt\s+you\s+already\s+have))`;
const CONTEXTUAL_ENGLISH_RECEIPT_SOURCE = String.raw`(?:(?:the\s+)?(?:returned|previous|prior|latest|valid|current|this|that)\s+(?:valid\s+)?receipt${ENGLISH_BARE_RECEIPT_BOUNDARY})`;
const CONTEXTUAL_KOREAN_RECEIPT_SOURCE = String.raw`(?:(?:현재|이전|최신|유효한?|반환된)\s*(?:유효한?\s*)?(?:파일\s*)?(?:receipt|리시트|영수증)${KOREAN_BARE_RECEIPT_BOUNDARY})`;
const CONTEXTUAL_RECEIPT_SOURCE = String.raw`(?:${CONTEXTUAL_ENGLISH_RECEIPT_SOURCE}|${CONTEXTUAL_KOREAN_RECEIPT_SOURCE})`;
const CAPABILITY_RECEIPT_CANDIDATE_SOURCE = String.raw`(?:${EXPLICIT_CAPABILITY_RECEIPT_SOURCE}|${CONTEXTUAL_RECEIPT_SOURCE})`;
const EXPLICIT_CAPABILITY_RECEIPT_PATTERN = new RegExp(
  EXPLICIT_CAPABILITY_RECEIPT_SOURCE,
  "iu",
);
const CONTEXTUAL_RECEIPT_PATTERN = new RegExp(CONTEXTUAL_RECEIPT_SOURCE, "iu");
const FILE_MUTATION_CONTEXT_PATTERN = /(?:\b(?:apply_edit_bundle|replace_in_file|write_file|file|header|source|path|mutation|patch|prediction\s+round)\b|\bnext\s+(?:edit|mutation|patch|replace|write)\b|(?:파일|헤더|소스|경로|예측\s*라운드)|다음\s*(?:파일\s*)?(?:편집|수정|교체|패치|쓰기))/iu;
const ACTIONABLE_RECEIPT_PATTERN = new RegExp(
  String.raw`\b(?:use|using|reuse|retry(?:\s+with)?|apply|pass|continue(?:\s+with)?|proceed(?:\s+with)?|keep(?:\s+using)?|supply|submit|provide|set|send|attach|include|carry|forward|feed)\b[^;.!?。！？\r\n]{0,240}${CAPABILITY_RECEIPT_CANDIDATE_SOURCE}|${CAPABILITY_RECEIPT_CANDIDATE_SOURCE}\s+(?:(?:must|should|will|can|to)\s+)(?:be\s+)?(?:used|reused|retried|applied|passed|supplied|submitted|provided|sent|attached|included|forwarded)\b|(?:사용|재사용|재시도|적용|전달|수정|교체|제공|제출|설정|첨부)(?:하|해)[^;.!?。！？\r\n]{0,160}${CAPABILITY_RECEIPT_CANDIDATE_SOURCE}|다시\s*시도(?:하|해)[^;.!?。！？\r\n]{0,160}${CAPABILITY_RECEIPT_CANDIDATE_SOURCE}|${CAPABILITY_RECEIPT_CANDIDATE_SOURCE}[^;.!?。！？\r\n]{0,160}(?:(?:사용|재사용|재시도|적용|전달|수정|교체|제공|제출|설정|첨부)(?:하|해)|다시\s*시도(?:하|해)|(?:계속\s*)?진행(?:하|해))`,
  "iu",
);
const RECEIPT_ASSIGNMENT_PATTERN = new RegExp(
  String.raw`${EXPLICIT_CAPABILITY_RECEIPT_SOURCE}(?:\s+(?:(?:is|remains?|stays?)\s+(?:still\s+)?(?:valid|usable)|still\s+works?|continues?\s+to\s+work)\b|\s*(?:은|는)\s*(?:여전히\s*)?(?:유효|사용\s*가능)|\s*$)`,
  "iu",
);
const RECEIPT_CLAUSE_BOUNDARY = /([;,:.!?。！？、，—–\r\n]+|\b(?:and|but|then|or)\b|(?:하지\s*말고|말고|하고|하거나|또는))/giu;
const RAW_CAPABILITY_ACTION_PATTERN = new RegExp(
  String.raw`\b(?:use|using|reuse|retry(?:\s+with)?|apply|pass|continue(?:\s+with)?|proceed(?:\s+with)?|keep(?:\s+using)?|supply|submit|provide|set|send|attach|include|carry|forward|feed)\b[^;.!?。！？\r\n]{0,240}\[ephemeral file capability omitted\]|\[ephemeral file capability omitted\][^;.!?。！？\r\n]{0,160}(?:(?:사용|재사용|재시도|적용|전달|수정|교체|제공|제출|설정|첨부)(?:하|해)|다시\s*시도(?:하|해)|(?:계속\s*)?진행(?:하|해))`,
  "iu",
);
const RAW_CAPABILITY_ASSIGNMENT_PATTERN = new RegExp(
  String.raw`${CAPABILITY_RECEIPT_CANDIDATE_SOURCE}[^;.!?。！？\r\n]{0,80}(?:is\b|=|:)\s*\[ephemeral file capability omitted\]`,
  "iu",
);

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
  if (/^\s*(?:면\s*(?:안\s*되|위험)|는지|인지|왜|어떻게)/u.test(after)) return true;
  if (/\b(?:is|are|was|were|be|becomes?|remains?)\s+(?:unsafe|risky|dangerous)\b/iu.test(after)) return true;
  if (/(?:왜|어떻게|whether|why|how)\s*$/iu.test(before)) return true;
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
  if (EPHEMERAL_KEY_TOKENS.has(normalized)) return true;
  if (RAW_FILE_RECEIPT_TEST.test(String(key || ""))) return true;
  return normalized === "receipt" && RAW_FILE_RECEIPT_TEST.test(String(value || ""));
}

function hasOperationalCapabilityEvidence(segment) {
  if (EXPLICIT_CAPABILITY_RECEIPT_PATTERN.test(segment)) return true;
  return CONTEXTUAL_RECEIPT_PATTERN.test(segment)
    && FILE_MUTATION_CONTEXT_PATTERN.test(segment);
}

function neutralizeOperationalReceiptProse(value, pattern = ACTIONABLE_RECEIPT_PATTERN) {
  const neutralized = String(value).split(RECEIPT_CLAUSE_BOUNDARY).map((segment, index) => {
    if (index % 2 === 1) return segment;
    if (pattern === ACTIONABLE_RECEIPT_PATTERN && !hasOperationalCapabilityEvidence(segment)) return segment;
    if (pattern === RAW_CAPABILITY_ACTION_PATTERN && !segment.includes(OMITTED_FILE_CAPABILITY)) return segment;
    const actionable = pattern.exec(segment);
    const assignment = pattern === ACTIONABLE_RECEIPT_PATTERN
      ? RECEIPT_ASSIGNMENT_PATTERN.exec(segment)
      : null;
    const match = [actionable, assignment]
      .filter(Boolean)
      .sort((left, right) => left.index - right.index)[0];
    if (!match) return segment;
    const metaRewrite = rewriteMetaReceiptAction(segment, match);
    if (metaRewrite !== null) return metaRewrite;
    if (receiptActionIsNonExecutable(segment, match)) return segment;
    const prefix = segment.slice(0, match.index).replace(/(?:\b(?:and|then|now|next|will|must|should|can|please)\s*)+$/iu, "");
    return `${prefix}fresh file snapshot required before mutation`;
  }).join("");
  return neutralized;
}

function sanitizeRawCapabilityTokens(value) {
  const source = String(value ?? "");
  const containedRawCapability = RAW_FILE_RECEIPT_TEST.test(source);
  let sanitized = source.replace(RAW_FILE_RECEIPT_PATTERN, OMITTED_FILE_CAPABILITY);
  if (containedRawCapability) {
    sanitized = neutralizeOperationalReceiptProse(sanitized, RAW_CAPABILITY_ACTION_PATTERN);
    sanitized = neutralizeOperationalReceiptProse(sanitized, RAW_CAPABILITY_ASSIGNMENT_PATTERN);
  }
  return sanitized;
}

function replaceCapabilityIdentifiers(value) {
  return String(value)
    .replace(/fileVersionReceipt/giu, "ephemeral file-mutation capability")
    .replace(/snapshotVersion/giu, "registry observation counter");
}

function sanitizeDerivedOperationalText(value) {
  return replaceCapabilityIdentifiers(
    neutralizeOperationalReceiptProse(sanitizeRawCapabilityTokens(value)),
  );
}

function sanitizeRawCapabilityText(value) {
  return sanitizeRawCapabilityTokens(value);
}

function sanitizeUserAuthoredText(value) {
  return sanitizeRawCapabilityText(value);
}

function sanitizeValue(value, policy, depth = 0) {
  if (depth > 24) return "[depth limited]";
  if (typeof value === "string") {
    const trimmed = value.trim();
    if ((trimmed.startsWith("{") && trimmed.endsWith("}"))
      || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed && typeof parsed === "object") {
          return JSON.stringify(sanitizeValue(parsed, policy, depth + 1));
        }
      } catch {
        // Clipped or prose-like JSON falls through to token/prose sanitization.
      }
    }
    return policy === SANITIZER_POLICIES.OPERATIONAL_CAPABILITY
      ? sanitizeDerivedOperationalText(value)
      : sanitizeRawCapabilityText(value);
  }
  if (Array.isArray(value)) {
    return value.slice(0, 80).map((item) => sanitizeValue(item, policy, depth + 1));
  }
  if (!value || typeof value !== "object") return value;

  const sanitized = {};
  for (const [key, item] of Object.entries(value)) {
    if (item === undefined || isEphemeralCapabilityKey(key, item)) continue;
    const safeKey = sanitizeRawCapabilityTokens(key);
    sanitized[safeKey] = sanitizeValue(item, policy, depth + 1);
  }
  return sanitized;
}

function sanitizeStructuredDurableValue(value) {
  return sanitizeValue(value, SANITIZER_POLICIES.RAW_CAPABILITY_ONLY);
}

function sanitizeDerivedOperationalValue(value) {
  return sanitizeValue(value, SANITIZER_POLICIES.OPERATIONAL_CAPABILITY);
}

function sanitizeDerivedRecordValue(value, depth = 0, operationalText = true) {
  if (depth > 24) return "[depth limited]";
  if (typeof value === "string") {
    const trimmed = value.trim();
    if ((trimmed.startsWith("{") && trimmed.endsWith("}"))
      || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed && typeof parsed === "object") {
          return JSON.stringify(sanitizeDerivedRecordValue(parsed, depth + 1, false));
        }
      } catch {
        // Clipped or prose-like JSON uses its explicitly selected root policy.
      }
    }
    return operationalText
      ? sanitizeDerivedOperationalText(value)
      : sanitizeRawCapabilityText(value);
  }
  if (Array.isArray(value)) {
    return value.slice(0, 80).map((item) => (
      sanitizeDerivedRecordValue(item, depth + 1, operationalText)
    ));
  }
  if (!value || typeof value !== "object") return value;

  const sanitized = {};
  for (const [key, item] of Object.entries(value)) {
    if (item === undefined || isEphemeralCapabilityKey(key, item)) continue;
    const safeKey = sanitizeRawCapabilityTokens(key);
    const fieldIsOperational = DERIVED_OPERATIONAL_FIELD_TOKENS.has(normalizeKeyToken(key));
    sanitized[safeKey] = sanitizeDerivedRecordValue(item, depth + 1, fieldIsOperational);
  }
  return sanitized;
}

function sanitizeDerivedOperationalRecord(value) {
  return sanitizeDerivedRecordValue(value);
}

// Backward-compatible operational aliases. New continuity assembly call sites
// select an origin-specific wrapper instead of relying on these defaults.
function sanitizeDurableText(value) {
  return sanitizeDerivedOperationalText(value);
}

function sanitizeDurableValue(value) {
  return sanitizeDerivedOperationalValue(value);
}

module.exports = {
  SANITIZER_POLICIES,
  isEphemeralCapabilityKey,
  sanitizeDerivedOperationalRecord,
  sanitizeDerivedOperationalText,
  sanitizeDerivedOperationalValue,
  sanitizeDurableText,
  sanitizeDurableValue,
  sanitizeRawCapabilityText,
  sanitizeStructuredDurableValue,
  sanitizeUserAuthoredText,
};

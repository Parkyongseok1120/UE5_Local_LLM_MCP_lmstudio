"use strict";

const { clip, clipHeadTail, visibleAssistantText } = require("./continuity-text.js");
const {
  sanitizeDerivedOperationalText,
  sanitizeStructuredDurableValue,
} = require("./durable-memory-sanitizer.js");

const ASSISTANT_MARKER = "[Prior assistant conversation evidence v1]";
const AUTHORITY = "prior_assistant_judgment_not_verified_fact";
const DELIVERED_RESPONSE_MARKER = "[Delivered partial assistant response; unverified]";
const isDeliveredPartialResponse = text => String(text || "").startsWith(DELIVERED_RESPONSE_MARKER + "\n");

/** Preserve delivered prose without committing an interrupted plan or tool call.
 * Inputs are serialized messages from a verified host prefix and its replacement.
 * Only the final text-only assistant tail is eligible; never copy tool exchanges.
 * This is lossless apart from the existing durable-material sanitization policy. */
function retainVisibleResponse(source, replacement) {
  const tail = messages => {
    let start = messages.length;
    while (start > 0 && messages[start - 1].role === "assistant"
      && messages[start - 1].content.every(part => part.type === "text")) start--;
    return { start, messages: messages.slice(start) };
  };
  const text = message => message.content.filter(part => part.type === "text").map(part => part.text).join("");
  const visible = tail(source).messages.map(message => {
    const raw = visibleAssistantText(text(message)).split("\n<!-- direct-continuity-note-v1 -->")[0];
    // An unparsed provider request is not a report. Quoted examples may remain
    // in normal completed history; this recovery path does not manufacture them.
    if (/<(?:tool_call|function=)|\|(?:tool_call|python_tag)\|/iu.test(raw)) return "";
    return sanitizeDerivedOperationalText(raw);
  }).join("");
  if (!visible.trim()) return replacement;
  const decoded = message => {
    const value = text(message);
    if (isDeliveredPartialResponse(value)) return value.slice(DELIVERED_RESPONSE_MARKER.length + 1);
    if (value.startsWith(ASSISTANT_MARKER + "\n")) {
      try { return JSON.parse(value.slice(ASSISTANT_MARKER.length)).previousResponse?.text || value; } catch { /* ordinary text */ }
    }
    return sanitizeDerivedOperationalText(visibleAssistantText(value));
  };
  const current = tail(replacement);
  const retained = current.messages.map(decoded).join("");
  if (retained.includes(visible.trimEnd())) return replacement;
  let missing = visible, at = replacement.length;
  if (retained && visible.startsWith(retained)) missing = visible.slice(retained.length);
  else if (retained && visible.endsWith(retained)) { missing = visible.slice(0, -retained.length); at = current.start; }
  // Keep this as ordinary assistant prose. A generated ASSISTANT_MARKER
  // checkpoint is excluded from semantic input during the next compaction.
  const record = { role: "assistant", content: [{ type: "text", text: DELIVERED_RESPONSE_MARKER + "\n" + missing }] };
  return [...replacement.slice(0, at), record, ...replacement.slice(at)];
}

function isRecord(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function generatedAssistantCheckpoint(message) {
  return message?.role === "assistant"
    && String(message.text || "").startsWith(`${ASSISTANT_MARKER}\n`);
}

function safeText(value, limit) {
  return clip(sanitizeDerivedOperationalText(value), limit, { trim: false });
}

// The prior answer (or the interrupted turn's last visible progress) is the
// context a "continue" turn needs most. It is assistant-authored, so it lives
// here rather than in the system checkpoint, and is sanitized like any other
// assistant judgment.
function boundedPreviousResponse(value, limit) {
  if (!isRecord(value) || typeof value.text !== "string" || !value.text.trim() || limit <= 0) return null;
  const text = clipHeadTail(sanitizeDerivedOperationalText(value.text), limit, { trim: false });
  if (!text.trim()) return null;
  return { text, final: value.final === true,
    ...(Number.isInteger(value.messageIndex) ? { messageIndex: value.messageIndex } : {}),
    ...(typeof value.forUserTurn === "string" && /^[a-f0-9]{64}$/u.test(value.forUserTurn) ? { forUserTurn: value.forUserTurn } : {}) };
}

function boundedAssistantEvidence(value, limits = {}) {
  if (!isRecord(value) || Object.keys(value).some((key) => ![
    "schemaVersion", "authority", "lastAssistantUpdate", "pendingAssistantItems", "recentRawTail", "previousResponse",
  ].includes(key))) return null;
  if (value.schemaVersion !== 1 || value.authority !== AUTHORITY) return null;
  const update = isRecord(value.lastAssistantUpdate) && typeof value.lastAssistantUpdate.text === "string"
    ? { text: safeText(value.lastAssistantUpdate.text, limits.update ?? 1000), source: "assistant_history" }
    : null;
  const pendingCount = limits.pendingCount ?? 4;
  const tailCount = limits.tailCount ?? 2;
  const pending = Array.isArray(value.pendingAssistantItems) && pendingCount > 0
    ? value.pendingAssistantItems.slice(-pendingCount).filter((item) => (
      isRecord(item) && item.kind === "assistant_progress_evidence" && typeof item.text === "string"
    )).map((item) => ({ kind: "assistant_progress_evidence", text: safeText(item.text, limits.pendingChars ?? 220) }))
    : [];
  const tail = Array.isArray(value.recentRawTail) && tailCount > 0
    ? value.recentRawTail.slice(-tailCount).filter((item) => (
      isRecord(item) && item.role === "assistant" && typeof item.text === "string"
    )).map((item) => ({ role: "assistant", text: safeText(item.text, limits.tailChars ?? 320),
      source: "prior_checkpoint",
      ...(Number.isInteger(item.continuityOrder) && item.continuityOrder >= 0 && item.continuityOrder < 8
        ? { continuityOrder: item.continuityOrder } : {}) }))
    : [];
  const previous = boundedPreviousResponse(value.previousResponse, limits.previous ?? 3000);
  return sanitizeStructuredDurableValue({
    schemaVersion: 1, authority: AUTHORITY,
    ...(previous ? { previousResponse: previous } : {}),
    ...(update?.text ? { lastAssistantUpdate: update } : {}),
    pendingAssistantItems: pending.filter((item) => item.text),
    recentRawTail: tail.filter((item) => item.text),
  });
}

function splitAssistantEvidence(memory) {
  const systemMemory = JSON.parse(JSON.stringify(memory));
  const work = systemMemory.currentWorkStatus || {};
  const orderedTail = (systemMemory.recentRawTail || []).map((item, continuityOrder) => ({
    ...item, continuityOrder,
  }));
  const previous = systemMemory.previousTurnFinalResponseEvidence;
  const evidence = boundedAssistantEvidence({
    schemaVersion: 1,
    authority: AUTHORITY,
    ...(previous?.present && previous.text ? { previousResponse: previous } : {}),
    lastAssistantUpdate: work.lastAssistantUpdate,
    pendingAssistantItems: (systemMemory.unresolvedItems || []).filter((item) => (
      item?.kind === "assistant_progress_evidence"
    )),
    recentRawTail: orderedTail.filter((item) => item?.role === "assistant"),
  });
  delete work.lastAssistantUpdate;
  if (previous) {
    delete previous.text;
    delete previous.forUserTurn;
  }
  systemMemory.unresolvedItems = (systemMemory.unresolvedItems || []).filter((item) => (
    item?.kind !== "assistant_progress_evidence"
  ));
  systemMemory.recentRawTail = orderedTail.filter((item) => item?.role !== "assistant");
  return { systemMemory, assistantEvidence: evidence };
}

function renderAssistantCheckpoint(evidence, maxChars, { previousOnly = false } = {}) {
  if (!evidence || maxChars < 180) return "";
  const none = { update: 0, pendingCount: 0, pendingChars: 0, tailCount: 0, tailChars: 0 };
  const variants = previousOnly ? [3000, 2000, 1200, 600].map(previous => ({ ...none, previous })) : [
    { previous: 3000, update: 1000, pendingCount: 4, pendingChars: 220, tailCount: 2, tailChars: 320 },
    { previous: 2000, update: 700, pendingCount: 3, pendingChars: 180, tailCount: 1, tailChars: 240 },
    { previous: 1200, update: 400, pendingCount: 2, pendingChars: 140, tailCount: 0, tailChars: 0 },
    { previous: 600, update: 220, pendingCount: 1, pendingChars: 100, tailCount: 0, tailChars: 0 },
  ];
  for (const limits of variants) {
    const candidate = boundedAssistantEvidence(evidence, limits);
    if (!candidate?.previousResponse && !candidate?.lastAssistantUpdate && !candidate?.pendingAssistantItems.length
      && !candidate?.recentRawTail.length) {
      return "";
    }
    const rendered = `${ASSISTANT_MARKER}\n${JSON.stringify(candidate)}`;
    if (rendered.length <= maxChars) return rendered;
  }
  return "";
}

function extractPriorAssistantEvidence(messages) {
  for (const message of [...messages].reverse()) {
    if (!generatedAssistantCheckpoint(message)) continue;
    const text = String(message.text || "");
    if (text.length > 8192) continue;
    try {
      const parsed = JSON.parse(text.slice(ASSISTANT_MARKER.length + 1));
      return boundedAssistantEvidence(parsed);
    } catch {
      // Invalid historical assistant evidence is not inherited.
    }
  }
  return null;
}

function mergePriorAssistantEvidence(previousState, evidence) {
  if (!previousState || !evidence) return previousState;
  const merged = JSON.parse(JSON.stringify(previousState));
  merged.currentWorkStatus ||= {};
  delete merged.currentWorkStatus.lastAssistantUpdate;
  if (evidence.lastAssistantUpdate) merged.currentWorkStatus.lastAssistantUpdate = evidence.lastAssistantUpdate;
  if (evidence.previousResponse) merged.previousTurnFinalResponseEvidence = {
    ...(isRecord(merged.previousTurnFinalResponseEvidence) ? merged.previousTurnFinalResponseEvidence : {}),
    present: true, ...evidence.previousResponse };
  merged.unresolvedItems = [
    ...(merged.unresolvedItems || []).filter((item) => item?.kind !== "assistant_progress_evidence"),
  ];
  merged.recentRawTail = [
    ...(merged.recentRawTail || []).filter((item) => item?.role !== "assistant"),
    ...(evidence.recentRawTail || []),
  ].sort((left, right) => (left.continuityOrder ?? -1) - (right.continuityOrder ?? -1));
  return merged;
}

module.exports = {
  retainVisibleResponse,
  isDeliveredPartialResponse,
  ASSISTANT_MARKER,
  extractPriorAssistantEvidence,
  generatedAssistantCheckpoint,
  mergePriorAssistantEvidence,
  renderAssistantCheckpoint,
  splitAssistantEvidence,
};

"use strict";

const { clip } = require("./continuity-text.js");
const {
  sanitizeDerivedOperationalText,
  sanitizeStructuredDurableValue,
} = require("./durable-memory-sanitizer.js");

const ASSISTANT_MARKER = "[Prior assistant conversation evidence v1]";
const AUTHORITY = "prior_assistant_judgment_not_verified_fact";

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

function boundedAssistantEvidence(value, limits = {}) {
  if (!isRecord(value) || Object.keys(value).some((key) => ![
    "schemaVersion", "authority", "lastAssistantUpdate", "pendingAssistantItems", "recentRawTail",
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
  return sanitizeStructuredDurableValue({
    schemaVersion: 1, authority: AUTHORITY,
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
  const evidence = boundedAssistantEvidence({
    schemaVersion: 1,
    authority: AUTHORITY,
    lastAssistantUpdate: work.lastAssistantUpdate,
    pendingAssistantItems: (systemMemory.unresolvedItems || []).filter((item) => (
      item?.kind === "assistant_progress_evidence"
    )),
    recentRawTail: orderedTail.filter((item) => item?.role === "assistant"),
  });
  delete work.lastAssistantUpdate;
  systemMemory.unresolvedItems = (systemMemory.unresolvedItems || []).filter((item) => (
    item?.kind !== "assistant_progress_evidence"
  ));
  systemMemory.recentRawTail = orderedTail.filter((item) => item?.role !== "assistant");
  return { systemMemory, assistantEvidence: evidence };
}

function renderAssistantCheckpoint(evidence, maxChars) {
  if (!evidence || maxChars < 180) return "";
  const variants = [
    { update: 1000, pendingCount: 4, pendingChars: 220, tailCount: 2, tailChars: 320 },
    { update: 700, pendingCount: 3, pendingChars: 180, tailCount: 1, tailChars: 240 },
    { update: 400, pendingCount: 2, pendingChars: 140, tailCount: 0, tailChars: 0 },
    { update: 220, pendingCount: 1, pendingChars: 100, tailCount: 0, tailChars: 0 },
  ];
  for (const limits of variants) {
    const candidate = boundedAssistantEvidence(evidence, limits);
    if (!candidate?.lastAssistantUpdate && !candidate?.pendingAssistantItems.length && !candidate?.recentRawTail.length) {
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
    if (text.length > 4096) continue;
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
  merged.unresolvedItems = [
    ...(merged.unresolvedItems || []).filter((item) => item?.kind !== "assistant_progress_evidence"),
    ...(evidence.pendingAssistantItems || []),
  ];
  merged.recentRawTail = [
    ...(merged.recentRawTail || []).filter((item) => item?.role !== "assistant"),
    ...(evidence.recentRawTail || []),
  ].sort((left, right) => (left.continuityOrder ?? -1) - (right.continuityOrder ?? -1));
  return merged;
}

module.exports = {
  ASSISTANT_MARKER,
  extractPriorAssistantEvidence,
  generatedAssistantCheckpoint,
  mergePriorAssistantEvidence,
  renderAssistantCheckpoint,
  splitAssistantEvidence,
};

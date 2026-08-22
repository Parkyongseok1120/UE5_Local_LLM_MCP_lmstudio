"use strict";

const { buildObjectiveContinuity } = require("./continuity-objectives.js");
const {
  coalesceFileObservations,
  migratePriorFileObservations,
  projectRoot,
} = require("./continuity-file-observations.js");
const {
  sanitizeDerivedOperationalText,
  sanitizeDerivedOperationalRecord,
  sanitizeDerivedOperationalValue,
  sanitizeStructuredDurableValue,
  sanitizeUserAuthoredText,
} = require("./durable-memory-sanitizer.js");
const {
  clip,
  normalizedTextKey,
  recentConversationTail,
  sentenceCandidates,
} = require("./continuity-text.js");

const CONTINUITY_MARKER = "[Direct continuity state v2]";
const LEGACY_CONTINUITY_MARKER = "[Direct continuity state v1]";
const CONTINUITY_MARKERS = Object.freeze([CONTINUITY_MARKER, LEGACY_CONTINUITY_MARKER]);

function sanitizeUserEvidenceRecord(value) {
  const sanitized = sanitizeStructuredDurableValue(value);
  if (typeof sanitized?.text === "string") {
    sanitized.text = sanitizeUserAuthoredText(sanitized.text);
  }
  return sanitized;
}

function sanitizePriorUserFields(state) {
  for (const key of ["latestUserMessage", "currentUserRequestVerbatim"]) {
    if (typeof state?.[key] === "string") state[key] = sanitizeUserAuthoredText(state[key]);
  }
  for (const key of ["activeObjective", "continuationAntecedent", "olderContinuationAnchor"]) {
    if (typeof state?.[key]?.text === "string") {
      state[key].text = sanitizeUserAuthoredText(state[key].text);
    }
  }
  for (const key of [
    "completedOrArchivedObjectives",
    "priorUserRequestsForContinuation",
    "historicalUserConstraintEvidence",
    "openQuestionEvidence",
  ]) {
    if (!Array.isArray(state?.[key])) continue;
    state[key] = state[key].map((item) => sanitizeUserEvidenceRecord(item));
  }
  if (Array.isArray(state?.latestUserConstraints)) {
    state.latestUserConstraints = state.latestUserConstraints.map((item) => (
      typeof item === "string" ? sanitizeUserAuthoredText(item) : sanitizeStructuredDurableValue(item)
    ));
  }
  return state;
}

function sanitizePriorContinuityState(value) {
  const state = sanitizePriorUserFields(sanitizeStructuredDurableValue(value));
  const work = state?.currentWorkStatus;
  if (work?.lastAssistantUpdate?.text) {
    work.lastAssistantUpdate.text = sanitizeDerivedOperationalText(work.lastAssistantUpdate.text);
  }
  for (const key of ["recentToolOutcomes", "recentBuildOrTestState"]) {
    if (Array.isArray(work?.[key])) {
      work[key] = work[key].map((item) => sanitizeDerivedOperationalRecord(item));
    }
  }
  for (const key of ["recentOlderToolOutcomes", "recentBuildOrTestState"]) {
    if (Array.isArray(state?.[key])) {
      state[key] = state[key].map((item) => sanitizeDerivedOperationalRecord(item));
    }
  }
  if (Array.isArray(state?.unresolvedItems)) {
    state.unresolvedItems = state.unresolvedItems.map((item) => (
      item?.kind === "assistant_progress_evidence"
        ? sanitizeDerivedOperationalValue(item)
        : sanitizeUserEvidenceRecord(item)
    ));
  }
  if (Array.isArray(state?.recentRawTail)) {
    state.recentRawTail = state.recentRawTail.map((item) => {
      const sanitized = sanitizeStructuredDurableValue(item);
      if (!sanitized || typeof sanitized !== "object") return sanitized;
      if (sanitized.text) {
        sanitized.text = sanitized.role === "assistant"
          ? sanitizeDerivedOperationalText(sanitized.text)
          : sanitizeUserAuthoredText(sanitized.text);
      }
      return sanitized;
    });
  }
  return state;
}

function extractJsonObject(text, startAt) {
  const start = text.indexOf("{", startAt);
  if (start < 0) return null;
  let depth = 0;
  let quoted = false;
  let escaped = false;
  for (let index = start; index < text.length; index += 1) {
    const char = text[index];
    if (quoted) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === "\"") quoted = false;
      continue;
    }
    if (char === "\"") {
      quoted = true;
      continue;
    }
    if (char === "{") depth += 1;
    if (char === "}" && --depth === 0) return text.slice(start, index + 1);
  }
  return null;
}

function extractPriorContinuityState(messages) {
  for (const message of [...messages].reverse()) {
    if (message.role !== "system") continue;
    const text = String(message.text || "");
    const marker = CONTINUITY_MARKERS
      .map((value) => ({ value, index: text.indexOf(value) }))
      .filter((candidate) => candidate.index >= 0)
      .sort((left, right) => left.index - right.index)[0];
    if (!marker) continue;
    const json = extractJsonObject(text, marker.index + marker.value.length);
    if (!json) continue;
    try {
      const parsed = JSON.parse(json);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return sanitizePriorContinuityState(parsed);
      }
    } catch {
      // A clipped or corrupt checkpoint is not continuity evidence.
    }
  }
  return null;
}

function lastAssistantUpdate(messages, minimumIndex = -1) {
  const message = [...messages].reverse().find((candidate) => (
    candidate.role === "assistant"
    && candidate.index >= minimumIndex
    && String(candidate.text || "").trim()
  ));
  if (!message) return null;
  return {
    messageIndex: message.index,
    text: clip(sanitizeDerivedOperationalText(message.text), 2400, { trim: false }),
    source: "assistant_history",
  };
}

function pendingAssistantItems(messages, activeObjective, maxItems = 8) {
  const activeIndex = Number.isInteger(activeObjective?.messageIndex)
    ? activeObjective.messageIndex
    : -1;
  const patterns = [
    /\b(?:need to|needs? to|will|next|remaining|pending|not yet|still need|retry)\b/iu,
    /(?:해야|할\s*(?:일|것)|다음|남았|미완료|아직|재시도|진행할|확인할|수정할|빌드할)/u,
  ];
  const values = [];
  const seen = new Set();
  for (const message of messages) {
    if (message.role !== "assistant" || message.index < activeIndex) continue;
    for (const sentence of sentenceCandidates(message.text)) {
      if (!patterns.some((pattern) => pattern.test(sentence))) continue;
      const text = clip(sanitizeDerivedOperationalText(sentence), 800);
      const key = normalizedTextKey(text);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      values.push({ kind: "assistant_progress_evidence", text, messageIndex: message.index });
    }
  }
  return values.slice(-Math.max(1, maxItems));
}

function mergeUnresolved(previousState, activeObjective, openQuestions, pendingItems) {
  const previousSameObjective = normalizedTextKey(previousState?.activeObjective?.text)
    === normalizedTextKey(activeObjective?.text);
  const combined = [
    ...(previousSameObjective ? previousState?.unresolvedItems || [] : []),
    ...(openQuestions || []).map((item) => ({ kind: "open_question_evidence", ...item })),
    ...pendingItems,
  ];
  const seen = new Set();
  return combined.filter((item) => {
    const key = normalizedTextKey(item?.text);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(-10);
}

function buildContinuityMemory(messages, facts, options = {}) {
  const previousState = extractPriorContinuityState(messages);
  const objective = buildObjectiveContinuity(messages, previousState, options);
  const pendingItems = pendingAssistantItems(messages, objective.activeObjective);
  const previousWork = previousState?.currentWorkStatus || {};
  const activeIndex = objective.activeObjective?.source === "current_history"
    && Number.isInteger(objective.activeObjective.messageIndex)
    ? objective.activeObjective.messageIndex
    : -1;
  const samePriorObjective = normalizedTextKey(previousState?.activeObjective?.text)
    === normalizedTextKey(objective.activeObjective?.text);
  const activeProjectExplicitlyCleared = facts.activeProject?.cleared === true;
  const activeProjectCandidate = activeProjectExplicitlyCleared
    ? null
    : (facts.activeProject || previousState?.activeProject || null);
  const activeProject = activeProjectCandidate ? {
    ...activeProjectCandidate,
    ...(activeProjectCandidate.descriptor ? {
      root: activeProjectCandidate.root || projectRoot(activeProjectCandidate.descriptor),
    } : {}),
  } : null;
  const previousFileObservations = migratePriorFileObservations(
    previousWork.modifiedOrObservedFiles || [],
    previousState,
    12,
  );
  const currentFileObservations = coalesceFileObservations(
    facts.modifiedOrObservedFiles || [],
    12,
    String(facts.activeProject?.descriptor || ""),
  );
  const currentWorkStatus = {
    lastAssistantUpdate: lastAssistantUpdate(messages, activeIndex)
      || (samePriorObjective ? previousWork.lastAssistantUpdate : null),
    recentToolOutcomes: [
      ...(previousWork.recentToolOutcomes || []),
      ...(facts.recentOlderToolOutcomes || []),
    ].slice(-6),
    modifiedOrObservedFiles: coalesceFileObservations(
      [...previousFileObservations, ...currentFileObservations],
      12,
    ),
    recentBuildOrTestState: [
      ...(previousWork.recentBuildOrTestState || []),
      ...(facts.recentBuildOrTestState || []),
    ].slice(-4),
  };
  return sanitizeStructuredDurableValue({
    schemaVersion: 2,
    authority: "factual_memory_only",
    latestUserMessage: objective.latestUserMessage,
    activeObjective: objective.activeObjective,
    continuationAntecedent: objective.continuationAntecedent,
    activeProject,
    currentWorkStatus,
    unresolvedItems: mergeUnresolved(
      previousState,
      objective.activeObjective,
      facts.openQuestionEvidence,
      pendingItems,
    ),
    completedOrArchivedObjectives: objective.completedOrArchivedObjectives,
    recentRawTail: recentConversationTail(
      activeIndex >= 0 ? messages.filter((message) => message.index >= activeIndex) : messages,
      samePriorObjective ? previousState?.recentRawTail : [],
      { maxItems: options.recentRawTailItems || 8, maxTextChars: options.recentRawTailTextChars || 2000 },
    ),
  });
}

module.exports = {
  CONTINUITY_MARKER,
  CONTINUITY_MARKERS,
  LEGACY_CONTINUITY_MARKER,
  buildContinuityMemory,
  extractPriorContinuityState,
};

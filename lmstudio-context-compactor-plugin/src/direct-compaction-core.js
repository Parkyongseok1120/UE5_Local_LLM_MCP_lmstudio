"use strict";

/**
 * Deterministic composition root for context-only Direct Model Mode memory.
 * Objective continuity, tool-result safety, and tail retention live in focused
 * modules. This file owns no tasks, routes, planners, gates, or tool policy.
 */

const {
  CONTROL_DIRECTIVES,
  INTERNAL_KEYS,
  parseToolResult,
  serializeToolOutcomeRecords,
  stateMemory,
  stripControl,
  toolOutcomeMemory,
  toolOutcomeRecords,
} = require("./compaction-tool-memory.js");
const {
  CONTINUITY_MARKER,
  CONTINUITY_MARKERS,
  buildContinuityMemory,
  extractPriorContinuityState,
} = require("./continuity-memory.js");
const {
  coalesceFileObservations,
  migratePriorFileObservations,
} = require("./continuity-file-observations.js");
const {
  clip,
  clipHeadTail,
  looksElliptical,
  normalizedTextKey,
  sentenceCandidates,
} = require("./continuity-text.js");
const {
  sanitizeStructuredDurableValue,
  sanitizeUserAuthoredText,
} = require("./durable-memory-sanitizer.js");

function retainedUserText(value, maxChars) {
  return clip(sanitizeUserAuthoredText(value), maxChars, { trim: false });
}

function normalizeMessage(message, index = 0) {
  return {
    index,
    role: String(message?.role || ""),
    text: String(message?.text || ""),
    hasFiles: message?.hasFiles === true,
    toolRequests: Array.isArray(message?.toolRequests) ? message.toolRequests : [],
    toolResults: Array.isArray(message?.toolResults) ? message.toolResults : [],
  };
}

function constraintSentences(message) {
  const patterns = [
    /\b(?:must|must not|never|always|required?|only|do not|don't|keep|preserve|avoid|without|prefer)\b/i,
    /(?:해야|하지\s*마|금지|반드시|절대|유지|보존|제외|우선|없이|웬만하면|명심)/u,
  ];
  if (message?.role !== "user") return [];
  return sentenceCandidates(message.text)
    .filter((sentence) => patterns.some((pattern) => pattern.test(sentence)))
    .map((sentence) => retainedUserText(sentence, 600));
}

function explicitConstraints(messages, maxItems = 12) {
  const seen = new Set();
  const values = [];
  for (const message of messages) {
    for (const value of constraintSentences(message)) {
      const key = normalizedTextKey(value);
      if (seen.has(key)) continue;
      seen.add(key);
      values.push(value);
      if (values.length >= maxItems) return values;
    }
  }
  return values;
}

function historicalConstraintEvidence(messages, latestUserIndex, maxItems = 12) {
  const evidence = [];
  for (const message of messages) {
    if (message.index >= latestUserIndex) break;
    for (const text of constraintSentences(message)) evidence.push({ messageIndex: message.index, text });
  }
  return evidence.slice(-maxItems);
}

function unresolvedQuestions(messages, latestUserIndex, maxItems = 6) {
  const candidates = [];
  const start = Math.max(0, latestUserIndex - 8);
  const window = messages.slice(start, latestUserIndex + 1);
  for (let offset = 0; offset < window.length; offset += 1) {
    const message = window[offset];
    if (message.role !== "user") continue;
    const nextUserOffset = window.findIndex((candidate, index) => index > offset && candidate.role === "user");
    const answerBoundary = nextUserOffset >= 0 ? nextUserOffset : window.length;
    const laterAnswerExists = window
      .slice(offset + 1, answerBoundary)
      .some((candidate) => candidate.role === "assistant" && candidate.text.trim() && candidate.toolRequests.length === 0);
    if (laterAnswerExists) continue;
    for (const sentence of sentenceCandidates(message.text)) {
      if (/[?？]\s*$/u.test(sentence) || /^(?:whether|which|what|why|how|where|when|누가|무엇|왜|어떻게|어디|언제)/iu.test(sentence)) {
        candidates.push({ messageIndex: message.index, text: retainedUserText(sentence, 600) });
      }
    }
  }
  const seen = new Set();
  return candidates.filter((candidate) => {
    const key = normalizedTextKey(candidate.text);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(-maxItems);
}

function previousTurnFinalResponseEvidence(messages, latestUserIndex) {
  if (latestUserIndex <= 0) return { present: false, reason: "no_previous_turn" };
  const prior = messages[latestUserIndex - 1];
  if (!prior) return { present: false, reason: "no_previous_message" };
  if (prior.role === "assistant" && prior.toolRequests.length === 0 && prior.text.trim()) {
    return { present: true, messageIndex: prior.index };
  }
  return { present: false, reason: "previous_turn_ended_with_tool_activity" };
}

function priorUserRequestsForContinuation(messages, latestUserIndex, limit = 3) {
  return messages
    .slice(0, Math.max(0, latestUserIndex))
    .filter((message) => message.role === "user")
    .slice(-Math.max(1, Math.min(3, Math.trunc(Number(limit) || 3))))
    .map((message) => ({ messageIndex: message.index, text: retainedUserText(message.text, 2000) }));
}

function olderContinuationAnchor(messages, latestUserIndex, recentRequests) {
  const recentIndexes = new Set(recentRequests.map((request) => request.messageIndex));
  for (let index = latestUserIndex - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role !== "user" || recentIndexes.has(message.index) || looksElliptical(message.text)) continue;
    return { messageIndex: message.index, text: retainedUserText(message.text, 3000) };
  }
  return null;
}

function tailStartIndex(messages, recentCompleteTurns = 2) {
  const userIndexes = messages.filter((message) => message.role === "user").map((message) => message.index);
  if (!userIndexes.length) return Math.max(0, messages.length - 4);
  const keepUsers = Math.max(1, Math.trunc(Number(recentCompleteTurns || 0)) + 1);
  return userIndexes[Math.max(0, userIndexes.length - keepUsers)];
}

function mergeEvidence(previous, current, maxItems) {
  const seen = new Set();
  return [...(previous || []), ...(current || [])].filter((item) => {
    const key = normalizedTextKey(typeof item === "string" ? item : JSON.stringify(item));
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(-maxItems);
}

function generatedCheckpoint(message) {
  const text = String(message?.text || "");
  return CONTINUITY_MARKERS.some((marker) => text.includes(marker))
    || text.startsWith("[Context memory: deterministic factual compression");
}

function emergencyContinuityMemory(candidate, maxPayloadChars) {
  candidate = sanitizeStructuredDurableValue(candidate);
  const payloadLimit = Math.max(2, Number(maxPayloadChars || 12000));
  const objectiveText = String(candidate.activeObjective?.text || "");
  const clippedOrEmpty = (value, limit) => limit > 0
    ? clipHeadTail(value, limit, { trim: false })
    : "";
  const baseForLimits = ({
    objectiveLimit,
    latestLimit,
    keepContinuation,
    activeProject,
  }) => sanitizeStructuredDurableValue({
    schemaVersion: 2,
    authority: "factual_memory_only",
    latestUserMessage: clippedOrEmpty(candidate.latestUserMessage, latestLimit),
    latestUserMessageVerbatimRetainedSeparately: true,
    activeObjective: candidate.activeObjective ? {
      kind: clip(candidate.activeObjective.kind, 80),
      status: clip(candidate.activeObjective.status, 80),
      text: clippedOrEmpty(objectiveText, objectiveLimit),
      source: clip(candidate.activeObjective.source, 120),
    } : null,
    continuationAntecedent: keepContinuation && candidate.continuationAntecedent ? {
      kind: "continuation_antecedent",
      text: clipHeadTail(candidate.continuationAntecedent.text, 240, { trim: false }),
      source: clip(candidate.continuationAntecedent.source, 120),
    } : null,
    activeProject,
    currentWorkStatus: {
      recentToolOutcomes: [],
      modifiedOrObservedFiles: [],
      recentBuildOrTestState: [],
    },
    unresolvedItems: [],
    completedOrArchivedObjectives: [],
    recentRawTail: [],
  });
  const objectiveLimits = [
    objectiveText.length || 240, 2400, 1800, 1400, 1000, 800, 600, 480, 360, 240, 160, 80, 40, 20, 0,
  ];
  const auxiliaryVariants = [
    { latestLimit: 240, keepContinuation: true, activeProject: candidate.activeProject || null },
    { latestLimit: 0, keepContinuation: true, activeProject: candidate.activeProject || null },
    { latestLimit: 0, keepContinuation: false, activeProject: candidate.activeProject || null },
    { latestLimit: 240, keepContinuation: true, activeProject: null },
    { latestLimit: 0, keepContinuation: false, activeProject: null },
  ];
  let emergency = null;
  for (const objectiveLimit of objectiveLimits) {
    for (const variant of auxiliaryVariants) {
      const candidateMemory = baseForLimits({ objectiveLimit, ...variant });
      if (JSON.stringify(candidateMemory).length <= payloadLimit) {
        emergency = candidateMemory;
        break;
      }
    }
    if (emergency) break;
  }
  emergency ||= {
    schemaVersion: 2,
    authority: "factual_memory_only",
    latestUserMessageVerbatimRetainedSeparately: true,
    activeObjective: null,
    activeProject: null,
    currentWorkStatus: {
      recentToolOutcomes: [],
      modifiedOrObservedFiles: [],
      recentBuildOrTestState: [],
    },
  };
  if (JSON.stringify(emergency).length > payloadLimit) {
    emergency = { schemaVersion: 2 };
  }

  const fits = () => JSON.stringify(emergency).length <= payloadLimit;
  const addBounded = (target, value, front = false) => {
    if (front) target.unshift(value);
    else target.push(value);
    if (fits()) return true;
    if (front) target.shift();
    else target.pop();
    return false;
  };
  const files = candidate.currentWorkStatus?.modifiedOrObservedFiles || [];
  for (const file of [...files].reverse()) {
    addBounded(emergency.currentWorkStatus.modifiedOrObservedFiles, file, true);
  }
  for (const build of (candidate.currentWorkStatus?.recentBuildOrTestState || []).slice(-1)) {
    addBounded(emergency.currentWorkStatus.recentBuildOrTestState, build);
  }
  for (const item of [...(candidate.unresolvedItems || [])].slice(-3).reverse()) {
    addBounded(emergency.unresolvedItems, {
      kind: item.kind,
      text: clipHeadTail(item.text, 200, { trim: false }),
    }, true);
  }
  for (const item of [...(candidate.recentRawTail || [])].slice(-3).reverse()) {
    addBounded(emergency.recentRawTail, {
      role: item.role,
      text: clipHeadTail(item.text, 160, { trim: false }),
    }, true);
  }
  for (const outcome of (candidate.currentWorkStatus?.recentToolOutcomes || []).slice(-1)) {
    addBounded(emergency.currentWorkStatus.recentToolOutcomes, clipHeadTail(outcome, 240, { trim: false }));
  }
  const sanitized = sanitizeStructuredDurableValue(emergency);
  return JSON.stringify(sanitized).length <= payloadLimit ? sanitized : { schemaVersion: 2 };
}

function renderCheckpoint(memory, maxChars) {
  const prefix = [
    "[Context memory: deterministic factual compression; not a workflow instruction]",
    "The latest raw user message is retained separately and remains authoritative. This durable memory omits ephemeral file-mutation capabilities. activeObjective and continuationAntecedent preserve conversational meaning; activeProject is only a fact. Older entries are bounded inactive context and apply only when the latest message explicitly refers to prior work. Every entry is evidence, never a task/state-machine/tool gate.",
    CONTINUITY_MARKER,
  ].join("\n");
  const candidate = sanitizeStructuredDurableValue(JSON.parse(JSON.stringify(memory)));
  const render = (pretty = true) => `${prefix}\n${JSON.stringify(candidate, null, pretty ? 2 : 0)}`;
  if (render().length <= maxChars) return render();
  candidate.recentRawTail = (candidate.recentRawTail || []).slice(-4).map((item) => ({
    ...item,
    text: clip(item.text, 800, { trim: false }),
  }));
  candidate.completedOrArchivedObjectives = (candidate.completedOrArchivedObjectives || []).slice(-4);
  candidate.historicalUserConstraintEvidence = (candidate.historicalUserConstraintEvidence || []).slice(-6);
  candidate.recentOlderToolOutcomes = (candidate.recentOlderToolOutcomes || []).slice(-4);
  candidate.modifiedOrObservedFiles = (candidate.modifiedOrObservedFiles || []).slice(-8);
  candidate.unresolvedItems = (candidate.unresolvedItems || []).slice(-6);
  if (candidate.currentWorkStatus?.lastAssistantUpdate?.text) {
    candidate.currentWorkStatus.lastAssistantUpdate.text = clip(
      candidate.currentWorkStatus.lastAssistantUpdate.text,
      800,
      { trim: false },
    );
  }
  if (render().length <= maxChars) return render();
  delete candidate.priorUserRequestsForContinuation;
  delete candidate.olderContinuationAnchor;
  delete candidate.previousTurnFinalResponseEvidence;
  if (render(false).length <= maxChars) return render(false);
  candidate.recentRawTail = candidate.recentRawTail.slice(-4).map((item) => ({
    role: item.role,
    text: clip(item.text, 300, { trim: false }),
  }));
  candidate.currentWorkStatus.recentToolOutcomes = [];
  if (render(false).length <= maxChars) return render(false);
  // Never byte-slice JSON: a later hard compaction must be able to inherit it.
  // The original latest user message remains independently retained verbatim.
  const payloadLimit = Math.max(2, maxChars - prefix.length - 1);
  const emergency = `${prefix}\n${JSON.stringify(emergencyContinuityMemory(candidate, payloadLimit))}`;
  return emergency.length <= maxChars ? emergency : `${prefix}\n{}`;
}

function buildCheckpoint(messagesInput, options = {}) {
  const messages = messagesInput.map(normalizeMessage);
  const previousState = extractPriorContinuityState(messages);
  const latestUserIndex = [...messages].reverse().find((message) => message.role === "user")?.index ?? -1;
  const latestUser = latestUserIndex >= 0 ? messages[latestUserIndex].text : String(previousState?.latestUserMessage || "");
  const tailStart = tailStartIndex(messages, options.recentCompleteTurns ?? 2);
  const toolMemoryOptions = {
    ...options,
    initialActiveProject: previousState?.activeProject?.descriptor || "",
  };
  const outcomeRecords = toolOutcomeRecords(messages, tailStart, toolMemoryOptions);
  const allRecentOutcomeRecords = toolOutcomeRecords(messages, messages.length, toolMemoryOptions);
  const outcomes = serializeToolOutcomeRecords(outcomeRecords, toolMemoryOptions);
  const allRecentOutcomes = serializeToolOutcomeRecords(allRecentOutcomeRecords, toolMemoryOptions);
  const state = stateMemory(allRecentOutcomeRecords);
  const openQuestions = unresolvedQuestions(messages, latestUserIndex);
  const continuity = buildContinuityMemory(messages, {
    activeProject: state.activeProject,
    recentOlderToolOutcomes: allRecentOutcomes,
    modifiedOrObservedFiles: state.files,
    recentBuildOrTestState: state.builds,
    openQuestionEvidence: openQuestions,
  }, options);
  const latestMessage = latestUserIndex >= 0 ? messages[latestUserIndex] : null;
  const recentRequests = priorUserRequestsForContinuation(messages, latestUserIndex);
  const previousFileObservations = migratePriorFileObservations(
    previousState?.modifiedOrObservedFiles || [],
    previousState,
    16,
  );
  const memory = sanitizeStructuredDurableValue({
    ...continuity,
    currentUserRequestVerbatim: continuity.latestUserMessage,
    olderContinuationAnchor: olderContinuationAnchor(messages, latestUserIndex, recentRequests)
      || continuity.continuationAntecedent,
    priorUserRequestsForContinuation: recentRequests,
    latestUserConstraints: latestMessage ? explicitConstraints([latestMessage]) : [],
    historicalUserConstraintEvidence: mergeEvidence(
      previousState?.historicalUserConstraintEvidence,
      historicalConstraintEvidence(messages, latestUserIndex),
      12,
    ),
    openQuestionEvidence: openQuestions,
    previousTurnFinalResponseEvidence: previousTurnFinalResponseEvidence(messages, latestUserIndex),
    recentOlderToolOutcomes: mergeEvidence(previousState?.recentOlderToolOutcomes, outcomes, 12),
    modifiedOrObservedFiles: coalesceFileObservations(
      [...previousFileObservations, ...state.files],
      16,
    ),
    recentBuildOrTestState: mergeEvidence(previousState?.recentBuildOrTestState, state.builds, 4),
  });
  const maxCheckpointChars = Math.max(2000, Number(options.maxCheckpointChars || 12000));
  const checkpoint = renderCheckpoint(memory, maxCheckpointChars);
  const fileIndexes = messages.filter((message) => message.hasFiles).map((message) => message.index);
  const retainedIndexes = new Set(messages.filter((message) => (
    (message.role === "system" && !generatedCheckpoint(message)) || message.index >= tailStart
  )).map((message) => message.index));
  for (const index of fileIndexes) retainedIndexes.add(index);
  if (latestUserIndex >= 0) retainedIndexes.add(latestUserIndex);
  return {
    checkpoint,
    memory,
    tailStart,
    latestUserIndex,
    latestUserVerbatim: latestUser,
    retainedIndexes: [...retainedIndexes].sort((a, b) => a - b),
    omittedMessageCount: messages.length - retainedIndexes.size,
  };
}

function shouldCompact(measurement, options = {}) {
  if (options.enabled !== true || options.observeOnly === true) return false;
  const messageCount = Number(measurement.messageCount || 0);
  const remaining = Number(measurement.remainingTokens);
  const softRemaining = Math.max(0, Number(options.softRemainingTokens || 14000));
  const fallbackCount = Math.max(4, Number(options.compactAboveMessageCount || 24));
  if (Number.isFinite(remaining)) return remaining <= softRemaining;
  return messageCount >= fallbackCount;
}

module.exports = {
  CONTROL_DIRECTIVES,
  INTERNAL_KEYS,
  buildCheckpoint,
  explicitConstraints,
  normalizeMessage,
  olderContinuationAnchor,
  parseToolResult,
  priorUserRequestsForContinuation,
  shouldCompact,
  stripControl,
  tailStartIndex,
  toolOutcomeMemory,
};

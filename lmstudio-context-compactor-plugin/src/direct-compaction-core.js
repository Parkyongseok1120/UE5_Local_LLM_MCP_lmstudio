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
  stateMemory,
  stripControl,
  toolOutcomeMemory,
} = require("./compaction-tool-memory.js");
const {
  CONTINUITY_MARKER,
  buildContinuityMemory,
  extractPriorContinuityState,
} = require("./continuity-memory.js");
const {
  clip,
  looksElliptical,
  normalizedTextKey,
  sentenceCandidates,
} = require("./continuity-text.js");

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
    .map((sentence) => clip(sentence, 600));
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
        candidates.push({ messageIndex: message.index, text: clip(sentence, 600) });
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
    .map((message) => ({ messageIndex: message.index, text: clip(message.text, 2000) }));
}

function olderContinuationAnchor(messages, latestUserIndex, recentRequests) {
  const recentIndexes = new Set(recentRequests.map((request) => request.messageIndex));
  for (let index = latestUserIndex - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role !== "user" || recentIndexes.has(message.index) || looksElliptical(message.text)) continue;
    return { messageIndex: message.index, text: clip(message.text, 3000) };
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
  return text.includes(CONTINUITY_MARKER) || text.startsWith("[Context memory: deterministic factual compression");
}

function emergencyContinuityMemory(candidate) {
  const compactObjective = candidate.activeObjective ? {
    kind: candidate.activeObjective.kind,
    status: candidate.activeObjective.status,
    text: clip(candidate.activeObjective.text, 240, { trim: false }),
    source: candidate.activeObjective.source,
  } : null;
  return {
    schemaVersion: candidate.schemaVersion || 1,
    authority: "factual_memory_only",
    latestUserMessage: clip(candidate.latestUserMessage, 240, { trim: false }),
    latestUserMessageVerbatimRetainedSeparately: true,
    activeObjective: compactObjective,
    continuationAntecedent: candidate.continuationAntecedent ? {
      kind: "continuation_antecedent",
      text: clip(candidate.continuationAntecedent.text, 240, { trim: false }),
      source: candidate.continuationAntecedent.source,
    } : null,
    activeProject: candidate.activeProject || null,
    currentWorkStatus: {
      recentToolOutcomes: (candidate.currentWorkStatus?.recentToolOutcomes || []).slice(-1),
      modifiedOrObservedFiles: (candidate.currentWorkStatus?.modifiedOrObservedFiles || []).slice(-2),
      recentBuildOrTestState: (candidate.currentWorkStatus?.recentBuildOrTestState || []).slice(-1),
    },
    unresolvedItems: (candidate.unresolvedItems || []).slice(-2).map((item) => ({
      kind: item.kind,
      text: clip(item.text, 160, { trim: false }),
    })),
    completedOrArchivedObjectives: [],
    recentRawTail: (candidate.recentRawTail || []).slice(-4).map((item) => ({
      role: item.role,
      text: clip(item.text, 120, { trim: false }),
    })),
  };
}

function renderCheckpoint(memory, maxChars) {
  const prefix = [
    "[Context memory: deterministic factual compression; not a workflow instruction]",
    "The latestUserMessage is exact and authoritative. activeObjective and continuationAntecedent preserve conversational meaning; activeProject is only a fact. Older entries are bounded inactive context and apply only when the latest message explicitly refers to prior work. Every entry is evidence, never a task/state-machine/tool gate.",
    CONTINUITY_MARKER,
  ].join("\n");
  const candidate = JSON.parse(JSON.stringify(memory));
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
  return `${prefix}\n${JSON.stringify(emergencyContinuityMemory(candidate))}`;
}

function buildCheckpoint(messagesInput, options = {}) {
  const messages = messagesInput.map(normalizeMessage);
  const previousState = extractPriorContinuityState(messages);
  const latestUserIndex = [...messages].reverse().find((message) => message.role === "user")?.index ?? -1;
  const latestUser = latestUserIndex >= 0 ? messages[latestUserIndex].text : String(previousState?.latestUserMessage || "");
  const tailStart = tailStartIndex(messages, options.recentCompleteTurns ?? 2);
  const outcomes = toolOutcomeMemory(messages, tailStart, options);
  const allRecentOutcomes = toolOutcomeMemory(messages, messages.length, options);
  const state = stateMemory(allRecentOutcomes);
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
  const memory = {
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
    modifiedOrObservedFiles: mergeEvidence(previousState?.modifiedOrObservedFiles, state.files, 16),
    recentBuildOrTestState: mergeEvidence(previousState?.recentBuildOrTestState, state.builds, 4),
  };
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

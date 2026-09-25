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
  extractPriorAssistantEvidence,
  generatedAssistantCheckpoint,
  mergePriorAssistantEvidence,
  renderAssistantCheckpoint,
  splitAssistantEvidence,
} = require("./continuity-assistant-evidence.js");
const {
  clip,
  clipHeadTail,
  looksElliptical,
  normalizedTextKey,
  sentenceCandidates,
  visibleAssistantText,
} = require("./continuity-text.js");
const {
  sanitizeStructuredDurableValue,
  sanitizeUserAuthoredText,
} = require("./durable-memory-sanitizer.js");
const { renderBudgetedCheckpoint } = require("./checkpoint-budget.js");

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
  const window = messages.filter((message) => message.index >= start && message.index <= latestUserIndex);
  for (let offset = 0; offset < window.length; offset += 1) {
    const message = window[offset];
    if (message.role !== "user") continue;
    const nextUserOffset = window.findIndex((candidate, index) => index > offset && candidate.role === "user");
    const answerBoundary = nextUserOffset >= 0 ? nextUserOffset : window.length;
    const laterAnswerExists = window
      .slice(offset + 1, answerBoundary)
      .some((candidate) => candidate.role === "assistant"
        && visibleAssistantText(candidate.text).trim()
        && candidate.toolRequests.length === 0);
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
  const prior = messages.filter((message) => message.index < latestUserIndex).at(-1);
  if (!prior) return { present: false, reason: "no_previous_message" };
  if (prior.role === "assistant" && prior.toolRequests.length === 0
    && visibleAssistantText(prior.text).trim()) {
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

function boundedCurrentTurnIndexes(messages, latestUserIndex, maxMessages) {
  const cap = Math.max(0, Math.min(256, Math.trunc(Number(maxMessages) || 0)));
  const groups = [];
  let index = Math.max(0, latestUserIndex + 1);
  while (index < messages.length) {
    const message = messages[index];
    if (message.role === "assistant" && message.toolRequests.length > 0) {
      const requestIds = message.toolRequests.map((request) => String(request.id || "")).filter(Boolean);
      const hasExactIds = requestIds.length === message.toolRequests.length;
      const remainingIds = new Set(requestIds);
      let remainingIdlessResults = message.toolRequests.length;
      let end = index + 1;
      while (end < messages.length && messages[end].role === "tool") {
        const results = messages[end].toolResults;
        if (results.length === 0) break;
        if (hasExactIds) {
          const resultIds = results.map((result) => String(result.toolCallId || ""));
          if (resultIds.some((resultId) => !resultId || !remainingIds.has(resultId))) break;
          for (const resultId of resultIds) remainingIds.delete(resultId);
        } else {
          remainingIdlessResults -= results.length;
        }
        end += 1;
        if (hasExactIds ? remainingIds.size === 0 : remainingIdlessResults <= 0) break;
      }
      const completeToolExchange = hasExactIds
        ? remainingIds.size === 0
        : remainingIdlessResults <= 0;
      groups.push({
        start: index,
        end,
        completeToolExchange,
        retainable: completeToolExchange,
      });
      index = end;
      continue;
    }
    groups.push({
      start: index,
      end: index + 1,
      completeToolExchange: false,
      retainable: message.role !== "tool",
    });
    index += 1;
  }

  const newestUnreadExchange = [...groups].reverse().find((group) => group.completeToolExchange) || null;
  const retained = new Set();
  let retainedCount = 0;
  for (const group of [...groups].reverse()) {
    const mustRetain = group === newestUnreadExchange;
    const groupSize = group.end - group.start;
    if (!mustRetain && (!group.retainable || retainedCount + groupSize > cap)) continue;
    for (let messageIndex = group.start; messageIndex < group.end; messageIndex += 1) {
      retained.add(messageIndex);
    }
    retainedCount += groupSize;
  }
  return retained;
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
  return generatedAssistantCheckpoint(message) || Boolean(splitSystemCheckpoint(message));
}

function splitSystemCheckpoint(message) {
  if (message?.role !== "system") return null;
  const text = String(message?.text || "");
  const starts = [
    "[Context memory: deterministic factual compression; not a workflow instruction]",
    ...CONTINUITY_MARKERS,
  ];
  const candidates = [];
  for (const start of starts) {
    let offset = text.indexOf(start);
    while (offset >= 0) {
      if (offset === 0 || text.slice(Math.max(0, offset - 2), offset) === "\n\n") {
        candidates.push(offset);
      }
      offset = text.indexOf(start, offset + start.length);
    }
  }
  for (const offset of [...new Set(candidates)].sort((left, right) => right - left)) {
    const checkpointText = text.slice(offset).trim();
    const marker = CONTINUITY_MARKERS.find(value => checkpointText.includes(value));
    if (!marker) continue;
    const jsonStart = checkpointText.indexOf("{", checkpointText.indexOf(marker) + marker.length);
    if (jsonStart < 0) continue;
    try {
      const parsed = JSON.parse(checkpointText.slice(jsonStart));
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) continue;
      return { instructionText: text.slice(0, offset).trim(), checkpointText };
    } catch {
      // A literal marker or malformed quoted example is ordinary system text.
    }
  }
  return null;
}

function invariantSystemText(message) {
  if (message?.role !== "system") return "";
  return splitSystemCheckpoint(message)?.instructionText ?? String(message?.text || "").trim();
}

function compactEmergencyGitObservation(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const compact = {};
  for (const key of [
    "action", "comparison", "base", "head", "currentHead", "blobOid", "path",
    "repositoryIdentity", "workspaceIdentity", "hasMore", "complete", "incomplete",
    "pageStart", "pageEnd", "pageHasMore", "sourceResultComplete",
    "sha256", "hashSource", "startLine", "endLine", "totalLines", "returnedCount", "total",
    "since", "until", "authorQuery", "authorQuerySemantics", "identitySemantics",
    "sourceConsistency", "submoduleWorktrees", "queryPathBase", "requestedPathsOmitted",
    "requestedPathsCount", "requestedPathsSha256", "resolvedRepositoryPathsOmitted",
    "resolvedRepositoryPathsCount", "resolvedRepositoryPathsSha256", "omittedItems", "canonicalProjectRoot", "canonicalProject",
    "projectIdentity",
  ]) {
    if (value[key] !== undefined) compact[key] = value[key];
  }
  for (const key of ["requestedPaths", "resolvedRepositoryPaths"]) {
    if (!Array.isArray(value[key])) continue;
    compact[key] = value[key].slice(0, 2).map(item => clip(item, 160, { trim: false }));
    const omitted = value[key].length - compact[key].length;
    if (omitted > 0) compact[`${key}Omitted`] = Number(compact[`${key}Omitted`] || 0) + omitted;
  }
  const sourceOmittedItems = Number(value.sourceOmittedItems ?? value.omittedItems ?? 0);
  const priorMemoryOmittedItems = Number(value.memoryOmittedItems || 0);
  compact.sourceOmittedItems = Math.max(0, sourceOmittedItems);
  if (Array.isArray(value.items) && value.items.length) {
    compact.items = value.items.slice(0, 1);
    compact.memoryOmittedItems = Math.max(0, priorMemoryOmittedItems + value.items.length - compact.items.length);
  } else {
    compact.memoryOmittedItems = Math.max(0, priorMemoryOmittedItems);
  }
  compact.omittedItems = compact.sourceOmittedItems + compact.memoryOmittedItems;
  return sanitizeStructuredDurableValue(compact);
}

function boundedSerializedOutcome(value, maxChars = 480) {
  try {
    const parsed = typeof value === "string" ? JSON.parse(value) : value;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const serialized = JSON.stringify(sanitizeStructuredDurableValue(parsed));
    if (serialized.length <= maxChars) return serialized;
    const compact = {};
    for (const key of [
      "ok", "status", "operation", "errorCode", "mode", "proofLevel", "exitCode",
      "upToDate", "actionsExecuted", "failedCount", "succeededCount", "summary",
      "outcomeDisplayState", "canonicalProject", "fileObservationState",
    ]) {
      if (parsed[key] !== undefined) compact[key] = typeof parsed[key] === "string"
        ? clip(parsed[key], 180, { trim: false }) : parsed[key];
    }
    const result = JSON.stringify(sanitizeStructuredDurableValue(compact));
    return Object.keys(compact).length && result.length <= maxChars ? result : null;
  } catch {
    return null;
  }
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
    compactionGeneration: candidate.compactionGeneration,
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
      gitObservations: [],
      historicalEvidence: [],
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
  const factualReserve = candidate.currentWorkStatus?.gitObservations?.length
    ? Math.min(2400, Math.max(700, Math.floor(payloadLimit * 0.3)))
    : 0;
  let emergency = null;
  for (const objectiveLimit of objectiveLimits) {
    for (const variant of auxiliaryVariants) {
      const candidateMemory = baseForLimits({ objectiveLimit, ...variant });
      if (JSON.stringify(candidateMemory).length <= payloadLimit - factualReserve) {
        emergency = candidateMemory;
        break;
      }
    }
    if (emergency) break;
  }
  emergency ||= {
    schemaVersion: 2,
    compactionGeneration: candidate.compactionGeneration,
    authority: "factual_memory_only",
    latestUserMessageVerbatimRetainedSeparately: true,
    activeObjective: null,
    activeProject: null,
    currentWorkStatus: {
      recentToolOutcomes: [],
      gitObservations: [],
      historicalEvidence: [],
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
  for (const observation of [...(candidate.currentWorkStatus?.gitObservations || [])].reverse()) {
    const compact = compactEmergencyGitObservation(observation);
    if (compact) addBounded(emergency.currentWorkStatus.gitObservations, compact, true);
  }
  for (const evidence of [...(candidate.currentWorkStatus?.historicalEvidence || [])].reverse()) {
    addBounded(emergency.currentWorkStatus.historicalEvidence, evidence, true);
  }
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
  for (const outcome of [...(candidate.currentWorkStatus?.recentToolOutcomes || [])].reverse()) {
    const bounded = boundedSerializedOutcome(outcome);
    if (bounded) addBounded(emergency.currentWorkStatus.recentToolOutcomes, bounded, true);
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
  // The raw latest user message is retained as a separate model-history
  // message. Keep only a bounded head/tail reminder in durable memory and do
  // not serialize its exact duplicate under a second field.
  if (typeof candidate.latestUserMessage === "string") {
    candidate.latestUserMessage = clipHeadTail(candidate.latestUserMessage, 1200, { trim: false });
    candidate.latestUserMessageVerbatimRetainedSeparately = true;
  }
  delete candidate.currentUserRequestVerbatim;
  if (candidate.currentWorkStatus) {
    // These legacy top-level mirrors remain on the in-process result for
    // compatibility, but serializing them would duplicate the canonical
    // currentWorkStatus facts in the next model input.
    delete candidate.recentOlderToolOutcomes;
    delete candidate.modifiedOrObservedFiles;
    delete candidate.recentBuildOrTestState;
  }
  const render = (pretty = true) => `${prefix}\n${JSON.stringify(candidate, null, pretty ? 2 : 0)}`;
  if (render().length <= maxChars) return render();
  candidate.recentRawTail = (candidate.recentRawTail || []).slice(-4).map((item) => ({
    ...item,
    text: clip(item.text, 800, { trim: false }),
  }));
  candidate.completedOrArchivedObjectives = (candidate.completedOrArchivedObjectives || []).slice(-4);
  candidate.historicalUserConstraintEvidence = (candidate.historicalUserConstraintEvidence || []).slice(-6);
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
  candidate.currentWorkStatus.recentToolOutcomes = candidate.currentWorkStatus.recentToolOutcomes
    .slice(-2)
    .map(outcome => boundedSerializedOutcome(outcome))
    .filter(Boolean);
  if (render(false).length <= maxChars) return render(false);
  // Never byte-slice JSON: a later hard compaction must be able to inherit it.
  // The original latest user message remains independently retained verbatim.
  const payloadLimit = Math.max(2, maxChars - prefix.length - 1);
  const emergency = `${prefix}\n${JSON.stringify(emergencyContinuityMemory(candidate, payloadLimit))}`;
  return emergency.length <= maxChars ? emergency : `${prefix}\n{}`;
}

function buildCheckpoint(messagesInput, options = {}) {
  const messages = messagesInput.map(normalizeMessage);
  const previousState = mergePriorAssistantEvidence(
    extractPriorContinuityState(messages),
    extractPriorAssistantEvidence(messages),
  );
  // Prior memory is merged through previousState. Feeding generated memory
  // back as conversational evidence recursively grows the next checkpoint.
  const semanticMessages = messages.filter((message) => !generatedCheckpoint(message));
  const latestUserIndex = [...messages].reverse().find((message) => message.role === "user")?.index ?? -1;
  const latestUser = latestUserIndex >= 0 ? messages[latestUserIndex].text : String(previousState?.latestUserMessage || "");
  const tailStart = tailStartIndex(messages, options.recentCompleteTurns ?? 2);
  const boundedCurrentTurn = options.maxCurrentTurnMessages !== undefined;
  const currentTurnIndexes = boundedCurrentTurn
    ? boundedCurrentTurnIndexes(messages, latestUserIndex, options.maxCurrentTurnMessages)
    : null;
  const fileIndexes = messages.filter((message) => (
    message.hasFiles
    && (!boundedCurrentTurn || message.role === "system" || message.role === "user")
  )).map((message) => message.index);
  const retainedIndexes = new Set(messages.filter((message) => (
    !generatedCheckpoint(message) && (
      message.role === "system"
      || (boundedCurrentTurn ? currentTurnIndexes.has(message.index) : message.index >= tailStart)
    )
  )).map((message) => message.index));
  for (const index of fileIndexes) retainedIndexes.add(index);
  if (latestUserIndex >= 0) retainedIndexes.add(latestUserIndex);
  const omittedToolMessageIndexes = new Set(messages.filter((message) => (
    message.role === "tool" && !retainedIndexes.has(message.index)
  )).map((message) => message.index));
  const toolMemoryOptions = {
    ...options,
    initialActiveProject: previousState?.activeProject?.descriptor || "",
  };
  const outcomeRecords = toolOutcomeRecords(messages, messages.length, {
    ...toolMemoryOptions,
    includeMessageIndexes: omittedToolMessageIndexes,
  });
  const allRecentOutcomeRecords = toolOutcomeRecords(messages, messages.length, {
    ...toolMemoryOptions,
    aggregateAll: true,
  });
  const allOmittedOutcomeRecords = toolOutcomeRecords(messages, messages.length, {
    ...toolMemoryOptions,
    includeMessageIndexes: omittedToolMessageIndexes,
    aggregateAll: true,
  });
  const outcomes = serializeToolOutcomeRecords(outcomeRecords, toolMemoryOptions);
  const state = stateMemory(allRecentOutcomeRecords);
  const durableState = stateMemory(allOmittedOutcomeRecords);
  const openQuestions = unresolvedQuestions(semanticMessages, latestUserIndex);
  const continuity = buildContinuityMemory(semanticMessages, {
    activeProject: state.activeProject,
    recentOlderToolOutcomes: outcomes,
    gitObservations: durableState.gitObservations,
    historicalEvidence: durableState.historicalEvidence,
    modifiedOrObservedFiles: durableState.files,
    recentBuildOrTestState: durableState.builds,
    openQuestionEvidence: openQuestions,
  }, { ...options, previousState });
  const latestMessage = latestUserIndex >= 0 ? messages[latestUserIndex] : null;
  const recentRequests = priorUserRequestsForContinuation(messages, latestUserIndex);
  const memory = sanitizeStructuredDurableValue({
    ...continuity,
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
    previousTurnFinalResponseEvidence: previousTurnFinalResponseEvidence(semanticMessages, latestUserIndex),
    recentOlderToolOutcomes: continuity.currentWorkStatus?.recentToolOutcomes || [],
    modifiedOrObservedFiles: continuity.currentWorkStatus?.modifiedOrObservedFiles || [],
    recentBuildOrTestState: continuity.currentWorkStatus?.recentBuildOrTestState || [],
  });
  const maxCheckpointChars = Math.max(2000, Number(options.maxCheckpointChars || 22000));
  const { systemMemory, assistantEvidence } = splitAssistantEvidence(memory);
  const assistantCheckpoint = options.checkpointPolicy === "mandatory" ? "" : renderAssistantCheckpoint(
    assistantEvidence,
    // Preserve the factual checkpoint's emergency file/evidence budget first.
    Math.min(2600, Math.max(0, maxCheckpointChars - 6000)),
  );
  const checkpoint = options.checkpointPolicy
    ? renderBudgetedCheckpoint(systemMemory, maxCheckpointChars - assistantCheckpoint.length,
      options.checkpointPolicy === "mandatory")
    : renderCheckpoint(systemMemory, maxCheckpointChars - assistantCheckpoint.length);
  let serializedState = {};
  try {
    const marker = checkpoint.indexOf(CONTINUITY_MARKER);
    serializedState = JSON.parse(checkpoint.slice(checkpoint.indexOf("{", marker)));
  } catch {
    // Keep diagnostics conservative if a future checkpoint format changes.
  }
  const sourceFiles = systemMemory.currentWorkStatus?.modifiedOrObservedFiles || [];
  const serializedFiles = serializedState.currentWorkStatus?.modifiedOrObservedFiles || [];
  const serializationDiagnostics = {
    schemaVersion: serializedState.schemaVersion ?? null,
    compactionGeneration: serializedState.compactionGeneration ?? null,
    sourceFileObservationCount: sourceFiles.length,
    serializedFileObservationCount: serializedFiles.length,
    serializedObservedRangeCount: serializedFiles.reduce((count, file) => (
      count + (Array.isArray(file?.observedLineRanges) ? file.observedLineRanges.length : 0)
    ), 0),
    omittedFileObservationCount: Math.max(0, sourceFiles.length - serializedFiles.length),
    omissionReasons: sourceFiles.length > serializedFiles.length ? ["checkpoint_character_budget"] : [],
  };
  return {
    checkpoint,
    assistantCheckpoint,
    memory,
    tailStart,
    latestUserIndex,
    latestUserVerbatim: latestUser,
    retainedIndexes: [...retainedIndexes].sort((a, b) => a - b),
    omittedMessageCount: messages.length - retainedIndexes.size,
    serializationDiagnostics,
  };
}

function shouldCompact(measurement, options = {}) {
  if (options.observeOnly === true) return false;
  const messageCount = Number(measurement.messageCount || 0);
  const remaining = Number(measurement.remainingTokens);
  const softRemaining = Math.max(0, Number(options.softRemainingTokens || 6000));
  const fallbackCount = Math.max(4, Number(options.compactAboveMessageCount || 24));
  if (measurement.exact === false && messageCount >= fallbackCount) return true;
  if (Number.isFinite(remaining)) return remaining <= softRemaining;
  return messageCount >= fallbackCount;
}

module.exports = {
  CONTROL_DIRECTIVES,
  INTERNAL_KEYS,
  buildCheckpoint,
  explicitConstraints,
  invariantSystemText,
  normalizeMessage,
  olderContinuationAnchor,
  parseToolResult,
  priorUserRequestsForContinuation,
  shouldCompact,
  stripControl,
  tailStartIndex,
  toolOutcomeMemory,
};

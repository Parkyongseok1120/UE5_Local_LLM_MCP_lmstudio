"use strict";

/**
 * Deterministic, context-only memory for Direct Model Mode.
 *
 * This module deliberately has no concept of tasks, routes, planners, gates,
 * required tools, tool filtering, or synthesis acknowledgement.  It reduces
 * older chat history to factual memory and always keeps the latest real user
 * message verbatim.
 */

const INTERNAL_KEYS = new Set([
  "activeSliceId", "allowedTools", "authorizationBound", "claimLedger",
  "commitEligible", "control", "controlEpoch", "controlFingerprint",
  "evidenceBundle", "evidenceStateHash", "foreignHealthy", "ownerCapability",
  "pendingGates", "phase", "phaseState", "planRevision", "promptContract", "requiredTool",
  "routeHash", "routeOwnership", "serverControl", "sourceEvidence",
  "state", "stateHash", "synthesisEvidence", "synthesisLatch", "synthesisReadiness",
  "taskAuthorization", "taskLifecycle", "taskRouteOwnership", "toolRoute",
]);

const CONTROL_DIRECTIVES = new Set([
  "agentInstruction", "doNotRetry", "doNotRetryTools", "nextAction",
  "nextActionArgs", "nextActionIsTool", "recoveryActionRequired",
  "requiredNextAction", "requiredNextTool", "requiredNextToolArgs",
  "requiredSequence", "retryPolicy", "stopCurrentPhase", "stopCurrentWorkflow",
]);

const CONTROL_KEY_ROOTS = new Set([
  "control", "phase", "route", "state", "synthesis", "task",
]);
const CONTROL_KEY_TOKENS = new Set(
  [...INTERNAL_KEYS, ...CONTROL_DIRECTIVES].map((key) => normalizeKeyToken(key)),
);

function isRecord(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function clip(value, maxChars) {
  const text = String(value ?? "").trim();
  if (text.length <= maxChars) return text;
  return `${text.slice(0, Math.max(0, maxChars - 18))} …[truncated]`;
}

function normalizeKeyToken(value) {
  return String(value || "").replace(/[^A-Za-z0-9]/g, "").toLowerCase();
}

function keyRoot(value) {
  const separated = String(value || "").replace(/([a-z0-9])([A-Z])/g, "$1_$2");
  return String(separated.split(/[^A-Za-z0-9]+/, 1)[0] || "").toLowerCase();
}

function isControlKey(value) {
  const normalized = normalizeKeyToken(value);
  return CONTROL_KEY_TOKENS.has(normalized) || CONTROL_KEY_ROOTS.has(keyRoot(value));
}

function sanitizeRetainedString(value, maxChars = 4000) {
  const sanitized = String(value ?? "").replace(
    /[A-Za-z][A-Za-z0-9_]*/g,
    (token) => isControlKey(token) ? "[control-token-omitted]" : token,
  );
  return clip(sanitized, maxChars);
}

function stripControl(value, depth = 0) {
  if (depth > 8) return "[depth limited]";
  if (Array.isArray(value)) return value.slice(0, 40).map((item) => stripControl(item, depth + 1));
  if (!isRecord(value)) return typeof value === "string" ? sanitizeRetainedString(value) : value;
  const out = {};
  for (const [key, item] of Object.entries(value)) {
    if (isControlKey(key)) continue;
    out[key] = stripControl(item, depth + 1);
  }
  return out;
}

function normalizeMessage(message, index = 0) {
  const role = String(message?.role || "");
  return {
    index,
    role,
    text: String(message?.text || ""),
    hasFiles: message?.hasFiles === true,
    toolRequests: Array.isArray(message?.toolRequests) ? message.toolRequests : [],
    toolResults: Array.isArray(message?.toolResults) ? message.toolResults : [],
  };
}

function sentenceCandidates(text) {
  return String(text || "")
    .split(/(?<=[.!?。！？])\s+|\r?\n+/u)
    .map((line) => line.replace(/^[-*\d.)\s]+/, "").trim())
    .filter(Boolean);
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
      const key = value.toLowerCase();
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
    const key = candidate.text.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(-maxItems);
}

function parseToolResult(content) {
  const text = String(content || "").trim();
  if (!text) return { summary: "empty tool result" };
  try {
    const source = stripControl(JSON.parse(text));
    const out = {};
    for (const key of [
      "ok", "status", "summary", "message", "errorCode", "path", "operation",
      "sha256", "previousSha256", "size", "truncated", "hasMore",
      "startLine", "endLine", "totalLines", "filesScanned", "findingCount",
      "validationOk", "blocksBuild", "exitCode", "likelyErrors", "fullLogPath",
      "upToDate", "actionsExecuted", "proofLevel", "failedCount", "succeededCount", "files",
    ]) {
      if (source[key] !== undefined) out[key] = source[key];
    }
    if (!Object.keys(out).length) out.summary = "tool result contained no retained factual fields";
    return out;
  } catch {
    return { summary: "malformed tool result omitted" };
  }
}

function toolOutcomeMemory(messages, beforeIndex, options = {}) {
  const maxItems = Math.max(1, Number(options.maxItems || 12));
  const maxChars = Math.max(200, Number(options.maxToolResultChars || 1200));
  const outcomes = [];
  for (const message of messages.slice(0, beforeIndex)) {
    if (message.role !== "tool") continue;
    for (const result of message.toolResults) {
      const parsed = parseToolResult(result?.content ?? result);
      outcomes.push(clip(JSON.stringify(parsed), maxChars));
    }
    if (!message.toolResults.length && message.text) {
      outcomes.push(clip(JSON.stringify(parseToolResult(message.text)), maxChars));
    }
  }
  return outcomes.slice(-maxItems);
}

function stateMemory(outcomes) {
  const files = [];
  const builds = [];
  for (const serialized of outcomes) {
    let item;
    try { item = JSON.parse(serialized); } catch { continue; }
    if (item.path && (item.operation || item.sha256 || item.previousSha256)) {
      files.push({
        path: item.path,
        operation: item.operation || "observed",
        sha256: item.sha256 || undefined,
        previousSha256: item.previousSha256 || undefined,
      });
    }
    if (Array.isArray(item.files)) {
      for (const file of item.files) {
        if (!isRecord(file) || !file.path) continue;
        files.push({
          path: file.path,
          operation: file.operation || item.operation || "observed",
          sha256: file.sha256 || undefined,
          previousSha256: file.previousSha256 || undefined,
        });
      }
    }
    if (item.exitCode !== undefined || item.proofLevel || item.fullLogPath || item.likelyErrors) {
      builds.push({
        ok: item.ok,
        exitCode: item.exitCode,
        proofLevel: item.proofLevel,
        upToDate: item.upToDate,
        actionsExecuted: item.actionsExecuted,
        likelyErrors: item.likelyErrors,
        fullLogPath: item.fullLogPath,
      });
    }
  }
  return {
    files: files.slice(-16),
    builds: builds.slice(-4),
  };
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
  const boundedLimit = Math.max(1, Math.min(3, Math.trunc(Number(limit) || 3)));
  return messages
    .slice(0, Math.max(0, latestUserIndex))
    .filter((message) => message.role === "user")
    .slice(-boundedLimit)
    .map((message) => ({
      messageIndex: message.index,
      text: clip(message.text, 2000),
    }));
}

function looksElliptical(text) {
  const value = String(text || "").trim();
  if (!value || value.length > 100) return false;
  return /^(?:yes|yeah|yep|ok(?:ay)?|sure|continue|go\s+on|proceed|do\s+it|keep\s+going|네|예|응|좋아|그래|계속(?:해|하세요|진행해)?|진행(?:해|하세요)?|해줘|하자)[\s.!?。！？]*$/iu.test(value);
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

function buildCheckpoint(messagesInput, options = {}) {
  const messages = messagesInput.map(normalizeMessage);
  const latestUserIndex = [...messages].reverse().find((message) => message.role === "user")?.index ?? -1;
  const latestUser = latestUserIndex >= 0 ? messages[latestUserIndex].text : "";
  const tailStart = tailStartIndex(messages, options.recentCompleteTurns ?? 2);
  const fileIndexes = messages.filter((message) => message.hasFiles).map((message) => message.index);
  const outcomes = toolOutcomeMemory(messages, tailStart, options);
  const state = stateMemory(outcomes);
  const latestMessage = latestUserIndex >= 0 ? messages[latestUserIndex] : null;
  const recentRequests = priorUserRequestsForContinuation(messages, latestUserIndex);
  const memory = {
    currentUserRequestVerbatim: latestUser,
    olderContinuationAnchor: olderContinuationAnchor(messages, latestUserIndex, recentRequests),
    priorUserRequestsForContinuation: recentRequests,
    latestUserConstraints: latestMessage ? explicitConstraints([latestMessage]) : [],
    historicalUserConstraintEvidence: historicalConstraintEvidence(messages, latestUserIndex),
    openQuestionEvidence: unresolvedQuestions(messages, latestUserIndex),
    previousTurnFinalResponseEvidence: previousTurnFinalResponseEvidence(messages, latestUserIndex),
    recentOlderToolOutcomes: outcomes,
    modifiedOrObservedFiles: state.files,
    recentBuildOrTestState: state.builds,
  };
  const preface = [
    "[Context memory: deterministic factual compression; not a workflow instruction]",
    "The latest real user message retained below is authoritative. Prior user requests and the older continuation anchor are bounded inactive context: use them only when the latest message explicitly refers to prior work.",
    JSON.stringify(memory, null, 2),
  ].join("\n");
  const maxCheckpointChars = Math.max(2000, Number(options.maxCheckpointChars || 12000));
  const checkpoint = clip(preface, maxCheckpointChars);
  const retainedIndexes = new Set(messages.filter((message) => message.role === "system" || message.index >= tailStart).map((message) => message.index));
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
  if (options.enabled === false || options.observeOnly === true) return false;
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

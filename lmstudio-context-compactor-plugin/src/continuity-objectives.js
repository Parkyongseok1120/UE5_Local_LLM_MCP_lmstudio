"use strict";

const {
  clip,
  looksElliptical,
  normalizedTextKey,
} = require("./continuity-text.js");

function userMessages(messages) {
  return messages.filter((message) => message.role === "user" && String(message.text || "").trim());
}

function substantiveUserMessages(messages) {
  return userMessages(messages).filter((message) => !looksElliptical(message.text));
}

function objectiveFromMessage(message, continuedBy = null) {
  if (!message) return null;
  return {
    kind: "user_objective",
    status: "active",
    text: clip(message.text, 4000, { trim: false }),
    messageIndex: Number.isInteger(message.index) ? message.index : undefined,
    source: "current_history",
    ...(continuedBy ? { continuedBy } : {}),
  };
}

function inheritedObjective(previousState, continuedBy = null) {
  const previous = previousState?.activeObjective;
  if (!previous || !String(previous.text || "").trim()) return null;
  return {
    kind: "user_objective",
    status: "active",
    text: clip(previous.text, 4000, { trim: false }),
    ...(Number.isInteger(previous.messageIndex) ? { messageIndex: previous.messageIndex } : {}),
    source: "prior_checkpoint",
    ...(continuedBy ? { continuedBy } : {}),
  };
}

function assistantEvidenceBetween(messages, startIndex, endIndex) {
  const evidence = messages.find((message) => (
    message.index > startIndex
    && message.index < endIndex
    && message.role === "assistant"
    && String(message.text || "").trim()
    && message.toolRequests.length === 0
  ));
  return evidence ? { present: true, messageIndex: evidence.index } : { present: false };
}

function archivedObjective(message, messages, nextUserIndex, reason) {
  return {
    kind: "user_objective",
    status: "completed_or_archived",
    text: clip(message.text, 2000, { trim: false }),
    messageIndex: message.index,
    archiveReason: reason,
    assistantResponseEvidence: assistantEvidenceBetween(messages, message.index, nextUserIndex),
  };
}

function mergeArchived(previousItems, currentItems, activeObjective, maxItems = 12) {
  const activeKey = normalizedTextKey(activeObjective?.text);
  const merged = [];
  const seen = new Set();
  for (const item of [...(previousItems || []), ...currentItems]) {
    const key = normalizedTextKey(item?.text);
    if (!key || key === activeKey || seen.has(key)) continue;
    seen.add(key);
    merged.push({
      kind: "user_objective",
      status: "completed_or_archived",
      text: clip(item.text, 2000, { trim: false }),
      ...(Number.isInteger(item.messageIndex) ? { messageIndex: item.messageIndex } : {}),
      archiveReason: String(item.archiveReason || "superseded_by_later_user_request"),
      ...(item.assistantResponseEvidence ? { assistantResponseEvidence: item.assistantResponseEvidence } : {}),
    });
  }
  return merged.slice(-Math.max(1, maxItems));
}

function buildObjectiveContinuity(messages, previousState = null, options = {}) {
  const users = userMessages(messages);
  const latest = users.at(-1) || null;
  const latestUserMessage = latest ? String(latest.text || "") : String(previousState?.latestUserMessage || "");
  const elliptical = Boolean(latest && looksElliptical(latest.text));
  const substantive = substantiveUserMessages(messages);
  const localAntecedent = elliptical
    ? substantive.filter((message) => message.index < latest.index).at(-1) || null
    : null;
  const continuation = elliptical && latest ? {
    messageIndex: latest.index,
    text: clip(latest.text, 1000, { trim: false }),
  } : null;
  const activeObjective = elliptical
    ? (objectiveFromMessage(localAntecedent, continuation) || inheritedObjective(previousState, continuation))
    : (objectiveFromMessage(substantive.at(-1) || latest) || inheritedObjective(previousState));

  const localArchived = [];
  const activeLocalIndex = activeObjective?.source === "current_history"
    ? activeObjective.messageIndex
    : Number.POSITIVE_INFINITY;
  for (const message of substantive) {
    if (message.index === activeLocalIndex) continue;
    const nextUser = users.find((candidate) => candidate.index > message.index);
    if (!nextUser) continue;
    localArchived.push(archivedObjective(
      message,
      messages,
      nextUser.index,
      "superseded_by_later_user_request",
    ));
  }
  const previousActive = previousState?.activeObjective;
  if (previousActive && activeObjective
    && normalizedTextKey(previousActive.text) !== normalizedTextKey(activeObjective.text)
    && !elliptical) {
    localArchived.push({
      ...previousActive,
      status: "completed_or_archived",
      archiveReason: "superseded_after_prior_compaction",
    });
  }

  const continuationAntecedent = elliptical && activeObjective ? {
    kind: "continuation_antecedent",
    text: activeObjective.text,
    ...(Number.isInteger(activeObjective.messageIndex) ? { messageIndex: activeObjective.messageIndex } : {}),
    source: activeObjective.source,
  } : null;

  return {
    latestUserMessage,
    activeObjective,
    continuationAntecedent,
    completedOrArchivedObjectives: mergeArchived(
      previousState?.completedOrArchivedObjectives,
      localArchived,
      activeObjective,
      Number(options.maxArchivedObjectives || 12),
    ),
  };
}

module.exports = {
  buildObjectiveContinuity,
  looksElliptical,
  substantiveUserMessages,
};

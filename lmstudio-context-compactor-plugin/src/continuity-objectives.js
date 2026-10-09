"use strict";

const {
  clip,
  clipHeadTail,
  looksElliptical,
  normalizedTextKey,
  visibleAssistantText,
} = require("./continuity-text.js");
const { sanitizeUserAuthoredText } = require("./durable-memory-sanitizer.js");
const { createHash } = require("node:crypto");
const fingerprint = value => createHash("sha256").update(JSON.stringify(value)).digest("hex");

function numberedReference(text) {
  if (text.length > 240) return null;
  const range = /(?:^|\s)(\d+)\s*(번)?\s*(?:부터|[~–-])\s*(\d+)\s*(번)?\s*(?:까지|만)?(?=\s|[,.;!?]|$)/.exec(text);
  if (range && (range[2] || range[4] || /^\s*\d/.test(text))) {
    const start = Number(range[1]), end = Number(range[3]);
    if (start >= 1 && end >= start && end - start < 32) return Array.from({ length: end - start + 1 }, (_, i) => start + i);
  }
  const numbers = [...text.matchAll(/(\d+)\s*번/g)].map(m => Number(m[1]));
  if (numbers.length && /진행|수정|확인|해줘|고쳐|검토|선택/.test(text)) return [...new Set(numbers)];
  if (/앞의?\s*(?:두|2)\s*(?:문제|항목)/.test(text)) return [1, 2];
  return null;
}

function numberedLists(message, messages) {
  const visible = visibleAssistantText(message.text || "");
  const lists = []; let list = null, fenced = false;
  for (const [line, raw] of visible.split(/\r?\n/).entries()) {
    if (/^\s*```|^\s*~~~/.test(raw)) {
      fenced = !fenced;
      if (list) list.at(-1).text += `\n${raw}`;
      continue;
    }
    if (fenced) { if (list) list.at(-1).text += `\n${raw}`; continue; }
    const match = /^\s*(?:\*\*)?(\d+)[.)]\s*(.+)/.exec(raw);
    if (match) {
      const number = Number(match[1]);
      if (!list || list.some(item => item.number === number)) { list = []; lists.push(list); }
      list.push({ number, startLine: line + 1, text: raw });
    } else if (/^\s*#{1,6}\s/.test(raw)) list = null;
    else if (list) list.at(-1).text += `\n${raw}`;
  }
  const messageFingerprint = fingerprint(visible);
  const occurrence = messages.filter(m => m.role === "assistant" && m.index <= message.index
    && fingerprint(visibleAssistantText(m.text || "")) === messageFingerprint).length;
  return lists.map((items, index) => ({ sourceMessageRef: `${messageFingerprint}:${occurrence}`,
    sourceFingerprint: messageFingerprint, revision: fingerprint(items), listIndex: index, items }));
}

function taskSelection(messages, previousState) {
  let selection = previousState?.taskSelection || null;
  for (const user of userMessages(messages)) {
    const selectedNumbers = numberedReference(user.text);
    if (!selectedNumbers) {
      if (/^(?:이전|앞의|기존)\s*(?:작업|계획).{0,12}(?:취소|폐기)/.test(user.text) && selection)
        selection = { ...selection, status: "user_canceled", canceledBy: retainedUserText(user.text, 1000) };
      else if (selection && /^(?:이제\s|다음(?:은|으로)\s|새\s*작업|이번에는\s|now\s|next[,\s])/iu.test(user.text)
        && !looksElliptical(user.text))
        selection = { ...selection, status: "superseded", supersededBy: retainedUserText(user.text, 1000) };
      else if (user.text.length <= 120 && /^(?:그|해당)\s*(?:수정|문제|작업)(?:부터|을|를|만)/u.test(user.text)) {
        // No semantic resolver is hidden here. Preserve the actual candidates
        // and leave a non-numbered referent unresolved for the model/user.
        const proposal = messages.filter(m => m.role === "assistant" && m.index < user.index
          && !(m.toolRequests || []).length && visibleAssistantText(m.text || "").trim()).at(-1);
        if (!proposal && selection?.selectionFingerprint === fingerprint(user.text)) continue;
        selection = { schemaVersion: 1, status: "active", completion: "not_inferred", referenceState: "ambiguous",
          selectionText: retainedUserText(user.text, 1000), selectionFingerprint: fingerprint(user.text),
          selectedNumbers: [], complete: false, omittedCandidates: 0,
          candidates: proposal ? [{ sourceMessageRef: fingerprint(visibleAssistantText(proposal.text)),
            sourceFingerprint: fingerprint(visibleAssistantText(proposal.text)), revision: fingerprint(proposal.text),
            excerpt: retainedUserText(visibleAssistantText(proposal.text), 1000), items: [] }] : [] };
      }
      continue;
    }
    const precedingUser = userMessages(messages).filter(m => m.index < user.index).at(-1);
    const proposals = messages.filter(m => m.role === "assistant" && m.index < user.index
      && (!precedingUser || m.index > precedingUser.index) && !(m.toolRequests || []).length);
    const lists = proposals.flatMap(m => numberedLists(m, messages));
    if (!lists.length && selection?.selectionFingerprint === fingerprint(user.text)) continue;
    const matching = lists.filter(list => selectedNumbers.every(number => list.items.some(item => item.number === number)));
    const candidates = (matching.length ? matching : lists).slice(-2).map(list => ({
      sourceMessageRef: list.sourceMessageRef, sourceFingerprint: list.sourceFingerprint, revision: list.revision,
      listIndex: list.listIndex,
      items: list.items.filter(item => selectedNumbers.includes(item.number)).slice(0, 8).map(item => ({
        ...item, text: retainedUserText(item.text, 600), complete: item.text.length <= 600,
      })),
    }));
    selection = { schemaVersion: 1, status: "active", completion: "not_inferred",
      referenceState: matching.length === 1 ? "resolved" : "ambiguous",
      selectionText: retainedUserText(user.text, 1000), selectionFingerprint: fingerprint(user.text),
      selectedNumbers, candidates, omittedCandidates: Math.max(0, matching.length - candidates.length),
      complete: matching.length === 1 && selectedNumbers.length <= 8 && candidates[0]?.items.every(item => item.complete),
    };
  }
  return selection;
}

function retainedUserText(value, maxChars) {
  const sanitized = sanitizeUserAuthoredText(value);
  return maxChars
    ? clipHeadTail(sanitized, maxChars, { trim: false })
    : sanitized;
}

function userMessages(messages) {
  return messages.filter((message) => message.role === "user" && String(message.text || "").trim());
}

function substantiveUserMessages(messages) {
  return userMessages(messages).filter((message) => !looksElliptical(message.text) && !numberedReference(message.text));
}

function objectiveFromMessage(message, continuedBy = null) {
  if (!message) return null;
  return {
    kind: "user_objective",
    status: "active",
    text: retainedUserText(message.text, 4000),
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
    text: retainedUserText(previous.text, 4000),
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
    && visibleAssistantText(message.text).trim()
    && message.toolRequests.length === 0
  ));
  return evidence ? { present: true, messageIndex: evidence.index } : { present: false };
}

function archivedObjective(message, messages, nextUserIndex, reason) {
  return {
    kind: "user_objective",
    status: "archived_completion_unverified",
    text: retainedUserText(message.text, 2000),
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
      status: "archived_completion_unverified",
      text: retainedUserText(item.text, 2000),
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
  const latestUserMessage = retainedUserText(
    latest ? latest.text : previousState?.latestUserMessage,
  );
  const selection = taskSelection(messages, previousState);
  const elliptical = Boolean(latest && (looksElliptical(latest.text) || numberedReference(latest.text)));
  const substantive = substantiveUserMessages(messages);
  const localAntecedent = elliptical
    ? substantive.filter((message) => message.index < latest.index).at(-1) || null
    : null;
  const continuation = elliptical && latest ? {
    messageIndex: latest.index,
    text: retainedUserText(latest.text, 1000),
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
      status: "archived_completion_unverified",
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
    taskSelection: selection,
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

// Read-only view for optional guidance. The objective owner remains the sole
// continuation parser; a checkpoint alone cannot prove verbatim completeness.
function guidanceObjective(messages, previousState = null) {
  const continuity = buildObjectiveContinuity(messages, previousState);
  const selection = continuity.taskSelection;
  const latest = userMessages(messages).at(-1);
  const selectionApplies = !latest || looksElliptical(latest.text) || numberedReference(latest.text)
    || selection?.selectionFingerprint === fingerprint(latest.text);
  if (selectionApplies && selection?.status === "active" && selection.referenceState === "resolved" && selection.complete) {
    const text = [...new Set([continuity.activeObjective?.text, selection.selectionText,
      ...selection.candidates[0].items.map(item => item.text)].filter(Boolean))].join("\n");
    if (text.length <= 4000) return { text, reason: "selected_user_task_source_retained" };
  }
  if (selectionApplies && selection?.status === "active" && selection.referenceState === "ambiguous") return { reason: "task_reference_ambiguous" };
  const objective = continuity.activeObjective;
  if (!objective || objective.source !== "current_history") return { reason: "objective_completeness_unknown" };
  const source = messages.find(m => m.index === objective.messageIndex && m.role === "user");
  if (!source) return { reason: "objective_completeness_unknown" };
  if (String(source.text || "").length > 4000) return { reason: "objective_truncated" };
  if (objective.text !== retainedUserText(source.text)) return { reason: "objective_completeness_unknown" };
  return { text: objective.text, reason: "user_objective_verified" };
}

function objectiveIdentity(continuity) {
  const objective = normalizedTextKey(continuity?.activeObjective?.text);
  return objective ? fingerprint([objective, continuity?.taskSelection || null]) : "";
}

module.exports = {
  guidanceObjective,
  objectiveIdentity,
  buildObjectiveContinuity,
  looksElliptical,
  substantiveUserMessages,
};

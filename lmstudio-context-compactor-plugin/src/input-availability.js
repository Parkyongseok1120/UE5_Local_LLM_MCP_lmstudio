"use strict";

const crypto = require("node:crypto");
const { decodeToolResultRecord } = require("./compaction-tool-memory.js");

const MAX_ENTRIES = 24;

function isRecord(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function positiveInteger(value) {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= 1 ? parsed : null;
}

function nonNegativeInteger(value) {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : null;
}

function digest(value) {
  return crypto.createHash("sha256").update(String(value || "unknown"), "utf8").digest("hex");
}

function fingerprint(value) {
  let serialized;
  try { serialized = JSON.stringify(value); } catch { serialized = String(value); }
  return digest(serialized === undefined ? "undefined" : serialized);
}

function normalizedPath(value) {
  return String(value || "").replace(/\\/gu, "/").replace(/^project:\/\//iu, "").trim();
}

function normalizeRanges(ranges) {
  const candidates = (ranges || []).map((range) => {
    const start = Number(Array.isArray(range) ? range[0] : range?.start);
    const end = Number(Array.isArray(range) ? range[1] : range?.end);
    return Number.isSafeInteger(start) && Number.isSafeInteger(end) && end >= start
      ? [start, end] : null;
  }).filter(Boolean).sort((left, right) => left[0] - right[0] || left[1] - right[1]);
  const merged = [];
  for (const range of candidates) {
    const previous = merged.at(-1);
    if (previous && range[0] <= previous[1] + 1) previous[1] = Math.max(previous[1], range[1]);
    else merged.push([...range]);
  }
  return merged;
}

function rangeContains(ranges, target) {
  return ranges.some((range) => range[0] <= target[0] && range[1] >= target[1]);
}

function rangesOverlap(left, right) {
  return left.some((a) => right.some((b) => a[0] <= b[1] && b[0] <= a[1]));
}

function classifyPayload(payload) {
  if (payload?.observation === "attached_document_character_range"
    || payload?.parsedTextSha256 || payload?.attachmentId) return "attachment";
  if (payload?.kind === "git_observation" && payload?.action === "read_file") return "commit_blob";
  if (payload?.path && (payload?.sha256 || payload?.hash)) return "worktree";
  return null;
}

function projectIdentity(payload, sourceType) {
  if (sourceType === "attachment") return `attachment:${payload.attachmentId || payload.attachmentName || "unknown"}`;
  return String(payload.workspaceIdentity || payload.projectIdentity || payload.canonicalProject
    || payload.canonicalProjectRoot || payload.activeProject || payload.projectPath || payload.project || "");
}

function payloadVersion(payload, sourceType) {
  if (sourceType === "attachment") return String(payload.parsedTextSha256 || "");
  return String(payload.sha256 || payload.hash || "");
}

function bodyField(payload) {
  if (Object.prototype.hasOwnProperty.call(payload, "content") && typeof payload.content === "string") {
    return payload.content;
  }
  if (Object.prototype.hasOwnProperty.call(payload, "text") && typeof payload.text === "string") {
    return payload.text;
  }
  return null;
}

function rawObservation(payload) {
  if (!isRecord(payload) || payload.ok === false || payload.errorCode) return null;
  const sourceType = classifyPayload(payload);
  if (!sourceType) return null;
  const versionRef = payloadVersion(payload, sourceType);
  const identity = projectIdentity(payload, sourceType);
  const path = sourceType === "attachment"
    ? String(payload.attachmentName || payload.attachmentId || "")
    : normalizedPath(payload.path);
  const body = bodyField(payload);
  let rangeUnit;
  let ranges = [];
  let total = null;
  let bodyMatchesRange = false;
  if (sourceType === "attachment") {
    const start = nonNegativeInteger(payload.startOffset);
    const endExclusive = nonNegativeInteger(payload.endOffset);
    total = nonNegativeInteger(payload.totalChars);
    rangeUnit = String(payload.rangeUnit || "utf16_code_units");
    if (start !== null && endExclusive !== null && endExclusive > start) {
      ranges = [[start, endExclusive - 1]];
      bodyMatchesRange = body !== null && body.length === endExclusive - start;
    }
  } else {
    const start = positiveInteger(payload.startLine);
    const end = positiveInteger(payload.endLine);
    total = positiveInteger(payload.totalLines);
    rangeUnit = "line";
    if (start !== null && end !== null && end >= start) {
      ranges = [[start, end]];
      const expected = end - start + 1;
      const actual = body === null ? null : body.split(/\r\n|\n|\r/u).length;
      const reported = payload.returnedLineCount === undefined
        ? expected : nonNegativeInteger(payload.returnedLineCount);
      bodyMatchesRange = actual === expected && reported === expected;
    }
  }
  return {
    sourceType,
    projectIdentity: identity,
    projectIdentityDigest: digest(identity || "unknown-project"),
    path,
    versionRef,
    rangeUnit,
    ranges: normalizeRanges(ranges),
    total,
    rawVerified: Boolean(identity && path && versionRef && ranges.length && bodyMatchesRange),
    bodyState: body === null ? "absent" : bodyMatchesRange ? "exact_range_body" : "range_body_mismatch",
    revisionRef: sourceType === "commit_blob" ? String(payload.head || payload.revision || "") : "",
    blobRef: sourceType === "commit_blob" ? String(payload.blobOid || "") : "",
  };
}

function historicalObservation(item) {
  if (!isRecord(item)) return null;
  const sourceType = ["worktree", "commit_blob", "attachment"].includes(item.sourceType)
    ? item.sourceType : classifyPayload(item);
  if (!sourceType) return null;
  const identity = String(item.projectIdentity || projectIdentity(item, sourceType));
  const path = sourceType === "attachment"
    ? String(item.path || item.attachmentName || item.attachmentId || "")
    : normalizedPath(item.path || item.canonicalPath || item.workspaceRelativePath || item.projectRelativePath);
  const versionRef = String(item.versionRef || item.sha256AtObservation || payloadVersion(item, sourceType));
  const rangeUnit = String(item.rangeUnit || (sourceType === "attachment" ? "utf16_code_units" : "line"));
  const ranges = normalizeRanges(item.ranges || item.observedLineRanges?.map((range) => (
    [range.startLine, range.endLine]
  )) || (sourceType === "attachment" && item.startOffset !== undefined && item.endOffset !== undefined
    ? [[Number(item.startOffset), Math.max(Number(item.startOffset), Number(item.endOffset) - 1)]]
    : item.startLine !== undefined && item.endLine !== undefined ? [[item.startLine, item.endLine]] : []));
  const total = Number(item.total ?? item.totalLinesAtObservation ?? item.totalLines ?? item.totalChars);
  return {
    sourceType,
    projectIdentity: identity,
    projectIdentityDigest: digest(identity || "unknown-project"),
    path,
    versionRef,
    rangeUnit,
    ranges,
    total: Number.isSafeInteger(total) && total >= 0 ? total : null,
    revisionRef: String(item.revisionRef || item.head || item.revision || ""),
    blobRef: String(item.blobRef || item.blobOid || ""),
  };
}

function observationKey(item) {
  return JSON.stringify([
    item.sourceType, item.projectIdentity, item.path, item.versionRef, item.rangeUnit,
    item.sourceType === "commit_blob" ? item.revisionRef : "",
  ]);
}

function decodedResult(result) {
  const decoded = decodeToolResultRecord(result?.content ?? result);
  return decoded.value && isRecord(decoded.value) ? decoded.value : null;
}

function currentRawObservations(messages) {
  const observations = [];
  for (const message of messages || []) {
    for (const result of message?.toolResults || []) {
      const observation = rawObservation(decodedResult(result));
      if (observation) observations.push(observation);
    }
  }
  return observations;
}

function historicalAvailabilityFromMemory(memory) {
  const work = memory?.currentWorkStatus || memory || {};
  const observations = [];
  for (const item of work.modifiedOrObservedFiles || memory?.modifiedOrObservedFiles || []) {
    const normalized = historicalObservation({
      ...item,
      sourceType: "worktree",
      projectIdentity: item.canonicalProject || item.canonicalProjectRoot || item.projectIdentity,
      versionRef: item.sha256AtObservation,
      ranges: (item.observedLineRanges || []).map((range) => [range.startLine, range.endLine]),
      total: item.totalLinesAtObservation,
    });
    if (normalized) observations.push(normalized);
  }
  for (const item of work.gitObservations || memory?.gitObservations || []) {
    if (item?.action !== "read_file") continue;
    const normalized = historicalObservation({
      ...item,
      sourceType: "commit_blob",
      projectIdentity: item.workspaceIdentity || item.repositoryIdentity || item.projectIdentity,
      versionRef: item.sha256,
      ranges: item.startLine && item.endLine ? [[item.startLine, item.endLine]] : [],
      total: item.totalLines,
    });
    if (normalized) observations.push(normalized);
  }
  for (const item of work.recentToolOutcomes || memory?.recentOlderToolOutcomes || []) {
    if (item?.observation !== "attached_document_character_range") continue;
    const normalized = historicalObservation({ ...item, sourceType: "attachment" });
    if (normalized) observations.push(normalized);
  }
  return observations;
}

function mergeObservations(items) {
  const merged = new Map();
  for (const item of items) {
    if (!item) continue;
    const key = observationKey(item);
    const previous = merged.get(key);
    if (!previous) merged.set(key, { ...item, ranges: normalizeRanges(item.ranges) });
    else merged.set(key, {
      ...previous,
      ...item,
      ranges: normalizeRanges([...(previous.ranges || []), ...(item.ranges || [])]),
      rawVerified: previous.rawVerified === true || item.rawVerified === true,
      bodyState: item.rawVerified ? item.bodyState : previous.bodyState || item.bodyState,
    });
  }
  return [...merged.values()];
}

function projectInputAvailability(messages, historicalItems = [], options = {}) {
  const modelInputId = String(options.modelInputId || "unknown");
  const maxEntries = Math.max(1, Math.min(128, Number(options.maxEntries || MAX_ENTRIES)));
  const historical = mergeObservations((historicalItems || []).map(historicalObservation).filter(Boolean));
  const current = mergeObservations(currentRawObservations(messages));
  const combined = mergeObservations([...historical, ...current]);
  const entries = combined.map((basis) => {
    const historicalMatch = historical.find((item) => observationKey(item) === observationKey(basis));
    const currentMatch = current.find((item) => observationKey(item) === observationKey(basis));
    const historicalRanges = normalizeRanges(historicalMatch?.ranges?.length
      ? historicalMatch.ranges : currentMatch?.ranges || []);
    const rawRanges = currentMatch?.rawVerified ? normalizeRanges(currentMatch.ranges) : [];
    let rawPresence;
    if (currentMatch && !currentMatch.rawVerified) rawPresence = "unknown";
    else if (!currentMatch || rawRanges.length === 0) rawPresence = basis.projectIdentity
      && basis.path && basis.versionRef ? "none" : "unknown";
    else if (historicalRanges.length > 0 && historicalRanges.every((range) => rangeContains(rawRanges, range))) {
      rawPresence = "full";
    } else if (historicalRanges.length > 0 && rangesOverlap(rawRanges, historicalRanges)) rawPresence = "partial";
    else rawPresence = historicalRanges.length ? "none" : "unknown";
    return {
      modelInputId,
      verificationBoundary: "final_sdk_chat",
      hostInputVerification: "unknown",
      sourceType: basis.sourceType,
      projectIdentityDigest: basis.projectIdentityDigest,
      path: basis.path,
      versionRef: basis.versionRef || null,
      ...(basis.revisionRef ? { revisionRef: basis.revisionRef } : {}),
      ...(basis.blobRef ? { blobRef: basis.blobRef } : {}),
      rangeUnit: basis.rangeUnit,
      historicallyReturnedRanges: historicalRanges,
      rawRangesInThisInput: rawRanges,
      rawPresence,
      comparisonBasis: "historically_returned_ranges",
      bodyVerification: currentMatch?.bodyState || "no_raw_result_body_in_input",
    };
  });
  const selected = entries.slice(-maxEntries);
  return {
    modelInputId,
    verificationBoundary: "final_sdk_chat",
    hostInputVerification: "unknown",
    entries: selected,
    omittedEntryCount: Math.max(0, entries.length - selected.length),
    entryListComplete: entries.length <= selected.length,
    metrics: {
      currentRawObservationCount: current.filter((item) => item.rawVerified).length,
      unverifiableRawObservationCount: current.filter((item) => !item.rawVerified).length,
    },
  };
}

function safeProviderId(value, fallback) {
  const text = String(value || "");
  return text ? text.slice(0, 160) : fallback;
}

function traceToolRound(messages, options = {}) {
  const executionId = String(options.executionId || "unknown-execution");
  const modelInputId = String(options.modelInputId || "unknown-input");
  const roundIndex = Number.isSafeInteger(options.roundIndex) ? options.roundIndex : 0;
  const requests = [];
  const results = [];
  const pending = new Map();
  const providerIdCounts = new Map();
  for (const message of messages || []) {
    for (const request of message?.toolRequests || []) {
      const providerRequestId = safeProviderId(request.id, "idless-request");
      providerIdCounts.set(providerRequestId, Number(providerIdCounts.get(providerRequestId) || 0) + 1);
    }
  }
  let ordinal = 0;
  let resultOrdinal = 0;
  for (const message of messages || []) {
    for (const request of message?.toolRequests || []) {
      const providerRequestId = safeProviderId(request.id, `idless-${ordinal}`);
      const callKey = `${executionId}:${roundIndex}:${providerRequestId}:${ordinal}`;
      const record = {
        callKey, causalModelInputId: modelInputId, providerRequestId,
        toolName: String(request.name || ""), generationState: "finalized",
        pairingState: providerIdCounts.get(providerRequestId) === 1 && request.id
          ? "unique_provider_id" : request.id ? "ambiguous_duplicate_provider_id" : "unknown_idless_request",
        proposedArgumentsFingerprint: fingerprint(request.arguments || {}),
      };
      requests.push(record);
      const queue = pending.get(providerRequestId) || [];
      queue.push(record);
      pending.set(providerRequestId, queue);
      ordinal += 1;
    }
    for (const result of message?.toolResults || []) {
      const providerRequestId = safeProviderId(result.toolCallId, "idless-result");
      const queue = pending.get(providerRequestId) || [];
      const ambiguous = providerIdCounts.get(providerRequestId) > 1 || !result.toolCallId;
      const request = ambiguous ? null : queue.shift();
      if (queue.length === 0) pending.delete(providerRequestId);
      const payload = decodedResult(result);
      const observation = rawObservation(payload);
      const failedStatus = ["failed", "error", "canceled", "cancelled", "not_applied"]
        .includes(String(payload?.status || "").toLowerCase());
      results.push({
        callKey: request?.callKey || `${executionId}:${roundIndex}:${providerRequestId}:unmatched-${resultOrdinal}`,
        causalModelInputId: modelInputId,
        providerRequestId,
        pairingState: ambiguous ? (result.toolCallId
          ? "ambiguous_duplicate_provider_id" : "unknown_idless_result")
          : request ? "exact_provider_id" : "unmatched_provider_id",
        executionState: !payload ? "unknown" : payload.ok === false || payload.errorCode || failedStatus
          ? String(payload?.status || "").toLowerCase().startsWith("cancel") ? "canceled" : "failed"
          : "succeeded",
        resultPayloadFingerprint: fingerprint(result?.content ?? result),
        rawBodyState: observation?.bodyState || "not_a_raw_file_result",
        ...(observation ? {
          sourceType: observation.sourceType,
          projectIdentityDigest: observation.projectIdentityDigest,
          path: observation.path,
          versionRef: observation.versionRef || null,
          returnedRanges: observation.ranges,
        } : {}),
      });
      resultOrdinal += 1;
    }
  }
  return { executionId, modelInputId, roundIndex, requests, results };
}

function renderInputAvailabilityMetadata(projection) {
  const payload = {
    modelInputId: projection.modelInputId,
    verificationBoundary: projection.verificationBoundary,
    hostInputVerification: projection.hostInputVerification,
    entries: projection.entries.map((entry) => ({
      sourceType: entry.sourceType,
      projectIdentityDigest: entry.projectIdentityDigest,
      path: entry.path,
      versionRef: entry.versionRef,
      rangeUnit: entry.rangeUnit,
      historicallyReturnedRanges: entry.historicallyReturnedRanges,
      rawRangesInThisInput: entry.rawRangesInThisInput,
      rawPresence: entry.rawPresence,
    })),
    omittedEntryCount: projection.omittedEntryCount,
    entryListComplete: projection.entryListComplete,
  };
  return [
    "[Current model-input raw availability]",
    JSON.stringify(payload),
    "These are final_sdk_chat input-presence facts only. They do not decide whether to reread, which tool to use, or whether review is complete. The model makes those decisions.",
  ].join("\n");
}

module.exports = {
  currentRawObservations,
  historicalAvailabilityFromMemory,
  historicalObservation,
  normalizeRanges,
  projectInputAvailability,
  rawObservation,
  renderInputAvailabilityMetadata,
  traceToolRound,
};

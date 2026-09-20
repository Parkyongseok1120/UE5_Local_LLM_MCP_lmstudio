"use strict";

const crypto = require("node:crypto");

function normalizeRanges(ranges) {
  const sorted = (ranges || []).map(range => {
    const start = Number(Array.isArray(range) ? range[0] : range?.start);
    const end = Number(Array.isArray(range) ? range[1] : range?.end);
    return Number.isSafeInteger(start) && Number.isSafeInteger(end) && end >= start
      ? [start, end] : null;
  }).filter(Boolean).sort((left, right) => left[0] - right[0] || left[1] - right[1]);
  const merged = [];
  for (const range of sorted) {
    const previous = merged.at(-1);
    if (previous && range[0] <= previous[1] + 1) previous[1] = Math.max(previous[1], range[1]);
    else merged.push([...range]);
  }
  return merged;
}

function rangeUnits(ranges) {
  return normalizeRanges(ranges).reduce((sum, [start, end]) => sum + end - start + 1, 0);
}

function intersectRanges(left, right) {
  const a = normalizeRanges(left);
  const b = normalizeRanges(right);
  const result = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    const start = Math.max(a[i][0], b[j][0]);
    const end = Math.min(a[i][1], b[j][1]);
    if (start <= end) result.push([start, end]);
    if (a[i][1] < b[j][1]) i += 1;
    else j += 1;
  }
  return normalizeRanges(result);
}

function subtractRanges(left, right) {
  let remaining = normalizeRanges(left);
  for (const [cutStart, cutEnd] of normalizeRanges(right)) {
    const next = [];
    for (const [start, end] of remaining) {
      if (cutEnd < start || cutStart > end) next.push([start, end]);
      else {
        if (start < cutStart) next.push([start, cutStart - 1]);
        if (end > cutEnd) next.push([cutEnd + 1, end]);
      }
    }
    remaining = next;
  }
  return normalizeRanges(remaining);
}

function calculateReadMetrics({ rangeUnit, returnedRanges, inputRanges, historicalRanges }) {
  const returned = normalizeRanges(returnedRanges);
  const input = normalizeRanges(inputRanges);
  const historical = normalizeRanges(historicalRanges);
  const inputOverlap = intersectRanges(returned, input);
  const historicalOutsideInput = subtractRanges(historical, input);
  const historicalReacquisition = intersectRanges(returned, historicalOutsideInput);
  const newEvidence = subtractRanges(returned, historical);
  return {
    rangeUnit,
    returnedUnits: rangeUnits(returned),
    inputOverlapUnits: rangeUnits(inputOverlap),
    historicalReacquisitionUnits: rangeUnits(historicalReacquisition),
    newEvidenceUnits: rangeUnits(newEvidence),
  };
}

function sourceKey(entry) {
  return entry.sourceKey || JSON.stringify([
    entry.sourceType || "",
    entry.projectIdentityDigest || entry.projectIdentity || "",
    entry.path || "",
    entry.versionRef || "",
    entry.rangeUnit || "",
    entry.revisionRef || "",
  ]);
}

function messageArray(input) {
  if (input?.getMessagesArray) return input.getMessagesArray();
  return Array.isArray(input) ? input : [];
}

function toolResults(message) {
  if (message?.getToolCallResults) return message.getToolCallResults();
  return Array.isArray(message?.toolResults) ? message.toolResults : [];
}

function decodePayload(result) {
  const content = result?.content ?? result;
  if (content && typeof content === "object" && !Array.isArray(content)) return content;
  if (typeof content !== "string") return null;
  try {
    const parsed = JSON.parse(content);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function projectionMatchesOracle(productEntries, oracleEntries) {
  const product = new Map((productEntries || []).map(entry => [sourceKey(entry), entry]));
  const oracle = new Map((oracleEntries || []).map(entry => [sourceKey(entry), entry]));
  const mismatches = [];
  for (const key of new Set([...product.keys(), ...oracle.keys()])) {
    const actual = product.get(key);
    const expected = oracle.get(key);
    const rangesMatch = JSON.stringify(normalizeRanges(actual?.rawRangesInThisInput))
      === JSON.stringify(normalizeRanges(expected?.rawRangesInThisInput));
    if (!actual || !expected || actual.rawPresence !== expected.rawPresence || !rangesMatch) {
      mismatches.push({ sourceKey: key, product: actual || null, oracle: expected || null });
    }
  }
  return { matches: mismatches.length === 0, mismatches };
}

function answerMatchesOracle(visibleAnswer, expected = {
  settlementOwner: "DailySales",
  duplicateGuard: true,
}) {
  const text = String(visibleAnswer || "");
  const owner = String(expected.settlementOwner).replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
  const ownerMatch = new RegExp(`\\bSettlementOwner\\b\\s*(?:=|:|is)\\s*[\\x60\"']?${owner}[\\x60\"']?`, "iu").test(text);
  const guardValue = expected.duplicateGuard ? "true" : "false";
  const guardMatch = new RegExp(`\\bDuplicateGuard\\b\\s*(?:=|:|is)\\s*[\\x60\"']?${guardValue}[\\x60\"']?`, "iu").test(text);
  const reasons = [];
  if (!ownerMatch) reasons.push("SettlementOwner relation/value was not stated exactly");
  if (!guardMatch) reasons.push("DuplicateGuard relation/value was not stated exactly");
  return { pass: reasons.length === 0, reasons, checks: { ownerMatch, guardMatch } };
}

function auditAnswerMatchesOracle(visibleAnswer) {
  const text = String(visibleAnswer || "");
  const resetLocation = /GuestManager(?:\.cs)?[^\n]{0,100}(?:line|줄|:)\s*189\b|(?:line|줄)\s*189\b[^\n]{0,100}GuestManager/iu.test(text);
  const addMoneyLocation = /GuestManager(?:\.cs)?[^\n]{0,100}(?:line|줄|:)\s*1036\b|(?:line|줄)\s*1036\b[^\n]{0,100}GuestManager/iu.test(text);
  const settlementDistinction = /(?:per[- ]?session|session|게스트|세션)[^\n]{0,160}(?:AddMoney|지급|wallet|지갑)[^\n]{0,200}(?:day[- ]?end|DailySales|일일|하루|보고|집계)|(?:day[- ]?end|DailySales|일일|하루|보고|집계)[^\n]{0,200}(?:per[- ]?session|session|게스트|세션)[^\n]{0,160}(?:AddMoney|지급|wallet|지갑)/iu.test(text);
  const directSearchNotConclusive = /(?:direct\s*C#|C#\s*(?:reference|search)|직접\s*C#|C#\s*(?:검색|참조))[^\n]{0,180}(?:not\s+(?:enough|sufficient|prove)|insufficient|cannot|아니|불충분|단정|증명)[^\n]{0,180}(?:UI|absent|부재)|(?:UI|absent|부재)[^\n]{0,180}(?:direct\s*C#|직접\s*C#|C#\s*(?:검색|참조))[^\n]{0,180}(?:not|아니|불충분|단정)/iu.test(text);
  const serializedBinding = /(?:ProjectLifeScope\.prefab|serialized|직렬화)[^\n]{0,220}(?:UICashPanel|AddCurrency)|(?:UICashPanel|AddCurrency)[^\n]{0,220}(?:ProjectLifeScope\.prefab|serialized|직렬화)/iu.test(text);
  const selfRemoval = /self[- ]?removal|self\s+remov|자기\s*(?:자신을\s*)?제거|현재\s*리스너[^\n]{0,80}제거/iu.test(text);
  const lowerRemovalRisk = /(?:lower|not[- ]?yet[- ]?visited|unvisited|낮은\s*인덱스|아직\s*(?:방문|호출)하지\s*않)[^\n]{0,220}(?:twice|duplicate|skip|두\s*번|중복|건너|누락)|(?:twice|duplicate|skip|두\s*번|중복|건너|누락)[^\n]{0,220}(?:lower|unvisited|낮은\s*인덱스|아직\s*(?:방문|호출)하지\s*않)/iu.test(text);
  const runtimeUnknown = /(?:runtime|런타임)[^\n]{0,180}(?:unknown|unproven|not\s+proven|미확정|입증되지|단정할\s*수\s*없)|(?:unknown|unproven|not\s+proven|미확정|입증되지|단정할\s*수\s*없)[^\n]{0,180}(?:runtime|런타임|원인)/iu.test(text);
  const runtimeMinimum = /(?:exact\s+log|정확한\s*로그|callback|콜백|AddCurrency)[^\n]{0,240}(?:active|활성|instance|인스턴스|amountText|assignment|할당)|(?:active|활성|instance|인스턴스|amountText|assignment|할당)[^\n]{0,240}(?:exact\s+log|정확한\s*로그|callback|콜백|AddCurrency)/iu.test(text);
  const runtimeOverclaim = /(?:root\s*cause|원인)(?:은|가|:|\s+is)\s*(?:definitely|확실히|분명히|UICashPanel|coroutine|코루틴|inactive|비활성)/iu.test(text);
  const checks = {
    resetLocation,
    addMoneyLocation,
    settlementDistinction,
    directSearchNotConclusive,
    serializedBinding,
    selfRemoval,
    lowerRemovalRisk,
    runtimeUnknown,
    runtimeMinimum,
    noRuntimeOverclaim: !runtimeOverclaim,
  };
  const reasons = Object.entries(checks)
    .filter(([, passed]) => !passed)
    .map(([name]) => `missing_or_failed:${name}`);
  return {
    pass: reasons.length === 0,
    score: Object.values(checks).filter(Boolean).length,
    maximumScore: Object.keys(checks).length,
    checks,
    reasons,
    humanReviewRequired: true,
  };
}

function digest(value) {
  return crypto.createHash("sha256").update(String(value || "unknown"), "utf8").digest("hex");
}

function oracleObservation(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)
    || payload.ok === false || payload.errorCode) return null;
  let sourceType = "worktree";
  if (payload.observation === "attached_document_character_range") sourceType = "attachment";
  else if (payload.kind === "git_observation" && payload.action === "read_file") sourceType = "commit_blob";
  const identity = sourceType === "attachment"
    ? `attachment:${payload.attachmentId || payload.attachmentName || ""}`
    : String(payload.workspaceIdentity || payload.projectIdentity || payload.canonicalProject
      || payload.canonicalProjectRoot || payload.activeProject || payload.projectPath || payload.project || "");
  const path = String(sourceType === "attachment"
    ? payload.attachmentName || payload.attachmentId || ""
    : payload.path || "").replace(/\\/gu, "/").replace(/^project:\/\//iu, "").trim();
  const versionRef = String(sourceType === "attachment"
    ? payload.parsedTextSha256 || "" : payload.sha256 || payload.hash || "");
  const body = typeof payload.content === "string" ? payload.content
    : typeof payload.text === "string" ? payload.text : null;
  let rangeUnit = "line";
  let ranges = [];
  let total = null;
  let verified = false;
  let emptyVerified = false;
  if (sourceType === "attachment") {
    const start = Number(payload.startOffset);
    const end = Number(payload.endOffset);
    total = Number(payload.totalChars);
    rangeUnit = String(payload.rangeUnit || "utf16_code_units");
    if (Number.isSafeInteger(start) && Number.isSafeInteger(end) && end > start) {
      ranges = [[start, end - 1]];
      verified = body !== null && body.length === end - start;
    }
  } else if (payload.offsetBytes !== undefined || payload.nextOffsetBytes !== undefined) {
    const start = Number(payload.offsetBytes);
    const end = Number(payload.nextOffsetBytes);
    total = Number(payload.size);
    rangeUnit = "utf8_byte";
    if (Number.isSafeInteger(start) && Number.isSafeInteger(end) && end >= start
      && Number.isSafeInteger(total) && total >= 0 && start >= 0 && end <= total) {
      if (end > start) ranges = [[start, end - 1]];
      verified = body !== null && !body.includes("\uFFFD")
        && Buffer.byteLength(body, "utf8") === end - start;
      emptyVerified = verified && end === start && total === 0;
    }
  } else {
    const start = Number(payload.startLine);
    const end = Number(payload.endLine);
    total = Number(payload.totalLines);
    if (Number.isSafeInteger(start) && Number.isSafeInteger(end) && start >= 1 && end >= start) {
      ranges = [[start, end]];
      const expected = end - start + 1;
      verified = body !== null && body.split(/\r\n|\n|\r/u).length === expected
        && (payload.returnedLineCount === undefined || Number(payload.returnedLineCount) === expected);
    }
  }
  if (!identity || !path || !versionRef || (!ranges.length && !emptyVerified)) verified = false;
  const revisionRef = sourceType === "commit_blob" ? String(payload.head || payload.revision || "") : "";
  return {
    sourceType,
    projectIdentity: identity,
    projectIdentityDigest: digest(identity || "unknown-project"),
    path,
    versionRef,
    rangeUnit,
    ranges: normalizeRanges(ranges),
    total: Number.isSafeInteger(total) && total >= 0 ? total : null,
    verified,
    emptyVerified,
    revisionRef,
    sourceKey: JSON.stringify([sourceType, digest(identity || "unknown-project"), path, versionRef, rangeUnit,
      revisionRef]),
  };
}

function oracleObservationsFromMessages(messages) {
  const observations = [];
  for (const message of messageArray(messages)) {
    for (const result of toolResults(message)) {
      const observation = oracleObservation(decodePayload(result));
      if (observation) observations.push(observation);
    }
  }
  return observations;
}

function mergeObservationRanges(observations, verifiedOnly = false) {
  const merged = new Map();
  for (const observation of observations || []) {
    if (verifiedOnly && !observation.verified) continue;
    const previous = merged.get(observation.sourceKey) || {
      ...observation,
      ranges: [],
      verifiedRanges: [],
      sawUnverified: false,
      emptyVerified: false,
    };
    previous.ranges = normalizeRanges([...previous.ranges, ...observation.ranges]);
    if (observation.verified) {
      previous.verifiedRanges = normalizeRanges([...previous.verifiedRanges, ...observation.ranges]);
    } else previous.sawUnverified = true;
    previous.emptyVerified ||= observation.emptyVerified === true;
    merged.set(observation.sourceKey, previous);
  }
  return merged;
}

function rangeContains(ranges, target) {
  return normalizeRanges(ranges).some(range => range[0] <= target[0] && range[1] >= target[1]);
}

function oracleProjection(messages, historicalObservations = []) {
  const historical = mergeObservationRanges(historicalObservations, true);
  const current = mergeObservationRanges(oracleObservationsFromMessages(messages));
  const entries = [];
  for (const key of new Set([...historical.keys(), ...current.keys()])) {
    const old = historical.get(key);
    const now = current.get(key);
    const historicalRanges = normalizeRanges(old?.verifiedRanges?.length
      ? old.verifiedRanges : now?.verifiedRanges || []);
    const rawRanges = normalizeRanges(now?.verifiedRanges || []);
    let rawPresence;
    if (now?.emptyVerified && now.total === 0) rawPresence = "full";
    else if (!now) rawPresence = "none";
    else if (!rawRanges.length) rawPresence = "unknown";
    else if (historicalRanges.every(range => rangeContains(rawRanges, range))) rawPresence = "full";
    else if (intersectRanges(rawRanges, historicalRanges).length) rawPresence = "partial";
    else rawPresence = historicalRanges.length ? "none" : "unknown";
    const basis = old || now;
    entries.push({
      sourceKey: key,
      sourceType: basis.sourceType,
      projectIdentityDigest: basis.projectIdentityDigest,
      path: basis.path,
      versionRef: basis.versionRef,
      revisionRef: basis.revisionRef,
      rangeUnit: basis.rangeUnit,
      historicallyReturnedRanges: historicalRanges,
      rawRangesInThisInput: rawRanges,
      rawPresence,
    });
  }
  return entries;
}

function evaluateCausalCalls({ initialMessages, modelInputs, calls }) {
  const historical = mergeObservationRanges(oracleObservationsFromMessages(initialMessages), true);
  const perCall = [];
  const statusCounts = { succeeded: 0, failed: 0, canceled: 0, unknown: 0 };
  const byUnit = {};
  let excludedCallCount = 0;
  for (const call of calls || []) {
    const state = ["succeeded", "failed", "canceled"].includes(call.executionState)
      ? call.executionState : "unknown";
    statusCounts[state] += 1;
    if (state !== "succeeded") {
      perCall.push({ ...call, measured: false, excludedReason: `execution_${state}` });
      continue;
    }
    const returned = oracleObservation(call.payload);
    const causalInput = modelInputs instanceof Map
      ? modelInputs.get(call.causalModelInputId) : modelInputs?.[call.causalModelInputId];
    if (!returned?.verified || !causalInput) {
      excludedCallCount += 1;
      perCall.push({ ...call, measured: false,
        excludedReason: !returned?.verified ? "unverified_result_body" : "missing_causal_input" });
      if (returned?.verified) {
        const prior = historical.get(returned.sourceKey)?.verifiedRanges || [];
        historical.set(returned.sourceKey, {
          ...returned,
          verifiedRanges: normalizeRanges([...prior, ...returned.ranges]),
        });
      }
      continue;
    }
    const input = mergeObservationRanges(oracleObservationsFromMessages(causalInput), true).get(returned.sourceKey);
    const priorRanges = historical.get(returned.sourceKey)?.verifiedRanges || [];
    const metrics = calculateReadMetrics({
      rangeUnit: returned.rangeUnit,
      returnedRanges: returned.ranges,
      inputRanges: input?.verifiedRanges || [],
      historicalRanges: priorRanges,
    });
    perCall.push({ ...call, measured: true, sourceKey: returned.sourceKey, ...metrics });
    const aggregate = byUnit[returned.rangeUnit] || {
      returnedUnits: 0,
      inputOverlapUnits: 0,
      historicalReacquisitionUnits: 0,
      newEvidenceUnits: 0,
    };
    for (const key of Object.keys(aggregate)) aggregate[key] += metrics[key];
    byUnit[returned.rangeUnit] = aggregate;
    historical.set(returned.sourceKey, {
      ...returned,
      verifiedRanges: normalizeRanges([...priorRanges, ...returned.ranges]),
    });
  }
  for (const [unit, values] of Object.entries(byUnit)) {
    values.overlapRate = values.returnedUnits ? values.inputOverlapUnits / values.returnedUnits : null;
    values.rangeUnit = unit;
  }
  return { perCall, byUnit, statusCounts, excludedCallCount };
}

module.exports = {
  auditAnswerMatchesOracle,
  answerMatchesOracle,
  calculateReadMetrics,
  evaluateCausalCalls,
  intersectRanges,
  normalizeRanges,
  oracleObservation,
  oracleObservationsFromMessages,
  oracleProjection,
  projectionMatchesOracle,
  rangeUnits,
  sourceKey,
  subtractRanges,
};

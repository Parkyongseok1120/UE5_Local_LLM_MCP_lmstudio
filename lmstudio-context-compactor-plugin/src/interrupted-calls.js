"use strict";
const { sanitizeStructuredDurableValue } = require("./durable-memory-sanitizer.js");
const { pathApiFor } = require("./continuity-file-observations.js");
const MARKER = "[Observed incomplete tool invocations v1]";
const omissionCount = value => Number.isSafeInteger(value) && value >= 0 ? value : 0;
function interruptedCallState(messages, previous = [], previousRetention = {}) {
  const items = [...previous];
  let omittedItemsAtLeast = omissionCount(previousRetention?.omittedItemsAtLeast);
  for (const message of messages) {
    const text = typeof message.getText === "function" ? message.getText() : message.text;
    const role = typeof message.getRole === "function" ? message.getRole() : message.role;
    if (role !== "assistant" || !String(text || "").startsWith(MARKER) || text.length > 24000) continue;
    try { const value = JSON.parse(text.slice(text.indexOf("\n") + 1));
      omittedItemsAtLeast = Math.max(omittedItemsAtLeast, omissionCount(value.omittedItemsAtLeast ?? value.omittedItems));
      for (const item of value.items || []) if (typeof item.callKey === "string" && item.resultObservation === "not_observed") items.push(item);
    } catch { /* Incomplete metadata never becomes a result. */ }
  }
  const unique = new Map();
  for (const item of items) {
    const prior = unique.get(item.callKey);
    unique.set(item.callKey, sanitizeStructuredDurableValue({ ...item,
      ...(prior?.resolution && !item.resolution ? { resolution: prior.resolution } : {}) }));
  }
  omittedItemsAtLeast = Math.max(omittedItemsAtLeast, unique.size - 16);
  return { items: [...unique.values()].slice(-16), retention: { retainedLimit: 16, omittedItemsAtLeast,
    completeness: omittedItemsAtLeast ? "partial" : "all_observed_records_retained" } };
}
function retainInterruptedCalls(messages, previous = []) { return interruptedCallState(messages, previous).items; }
function renderInterruptedCalls(items, retention = {}) {
  const selected = items.slice(-16).map(item => sanitizeStructuredDurableValue(item));
  const render = () => `${MARKER}\n${JSON.stringify({ authority: "historical_runtime_observation_only",
    retainedLimit: 16, omittedItemsAtLeast: Math.max(omissionCount(retention?.omittedItemsAtLeast), items.length - selected.length), items: selected })}`;
  while (selected.length && render().length > 24000) selected.shift();
  return render();
}

// The caller supplies trusted observations from the existing Reference owner.
// An operation lookup resolves an operation's later status, never invents a
// missing SDK return or proves when its original dispatch occurred.
function resolveInterruptedCalls(items, observations) {
  const projectKey = value => {
    if (typeof value !== "string") return "";
    const api = pathApiFor(value);
    if (!api.isAbsolute(value)) return "";
    const normalized = api.normalize(value).replace(/[\\/]+$/, "");
    return api.sep === "\\" ? normalized.toLowerCase() : normalized;
  };
  return items.map(item => {
    if (!item.operationId || item.providerDispatch === "not_dispatched" || item.resolution) return item;
    const project = projectKey(item.projectIdentity);
    if (!project || item.provider !== "mcp/unity-tools") return item;
    const sameInvocation = candidate => candidate.provider === item.provider
      && candidate.operationId === item.operationId && projectKey(candidate.projectIdentity) === project;
    const peers = items.filter(sameInvocation);
    if (new Set(peers.map(p => JSON.stringify([p.toolName, p.proposedArgumentsFingerprint]))).size !== 1) return item;
    const matches = observations.filter(o => o.kind === "operation" && o.origin?.provider === item.provider
      && o.origin.tool === "unity_operation" && projectKey(o.project) === project
      && o.data?.operationId === item.operationId && o.data.operationKind === "operation_lookup"
      && !o.data.correlation && o.data.originalOrigin
      && ["completed", "failed", "canceled"].includes(o.data.operationStatus));
    if (matches.length !== 1) return item;
    const match = matches[0];
    return { ...item, resolution: sanitizeStructuredDurableValue({
      kind: "later_operation_observation", observationId: match.id, lookupOrigin: match.origin,
      operationStatus: match.data.operationStatus, effect: match.data.effect,
      originalOrigin: match.data.originalOrigin, delivery: match.data.resultDelivery,
      saved: match.data.saved, dirty: match.data.dirty, verification: match.data.verification,
    }) };
  });
}
module.exports = { MARKER, interruptedCallState, retainInterruptedCalls, renderInterruptedCalls, resolveInterruptedCalls };

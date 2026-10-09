"use strict";

// Pure causal pairing shared by SDK-history and normalized-memory consumers.
// Consecutive assistant request blocks form one batch until its first result.
function exchangeIndex(history) {
  const messages = Array.isArray(history) ? history : history.getMessagesArray();
  const matches = new Map(), batches = new Map(), duplicateIds = new Set();
  let active = null, ambiguous = false;
  const roleOf = m => typeof m.getRole === "function" ? m.getRole() : m.role;
  const requestsOf = m => typeof m.getToolCallRequests === "function" ? m.getToolCallRequests() : m.toolRequests || [];
  const resultsOf = m => typeof m.getToolCallResults === "function" ? m.getToolCallResults() : m.toolResults || [];
  for (let mi = 0; mi < messages.length; mi++) {
    const message = messages[mi], role = roleOf(message), requests = requestsOf(message);
    if (active && (!["assistant", "tool"].includes(role) || (role === "assistant" && !requests.length))) {
      if (active.consumed.size !== active.requests.size) ambiguous = true;
      active = null;
    }
    if (requests.length) {
      if (active && active.consumed.size) {
        if (active.consumed.size !== active.requests.size) ambiguous = true;
        active = null;
      }
      if (!active) active = { batch: mi, requests: new Map(), consumed: new Set(), keys: new Map(), invalid: new Set() };
      for (const request of requests) {
        const id = String(request.id || "");
        if (!id || active.requests.has(id) || active.invalid.has(id)) {
          ambiguous = true;
          if (id) { active.requests.delete(id); active.invalid.add(id); duplicateIds.add(id); }
        } else active.requests.set(id, request);
      }
    }
    const results = resultsOf(message);
    for (let ri = 0; ri < results.length; ri++) {
      const id = String(results[ri].toolCallId || "");
      if (!active || !id || !active.requests.has(id) || active.consumed.has(id)) {
        ambiguous = true;
        if (id && active?.consumed.has(id)) {
          matches.delete(active.keys.get(id)); batches.delete(active.keys.get(id)); active.invalid.add(id); duplicateIds.add(id);
        }
        continue;
      }
      active.consumed.add(id); active.keys.set(id, `${mi}:${ri}`);
      matches.set(`${mi}:${ri}`, active.requests.get(id));
      batches.set(`${mi}:${ri}`, active.batch);
    }
    // Retain a completed batch until a boundary/new request so a duplicate
    // result arriving in the next tool block invalidates its earlier match.
  }
  if (active && active.consumed.size !== active.requests.size) ambiguous = true;
  return { matches, batches, ambiguous, duplicateIds: [...duplicateIds] };
}

// Ordering within a parallel batch is not causal. Only a single observation
// in the latest batch can establish a latest value, regardless of array order.
function uniqueLatest(items, batchOf = item => item.batch) {
  if (!items.length) return null;
  const batches = items.map(batchOf);
  if (batches.some(batch => !Number.isFinite(batch))) return null;
  const latest = Math.max(...batches);
  const candidates = items.filter((item, index) => batches[index] === latest);
  return candidates.length === 1 ? candidates[0] : null;
}

module.exports = { exchangeIndex, uniqueLatest };

"use strict";

const { Chat, ChatMessage, rawFunctionTool } = require("@lmstudio/sdk");
const { EvidenceArchive, hash, redact, atomicWrite, readFile } = require("./evidence-archive.js");
const path = require("node:path");
const modelNotes = require("./continuity-model-notes.js");
const { decodeToolResultRecord } = require("./compaction-tool-memory.js");
const { REASONING_SEPARATOR, visibleAssistantText } = require("./continuity-text.js");
const { isEphemeralCapabilityKey } = require("./durable-memory-sanitizer.js");
const evidenceIdentity = require("./evidence-identity.js");

function serialize(history) {
  return history.getMessagesArray().map(m => {
    if (m.hasFiles()) throw new Error("typed_files_not_serializable");
    const role = m.getRole();
    const content = role === "tool" ? m.getToolCallResults().map(r => ({ type: "toolCallResult", ...r }))
      : [...(m.getText() ? [{ type: "text", text: m.getText() }] : []),
        ...m.getToolCallRequests().map(r => ({ type: "toolCallRequest", toolCallRequest: r }))];
    return { role, content };
  });
}

function deserialize(messages) { const chat = Chat.empty(); for (const m of messages) chat.append(ChatMessage.from(m)); return chat; }

function exchangeIndex(history) {
  const matches = new Map();
  let active = null, ambiguous = false;
  const messages = history.getMessagesArray();
  for (let messageIndex = 0; messageIndex < messages.length; messageIndex++) {
    const message = messages[messageIndex], requests = message.getToolCallRequests();
    if (active && !message.isAssistantMessage() && message.getRole() !== "tool") {
      ambiguous = true; active = null;
    }
    if (requests.length) {
      // Host history splits finalized requests into assistant blocks. Requests
      // before the first result still belong to one batch; never merge across
      // a partially returned exchange or accept duplicate request IDs.
      if (active && active.consumed.size > 0) ambiguous = true;
      const byId = active && active.consumed.size === 0 ? active.requests : new Map();
      for (const request of requests) {
        if (!request.id || byId.has(request.id)) ambiguous = true;
        else byId.set(request.id, request);
      }
      active = { requests: byId, consumed: new Set() };
    }
    const results = message.getToolCallResults();
    for (let resultIndex = 0; resultIndex < results.length; resultIndex++) {
      const result = results[resultIndex], id = result.toolCallId;
      if (!active || !id || !active.requests.has(id) || active.consumed.has(id)) {
        ambiguous = true;
        continue;
      }
      active.consumed.add(id);
      matches.set(`${messageIndex}:${resultIndex}`, active.requests.get(id));
    }
    if (active && active.consumed.size === active.requests.size) active = null;
  }
  if (active && active.consumed.size !== active.requests.size) ambiguous = true;
  return { matches, ambiguous };
}

const identityFields = ["repositoryIdentity", "workspaceIdentity", "projectIdentity", "canonicalProjectRoot",
  "canonicalProject", "path", "action", "comparison", "base", "head", "currentHead", "blobOid"];

function sourceIdentity(payload) {
  const identity = {};
  for (const key of identityFields) if (payload[key] !== undefined) identity[key] = payload[key];
  return redact(identity);
}

function semanticFacts(payload) {
  const facts = {};
  for (const key of ["kind", "ok", "status", "errorCode", "action", "comparison", "base", "head",
    "since", "until", "authorQuery", "authorQuerySemantics", "pageStart", "pageEnd", "pageHasMore",
    "hasMore", "sourceResultComplete", "returnedRange", "lineRange", "startLine", "endLine",
    "totalLines", "returnedCount", "total"]) {
    if (payload[key] !== undefined) facts[key] = payload[key];
  }
  if (facts.pageHasMore === undefined && payload.hasMore !== undefined) facts.pageHasMore = payload.hasMore;
  if (facts.sourceResultComplete === undefined && payload.hasMore !== undefined) {
    facts.sourceResultComplete = payload.hasMore === false;
  }
  return redact(facts);
}

function semanticEvidenceView(payload, maxChars = 640) {
  const facts = semanticFacts(payload);
  if (payload.kind !== "git_observation") return JSON.stringify(redact(payload)).slice(0, maxChars);
  const view = { ...facts };
  if (Array.isArray(payload.items)) {
    view.items = [];
    for (const item of payload.items) {
      const next = { ...view, items: [...view.items, redact(item)] };
      if (JSON.stringify(next).length > maxChars) break;
      view.items.push(redact(item));
    }
    view.itemsOmitted = Math.max(0, payload.items.length - view.items.length);
  } else if (typeof payload.text === "string") {
    view.text = payload.text.slice(0, Math.max(0, maxChars - JSON.stringify(view).length - 32));
  }
  return JSON.stringify(view).slice(0, maxChars);
}

function gitBodyView(payload) {
  if (typeof payload.content === "string") return { field: "content", body: redact(payload.content) };
  if (typeof payload.text === "string") return { field: "text", body: redact(payload.text) };
  if (typeof payload.body === "string") return { field: "body", body: redact(payload.body) };
  if (Array.isArray(payload.items)) return { field: "items", body: JSON.stringify(redact(payload.items)) };
  if (Array.isArray(payload.rows)) return { field: "rows", body: JSON.stringify(redact(payload.rows)) };
  return null;
}

function rawExposureKey(request, result, originalEnvelope) {
  return hash([request.name, request.arguments || {}, String(result.toolCallId || ""), originalEnvelope]);
}

function serializedResultContent(content) {
  return typeof content === "string" ? content : JSON.stringify(content);
}

function inputResultKeys(history) {
  const index = exchangeIndex(history), keys = new Set();
  history.getMessagesArray().forEach((message, mi) => message.getToolCallResults().forEach((result, ri) => {
    const request = index.matches.get(`${mi}:${ri}`);
    if (request) keys.add(rawExposureKey(request, result, serializedResultContent(result.content)));
  }));
  return keys;
}

function collectEvidenceRefs(value, output = new Map()) {
  if (Array.isArray(value)) {
    for (const item of value) collectEvidenceRefs(item, output);
    return output;
  }
  if (!value || typeof value !== "object") return output;
  const ref = value.archiveRef && typeof value.archiveRef === "object" ? value.archiveRef : value;
  const evidenceId = typeof ref.evidenceId === "string" ? ref.evidenceId
    : typeof ref.ref === "string" && ref.ref.startsWith("ev_") ? ref.ref : "";
  if (/^ev_[a-f0-9]{64}$/u.test(evidenceId) && /^[a-f0-9]{64}$/u.test(String(ref.version || ""))) {
    output.set(evidenceId, String(ref.version));
  }
  for (const item of Object.values(value)) collectEvidenceRefs(item, output);
  return output;
}

function liveEvidenceRefs(history) {
  const refs = new Map();
  for (const message of history.getMessagesArray()) {
    for (const result of message.getToolCallResults()) {
      const decoded = decodeToolResultRecord(result.content);
      if (decoded.value) collectEvidenceRefs(decoded.value, refs);
    }
    const text = message.getText();
    const marker = text.indexOf("[Direct continuity state v2]");
    if (marker >= 0) {
      const jsonStart = text.indexOf("{", marker);
      if (jsonStart >= 0) {
        try { collectEvidenceRefs(JSON.parse(text.slice(jsonStart)), refs); } catch { /* Not a complete checkpoint. */ }
      }
    }
  }
  return refs;
}

class WorkingContext {
  constructor(scope, options = {}) {
    this.archive = options.archive || new EvidenceArchive(scope, options);
    this.scope = this.archive.scope;
    this.manifest = null;
    this.generation = 0;
    this.refs = new Map();
    this.returnedRefs = new Map();
    this.pendingHistoricalResults = new Set();
    this.rawRefs = new Map();
    this.lastCommitReason = null;
    this.committedInExecution = false;
    this.closed = false;
    this.exposure = new Map();
    this.note = null;
    this.lineage = options.lineage || null;
    this.parentLineage = options.parentLineage || null;
    this.lastSummaryInput = "";
    this.consumedRawResults = new Set();
    this.cost = { summaryCalls: 0, summaryPromptTokens: 0, summaryPredictedTokens: 0, summaryMs: 0, unknownUsageCalls: 0 };
  }

  windowFile(lineage) { return lineage ? `window-${lineage}.json` : "window.json"; }

  get hasArchivedEvidence() {
    if (this.refs.size || this.returnedRefs.size || this.archive.stats.captured) return true;
    // A restored window may no longer mention the earliest consumed pages.
    // Discovery still needs a usable result budget for those scoped records.
    try { return this.archive.entries().some(entry => this.archive.load(entry.key).ok); }
    catch { return false; }
  }

  archiveObservation(request, result, value, metadata = {}) {
    const canonical = evidenceIdentity.normalizeObservation(value, request,
      metadata.toolProviders?.[request.name] || value.provider || "unknown-provider");
    const envelope = serializedResultContent(result.content);
    const callKey = rawExposureKey(request, result, envelope);
    // Restored sanitized envelopes keep their binding to the original record.
    // Re-archiving them would change identity and strand a pending consumer.
    const bound = this.rawRefs.get(callKey);
    if (bound) {
      const existing = this.archive.load(bound[0]);
      if (existing.ok && existing.record.archivedBodyHash === bound[1]) return existing;
    }
    const saved = this.archive.put(envelope, { ...metadata, callKey,
      providerRequestId: result.toolCallId, toolName: request.name,
      sourceKind: value.kind || "observation", sourceIdentity: sourceIdentity(value),
      sourceVersion: canonical.sourceVersion, sourceIdentityDigest: canonical.sourceIdentityDigest,
      sourceQueryKey: canonical.sourceQueryKey, provider: canonical.provider,
      semanticQueryDigest: canonical.semanticQueryDigest, semanticFacts: semanticFacts(value),
      resultStatus: value.ok === false || (value.error !== undefined && value.error !== null && value.error !== false)
        ? false : value.resultStatus ?? value.status ?? value.ok ?? "unknown",
      errorCode: value.errorCode ?? null, operationId: value.operationId ?? null,
      originRanges: value.range || value.returnedRange || value.lineRange || value.page
        || (Number.isInteger(value.startLine) && Number.isInteger(value.endLine) ? [value.startLine, value.endLine] : null)
        || (value.pageStart !== undefined ? [value.pageStart, value.pageEnd] : null),
    }, { liveRefs: new Set([...this.refs.keys(), ...this.returnedRefs.keys()]) });
    if (saved.ok) {
      const ref = [saved.record.evidenceId, saved.record.archivedBodyHash];
      this.refs.set(...ref);
      this.rawRefs.set(callKey, ref);
    }
    return saved;
  }

  /** Only call for an accepted input. Speculative projection must not unpin
   * either the current durable window or a returned result awaiting consumption. */
  inputRefs(history) {
    const live = liveEvidenceRefs(history), index = exchangeIndex(history);
    history.getMessagesArray().forEach((message, mi) => message.getToolCallResults().forEach((result, ri) => {
      const request = index.matches.get(`${mi}:${ri}`);
      const ref = request && this.rawRefs.get(rawExposureKey(request, result, serializedResultContent(result.content)));
      if (ref) live.set(...ref);
    }));
    return live;
  }

  reconcileRefs(history, consumed = false, requirePending = false) {
    const live = this.inputRefs(history);
    const resultKeys = inputResultKeys(history);
    // A bounded candidate may not silently omit a result before its first
    // consumer. Keep it pinned and block that candidate with an explicit error.
    if (requirePending && ([...this.returnedRefs.keys()].some(id => !live.has(id))
      || [...this.pendingHistoricalResults].some(key => !resultKeys.has(key)))) {
      throw new Error("CONTEXT_PENDING_EVIDENCE_OMITTED");
    }
    if (consumed) {
      for (const key of resultKeys) {
        if (this.pendingHistoricalResults.delete(key)) this.consumedRawResults.add(key);
      }
      for (const id of live.keys()) this.returnedRefs.delete(id);
      for (const [key, [id]] of this.rawRefs) if (live.has(id)) this.consumedRawResults.add(key);
    }
    this.refs = new Map([...(this.manifest?.refs || []), ...live, ...this.returnedRefs]);
    for (const [key, [id]] of this.rawRefs) if (!this.refs.has(id)) {
      this.rawRefs.delete(key); this.consumedRawResults.delete(key);
    }
  }

  persistable(history) {
    const replacement = serialize(history); // Preserve typed-file rejection.
    const clean = value => {
      if (Array.isArray(value)) return value.map(clean);
      if (!value || typeof value !== "object") return redact(value);
      return Object.fromEntries(Object.entries(value).filter(([key, item]) =>
        !/^(?:receipt|approval|capability|authorization|nextCursor|cursor|transport)$/iu.test(key)
          && !isEphemeralCapabilityKey(key, item)).map(([key, item]) => [key, clean(item)]));
    };
    for (const message of replacement) for (const part of message.content) {
      if (part.type === "text" && message.role === "assistant") part.text = visibleAssistantText(part.text);
      if (part.type === "toolCallResult" && typeof part.content === "string") {
        try { part.content = JSON.stringify(clean(JSON.parse(part.content))); } catch { part.content = redact(part.content); }
      }
    }
    return deserialize(clean(replacement));
  }

  captureReturned(history, isObservation, metadata = {}) {
    const index = exchangeIndex(history);
    let captured = 0;
    const messages = history.getMessagesArray();
    for (let mi = 0; mi < messages.length; mi++) {
      const results = messages[mi].getToolCallResults();
      for (let ri = 0; ri < results.length; ri++) {
        const result = results[ri], request = index.matches.get(`${mi}:${ri}`);
        if (!request || !isObservation(request)) continue;
        const envelope = serializedResultContent(result.content);
        // Duplicate callbacks/replays cannot reopen a completed consumer.
        if (this.consumedRawResults.has(rawExposureKey(request, result, envelope))) continue;
        const value = decodeToolResultRecord(envelope).value;
        if (!value || value.kind === "archived_tool_result_projection") continue;
        if (["historical_evidence_range", "historical_evidence_index"].includes(value.kind)) {
          // Rehydration is a new returned page even when its source ID was
          // consumed before. Pin that exact page until its first consumer;
          // re-archiving it would create recursive archive envelopes.
          this.pendingHistoricalResults.add(rawExposureKey(request, result, envelope));
          continue;
        }
        const saved = this.archiveObservation(request, result, value, metadata);
        if (saved.ok) { this.returnedRefs.set(saved.record.evidenceId, saved.record.archivedBodyHash); captured++; }
      }
    }
    return captured;
  }

  summaryRefs() {
    return new Set(this.summaryEvidence().map(item => item.ref));
  }

  summaryEvidence(maxItems = 8, maxChars = 6000) {
    const output = [];
    const entries = [...this.refs].reverse();
    for (const [id, version] of entries) {
      const loaded = this.archive.load(id);
      if (!loaded.ok || loaded.record.archivedBodyHash !== version) continue;
      const record = loaded.record, call = record.metadata?.providerRequestId;
      if (typeof call !== "string" || !call) continue;
      const decoded = decodeToolResultRecord(record.body);
      const semantic = decoded.value || {};
      const excerpt = semanticEvidenceView(semantic, 640);
      const item = { ref: `tool-call:archive-${hash([id, version]).slice(0, 32)}`, evidenceId: id, version,
        source: record.metadata?.toolName || record.metadata?.sourceKind || "observation",
        sourceIdentityDigest: hash(record.metadata?.sourceIdentity || {}),
        verifiedExcerpt: excerpt,
        facts: record.metadata?.semanticFacts || semanticFacts(semantic) };
      if (JSON.stringify([...output, item]).length > maxChars) continue;
      output.push(item);
      if (output.length >= maxItems) break;
    }
    return output.reverse();
  }

  project(history, isObservation, metadata = {}, maxChars = 2048) {
    const index = exchangeIndex(history);
    if (index.ambiguous) return { history, changed: false, reason: "pending_or_ambiguous_exchange" };
    const projected = Chat.empty();
    let changed = false, archiveFailed = false, preservedFirstConsumer = false;
    const messages = history.getMessagesArray();
    for (let messageIndex = 0; messageIndex < messages.length; messageIndex++) {
      const message = messages[messageIndex];
      const results = message.getToolCallResults();
      if (!results.length) { projected.append(message); continue; }
      const copies = results.map((result, resultIndex) => {
        const request = index.matches.get(`${messageIndex}:${resultIndex}`);
        if (!request || !isObservation(request)) return result;
        const originalEnvelope = serializedResultContent(result.content);
        const decoded = decodeToolResultRecord(originalEnvelope), parsed = decoded.value;
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)
          || ["archived_tool_result_projection", "historical_evidence_range", "historical_evidence_index"].includes(parsed.kind)) return result;
        // The caller's known observation classification alone is not execution proof.
        if (parsed.pending === true || ["pending", "running", "unknown", "ambiguous"].includes(parsed.status)) return result;
        // A projection has fixed identity/range metadata. Avoid archiving and
        // replacing tiny observations that cannot become cheaper overall.
        if (originalEnvelope.length <= Math.max(maxChars, 1024)) return result;
        const identity = sourceIdentity(parsed), facts = semanticFacts(parsed);
        const saved = this.archiveObservation(request, result, parsed, metadata);
        if (!saved.ok) { archiveFailed = true; return result; }
        const record = saved.record;
        this.refs.set(record.evidenceId, record.archivedBodyHash);
        const preserveLimit = Number(metadata.preserveUnconsumedRawMaxChars ?? metadata.preserveUnconsumedRawGitMaxChars ?? 0);
        if (preserveLimit > 0
          && originalEnvelope.length <= preserveLimit
          && !this.consumedRawResults.has(rawExposureKey(request, result, originalEnvelope))) {
          preservedFirstConsumer = true;
          return result;
        }
        const bodyView = gitBodyView(parsed);
        const semanticBody = JSON.stringify(redact(parsed));
        const boundedBody = bodyView?.body || semanticBody;
        const bodyFirst = Boolean(bodyView);
        let projectedEnd = Math.min(maxChars, semanticBody.length);
        const directSemanticBody = originalEnvelope.trim() === JSON.stringify(parsed);
        const projection = { kind: "archived_tool_result_projection", originalCallId: result.toolCallId,
          toolName: request.name,
          ...(bodyFirst ? { viewMode: "body_first_bounded", bodyField: bodyView.field } : {}),
          originalEnvelopeChars: originalEnvelope.length,
          originalEnvelopeBytes: Buffer.byteLength(originalEnvelope),
          resultStatus: record.metadata.resultStatus, errorCode: record.metadata.errorCode,
          sourceIdentity: identity, sourceIdentityDigest: record.metadata.sourceIdentityDigest,
          sourceVersion: record.metadata.sourceVersion, sourceQueryKey: record.metadata.sourceQueryKey,
          provider: record.metadata.provider, semanticQueryDigest: record.metadata.semanticQueryDigest,
          sourceRange: record.metadata.originRanges
            || (parsed.pageStart !== undefined || parsed.pageEnd !== undefined ? [parsed.pageStart, parsed.pageEnd] : null),
          sourceCollectionComplete: parsed.sourceResultComplete ?? "unknown",
          sourcePageStart: parsed.pageStart,
          sourcePageEnd: parsed.pageEnd,
          sourcePageHasMore: parsed.pageHasMore ?? parsed.hasMore ?? "unknown",
          sourceReturnedCount: parsed.returnedCount,
          sourceTotal: parsed.total,
          sourceAction: parsed.action,
          sourceSince: parsed.since,
          sourceUntil: parsed.until,
          sourceAuthorQuery: parsed.authorQuery,
          sourceAuthorQuerySemantics: parsed.authorQuerySemantics,
          pageHasMore: parsed.pageHasMore ?? parsed.hasMore ?? "unknown",
          ...(typeof parsed.nextCursor === "string" && parsed.nextCursor
            ? { transport: { nextCursor: parsed.nextCursor, durable: false, guessed: false } } : {}),
          archiveRef: { evidenceId: record.evidenceId, version: record.archivedBodyHash, archiveState: record.archiveState },
          sourceHash: record.sourceHash, redacted: record.redacted,
          rangeUnit: record.rangeUnit, archivedRanges: record.archivedRanges,
          bodyRangeUnit: record.rangeUnit,
          projectedRawRanges: [],
          projectedSanitizedRanges: [],
          projectedBodyRanges: [],
          omittedRanges: [],
          omittedBodyRanges: [],
          bodyTotalChars: bodyFirst ? boundedBody.length : undefined,
          bodyOmittedChars: bodyFirst ? boundedBody.length : undefined,
          bodyComplete: false,
          fullRawProvided: false, currentFile: false, grantsMutation: false,
          excerpt: "" };
        const fixedCost = JSON.stringify(projection).length;
        projectedEnd = Math.min(bodyFirst ? maxChars : projectedEnd,
          Math.max(0, originalEnvelope.length - fixedCost - 1));
        if (!bodyFirst) {
          projection.projectedRawRanges = directSemanticBody && !record.redacted ? [[0, projectedEnd]] : [];
          projection.projectedSanitizedRanges = [[0, projectedEnd]];
          projection.omittedRanges = [[Math.min(projectedEnd, record.body.length), record.body.length]];
          projection.excerpt = semanticBody.slice(0, projectedEnd);
        } else {
          projection.omittedRanges = [[0, record.body.length]];
          projection.projectedBodyRanges = [[0, Math.min(projectedEnd, boundedBody.length)]];
          projection.omittedBodyRanges = [[Math.min(projectedEnd, boundedBody.length), boundedBody.length]];
          projection.bodyOmittedChars = Math.max(0, boundedBody.length - projectedEnd);
          projection.bodyComplete = projection.bodyOmittedChars === 0;
          projection.excerpt = boundedBody.slice(0, projectedEnd);
        }
        let projectedContent = JSON.stringify(projection);
        while (projectedContent.length >= originalEnvelope.length && projectedEnd > 0) {
          projectedEnd = Math.max(0, projectedEnd
            - Math.max(16, projectedContent.length - originalEnvelope.length + 16));
          if (!bodyFirst) {
            projection.projectedRawRanges = directSemanticBody && !record.redacted ? [[0, projectedEnd]] : [];
            projection.projectedSanitizedRanges = [[0, projectedEnd]];
            projection.omittedRanges = [[Math.min(projectedEnd, record.body.length), record.body.length]];
            projection.excerpt = semanticBody.slice(0, projectedEnd);
          } else {
            projection.projectedBodyRanges = [[0, Math.min(projectedEnd, boundedBody.length)]];
            projection.omittedBodyRanges = [[Math.min(projectedEnd, boundedBody.length), boundedBody.length]];
            projection.bodyOmittedChars = Math.max(0, boundedBody.length - projectedEnd);
            projection.bodyComplete = projection.bodyOmittedChars === 0;
            projection.excerpt = boundedBody.slice(0, projectedEnd);
          }
          projectedContent = JSON.stringify(projection);
        }
        if (projectedContent.length >= originalEnvelope.length) return result;
        changed = true;
        return { ...result, content: projectedContent };
      });
      projected.append(ChatMessage.from({ role: "tool", content: copies.map(r => ({ type: "toolCallResult", ...r })) }));
    }
    return { history: changed ? projected : history, changed, archiveFailed,
      reason: archiveFailed ? "partial_archive_failure"
        : preservedFirstConsumer ? "first_consumer_raw_git_preserved" : "verified" };
  }

  tool() {
    return rawFunctionTool({ name: "evidence_first_read_context",
      description: "Read previously returned historical evidence by exact archive ID and version. Use action=catalog with an optional exact path to discover IDs lost from working context; continue catalog pages using nextOffset as startOffset. Then use action=read with the returned evidenceId and version. Historical access is not a fresh file read and grants no write/build approval.",
      parametersJsonSchema: { type: "object", additionalProperties: false,
        properties: { action: { type: "string", enum: ["read", "catalog"] }, path: { type: "string" },
          evidenceId: { type: "string" }, version: { type: "string" },
          startOffset: { type: "integer", minimum: 0 }, maxChars: { type: "integer", minimum: 1, maximum: 8192 } },
        anyOf: [ { required: ["evidenceId", "version"], properties: { action: { const: "read" } } },
          { required: ["action"], properties: { action: { const: "catalog" } } } ] },
      implementation: async (args, ctx) => {
        if (ctx.signal.aborted) throw ctx.signal.reason;
        if (args.action === "catalog") return this.archive.catalog(args);
        return this.archive.read(args.evidenceId, args.version, args.startOffset ?? 0, args.maxChars ?? 4096);
      } });
  }

  refsValid(refs = [...this.refs]) { return refs.every(([id, version]) => {
    const record = this.archive.load(id); return record.ok && record.record.archivedBodyHash === version;
  }); }

  captureExposure(history, modelInputId, completed = false) {
    this.reconcileRefs(history, completed, !completed);
    const index = exchangeIndex(history);
    const messages = history.getMessagesArray();
    for (let messageIndex = 0; messageIndex < messages.length; messageIndex++) {
      const message = messages[messageIndex];
      const results = message.getToolCallResults();
      for (let resultIndex = 0; resultIndex < results.length; resultIndex++) {
        const result = results[resultIndex];
      try {
        const value = decodeToolResultRecord(result.content).value;
        if (!value) continue;
        if (value.kind === "archived_tool_result_projection") {
          this.exposure.set(`${modelInputId}:${result.toolCallId}`, { modelInputId, callId: result.toolCallId,
            source: "model_facing_projection",
            stage: completed ? "generation_completed_after_input" : "included_in_sdk_input",
            hostInputVerification: "unknown", projectedRawRanges: value.projectedRawRanges,
            projectedSanitizedRanges: value.projectedSanitizedRanges,
            fullRawProvided: false, evidenceId: value.archiveRef.evidenceId });
        } else if (value.kind === "historical_evidence_range") {
          this.exposure.set(`${modelInputId}:${result.toolCallId}`, { modelInputId, callId: result.toolCallId,
            source: "archive_rehydration", currentSourceRead: false,
            stage: completed ? "generation_completed_after_input" : "included_in_sdk_input",
            hostInputVerification: "unknown", returnedRange: value.returnedRange,
            fullRawProvided: value.fullRawProvided === true,
            redacted: value.redacted, evidenceId: value.evidenceId });
        } else {
          const request = index.matches.get(`${messageIndex}:${resultIndex}`);
          if (!request) continue;
          const originalEnvelope = serializedResultContent(result.content);
          const key = rawExposureKey(request, result, originalEnvelope);
          this.exposure.set(`${modelInputId}:${result.toolCallId}`, { modelInputId, callId: result.toolCallId,
            source: "current_raw_tool_result",
            stage: completed ? "generation_completed_after_input" : "included_in_sdk_input",
            hostInputVerification: "unknown", rawChars: originalEnvelope.length });
          if (completed) this.consumedRawResults.add(key);
        }
      } catch { /* No archive view. */ }
      }
    }
  }

  commit(source, candidate, measurement, expectedPrefix, modelFingerprint) {
    if (this.closed) { this.lastCommitReason = "execution_closed"; return false; }
    if (!measurement?.exact || measurement.remainingTokens < 0) {
      this.lastCommitReason = !measurement?.exact ? "measurement_not_exact" : "hard_limit";
      return false;
    }
    const saved = this.#writeWindow(source, candidate, measurement, expectedPrefix, modelFingerprint);
    if (saved) this.committedInExecution = true;
    return saved;
  }

  seal(source, candidate) {
    if (this.closed) { this.lastCommitReason = "execution_closed"; return false; }
    // A terminal checkpoint extends an already measured window in this
    // execution. It is not a measurement and must never authorize dispatch.
    if (!this.committedInExecution || !this.manifest) {
      this.lastCommitReason = "no_committed_window_in_execution"; return false;
    }
    try {
      const prefix = serialize(source);
      if (hash(prefix.slice(0, this.manifest.sourceLength)) !== this.manifest.sourcePrefixHash) {
        this.lastCommitReason = "source_prefix_changed"; return false;
      }
      const saved = this.#writeWindow(source, candidate, null, hash(prefix), this.manifest.modelFingerprint);
      if (saved) this.closed = true;
      return saved;
    } catch (error) { this.lastCommitReason = error.message || "window_write_failed"; return false; }
  }

  #writeWindow(source, candidate, measurement, expectedPrefix, modelFingerprint) {
    this.lastCommitReason = null;
    try {
      const prefix = serialize(source);
      const candidateRefs = this.inputRefs(candidate);
      const candidateKeys = inputResultKeys(candidate);
      const rejection = hash(prefix) !== expectedPrefix ? "source_prefix_changed"
        : !this.refsValid([...candidateRefs]) ? "archive_refs_unavailable"
          : exchangeIndex(candidate).ambiguous ? "pending_or_ambiguous_exchange"
            : [...this.returnedRefs.keys()].some(id => !candidateRefs.has(id))
              || [...this.pendingHistoricalResults].some(key => !candidateKeys.has(key)) ? "pending_evidence_omitted" : null;
      if (rejection) { this.lastCommitReason = rejection; return false; }
      // Validate the exact live candidate first. Persistence owns sanitization;
      // stripping capabilities before validation changes raw-envelope identity.
      const persisted = this.persistable(candidate), replacement = serialize(persisted);
      const rawBindings = [], consumedBindings = [], pendingHistoricalResults = [];
      const index = exchangeIndex(candidate), persistedIndex = exchangeIndex(persisted), persistedMessages = persisted.getMessagesArray();
      candidate.getMessagesArray().forEach((message, mi) => message.getToolCallResults().forEach((result, ri) => {
        const request = index.matches.get(`${mi}:${ri}`);
        if (!request) return;
        const key = rawExposureKey(request, result, serializedResultContent(result.content));
        const ref = this.rawRefs.get(key);
        const cleanResult = persistedMessages[mi].getToolCallResults()[ri];
        const cleanRequest = persistedIndex.matches.get(`${mi}:${ri}`);
        const cleanKey = rawExposureKey(cleanRequest, cleanResult, serializedResultContent(cleanResult.content));
        if (ref) rawBindings.push([cleanKey, ref]);
        if (this.consumedRawResults.has(key)) consumedBindings.push(cleanKey);
        if (this.pendingHistoricalResults.has(key)) pendingHistoricalResults.push(cleanKey);
      }));
      // Never persist live receipt/secret material or raw reasoning in a window.
      if (JSON.stringify(redact(replacement)) !== JSON.stringify(replacement)
        || replacement.some(m => m.content.some(p => p.type === "text" && p.text.includes(REASONING_SEPARATOR)))) {
        this.lastCommitReason = "non_persistable_live_material"; return false;
      }
      const body = { schemaVersion: 1, scope: this.scope, windowId: hash([this.scope, expectedPrefix, replacement]),
        generation: this.generation + 1, parentWindowId: this.manifest?.windowId || null,
        lineage: this.lineage, parentLineage: this.parentLineage,
        sourcePrefixHash: expectedPrefix, sourceLength: prefix.length,
        replacement, refs: [...candidateRefs], rawBindings, consumedBindings,
        pendingRefs: [...this.returnedRefs], pendingHistoricalResults, note: this.note,
        lastSummaryInput: this.lastSummaryInput, modelFingerprint, measurement,
        measurementSubject: measurement ? "live_candidate_before_persistence_sanitization" : "terminal_unmeasured",
        remeasureRequired: true };
      const manifest = { ...body, digest: hash(body) };
      if (this.archive.directory) {
        this.archive.ensureDirectory(true);
        atomicWrite(this.archive.directory, this.windowFile(this.lineage), manifest);
      }
      this.manifest = manifest; this.generation = body.generation; this.refs = candidateRefs;
      return true;
    } catch (error) { this.lastCommitReason = error.message || "window_write_failed"; return false; }
  }

  restore(source) {
    try {
      let manifest = this.manifest;
      if (this.archive.directory) {
        this.archive.ensureDirectory();
        manifest = JSON.parse(readFile(path.join(this.archive.directory, this.windowFile(this.parentLineage)), this.archive.options.maxBytes));
      }
      if (!manifest) return { history: source, reason: "no_manifest" };
      const { digest, ...body } = manifest;
      const messages = serialize(source);
      const rejection = digest !== hash(body) ? "manifest_integrity_failed"
        : body.schemaVersion !== 1 ? "manifest_schema_mismatch"
          : body.scope !== this.scope ? "scope_mismatch"
            : this.parentLineage && body.lineage !== this.parentLineage ? "lineage_mismatch"
              : !Number.isSafeInteger(body.sourceLength) || body.sourceLength < 0
                || body.sourceLength > messages.length ? "source_length_mismatch"
                : hash(messages.slice(0, body.sourceLength)) !== body.sourcePrefixHash ? "source_prefix_mismatch"
                  : !this.refsValid(body.refs) ? "archive_refs_unavailable" : null;
      if (rejection) return { history: source, reason: rejection };
      this.manifest = manifest; this.generation = body.generation; this.refs = new Map(body.refs); this.note = body.note;
      this.rawRefs = new Map(body.rawBindings || []);
      this.returnedRefs = new Map(body.pendingRefs || []);
      this.pendingHistoricalResults = new Set(body.pendingHistoricalResults || []);
      this.consumedRawResults = new Set(body.consumedBindings || []);
      this.lastSummaryInput = typeof body.lastSummaryInput === "string" ? body.lastSummaryInput : "";
      return { history: deserialize([...body.replacement, ...messages.slice(body.sourceLength)]), reason: "restored_remeasure_required" };
    } catch { return { history: source, reason: "unavailable" }; }
  }
}

function validateSemanticNote(text, refs, generation, parentWindow) {
  try {
    const value = JSON.parse(text);
    if (value.reviewClaims !== undefined || Object.keys(value).some(k => !["decisions", "rejectedHypotheses", "openQuestions"].includes(k))) return null;
    const note = modelNotes.validateDraftNote(value);
    if (!note) return null;
    const items = [...note.decisions, ...note.rejectedHypotheses, ...note.openQuestions];
    if (!items.length || items.some(item => !item.refs?.length || item.refs.some(ref => !refs.has(ref)))) return null;
    return { source: "assistant_summary", semanticTruthVerified: false, summaryGeneration: generation,
      parentWindow, ...note };
  } catch { return null; }
}

module.exports = { WorkingContext, serialize, deserialize, exchangeIndex, validateSemanticNote, hash };

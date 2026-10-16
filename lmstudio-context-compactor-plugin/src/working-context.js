"use strict";

const { Chat, ChatMessage, rawFunctionTool } = require("@lmstudio/sdk");
const { EvidenceArchive, evidenceBodyView, evidenceSemanticFacts: semanticFacts, hash, redact, atomicWrite, readFile } = require("./evidence-archive.js");
const path = require("node:path");
const modelNotes = require("./continuity-model-notes.js");
const { SUMMARY_POLICY } = require("./semantic-handoff-policy.js");
const { ARCHIVE_READ_CONTRACT } = require("./evidence-read-contract.js");
const { decodeToolResultRecord } = require("./compaction-tool-memory.js");
const { REASONING_SEPARATOR, visibleAssistantText } = require("./continuity-text.js");
const { isEphemeralCapabilityKey } = require("./durable-memory-sanitizer.js");
const evidenceIdentity = require("./evidence-identity.js");
const { exchangeIndex } = require("./tool-exchange-index.js");
const { retainInterruptedCalls } = require("./interrupted-calls.js");
const { canonicalTranscript } = require("./transcript-canonical.js");
const { retainVisibleResponse } = require("./continuity-assistant-evidence.js");

const CANONICAL_MESSAGE_HASH_LIMIT = 512;

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

const identityFields = ["repositoryIdentity", "workspaceIdentity", "projectIdentity", "canonicalProjectRoot",
  "canonicalProject", "path", "action", "comparison", "base", "head", "currentHead", "blobOid",
  "sourceMode", "sourceIdentity", "observedEngineIdentity", "evidenceSource"];

function sourceIdentity(payload) {
  const identity = {};
  for (const key of identityFields) if (payload[key] !== undefined) identity[key] = payload[key];
  return redact(identity);
}


function semanticEvidenceView(payload, maxChars = 640) {
  const facts = semanticFacts(payload);
  if (payload.kind !== "git_observation") {
    const body = gitBodyView(payload);
    if (!body) return JSON.stringify({ ...facts, metadataOnly: true }).slice(0, maxChars);
    const view = { bodyField: body.field, excerpt: "", bodyTotalChars: body.body.length,
      bodyRangeUnit: "utf16_code_units", projectedBodyRanges: [], bodyComplete: false,
      fullRawProvided: false };
    const allowance = Math.max(0, maxChars - JSON.stringify(view).length - 16);
    view.excerpt = body.body.slice(0, allowance);
    while (JSON.stringify(view).length > maxChars && view.excerpt.length) view.excerpt = view.excerpt.slice(0, -1);
    view.projectedBodyRanges = [[0, view.excerpt.length]];
    view.bodyComplete = view.excerpt.length === body.body.length;
    return JSON.stringify(view);
  }
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
  return evidenceBodyView(redact(payload));
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
    this.capturedCurrentResult = false;
    this.restoredResultKeys = new Set();
    this.restoredInterruptionKeys = new Set();
    this.closed = false;
    this.exposure = new Map();
    this.note = null;
    this.referenceObservations = [];
    this.lineage = options.lineage || null;
    this.parentLineage = options.parentLineage || null;
    this.archive.setAccess(this.lineage || scope.lineage);
    this.archive.protectedWindows = new Set([this.windowFile(this.lineage), this.windowFile(this.parentLineage)]);
    this.windowDigest = undefined;
    this.lastSummaryInput = "";
    this.summaryAttempts = [];
    this.consumedRawResults = new Set();
    this.archivedProse = new Set();
    this.archivedEvidenceKnown = undefined;
    this.cost = { summaryCalls: 0, summaryPromptTokens: 0, summaryPredictedTokens: 0, summaryMs: 0, unknownUsageCalls: 0 };
  }

  windowFile(lineage) { return lineage ? `window-${lineage}.json` : "window.json"; }

  get hasArchivedEvidence() {
    if (this.refs.size || this.returnedRefs.size || this.archive.stats.captured) return true;
    // A restored window may no longer mention the earliest consumed pages.
    // Discovery still needs a usable result budget for those scoped records.
    // Scanning every record is costly and this getter runs several times per
    // round, so the answer is memoized until a restore can change it.
    if (this.archivedEvidenceKnown !== undefined) return this.archivedEvidenceKnown;
    try { this.archivedEvidenceKnown = this.archive.entries().some(entry => this.archive.load(entry.key).ok); }
    catch { this.archivedEvidenceKnown = false; }
    return this.archivedEvidenceKnown;
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
        || (value.pageStart !== undefined ? [value.pageStart, value.pageEnd] : null)
        || (Number.isInteger(value.offsetBytes) && Number.isInteger(value.nextOffsetBytes)
          ? [value.offsetBytes, value.nextOffsetBytes] : null),
      originRangeUnit: value.rangeUnit || (Number.isInteger(value.offsetBytes) ? "utf8_byte"
        : Number.isInteger(value.startLine) ? "line" : value.pageStart !== undefined ? "page" : "unknown"),
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
    this.refs = new Map([...(this.manifest?.refs || []), ...live, ...this.returnedRefs, ...this.noteRecordRefs(this.note)]);
    try {
      this.archive.setPins(this.refs.keys());
      this.lastRetentionReason = null;
    } catch (error) {
      // Existing puts/windows already pin these records. A failed retention
      // update keeps those conservative pins and must not reject raw input.
      this.lastRetentionReason = error.message || "archive_retention_unavailable";
      this.archive.stats.retentionUpdateFailures = (this.archive.stats.retentionUpdateFailures || 0) + 1;
    }
    for (const [key, [id]] of this.rawRefs) if (!this.refs.has(id)) {
      this.rawRefs.delete(key); this.consumedRawResults.delete(key);
    }
  }

  /** Archive user/assistant prose that an accepted compaction removed from
   * the input, so it stays rehydratable through evidence_first_read_context
   * instead of surviving only as clipped checkpoint excerpts. */
  archiveConversationProse(history, retainedIndexes = []) {
    const retained = new Set(retainedIndexes);
    let archived = 0;
    history.getMessagesArray().forEach((message, index) => {
      const role = message.getRole();
      if (retained.has(index) || (role !== "user" && role !== "assistant") || message.hasFiles()) return;
      const text = role === "assistant" ? visibleAssistantText(message.getText()) : message.getText();
      // Generated checkpoints, notes and runtime records start with a marker.
      if (!text.trim() || /^\[[A-Z][^\]\n]{0,80}\]/u.test(text)) return;
      const key = hash([role, text]);
      if (this.archivedProse.has(key)) return;
      const saved = this.archive.put(JSON.stringify({ kind: "conversation_message", role, text }), {
        callKey: key, toolName: "conversation", sourceKind: "conversation_message",
        sourceIdentity: { path: `conversation://${role}` }, resultStatus: true,
        semanticFacts: { kind: "conversation_message", role, chars: text.length },
      });
      if (!saved.ok) return;
      this.archivedProse.add(key);
      archived++;
    });
    return archived;
  }

  /** Message indexes of results that must reach their first consumer. The
   * compaction candidate selector retains these instead of discovering the
   * omission only at the post-selection exposure check. */
  pendingResultIndexes(history) {
    if (!this.returnedRefs.size && !this.pendingHistoricalResults.size) return [];
    const index = exchangeIndex(history), output = new Set();
    history.getMessagesArray().forEach((message, mi) => message.getToolCallResults().forEach((result, ri) => {
      const request = index.matches.get(`${mi}:${ri}`);
      if (!request) return;
      const key = rawExposureKey(request, result, serializedResultContent(result.content));
      const ref = this.rawRefs.get(key);
      if (this.pendingHistoricalResults.has(key) || (ref && this.returnedRefs.has(ref[0]))) output.add(mi);
    }));
    return [...output];
  }

  /** Degrade path for an input that still omits pending results: release
   * them from first-consumer tracking (they stay pinned and rehydratable
   * through the archive) instead of failing the whole execution. */
  releaseOmittedPending(history) {
    const live = this.inputRefs(history), keys = inputResultKeys(history), released = [];
    for (const [id, version] of [...this.returnedRefs]) {
      if (live.has(id)) continue;
      this.returnedRefs.delete(id); this.refs.set(id, version); released.push(id);
    }
    for (const key of [...this.pendingHistoricalResults]) {
      if (keys.has(key)) continue;
      this.pendingHistoricalResults.delete(key); released.push(key);
    }
    return released;
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
        if (!request) continue;
        const envelope = serializedResultContent(result.content);
        if (!this.restoredResultKeys.has(rawExposureKey(request, result, envelope))) this.capturedCurrentResult = true;
        if (!isObservation(request)) continue;
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

  noteRecordRefs(note) {
    const requested = new Set(["decisions", "rejectedHypotheses", "openQuestions", "reviewClaims"]
      .flatMap(key => (note?.[key] || []).flatMap(item => item.refs || [])));
    const verified = new Map();
    if (!requested.size) return verified;
    for (const [id, version] of this.refs) {
      const ref = `tool-call:archive-${hash([id, version]).slice(0, 32)}`;
      if (!requested.has(ref)) continue;
      const loaded = this.archive.load(id);
      if (loaded.ok && loaded.record.archivedBodyHash === version) verified.set(id, version);
    }
    return verified;
  }

  verifiedNoteRefs(note) {
    return new Set([...this.noteRecordRefs(note)].map(([id, version]) =>
      `tool-call:archive-${hash([id, version]).slice(0, 32)}`));
  }

  summaryEvidence(maxItems = 8, maxChars = 6000) {
    const output = [];
    const seen = new Set();
    const entries = [...this.refs].reverse();
    for (const [id, version] of entries) {
      const loaded = this.archive.load(id);
      if (!loaded.ok || loaded.record.archivedBodyHash !== version) continue;
      const record = loaded.record, call = record.metadata?.providerRequestId;
      if (typeof call !== "string" || !call) continue;
      const decoded = decodeToolResultRecord(record.body);
      const semantic = decoded.value || {};
      const excerpt = semanticEvidenceView(semantic, 640);
      const facts = record.metadata?.semanticFacts || semanticFacts(semantic);
      // A new archive/call ID alone is not new material for a summary.
      const materialFingerprint = hash([record.metadata?.sourceIdentityDigest,
        record.metadata?.sourceVersion, record.metadata?.semanticQueryDigest, facts, excerpt]);
      if (seen.has(materialFingerprint)) continue;
      const item = { ref: `tool-call:archive-${hash([id, version]).slice(0, 32)}`, evidenceId: id, version,
        source: record.metadata?.toolName || record.metadata?.sourceKind || "observation",
        sourceIdentityDigest: record.metadata?.sourceIdentityDigest || "unknown-source",
        verifiedExcerpt: excerpt,
        facts, materialFingerprint };
      if (JSON.stringify([...output, item]).length > maxChars) continue;
      seen.add(materialFingerprint);
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
        if ((metadata.preserveUnconsumedRaw === true
          || (preserveLimit > 0 && originalEnvelope.length <= preserveLimit))
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
          sourceFacts: facts, sourceRangeUnit: record.metadata.originRangeUnit,
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
          archiveRef: { evidenceId: record.evidenceId, version: record.archivedBodyHash, archiveState: record.archiveState,
            readHandle: this.archive.readHandleFor(record), view: "body" },
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
      description: "Read historical evidence, not current files. Discover records with action=catalog,catalogVersion=2 and continue using nextContinuation as continuation. Prefer a returned readHandle with view=body for source content, or exact evidenceId/version for the original envelope. Keep maxChars fixed across pages and use nextOffset; maxChars limits the ENTIRE JSON RESPONSE, not remaining content. No write/build authority.",
      parametersJsonSchema: { type: "object", additionalProperties: false,
        properties: { action: { type: "string", enum: ["read", "catalog"] },
          catalogVersion: { type: "integer", enum: [1, 2], description: "Use 2 for stable snapshot continuation; omitted preserves legacy offset paging." }, path: { type: "string" },
          evidenceId: { type: "string" }, version: { type: "string" },
          readHandle: { type: "string" }, view: { type: "string", enum: ["body", "envelope"] },
          continuation: { type: "string", description: "Copy catalog nextContinuation exactly; unavailable after execution restart." },
          startOffset: { type: "integer", minimum: 0, description: "UTF-16 offset in the selected representation; use read nextOffset." },
          maxChars: { type: "integer", minimum: 1, maximum: ARCHIVE_READ_CONTRACT.maximumEnvelopeChars,
            description: "Whole serialized JSON response budget, including metadata and escaping. Default 4096. Keep fixed while paging; do not subtract already-read content." } },
        anyOf: [ { required: ["evidenceId", "version"], properties: { action: { const: "read" } } },
          { required: ["readHandle"], properties: { action: { const: "read" } } },
          { required: ["action"], properties: { action: { const: "catalog" } } } ] },
      implementation: async (args, ctx) => {
        if (ctx.signal.aborted) throw ctx.signal.reason;
        if (args.readHandle && (args.evidenceId !== undefined || args.version !== undefined))
          return { ok: false, errorCode: "selector_conflict" };
        if (args.action === "catalog") {
          if (args.continuation && args.startOffset !== undefined || args.catalogVersion === 2 && args.startOffset !== undefined
            || args.continuation && args.catalogVersion === 1) return { ok: false, errorCode: "pagination_conflict" };
          return args.catalogVersion === 2 || args.continuation ? this.archive.catalogPage(args) : this.archive.catalog(args);
        }
        if (args.readHandle) return this.archive.readByHandle(args.readHandle,
          args.startOffset ?? 0, args.maxChars ?? ARCHIVE_READ_CONTRACT.defaultEnvelopeChars, args.view ?? "body");
        return this.archive.read(args.evidenceId, args.version, args.startOffset ?? 0, args.maxChars ?? ARCHIVE_READ_CONTRACT.defaultEnvelopeChars, args.view ?? "envelope");
      } });
  }

  refsValid(refs = [...this.refs], allowExpired = false) { return refs.every(([id, version]) => {
    const record = this.archive.load(id, { allowExpired }); return record.ok && record.record.archivedBodyHash === version;
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
            bodyField: value.bodyField, bodyRangeUnit: value.bodyRangeUnit,
            projectedBodyRanges: value.projectedBodyRanges, omittedBodyRanges: value.omittedBodyRanges,
            bodyTotalChars: value.bodyTotalChars, bodyOmittedChars: value.bodyOmittedChars,
            bodyComplete: value.bodyComplete,
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
    const firstInterruption = !this.committedInExecution && (this.capturedCurrentResult
      || retainInterruptedCalls(candidate.getMessagesArray()).some(item => !this.restoredInterruptionKeys.has(item.callKey)));
    if ((!this.committedInExecution || !this.manifest) && !firstInterruption) {
      this.lastCommitReason = "no_committed_window_in_execution";
      try { this.archive.close(); } catch { /* The inherited durable window still protects pending refs. */ }
      return false;
    }
    try {
      const prefix = serialize(source);
      if (this.manifest && hash(prefix.slice(0, this.manifest.sourceLength)) !== this.manifest.sourcePrefixHash) {
        this.lastCommitReason = "source_prefix_changed"; return false;
      }
      const delivered = deserialize(retainVisibleResponse(prefix, serialize(candidate)));
      const saved = this.#writeWindow(source, delivered, null, hash(prefix), this.manifest?.modelFingerprint || "terminal_observation_only");
      if (saved) { this.closed = true; try { this.archive.close(); } catch { /* Window remains pinned; diagnostics retain the lease. */ } }
      return saved;
    } catch (error) { this.lastCommitReason = error.message || "window_write_failed"; return false; }
  }

  close() {
    this.closed = true;
    try { this.archive.close(); return true; }
    catch (error) {
      this.lastRetentionReason = error.message || "archive_close_unavailable";
      return false;
    }
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
      for (const ref of this.noteRecordRefs(this.note)) candidateRefs.set(...ref);
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
      const returned = new Map(persistedMessages.flatMap(m => m.getToolCallResults()).map(r => [r.toolCallId, hash(r.content)]));
      const referenceObservations = this.referenceObservations.map(o => ({ ...o,
        fingerprint: returned.get(o.id) || o.fingerprint }));
      // The next execution matches this prefix against the host's own stored
      // transcript, so persist a layout-independent identity alongside the
      // exact in-process one, plus per-entry hashes to locate a divergence.
      const canonical = canonicalTranscript(source.getMessagesArray()).entries;
      const body = { schemaVersion: 2, scope: this.scope, windowId: hash([this.scope, expectedPrefix, replacement]),
        generation: this.generation + 1, parentWindowId: this.manifest?.windowId || null,
        lineage: this.lineage, parentLineage: this.parentLineage,
        sourcePrefixHash: expectedPrefix, sourceLength: prefix.length,
        prefixCanonicalization: "v1", sourceCanonicalLength: canonical.length, sourceCanonicalHash: hash(canonical),
        sourceCanonicalMessageHashes: canonical.length <= CANONICAL_MESSAGE_HASH_LIMIT
          ? canonical.map(entry => hash(entry).slice(0, 16)) : null,
        replacement, refs: [...candidateRefs], membership: this.archive.exportMembership(), rawBindings, consumedBindings,
        pendingRefs: [...this.returnedRefs], pendingHistoricalResults, note: this.note, referenceObservations,
        createdAt: this.archive.now(),
        lastSummaryInput: this.lastSummaryInput, summaryAttempts: this.summaryAttempts.slice(-SUMMARY_POLICY.retainedEvents), modelFingerprint, measurement,
        measurementSubject: measurement ? "live_candidate_before_persistence_sanitization" : "terminal_unmeasured",
        remeasureRequired: true };
      const manifest = { ...body, digest: hash(body) };
      if (this.archive.directory) {
        this.archive.ensureDirectory(true);
        this.archive.withStorageLock(() => {
          const target = path.join(this.archive.directory, this.windowFile(this.lineage));
          let existing;
          try { existing = JSON.parse(readFile(target, this.archive.options.maxBytes)).digest; }
          catch (error) { if (error.code !== "ENOENT") throw error; }
          if (existing !== this.windowDigest) throw new Error("window_writer_conflict");
          if (!this.refsValid([...candidateRefs])) throw new Error("archive_refs_unavailable");
          atomicWrite(this.archive.directory, this.windowFile(this.lineage), manifest);
          this.windowDigest = manifest.digest;
          this.archive.cleanupWindows();
        });
      }
      this.manifest = manifest; this.generation = body.generation; this.refs = candidateRefs;
      return true;
    } catch (error) { this.lastCommitReason = error.message || "window_write_failed"; return false; }
  }

  /** Locate where the sealed prefix ends in the host's current transcript. */
  matchSourcePrefix(body, source, messages) {
    if (body.prefixCanonicalization !== "v1") {
      if (!Number.isSafeInteger(body.sourceLength) || body.sourceLength < 0 || body.sourceLength > messages.length)
        return { rejection: "source_length_mismatch" };
      return hash(messages.slice(0, body.sourceLength)) === body.sourcePrefixHash
        ? { rawStart: body.sourceLength } : { rejection: "source_prefix_mismatch" };
    }
    const { entries, rawEnds } = canonicalTranscript(source.getMessagesArray());
    const length = body.sourceCanonicalLength;
    if (!Number.isSafeInteger(length) || length < 0 || length > entries.length)
      return { rejection: "source_length_mismatch", detail: { sealedEntries: length, currentEntries: entries.length } };
    if (hash(entries.slice(0, length)) !== body.sourceCanonicalHash) {
      const sealed = Array.isArray(body.sourceCanonicalMessageHashes) ? body.sourceCanonicalMessageHashes : null;
      const firstDifferentIndex = sealed ? sealed.findIndex((value, index) => hash(entries[index]).slice(0, 16) !== value) : null;
      return { rejection: "source_prefix_mismatch", detail: { sealedEntries: length, firstDifferentIndex,
        currentRole: firstDifferentIndex >= 0 ? entries[firstDifferentIndex]?.role ?? null : null } };
    }
    return { rawStart: length ? rawEnds[length - 1] + 1 : 0 };
  }

  restore(source) {
    if (this.archive.directory && !this.archive.lockDepth) {
      try { return this.archive.withStorageLock(() => this.restore(source)); }
      catch { return { history: source, reason: "archive_lock_unavailable" }; }
    }
    // Each early exit names its cause; "unavailable" is reserved for an
    // unexpected failure after the window was read and verified.
    let manifest = this.manifest;
    if (this.archive.directory) {
      let text;
      try {
        this.archive.ensureDirectory();
        text = readFile(path.join(this.archive.directory, this.windowFile(this.parentLineage)), this.archive.options.maxBytes);
      } catch (error) {
        return { history: source, reason: error?.code === "ENOENT" ? "no_window_file" : "window_read_failed" };
      }
      try { manifest = JSON.parse(text); } catch { return { history: source, reason: "window_parse_failed" }; }
    }
    if (!manifest) return { history: source, reason: "no_manifest" };
    let messages;
    try { messages = serialize(source); }
    catch (error) { return { history: source, reason: error?.message === "typed_files_not_serializable" ? "typed_files_not_serializable" : "source_unserializable" }; }
    try {
      const { digest, ...body } = manifest;
      const header = digest !== hash(body) ? "manifest_integrity_failed"
        : ![1, 2].includes(body.schemaVersion) ? "manifest_schema_mismatch"
          : body.scope !== this.scope ? "scope_mismatch"
            : this.parentLineage && body.lineage !== this.parentLineage ? "lineage_mismatch" : null;
      if (header) return { history: source, reason: header };
      const prefix = this.matchSourcePrefix(body, source, messages);
      if (prefix.rejection) return { history: source, reason: prefix.rejection, detail: prefix.detail };
      const rawStart = prefix.rawStart;
      // Only a verified ancestor window can grant inherited membership. Raw
      // refs in SDK messages are pin candidates, never access authorization.
      const oldMembership = [...this.archive.membership];
      let restored, refs, rawRefs, returnedRefs, pendingHistoricalResults, consumedRawResults;
      try {
        // The scope, integrity and host prefix have already been verified. This
        // also repairs older windows that omitted the last displayed report.
        const replacement = retainVisibleResponse(messages.slice(0, rawStart), body.replacement);
        restored = deserialize([...replacement, ...messages.slice(rawStart)]);
        refs = new Map(body.refs); rawRefs = new Map(body.rawBindings || []);
        returnedRefs = new Map(body.pendingRefs || []);
        pendingHistoricalResults = new Set(body.pendingHistoricalResults || []);
        consumedRawResults = new Set(body.consumedBindings || []);
        this.archive.setAccess(this.archive.lineage, body.schemaVersion === 2 ? body.membership || [] : body.refs || []);
        // The sealed window is what protects its refs; an idle conversation
        // must still resume after the records' own TTL has elapsed.
        if (!this.refsValid(body.refs, true)) {
          this.archive.setAccess(this.archive.lineage, oldMembership);
          return { history: source, reason: "archive_refs_unavailable" };
        }
        this.archive.protectRecords(refs.keys());
        this.archive.setPins(refs.keys());
      } catch (error) {
        this.archive.setAccess(this.archive.lineage, oldMembership);
        throw error;
      }
      if (body.lineage === this.lineage) this.windowDigest = manifest.digest;
      this.archivedEvidenceKnown = undefined;
      this.manifest = manifest; this.generation = body.generation; this.refs = refs; this.note = body.note;
      this.referenceObservations = Array.isArray(body.referenceObservations) ? body.referenceObservations : [];
      this.rawRefs = rawRefs; this.returnedRefs = returnedRefs;
      this.pendingHistoricalResults = pendingHistoricalResults; this.consumedRawResults = consumedRawResults;
      this.restoredResultKeys = inputResultKeys(restored);
      this.restoredInterruptionKeys = new Set(retainInterruptedCalls(restored.getMessagesArray()).map(item => item.callKey));
      this.lastSummaryInput = typeof body.lastSummaryInput === "string" ? body.lastSummaryInput : "";
      this.summaryAttempts = Array.isArray(body.summaryAttempts)
        ? body.summaryAttempts.filter(key => typeof key === "string" && /^[a-f0-9]{64}$/.test(key)).slice(-SUMMARY_POLICY.retainedEvents) : [];
      return { history: restored, reason: "restored_remeasure_required" };
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

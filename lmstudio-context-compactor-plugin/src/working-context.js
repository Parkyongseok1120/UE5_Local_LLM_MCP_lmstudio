"use strict";

const { Chat, ChatMessage, rawFunctionTool } = require("@lmstudio/sdk");
const { EvidenceArchive, hash, redact, atomicWrite, readFile, safeDirectory } = require("./evidence-archive.js");
const path = require("node:path");
const modelNotes = require("./continuity-model-notes.js");
const { REASONING_SEPARATOR } = require("./continuity-text.js");

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
  const requests = new Map(), results = new Map();
  for (const m of history.getMessagesArray()) {
    for (const r of m.getToolCallRequests()) requests.set(r.id, [...(requests.get(r.id) || []), r]);
    for (const r of m.getToolCallResults()) results.set(r.toolCallId, [...(results.get(r.toolCallId) || []), r]);
  }
  const ambiguous = [...new Set([...requests.keys(), ...results.keys()])].some(id => !id
    || requests.get(id)?.length !== 1 || results.get(id)?.length !== 1);
  return { requests, results, ambiguous };
}

class WorkingContext {
  constructor(scope, options = {}) {
    this.archive = options.archive || new EvidenceArchive(scope, options);
    this.scope = this.archive.scope;
    this.manifest = null;
    this.generation = 0;
    this.refs = new Map();
    this.exposure = new Map();
    this.note = null;
    this.lineage = options.lineage || null;
    this.parentLineage = options.parentLineage || null;
    this.lastSummaryInput = "";
    this.cost = { summaryCalls: 0, summaryPromptTokens: 0, summaryPredictedTokens: 0, summaryMs: 0, unknownUsageCalls: 0 };
  }

  windowFile(lineage) { return lineage ? `window-${lineage}.json` : "window.json"; }

  summaryRefs() {
    const refs = new Set();
    for (const [id] of this.refs) {
      const loaded = this.archive.load(id), call = loaded.ok ? loaded.record.metadata?.providerRequestId : null;
      if (typeof call === "string" && call) refs.add(`tool-call:${call}`);
    }
    return refs;
  }

  project(history, isObservation, metadata = {}, maxChars = 2048) {
    const index = exchangeIndex(history);
    if (index.ambiguous) return { history, changed: false, reason: "pending_or_ambiguous_exchange" };
    const projected = Chat.empty();
    let changed = false, archiveFailed = false;
    for (const message of history.getMessagesArray()) {
      const results = message.getToolCallResults();
      if (!results.length) { projected.append(message); continue; }
      const copies = results.map(result => {
        const request = index.requests.get(result.toolCallId)?.[0];
        if (!request || !isObservation(request)) return result;
        let parsed;
        try { parsed = JSON.parse(result.content); } catch { return result; }
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)
          || parsed.kind === "archived_tool_result_projection" || parsed.kind === "historical_evidence_range") return result;
        // The caller's known observation classification alone is not execution proof.
        if (parsed.pending === true || ["pending", "running", "unknown", "ambiguous"].includes(parsed.status)) return result;
        const saved = this.archive.put(result.content, { ...metadata,
          callKey: `${metadata.executionId || "history"}:${result.toolCallId}`,
          providerRequestId: result.toolCallId, toolName: request.name,
          sourceKind: parsed.kind || "observation", sourceVersion: parsed.sha256 || parsed.head || null,
          semanticQuery: request.arguments, resultStatus: parsed.status ?? parsed.ok ?? "unknown",
          errorCode: parsed.errorCode ?? null,
          operationId: parsed.operationId ?? null,
          buildAttempt: parsed.buildAttempt ?? parsed.attemptId ?? null,
          originRanges: parsed.returnedRange || parsed.lineRange || parsed.page || null });
        if (!saved.ok) { archiveFailed = true; return result; }
        const record = saved.record;
        this.refs.set(record.evidenceId, record.archivedBodyHash);
        if (result.content.length <= maxChars) return result;
        const projectedEnd = Math.min(maxChars, record.body.length);
        const projection = { kind: "archived_tool_result_projection", originalCallId: result.toolCallId,
          resultStatus: parsed.status ?? parsed.ok ?? "unknown", errorCode: parsed.errorCode ?? null,
          sourceRange: parsed.returnedRange || parsed.lineRange || null,
          sourceCollectionComplete: parsed.sourceResultComplete ?? "unknown",
          pageHasMore: parsed.pageHasMore ?? parsed.hasMore ?? "unknown",
          ...(typeof parsed.nextCursor === "string" && parsed.nextCursor
            ? { transport: { nextCursor: parsed.nextCursor, durable: false, guessed: false } } : {}),
          archiveRef: { evidenceId: record.evidenceId, version: record.archivedBodyHash, archiveState: record.archiveState },
          sourceHash: record.sourceHash, redacted: record.redacted,
          rangeUnit: record.rangeUnit, archivedRanges: record.archivedRanges,
          projectedRawRanges: record.redacted ? [] : [[0, projectedEnd]],
          projectedSanitizedRanges: record.redacted ? [[0, projectedEnd]] : [],
          omittedRanges: [[projectedEnd, record.body.length]],
          fullRawProvided: false, currentFile: false, grantsMutation: false,
          excerpt: record.body.slice(0, projectedEnd) };
        changed = true;
        return { ...result, content: JSON.stringify(projection) };
      });
      projected.append(ChatMessage.from({ role: "tool", content: copies.map(r => ({ type: "toolCallResult", ...r })) }));
    }
    return { history: changed ? projected : history, changed, archiveFailed, reason: archiveFailed ? "partial_archive_failure" : "verified" };
  }

  tool() {
    return rawFunctionTool({ name: "evidence_first_read_context",
      description: "Read a bounded range of previously returned historical evidence by exact archive ID and version. This is not a fresh file read and grants no write/build approval. Missing/expired evidence may require the original source tool.",
      parametersJsonSchema: { type: "object", additionalProperties: false,
        properties: { evidenceId: { type: "string" }, version: { type: "string" },
          startOffset: { type: "integer", minimum: 0 }, maxChars: { type: "integer", minimum: 1, maximum: 8192 } },
        required: ["evidenceId", "version"] },
      implementation: async (args, ctx) => {
        if (ctx.signal.aborted) throw ctx.signal.reason;
        return this.archive.read(args.evidenceId, args.version, args.startOffset ?? 0, args.maxChars ?? 4096);
      } });
  }

  refsValid(refs = [...this.refs]) { return refs.every(([id, version]) => {
    const record = this.archive.load(id); return record.ok && record.record.archivedBodyHash === version;
  }); }

  captureExposure(history, modelInputId, completed = false) {
    for (const message of history.getMessagesArray()) for (const result of message.getToolCallResults()) {
      try {
        const value = JSON.parse(result.content);
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
            fullRawProvided: value.hasMore === false && value.redacted === false,
            redacted: value.redacted, evidenceId: value.evidenceId });
        }
      } catch { /* No archive view. */ }
    }
  }

  commit(source, candidate, measurement, expectedPrefix, modelFingerprint) {
    try {
      const prefix = serialize(source), replacement = serialize(candidate);
      if (hash(prefix) !== expectedPrefix || !measurement.exact || measurement.remainingTokens < 0
        || !this.refsValid() || exchangeIndex(candidate).ambiguous) return false;
      // Never persist live receipt/secret material or raw reasoning in a window.
      if (JSON.stringify(redact(replacement)) !== JSON.stringify(replacement)
        || replacement.some(m => m.content.some(p => p.type === "text" && p.text.includes(REASONING_SEPARATOR)))) return false;
      const body = { schemaVersion: 1, scope: this.scope, windowId: hash([this.scope, expectedPrefix, replacement]),
        generation: this.generation + 1, parentWindowId: this.manifest?.windowId || null,
        lineage: this.lineage, parentLineage: this.parentLineage,
        sourcePrefixHash: expectedPrefix, sourceLength: prefix.length,
        replacement, refs: [...this.refs], note: this.note,
        lastSummaryInput: this.lastSummaryInput, modelFingerprint, measurement };
      const manifest = { ...body, digest: hash(body) };
      if (this.archive.directory) atomicWrite(this.archive.directory, this.windowFile(this.lineage), manifest);
      this.manifest = manifest; this.generation = body.generation;
      return true;
    } catch { return false; }
  }

  restore(source) {
    try {
      let manifest = this.manifest;
      if (this.archive.directory) {
        safeDirectory(this.archive.directory);
        manifest = JSON.parse(readFile(path.join(this.archive.directory, this.windowFile(this.parentLineage)), this.archive.options.maxBytes));
      }
      if (!manifest) return { history: source, reason: "no_manifest" };
      const { digest, ...body } = manifest;
      const messages = serialize(source);
      if (digest !== hash(body) || body.schemaVersion !== 1 || body.scope !== this.scope
        || (this.parentLineage && body.lineage !== this.parentLineage)
        || hash(messages.slice(0, body.sourceLength)) !== body.sourcePrefixHash
        || !this.refsValid(body.refs)) return { history: source, reason: "invalid_scope_prefix_or_refs" };
      this.manifest = manifest; this.generation = body.generation; this.refs = new Map(body.refs); this.note = body.note;
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

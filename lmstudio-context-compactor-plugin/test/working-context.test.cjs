"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { EvidenceArchive } = require("../src/evidence-archive.js");
const { WorkingContext, serialize, hash, validateSemanticNote } = require("../src/working-context.js");
const { parseToolResult, stateMemory } = require("../src/compaction-tool-memory.js");
const { buildCheckpoint } = require("../src/direct-compaction-core.js");
const scope = { conversation: "chat-1", lineage: "fork-1", workspace: "workspace-1", repository: "worktree-1" };
function exchange(count = 5) {
  const h = Chat.from([{ role: "system", content: "Keep constraints." }, { role: "user", content: "Inspect every page." }]);
  h.append(ChatMessage.from({ role: "assistant", content: Array.from({ length: count }, (_, i) => ({
    type: "toolCallRequest", toolCallRequest: { type: "function", id: `call_${i}`, name: "git_list", arguments: { page: i } } })) }));
  for (let i = 0; i < count; i++) h.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: `call_${i}`,
    content: JSON.stringify({ ok: true, kind: "git_observation", rows: Array.from({ length: 232 }, (_, n) => `File${n}.cpp`), hasMore: true }) }] }));
  return h;
}

test("S1 five-call projection preserves SDK pairing and rehydrates exact archived pages", () => {
  const c = new WorkingContext(scope), h = exchange();
  const before = h.getMessagesArray().flatMap(m => m.getToolCallResults()).map(r => r.content);
  const p = c.project(h, () => true, { executionId: "exec", modelInputId: "input" });
  assert.equal(p.changed, true);
  assert.equal(p.history.length, h.length);
  const results = p.history.getMessagesArray().flatMap(m => m.getToolCallResults());
  assert.equal(results.length, 5);
  results.forEach((r, i) => {
    const v = JSON.parse(r.content); assert.equal(v.originalCallId, `call_${i}`);
    assert.equal(v.fullRawProvided, false); assert.equal(v.pageHasMore, true);
    const read = c.archive.read(v.archiveRef.evidenceId, v.archiveRef.version, 0, 8192);
    assert.equal(read.content, before[i]); assert.equal(read.currentFile, false);
    assert.equal(JSON.parse(read.content).rows.length, 232);
  });
  assert.deepEqual(h.getMessagesArray().flatMap(m => m.getToolCallResults()).map(r => r.content), before);
});

test("P1 direct JSON and MCP result envelopes produce equivalent projections", () => {
  const semantic = { ok: true, kind: "git_observation", status: "complete", errorCode: null,
    pageStart: 1, pageEnd: 25, pageHasMore: true, sourceResultComplete: false,
    returnedRange: [1, 25], nextCursor: "turn-only", rows: ["x".repeat(4000)] };
  const contents = [
    JSON.stringify(semantic),
    JSON.stringify([{ type: "text", text: JSON.stringify(semantic) }]),
    JSON.stringify({ content: [{ type: "text", text: JSON.stringify(semantic) }], structuredContent: semantic }),
  ];
  const values = contents.map((content, index) => {
    const history = Chat.from([{ role: "user", content: "inspect" }]);
    history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
      type: "function", id: `envelope-${index}`, name: "git_changed_files", arguments: { page: 1 },
    } }] }));
    history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult",
      toolCallId: `envelope-${index}`, content }] }));
    const context = new WorkingContext({ ...scope, lineage: `envelope-${index}` });
    const projected = context.project(history, () => true, { executionId: "exec", roundIndex: 1 }, 512);
    assert.equal(projected.changed, true);
    return JSON.parse(projected.history.getMessagesArray().at(-1).getToolCallResults()[0].content);
  });
  for (const value of values) {
    assert.equal(value.resultStatus, "complete");
    assert.equal(value.errorCode, null);
    assert.equal(value.pageHasMore, true);
    assert.equal(value.sourceCollectionComplete, false);
    assert.deepEqual(value.sourceRange, [1, 25]);
    assert.deepEqual(value.transport, { nextCursor: "turn-only", durable: false, guessed: false });
    assert.match(value.archiveRef.evidenceId, /^ev_[a-f0-9]{64}$/u);
  }
});

test("P1 archived projection becomes a durable evidence index in the next checkpoint", () => {
  const context = new WorkingContext(scope), history = exchange(1);
  const projected = context.project(history, () => true, { executionId: "exec", roundIndex: 1 }, 512);
  const projection = projected.history.getMessagesArray().at(-1).getToolCallResults()[0];
  const parsed = parseToolResult(projection.content);
  const memory = stateMemory([parsed]);
  assert.equal(memory.historicalEvidence.length, 1);
  assert.equal(memory.historicalEvidence[0].evidenceId, JSON.parse(projection.content).archiveRef.evidenceId);

  const messages = projected.history.getMessagesArray().map(message => ({
    role: message.getRole(), text: message.getText(), hasFiles: message.hasFiles(),
    toolRequests: message.getToolCallRequests(), toolResults: message.getToolCallResults(),
  }));
  messages.push({ role: "user", text: "continue", hasFiles: false, toolRequests: [], toolResults: [] });
  const checkpoint = buildCheckpoint(messages, { recentCompleteTurns: 0, maxCurrentTurnMessages: 0 });
  assert.match(checkpoint.checkpoint, /historicalEvidence/u);
  assert.match(checkpoint.checkpoint, new RegExp(memory.historicalEvidence[0].evidenceId, "u"));
  assert.doesNotMatch(checkpoint.checkpoint, /File231\.cpp/u);
});

test("E2E two-generation compaction rehydrates A after its projection leaves history", async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "hybrid-rehydrate-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const scoped = { ...scope, lineage: "rehydrate-lineage" };
  const context = new WorkingContext(scoped, { root, durable: true });
  const full = Chat.from([{ role: "user", content: "Inspect A and B, then continue." }]);
  const appendObservation = (history, id, marker) => {
    history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
      type: "function", id, name: "git_diff_file", arguments: { path: `${id}.cs` },
    } }] }));
    history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
      content: JSON.stringify({ ok: true, kind: "git_observation", status: "complete",
        returnedRange: [0, 6000], body: `${"x".repeat(3000)}${marker}${"y".repeat(3000)}` }) }] }));
  };
  appendObservation(full, "call-A", "A-SENTINEL");
  const first = context.project(full, () => true, { executionId: "exec", roundIndex: 1 }, 512);
  const aProjection = JSON.parse(first.history.getMessagesArray().at(-1).getToolCallResults()[0].content);

  const secondInput = Chat.from(first.history);
  appendObservation(secondInput, "call-B", "B-SENTINEL");
  const fullSecond = Chat.from(full);
  appendObservation(fullSecond, "call-B", "B-SENTINEL");
  const second = context.project(secondInput, () => true, { executionId: "exec", roundIndex: 2 }, 512);
  const normalized = second.history.getMessagesArray().map(message => ({ role: message.getRole(),
    text: message.getText(), hasFiles: message.hasFiles(), toolRequests: message.getToolCallRequests(),
    toolResults: message.getToolCallResults() }));
  const checkpoint = buildCheckpoint(normalized, { recentCompleteTurns: 0, maxCurrentTurnMessages: 0 });
  const candidate = Chat.from([{ role: "system", content: checkpoint.checkpoint },
    { role: "user", content: "Inspect A and B, then continue." }]);
  assert.doesNotMatch(candidate.toString(), /A-SENTINEL|B-SENTINEL/u);
  assert.match(candidate.toString(), new RegExp(aProjection.archiveRef.evidenceId, "u"));
  assert.equal(context.commit(fullSecond, candidate,
    { exact: true, inputTokens: 2000, remainingTokens: 10000 },
    hash(serialize(fullSecond)), "model-template"), true);

  const nextTurn = Chat.from(fullSecond); nextTurn.append("user", "계속해");
  const restarted = new WorkingContext(scoped, { root, durable: true });
  const restored = restarted.restore(nextTurn);
  assert.equal(restored.reason, "restored_remeasure_required");
  assert.match(restored.history.toString(), /계속해/u);
  assert.match(restored.history.toString(), new RegExp(aProjection.archiveRef.evidenceId, "u"));
  assert.doesNotMatch(restored.history.toString(), /A-SENTINEL/u);
  const reread = await restarted.tool().implementation({ evidenceId: aProjection.archiveRef.evidenceId,
    version: aProjection.archiveRef.version, startOffset: 0, maxChars: 8192 },
  { signal: new AbortController().signal, status() {}, warn() {}, callId: 1 });
  assert.equal(reread.ok, true);
  assert.match(reread.content, /A-SENTINEL/u);
  assert.equal(reread.currentFile, false);
  assert.equal(reread.grantsMutation, false);
});

test("S1 pending, duplicate IDs, unknown effects and archive failure keep original messages", () => {
  const h = exchange(1), c = new WorkingContext(scope);
  assert.equal(c.project(h, () => false).history, h);
  h.append(h.getMessagesArray()[2]);
  assert.equal(c.project(h, () => true).reason, "pending_or_ambiguous_exchange");
  const full = exchange(1), bounded = new WorkingContext(scope, { maxBytes: 100 });
  assert.equal(bounded.project(full, () => true).history, full);
  assert.equal(bounded.project(full, () => true).archiveFailed, true);
});

test("S1 archive range/version/scope/hash/TTL/quota failures are explicit", () => {
  let now = 100;
  const a = new EvidenceArchive(scope, { now: () => now, ttlMs: 50, maxRecords: 1 });
  const r = a.put("hello", { callKey: "one" }).record;
  assert.equal(a.read("../secret", r.archivedBodyHash).errorCode, "invalid_evidence_id");
  assert.equal(a.read(r.evidenceId, "guessed").errorCode, "version_mismatch");
  assert.equal(a.read(r.evidenceId, r.archivedBodyHash, -1).errorCode, "invalid_range");
  assert.equal(a.put("other", {}, { liveRefs: new Set([r.evidenceId]) }).errorCode, "quota");
  assert.equal(new EvidenceArchive({ ...scope, conversation: "other" }).load(r.evidenceId).ok, false);
  now = 151; assert.equal(a.load(r.evidenceId).errorCode, "expired");
  now = 100; r.body = "tampered"; assert.equal(a.load(r.evidenceId).errorCode, "integrity_failed");
});

test("P2 expired unreferenced records are reclaimed while live records remain protected", () => {
  let now = 100;
  const archive = new EvidenceArchive(scope, { now: () => now, ttlMs: 50, maxRecords: 2 });
  const dead = archive.put("dead", { callKey: "dead" }).record;
  const live = archive.put("live", { callKey: "live" }).record;
  now = 151;
  const inserted = archive.put("new", { callKey: "new" }, { liveRefs: new Set([live.evidenceId]) });
  assert.equal(inserted.ok, true);
  assert.equal(archive.load(dead.evidenceId).errorCode, "unavailable");
  assert.notEqual(archive.load(live.evidenceId).errorCode, "unavailable");
});

test("S1 a Git transport cursor is projected for the current round but never archived", () => {
  const h = Chat.from([{ role: "user", content: "next page" }]);
  h.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
    type: "function", id: "cursor-call", name: "git_list", arguments: { cursor: "prior" },
  } }] }));
  h.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "cursor-call",
    content: JSON.stringify({ ok: true, rows: ["x".repeat(3000)], nextCursor: "opaque-server-token", hasMore: true }) }] }));
  const c = new WorkingContext(scope), p = c.project(h, () => true, {}, 512);
  const value = JSON.parse(p.history.getMessagesArray().at(-1).getToolCallResults()[0].content);
  assert.deepEqual(value.transport, { nextCursor: "opaque-server-token", durable: false, guessed: false });
  const archived = c.archive.read(value.archiveRef.evidenceId, value.archiveRef.version, 0, 4096);
  assert.doesNotMatch(archived.content, /opaque-server-token/);
  assert.equal(archived.redacted, true);
  assert.equal(c.commit(h, p.history, { exact: true, inputTokens: 1000, remainingTokens: 10000 },
    hash(serialize(h)), "model"), false);
});

test("S1 durable archive restarts, canonicalizes a trusted root alias and rejects internal links", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "hybrid-archive-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const a = new EvidenceArchive(scope, { root, durable: true });
  const original = JSON.stringify({ rows: Array.from({ length: 650 }, (_, i) => i), name: "ReceiptActor",
    fileVersionReceipt: "fvr1_secret", apiKey: "secret-value", nextCursor: "transport" });
  const r = a.put(original).record;
  const b = new EvidenceArchive(scope, { root, durable: true });
  const read = b.read(r.evidenceId, r.archivedBodyHash, 0, 8192);
  assert.equal(JSON.parse(read.content).rows.length, 650);
  assert.match(read.content, /ReceiptActor/); assert.doesNotMatch(read.content, /secret-value|fvr1_secret|"transport"/);
  assert.equal(read.redacted, true);
  assert.equal(new EvidenceArchive({ ...scope, repository: "other" }, { root, durable: true }).load(r.evidenceId).ok, false);
  const alias = path.join(path.dirname(root), `${path.basename(root)}-alias`);
  fs.symlinkSync(root, alias, "junction");
  t.after(() => fs.rmSync(alias, { recursive: true, force: true }));
  const aliased = new EvidenceArchive(scope, { root: alias, durable: true });
  assert.equal(aliased.put("root alias is canonicalized", { callKey: "alias" }).ok, true);
  const internal = path.join(root, "internal-link"); fs.symlinkSync(a.directory, internal, "junction");
  assert.throws(() => require("../src/evidence-archive.js").safeDirectory(internal), /unsafe_directory/u);
});

test("P2 projection has a whole-result cost gate and short observations are not archived", () => {
  const history = Chat.from([{ role: "user", content: "inspect" }]);
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
    type: "function", id: "small", name: "git_status", arguments: {},
  } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "small",
    content: JSON.stringify({ ok: true, status: "clean", body: "tiny" }) }] }));
  const context = new WorkingContext(scope);
  const projected = context.project(history, () => true, {}, 1);
  assert.equal(projected.changed, false);
  assert.equal(context.refs.size, 0);
  assert.equal(context.archive.stats.captured, 0);
});

test("P2 evidence reads bound metadata and distinguish EOF from complete raw coverage", () => {
  const archive = new EvidenceArchive(scope);
  const record = archive.put("0123456789".repeat(200), {
    callKey: "large-query", providerRequestId: "read-1", toolName: "git_changed_files",
    semanticQuery: { query: "q".repeat(100000) }, sourceIdentity: { repositoryIdentity: "repo" },
  }).record;
  const tail = archive.read(record.evidenceId, record.archivedBodyHash, 1900, 1024);
  assert.equal(tail.hasMore, false);
  assert.equal(tail.reachedEnd, true);
  assert.equal(tail.fullRawProvided, false);
  assert.equal(tail.coverageState, "partial");
  assert.ok(JSON.stringify(tail).length < 2048);
  assert.equal(Object.prototype.hasOwnProperty.call(tail, "metadata"), false);
});

test("P2 provider request IDs may repeat in separate completed causal batches", () => {
  const history = Chat.from([{ role: "user", content: "inspect twice" }]);
  for (let round = 0; round < 2; round++) {
    history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
      type: "function", id: "provider-reused", name: "git_list", arguments: { page: round },
    } }] }));
    history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "provider-reused",
      content: JSON.stringify({ ok: true, status: "complete", rows: [String(round).repeat(3000)] }) }] }));
  }
  const context = new WorkingContext(scope);
  const projected = context.project(history, () => true, { executionId: "exec" }, 512);
  assert.equal(projected.changed, true);
  assert.equal(projected.history.getMessagesArray().flatMap(message => message.getToolCallResults()).length, 2);
});

test("S2 atomic window restoration rejects edits, stale candidates, fork and missing archive", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "hybrid-window-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const c = new WorkingContext(scope, { root, durable: true }), h = exchange(1);
  const p = c.project(h, () => true);
  const measurement = { exact: true, inputTokens: 9000, remainingTokens: 10000 };
  assert.equal(c.commit(h, p.history, measurement, "stale", "model-template"), false);
  assert.equal(c.commit(h, p.history, measurement, hash(serialize(h)), "model-template"), true);
  const next = Chat.from(h); next.append("user", "continue");
  const restarted = new WorkingContext(scope, { root, durable: true });
  const restored = restarted.restore(next);
  assert.equal(restored.reason, "restored_remeasure_required");
  assert.equal(restored.history.getMessagesArray().at(-1).getText(), "continue");
  const edited = Chat.from([{ role: "user", content: "edited" }]);
  assert.equal(restarted.restore(edited).history, edited);
  assert.equal(new WorkingContext({ ...scope, lineage: "fork-2" }, { root, durable: true }).restore(next).history, next);
  const manifestFile = path.join(c.archive.directory, "window.json");
  fs.writeFileSync(manifestFile, "half-written");
  assert.equal(restarted.restore(next).history, next);
});

test("S3 semantic note validation rejects invented refs and execution fact schema", () => {
  const refs = new Set(["tool-call:call_1"]);
  const good = { decisions: [{ statement: "Prefer smaller pages", rationale: "Response pressure", refs: [...refs] }] };
  const note = validateSemanticNote(JSON.stringify(good), refs, 1, null);
  assert.equal(note.source, "assistant_summary"); assert.equal(note.semanticTruthVerified, false);
  assert.equal(validateSemanticNote('{"buildSuccess":true}', refs, 1, null), null);
  assert.equal(validateSemanticNote(JSON.stringify(good), new Set(), 1, null), null);
  assert.equal(validateSemanticNote("partial {", refs, 1, null), null);
});

test("S4 ten atomic generations keep the latest constraint and decision", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "hybrid-generations-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = Chat.from([{ role: "system", content: "constraint: never replay a mutation" },
    { role: "user", content: "continue" }]);
  const candidate = Chat.from([{ role: "system", content: "constraint: never replay a mutation" },
    { role: "assistant", content: "bounded facts" }, { role: "user", content: "continue" }]);
  const c = new WorkingContext(scope, { root, durable: true });
  for (let generation = 1; generation <= 10; generation++) {
    c.note = { source: "assistant_summary", semanticTruthVerified: false, summaryGeneration: generation,
      decisions: [{ statement: `decision-${generation}` }] };
    assert.equal(c.commit(source, candidate, { exact: true, inputTokens: 1000, remainingTokens: 10000 },
      hash(serialize(source)), "same-model-template"), true);
  }
  const restarted = new WorkingContext(scope, { root, durable: true });
  const restored = restarted.restore(source);
  assert.equal(restored.reason, "restored_remeasure_required");
  assert.equal(restarted.generation, 10);
  assert.equal(restarted.note.decisions[0].statement, "decision-10");
  assert.match(restored.history.toString(), /never replay a mutation/);
});

test("S3 a semantic event attempt survives window restore and cannot be implicitly retried", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "hybrid-summary-event-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const source = Chat.from([{ role: "user", content: "keep this request" }]);
  const first = new WorkingContext(scope, { root, durable: true, lineage: "lineage-1" });
  first.lastSummaryInput = "event-fingerprint-1";
  assert.equal(first.commit(source, source, { exact: true, inputTokens: 100, remainingTokens: 1000 },
    hash(serialize(source)), "model-a"), true);
  const next = new WorkingContext(scope, {
    root, durable: true, lineage: "lineage-2", parentLineage: "lineage-1",
  });
  assert.equal(next.restore(source).reason, "restored_remeasure_required");
  assert.equal(next.lastSummaryInput, "event-fingerprint-1");
});

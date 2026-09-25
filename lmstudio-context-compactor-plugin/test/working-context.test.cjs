"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { EvidenceArchive } = require("../src/evidence-archive.js");
const { WorkingContext, serialize, hash, validateSemanticNote, exchangeIndex } = require("../src/working-context.js");
const { parseToolResult, stateMemory } = require("../src/compaction-tool-memory.js");
const { buildCheckpoint } = require("../src/direct-compaction-core.js");
const scope = { conversation: "chat-1", lineage: "fork-1", workspace: "workspace-1", repository: "worktree-1" };

test("terminal seal is a remeasured handoff, cannot reopen an execution, and fails atomically", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "sealed-window-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const history = exchange(1), options = { root, durable: true, lineage: "first" };
  const context = new WorkingContext(scope, options);
  assert.equal(context.seal(history, history), false);
  assert.equal(context.lastCommitReason, "no_committed_window_in_execution");
  context.captureReturned(history, () => true);
  assert.equal(context.commit(history, history, { exact: true, remainingTokens: 5000 }, hash(serialize(history)), "model"), true);
  const target = path.join(context.archive.directory, context.windowFile("first"));
  const before = fs.readFileSync(target, "utf8");
  const edited = Chat.from(history); edited.getMessagesArray()[0].replaceText("edited system");
  assert.equal(context.seal(edited, history), false);
  assert.equal(context.lastCommitReason, "source_prefix_changed");
  assert.equal(fs.readFileSync(target, "utf8"), before);
  const omitted = Chat.from([{ role: "user", content: "missing returned fact" }]);
  assert.equal(context.seal(history, omitted), false);
  assert.equal(context.lastCommitReason, "pending_evidence_omitted");
  assert.equal(fs.readFileSync(target, "utf8"), before);
  const visible = Chat.from(history); visible.append("assistant", "incomplete user-visible answer");
  assert.equal(context.seal(visible, history), true);
  const manifest = JSON.parse(fs.readFileSync(target, "utf8"));
  assert.equal(manifest.measurement, null);
  assert.equal(manifest.measurementSubject, "terminal_unmeasured");
  assert.equal(manifest.remeasureRequired, true);
  assert.equal(context.seal(visible, history), false);
  assert.equal(context.commit(visible, history, { exact: true, remainingTokens: 5000 }, hash(serialize(visible)), "m"), false);
  assert.equal(context.lastCommitReason, "execution_closed");
  const next = new WorkingContext(scope, { root, durable: true, lineage: "next", parentLineage: "first" });
  visible.append("user", "report now");
  const restored = next.restore(visible);
  assert.equal(restored.reason, "restored_remeasure_required");
  assert.doesNotMatch(restored.history.toString(), /incomplete user-visible answer/);
  assert.match(restored.history.toString(), /report now/);
  assert.equal(next.returnedRefs.size, 1, "shutdown is not a completed consumer");
  assert.equal(next.seal(visible, restored.history), false, "restoration alone never authorizes a new seal");
});

test("restore distinguishes corrupt windows, edited prefixes, scopes, lineage and unavailable evidence", () => {
  let now = 100;
  const context = new WorkingContext(scope, { now: () => now, ttlMs: 50 });
  const history = exchange(1);
  context.captureReturned(history, () => true);
  assert.equal(context.commit(history, history, { exact: true, remainingTokens: 5000 }, hash(serialize(history)), "m"), true);
  const valid = structuredClone(context.manifest);
  const change = (patch, reason, rehash = true) => {
    const { digest, ...body } = { ...valid, ...patch };
    context.manifest = { ...body, digest: rehash ? hash(body) : "invalid" };
    assert.equal(context.restore(history).reason, reason);
    context.manifest = valid;
  };
  change({}, "manifest_integrity_failed", false);
  change({ schemaVersion: 10 }, "manifest_schema_mismatch");
  change({ scope: "other" }, "scope_mismatch");
  context.parentLineage = "other";
  assert.equal(context.restore(history).reason, "lineage_mismatch");
  context.parentLineage = null;
  change({ sourceLength: history.length + 1 }, "source_length_mismatch");
  change({ sourcePrefixHash: "different" }, "source_prefix_mismatch");
  now = 151;
  assert.equal(context.restore(history).reason, "archive_refs_unavailable");
  now = 100;
  const record = context.archive.records.values().next().value; record.body += "corruption";
  assert.equal(context.restore(history).reason, "archive_refs_unavailable");
});

test("host-split request batches archive every result and still reject partial or duplicate exchanges", () => {
  const sdk = exchange(2), split = Chat.empty();
  for (const m of sdk.getMessagesArray()) {
    if (m.getToolCallRequests().length) for (const request of m.getToolCallRequests())
      split.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: request }] }));
    else split.append(m);
  }
  assert.equal(exchangeIndex(split).ambiguous, false);
  assert.equal(exchangeIndex(split).matches.size, 2);
  const context = new WorkingContext(scope);
  assert.equal(context.project(split, () => true, {}, 512).changed, true);
  assert.equal(context.archive.entries().length, 2);
  const duplicate = Chat.from(split); duplicate.append(split.getMessagesArray()[2]);
  assert.equal(exchangeIndex(duplicate).ambiguous, true);
  const interleaved = Chat.empty(), messages = split.getMessagesArray();
  for (const i of [0, 1, 2, 3, 4, 2, 5]) interleaved.append(messages[i]);
  assert.equal(exchangeIndex(interleaved).ambiguous, true);
  const acrossTurn = Chat.empty();
  for (const i of [0, 1, 2, 1, 3, 4, 5]) acrossTurn.append(messages[i]);
  assert.equal(exchangeIndex(acrossTurn).ambiguous, true, "a user boundary cannot extend an unfinished batch");
});

test("pending raw identity survives persistence sanitization, cancellation and restart", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "pending-window-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const h = Chat.from([{ role: "user", content: "Remember the early fact." }]);
  h.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
    type: "function", id: "r", name: "read_file", arguments: { path: "a.txt" } } }] }));
  h.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "r",
    content: JSON.stringify({ ok: true, kind: "workspace_file_observation", path: "a.txt", hash: "source-hash",
      startLine: 1, endLine: 16, receipt: "live-receipt", nextCursor: "live-cursor", text: "EARLY_FACT " + "x".repeat(4000) }) }] }));
  const c = new WorkingContext(scope, { root, durable: true });
  c.captureReturned(h, () => true);
  const id = [...c.returnedRefs.keys()][0];
  assert.equal(c.commit(h, h, { exact: true, inputTokens: 2000, remainingTokens: 5000 }, hash(serialize(h)), "model"), true, c.lastCommitReason);
  assert.ok(h.toString().includes("live-cursor"), "live capability remains live");
  assert.ok(!JSON.stringify(c.manifest).includes("live-cursor"));
  assert.ok(!JSON.stringify(c.manifest).includes("live-receipt"));
  assert.deepEqual(c.archive.catalog().entries[0].sourceRange, [1, 16]);
  const restarted = new WorkingContext(scope, { root, durable: true });
  const restored = restarted.restore(h);
  assert.equal(restored.reason, "restored_remeasure_required");
  assert.equal(restarted.returnedRefs.has(id), true, "commit is not consumer completion");
  assert.throws(() => restarted.captureExposure(Chat.empty(), "cancel-retry", false), /PENDING_EVIDENCE_OMITTED/);
  restarted.captureExposure(restored.history, "retry", false);
  const projected = restarted.project(restored.history, () => true, {}, 256);
  assert.equal(projected.changed, true);
  assert.equal(restarted.archive.entries().length, 1, "sanitized replay retains the original archive identity");
  restarted.captureExposure(projected.history, "retry", true);
  assert.equal(restarted.returnedRefs.size, 0);
  assert.equal(restarted.commit(h, projected.history, { exact: true, remainingTokens: 5000 }, hash(serialize(h)), "model"), true);
  const again = new WorkingContext(scope, { root, durable: true });
  assert.equal(again.restore(h).reason, "restored_remeasure_required");
  assert.equal(again.returnedRefs.size, 0);
  assert.equal(again.archive.read(id, again.archive.load(id).record.archivedBodyHash, 0, 8192).content.includes("EARLY_FACT"), true);
});

test("a returned historical page cannot be omitted or consumed by an older page with the same archive ID", () => {
  const c = new WorkingContext(scope), h = Chat.from([{ role: "user", content: "Recover the early page" }]);
  const saved = c.archive.put("EARLY_FACT").record;
  function append(chat, id, startOffset) {
    chat.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
      type: "function", id, name: "evidence_first_read_context", arguments: { evidenceId: saved.evidenceId, startOffset } } }] }));
    chat.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
      content: JSON.stringify(c.archive.read(saved.evidenceId, saved.archivedBodyHash, startOffset, 4096)) }] }));
  }
  append(h, "old", 4); c.captureReturned(h, () => true); c.captureExposure(h, "old", true);
  const next = Chat.from(h); append(next, "new", 0); c.captureReturned(next, () => true);
  assert.equal(c.pendingHistoricalResults.size, 1);
  assert.throws(() => c.captureExposure(h, "missing-new-page", false), /PENDING_EVIDENCE_OMITTED/);
  assert.equal(c.commit(next, h, { exact: true, remainingTokens: 1000 }, hash(serialize(next)), "m"), false);
  assert.equal(c.commit(next, next, { exact: true, remainingTokens: 1000 }, hash(serialize(next)), "m"), true);
  assert.equal(c.restore(next).reason, "restored_remeasure_required");
  assert.equal(c.pendingHistoricalResults.size, 1);
  c.captureExposure(next, "retry", false); c.captureExposure(next, "retry", true);
  c.captureReturned(next, () => true);
  assert.equal(c.pendingHistoricalResults.size, 0, "duplicate callback does not reopen the consumer");
  assert.equal(c.archive.entries().length, 1, "historical pages are not recursively archived");
});

test("archive read never claims success for a nonadvancing body page", () => {
  const archive = new EvidenceArchive(scope);
  const r = archive.put("x".repeat(10000), { sourceIdentity: { path: "long-name".repeat(70) } }).record;
  for (let maxChars = 512; maxChars <= 2200; maxChars += 7) {
    const value = archive.read(r.evidenceId, r.archivedBodyHash, 0, maxChars);
    if (value.ok) { assert.ok(value.returnedRange[1] > 0); assert.ok(JSON.stringify(value).length <= maxChars); }
    else assert.equal(value.errorCode, "response_budget_too_small");
  }
});
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
    hash(serialize(h)), "model"), true);
  assert.doesNotMatch(JSON.stringify(c.manifest), /opaque-server-token/);
  assert.doesNotMatch(c.restore(h).history.toString(), /opaque-server-token/);
  assert.match(p.history.toString(), /opaque-server-token/, "live transport is not changed by persistence");
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

test("regression: a 32KB Git diff keeps a body-first bounded view for its first consumer", () => {
  const history = Chat.from([{ role: "user", content: "Inspect the complete Git diff body." }]);
  const request = { id: "large-diff-first-consumer", type: "function", name: "git_diff_file",
    arguments: { comparison: "range", base: "a".repeat(40), head: "b".repeat(40), path: "Source/Large.cpp" } };
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: request }] }));
  const payload = {
    schemaVersion: 1, kind: "git_observation", status: "observed", ok: true, action: "diff_file",
    comparison: "range", base: request.arguments.base, head: request.arguments.head,
    currentHead: request.arguments.head, repositoryIdentity: "repository-identity-".repeat(8),
    workspaceIdentity: "workspace-identity-".repeat(8), projectIdentity: "C:/Synthetic/Game.uproject",
    canonicalProjectRoot: "C:/Synthetic", queryPathBase: "workspace_root",
    requestedPaths: Array.from({ length: 8 }, (_, index) => `Source/VeryLongPath/${index}-${"x".repeat(180)}.cpp`),
    requestedPathsCount: 8, requestedPathsOmitted: 0, resolvedRepositoryPaths: [],
    resolvedRepositoryPathsCount: 1, resolvedRepositoryPathsOmitted: 0,
    observedAt: "2026-09-22T10:00:00.000Z",
    sourceConsistency: "bounded_collection_not_atomic_filesystem_snapshot",
    path: request.arguments.path, pageStart: 1, pageEnd: 900, pageHasMore: false,
    sourceResultComplete: true, returnedRange: [1, 900],
    text: `@@ -1,3 +1,900 @@\nDIFF_BODY_SENTINEL\n${"int changed_line;\\n".repeat(1800)}`,
  };
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult",
    toolCallId: request.id, content: JSON.stringify(payload) }] }));
  assert.ok(JSON.stringify(payload).length > 32768);
  const context = new WorkingContext(scope);
  const projected = context.project(history, () => true,
    { executionId: "failure-fixture", preserveUnconsumedRawGitMaxChars: 0 }, 512);
  const value = JSON.parse(projected.history.getMessagesArray().at(-1).getToolCallResults()[0].content);
  assert.equal(projected.changed, true);
  assert.equal(value.kind, "archived_tool_result_projection");
  assert.match(value.excerpt, /DIFF_BODY_SENTINEL/u);
  assert.equal(value.bodyField, "text");
  assert.equal(value.viewMode, "body_first_bounded");
  assert.equal(value.fullRawProvided, false);
  assert.ok(value.bodyTotalChars > value.excerpt.length);
  assert.equal(value.bodyComplete, false);
  assert.deepEqual(value.projectedBodyRanges[0], [0, value.excerpt.length]);
  assert.ok(value.archiveRef?.evidenceId);
});

test("regression: the 8192-character first-consumer boundary is exact for direct and MCP envelopes", () => {
  const contentAt = (target, envelope) => {
    let body = "x".repeat(target);
    let content = "";
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const payload = { ok: true, kind: "git_observation", status: "observed", action: "diff_file",
        text: body, path: "Assets/Boundary.cs", pageHasMore: false };
      content = envelope === "direct" ? JSON.stringify(payload)
        : JSON.stringify({ content: [{ type: "text", text: JSON.stringify(payload) }] });
      const delta = target - content.length;
      if (delta === 0) return content;
      body = delta > 0 ? body + "x".repeat(delta) : body.slice(0, Math.max(0, body.length + delta));
    }
    assert.equal(content.length, target);
    return content;
  };
  for (const envelope of ["direct", "mcp"]) {
    for (const target of [8191, 8192, 8193]) {
      const history = Chat.from([{ role: "user", content: `boundary ${envelope} ${target}` }]);
      const request = { type: "function", id: `${envelope}-${target}`, name: "git_diff_file",
        arguments: { comparison: "range", base: "base", head: "head", path: "Assets/Boundary.cs" } };
      const content = contentAt(target, envelope);
      assert.equal(content.length, target);
      history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
        toolCallRequest: request }] }));
      history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult",
        toolCallId: request.id, content }] }));
      const context = new WorkingContext({ ...scope, lineage: `${envelope}-${target}` });
      const projected = context.project(history, () => true,
        { executionId: "boundary", preserveUnconsumedRawGitMaxChars: 8192 }, 512);
      const result = projected.history.getMessagesArray().at(-1).getToolCallResults()[0];
      if (target <= 8192) {
        assert.equal(projected.changed, false);
        assert.equal(result.content, content);
      } else {
        const value = JSON.parse(result.content);
        assert.equal(projected.changed, true);
        assert.equal(value.viewMode, "body_first_bounded");
        assert.equal(value.bodyField, "text");
        assert.equal(value.originalEnvelopeChars, 8193);
        assert.equal(value.originalEnvelopeBytes, Buffer.byteLength(content));
        assert.equal(value.bodyRangeUnit, "utf16_code_units");
        assert.ok(value.projectedBodyRanges[0][1] > 0);
        assert.ok(value.omittedBodyRanges[0][1] > value.projectedBodyRanges[0][1]);
      }
    }
  }
});

test("Git source page continuation survives archive EOF as a separate contract", () => {
  const history = Chat.from([{ role: "user", content: "Inspect page one." }]);
  const request = { type: "function", id: "page-25-of-232", name: "git_changed_files",
    arguments: { comparison: "range", base: "base", head: "head", limit: 25 } };
  const items = Array.from({ length: 25 }, (_, index) => ({ status: "modified",
    path: `Assets/Long-Path-${index}-${"x".repeat(80)}.cs` }));
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: request }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult",
    toolCallId: request.id, content: JSON.stringify({ ok: true, kind: "git_observation", status: "observed",
      action: "changed_files", comparison: "range", base: "base", head: "head",
      pageStart: 1, pageEnd: 25, pageHasMore: true, sourceResultComplete: true,
      returnedCount: 25, total: 232, items }) }] }));
  const context = new WorkingContext({ ...scope, lineage: "source-vs-archive" });
  const projected = context.project(history, () => true, { executionId: "exec", roundIndex: 1 }, 512);
  assert.equal(projected.changed, true);
  const projection = JSON.parse(projected.history.getMessagesArray().at(-1).getToolCallResults()[0].content);
  assert.equal(projection.sourcePageHasMore, true);
  assert.equal(projection.sourceTotal, 232);
  const read = context.archive.read(projection.archiveRef.evidenceId, projection.archiveRef.version, 0, 8192);
  assert.equal(read.archiveReachedEnd, true);
  assert.equal(read.archiveHasMore, false);
  assert.equal(read.nextOffset, null);
  assert.equal(read.representation, "sanitized_archived_tool_envelope");
  assert.equal(read.sourcePageHasMore, true);
  assert.equal(read.sourceReturnedCount, 25);
  assert.equal(read.sourceTotal, 232);
  const parsed = parseToolResult(JSON.stringify(read));
  assert.equal(parsed.historicalEvidence.sourcePageHasMore, true);
  assert.equal(parsed.historicalEvidence.pageHasMore, true);
  assert.equal(parsed.historicalEvidence.archiveReachedEnd, true);
  assert.equal(parsed.historicalEvidence.archiveHasMore, false);
  const memory = stateMemory([parsed]);
  assert.equal(memory.historicalEvidence[0].sourcePageHasMore, true);
  assert.equal(memory.historicalEvidence[0].archiveReachedEnd, true);
  assert.equal(memory.historicalEvidence[0].archiveHasMore, false);

  const chunkOne = context.archive.read(projection.archiveRef.evidenceId, projection.archiveRef.version, 0, 2048);
  const chunkTwo = context.archive.read(projection.archiveRef.evidenceId, projection.archiveRef.version,
    chunkOne.nextOffset, 2048);
  assert.equal(chunkOne.archiveReachedEnd, false);
  assert.equal(chunkOne.archiveHasMore, true);
  assert.equal(chunkTwo.returnedRange[0], chunkOne.nextOffset);
  assert.equal(chunkTwo.archiveReachedEnd, false);
  assert.equal(chunkTwo.sourcePageHasMore, true);
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

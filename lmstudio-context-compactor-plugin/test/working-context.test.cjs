"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { EvidenceArchive } = require("../src/evidence-archive.js");
const { WorkingContext, serialize, hash, validateSemanticNote } = require("../src/working-context.js");
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
  assert.equal(a.put("other").errorCode, "quota");
  assert.equal(new EvidenceArchive({ ...scope, conversation: "other" }).load(r.evidenceId).ok, false);
  now = 151; assert.equal(a.load(r.evidenceId).errorCode, "expired");
  now = 100; r.body = "tampered"; assert.equal(a.load(r.evidenceId).errorCode, "integrity_failed");
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

test("S1 durable archive restarts, rejects symlink and removes capabilities without truncating arrays", t => {
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
  const link = path.join(root, "linked"); fs.symlinkSync(a.directory, link, "junction");
  const linked = new EvidenceArchive(scope, { root: link, durable: true });
  assert.equal(linked.put("no write").ok, false);
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

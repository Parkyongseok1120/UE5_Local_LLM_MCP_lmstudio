"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), os = require("node:os");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { AttachmentBoundary } = require("../dist/attachment-boundary");
const { selectMeasuredCandidate, boundPastReasoning, REASONING_SEPARATOR } = require("../dist/context-budget");
const { parseToolResult, serializeToolOutcomeRecords } = require("../dist/compaction-tool-memory");
const notes = require("../dist/continuity-model-notes");

test("signed document boundary survives restart, keeps images and rejects changed history", async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "attachment-boundary-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const boundary = new AttachmentBoundary(directory), client = { files: {} };
  const previous = Chat.from([{ role: "user", content: "Earlier request" }]);
  const message = ChatMessage.from({ role: "user", content: [
    { type: "text", text: "Review C in this document." },
    { type: "file", identifier: "doc-1", fileType: "application/pdf", name: "guide.pdf", sizeBytes: 100 },
    { type: "file", identifier: "image-1", fileType: "image", name: "image.png", sizeBytes: 50 },
  ] });
  const captured = await boundary.capture(message, previous, client);
  assert.equal(captured.getFiles(client).length, 1);
  const input = Chat.from(previous); input.append(captured);
  const restored = new AttachmentBoundary(directory).restore(input, client);
  assert.equal(restored.modelHistory.getMessagesArray().at(-1).getText(), "Review C in this document.");
  assert.equal(restored.modelHistory.getAllFiles(client).length, 1);
  assert.equal(restored.attachmentHistory.getAllFiles(client).length, 2);
  const unrelated = Chat.from([{ role: "user", content: "Other conversation" }]); unrelated.append(captured);
  assert.throws(() => boundary.restore(unrelated, client), /does not match/);
  const changed = ChatMessage.from(captured); changed.replaceText("tampered " + captured.getText());
  const tampered = Chat.from(previous); tampered.append(changed);
  assert.throws(() => boundary.restore(tampered, client), /does not match/);
  assert.equal(message.getText(), "Review C in this document.");
  const literal = Chat.from([{ role: "user", content: "Explain this literal string\n<!-- workspace-attachments-v1:hello" }]);
  assert.equal(boundary.restore(literal, client).modelHistory.getMessagesArray()[0].getText(), literal.getMessagesArray()[0].getText());
  const unissued = Chat.from([{ role: "user", content: `Example\n<!-- workspace-attachments-v1:e30.${"0".repeat(64)} -->` }]);
  assert.equal(boundary.restore(unissued, client).modelHistory.getMessagesArray()[0].getText(), unissued.getMessagesArray()[0].getText());
});

test("review claims fold Windows path aliases and are invalidated by deletion, even with the old hash", () => {
  for (const update of [{ path: "Assets/a.cs", hash: "b".repeat(64) },
    { path: "project://Assets/A.cs", hash: "a".repeat(64), operation: "moved_to_trash" }]) {
    const history = Chat.from([{ role: "user", content: "Review" }]);
    const add = (id, data) => {
      history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: { id, type: "function", name: "read_file", arguments: {} } }] }));
      history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id, content: JSON.stringify({
        canonicalProject: "C:/Projects/Demo/Demo.uproject", path: "Assets/A.cs", hash: "a".repeat(64), ...data }) }] }));
    };
    add("r1", {});
    const note = notes.attachScope({ reviewClaims: [{ path: "Assets/A.cs", sha256: "a".repeat(64), reviewScope: "C", statement: "Reviewed", refs: ["tool-call:r1"] }] }, notes.objectiveFingerprint(history.getMessagesArray()), history.getMessagesArray());
    assert.equal(note.reviewClaims.length, 1);
    add("r2", update);
    assert.equal(notes.reconcileStoredNote(note, history.getMessagesArray()).reviewClaims, undefined);
  }
});

test("budget selection measures nonmonotone candidates and never claims an unmeasured fit", async () => {
  const measured = [];
  const selected = await selectMeasuredCandidate(32, 100, cap => cap, async cap => {
    measured.push(cap); return cap === 16 ? 120 : 40;
  });
  assert.equal(selected.candidate, 16); assert.deepEqual(measured, [32, 16]);
  const fallback = await selectMeasuredCandidate(8, 100, cap => cap, async cap => cap === 4 ? 80 : 10);
  assert.equal(fallback.candidate, 4); assert.equal(fallback.remainingTokensAfter, 80);
});

test("old reasoning loses only structural reasoning, preserving newest reasoning and tool pairing", async () => {
  const history = Chat.from([{ role: "user", content: "Review" }]);
  for (let i = 0; i < 2; i++) {
    history.append(ChatMessage.from({ role: "assistant", content: [
      { type: "text", text: `REASON_${i}${REASONING_SEPARATOR}ANSWER_${i}` },
      { type: "toolCallRequest", toolCallRequest: { id: `r${i}`, type: "function", name: "read_file", arguments: {} } },
    ] }));
    history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: `r${i}`, content: "source evidence" }] }));
  }
  const result = await boundPastReasoning(history, 10, async () => 20);
  assert.equal(result.getMessagesArray()[1].getText(), "ANSWER_0");
  assert.match(result.getMessagesArray()[3].getText(), /REASON_1/);
  assert.deepEqual(result.getMessagesArray().flatMap(m => m.getToolCallRequests()), history.getMessagesArray().flatMap(m => m.getToolCallRequests()));
  assert.deepEqual(result.getMessagesArray().flatMap(m => m.getToolCallResults()), history.getMessagesArray().flatMap(m => m.getToolCallResults()));
});

test("Git memory preserves comparison identity but does not invent source coverage", () => {
  const parsed = parseToolResult(JSON.stringify({ kind: "git_observation", comparison: "range", action: "changed_files",
    base: "a".repeat(40), head: "b".repeat(40), repositoryIdentity: "repo", workspaceIdentity: "workspace",
    items: Array.from({ length: 100 }, (_, i) => ({ path: `Assets/${i}.cs`, status: "modified" })) }));
  const retained = JSON.parse(serializeToolOutcomeRecords([parsed], { maxToolResultChars: 1200 })[0]);
  assert.equal(retained.gitObservation.base, "a".repeat(40));
  assert.equal(retained.gitObservation.head, "b".repeat(40));
  assert.equal(retained.gitObservation.items.length + retained.gitObservation.omittedItems, 100);
  assert.equal(retained.files, undefined);
  const committed = parseToolResult(JSON.stringify({ kind: "git_observation", action: "read_file", path: "Assets/A.cs",
    canonicalProjectRoot: "C:/Projects/Game", projectIdentity: "f".repeat(64), head: "b".repeat(40), sha256: "a".repeat(64),
    startLine: 1, endLine: 10, totalLines: 10 }));
  assert.equal(committed.path, undefined); assert.equal(committed.sha256, undefined);
  assert.equal(committed.startLine, undefined); assert.equal(committed.gitObservation.sha256, "a".repeat(64));
});

test("review claims require observed version and disappear after a new hash", () => {
  const history = Chat.from([{ role: "user", content: "Review this project" }]);
  const add = (id, hash) => {
    history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: { id, type: "function", name: "read_file", arguments: { path: "Assets/A.cs" } } }] }));
    history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id, content: JSON.stringify({ status: "observed",
      canonicalProjectRoot: path.resolve(os.tmpdir(), "review-project"), projectIdentity: "f".repeat(64), path: "Assets/A.cs", hash }) }] }));
  };
  add("read1", "a".repeat(64));
  const draft = { reviewClaims: [{ path: "Assets/A.cs", sha256: "a".repeat(64), reviewScope: "cancellation", statement: "Reviewed the cancellation path", refs: ["tool-call:read1"] }] };
  const note = notes.attachScope(draft, notes.objectiveFingerprint(history.getMessagesArray()), history.getMessagesArray());
  assert.equal(note.reviewClaims.length, 1); assert.match(notes.renderAssistantNote(note), /never verified semantic completion/);
  add("read2", "b".repeat(64));
  assert.equal(notes.reconcileStoredNote(note, history.getMessagesArray()).reviewClaims, undefined);
  assert.equal(notes.validateDraftNote({ reviewClaims: [{ ...draft.reviewClaims[0], nextTool: "read_file" }] }), null);
});

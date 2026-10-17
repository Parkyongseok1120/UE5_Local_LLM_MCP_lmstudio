"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const notes = require("../src/continuity-model-notes.js");

function footer(value) {
  return `Visible answer.\n<!-- direct-continuity-note-v1 -->\n<continuity-note>\n${JSON.stringify(value)}\n</continuity-note>`;
}

test("the footer parser accepts only bounded descriptive note fields", () => {
  const accepted = notes.splitVisibleAnswer(footer({ decisions: [{ statement: "Use both extensions",
    rationale: "The search returned C# and JSON files" }] }));
  assert.equal(accepted.visibleText, "Visible answer.");
  assert.equal(accepted.note.decisions[0].statement, "Use both extensions");
  for (const field of ["nextTool", "requiredTool", "allowedTools", "phase", "requiredSequence",
    "retryTool", "completionGate", "toolArgs"]) {
    const rejected = notes.splitVisibleAnswer(footer({ decisions: [], [field]: "read_file" }));
    assert.equal(rejected.visibleText, "Visible answer.");
    assert.equal(rejected.note, null);
  }
  assert.equal(notes.splitVisibleAnswer(footer({ decisions: Array.from({ length: 5 }, (_, i) => ({
    statement: `Decision ${i}`, rationale: "Reason",
  })) })).note, null);
  assert.equal(notes.splitVisibleAnswer(footer({ decisions: [{ statement: "A", rationale: "B",
    refs: ["long-runtime-receipt-payload-12345." + "a".repeat(64)] }] })).note, null);
});

test("assistant judgments have stable ids and explicit lifecycle states", () => {
  const opened = notes.validateDraftNote({ openQuestions: [{ question: "Does the range cover the file?" }] });
  assert.match(opened.openQuestions[0].id, /^j_[a-f0-9]{16}$/u);
  assert.equal(opened.openQuestions[0].status, "open");

  const resolved = notes.validateDraftNote({ openQuestions: [{
    id: opened.openQuestions[0].id,
    status: "resolved",
    question: "Does the range cover the file?",
  }] });
  assert.equal(resolved.openQuestions[0].id, opened.openQuestions[0].id);
  assert.equal(resolved.openQuestions[0].status, "resolved");

  const replacement = notes.validateDraftNote({ decisions: [{
    id: "j_replacement",
    status: "open",
    statement: "Use a literal search",
    rationale: "The regex hypothesis was rejected",
    supersedes: [opened.openQuestions[0].id],
  }] });
  assert.deepEqual(replacement.decisions[0].supersedes, [opened.openQuestions[0].id]);
  assert.equal(notes.validateDraftNote({
    decisions: [{ id: "j_duplicate", statement: "A", rationale: "B" }],
    openQuestions: [{ id: "j_duplicate", question: "C?" }],
  }), null);
});

test("malformed reserved footers are removed while quoted code examples remain visible", () => {
  const malformed = "Visible answer.\n<!-- direct-continuity-note-v1 -->\n<continuity-note>\n{broken";
  assert.deepEqual(notes.splitVisibleAnswer(malformed), { visibleText: "Visible answer.", hasFooter: true, note: null });
  const example = `Here is an example:\n\`\`\`text\n${footer({})}\n\`\`\``;
  assert.equal(notes.splitVisibleAnswer(example).hasFooter, false);
  assert.equal(notes.splitVisibleAnswer("Visible answer.").hasFooter, false);
  const empty = notes.splitVisibleAnswer(footer({}));
  assert.deepEqual(empty.note, { decisions: [], rejectedHypotheses: [], openQuestions: [] });
  const oversized = `Visible answer.\n<!-- direct-continuity-note-v1 -->\n<continuity-note>\n${"x".repeat(1801)}\n</continuity-note>`;
  assert.equal(notes.splitVisibleAnswer(oversized).visibleText, "Visible answer.");
  assert.equal(notes.splitVisibleAnswer(oversized).note, null);
});

test("stored notes follow the exact visible transcript and objective", (t) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "continuity-model-notes-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const store = new notes.ContinuityNoteStore(directory);
  const chat = Chat.from([{ role: "user", content: "Inspect the project." },
    { role: "assistant", content: "I found the issue." }]);
  const messages = chat.getMessagesArray();
  const fingerprint = notes.objectiveFingerprint(messages);
  const note = notes.attachScope({ decisions: [{ statement: "Keep the file scope explicit",
    rationale: "Two projects contain the same relative path" }], rejectedHypotheses: [], openQuestions: [] }, fingerprint);
  const key = notes.historyKey(messages, directory);
  assert.equal(store.write(key, note), true);
  assert.deepEqual(store.read(key), note);
  assert.equal(notes.historyKey(Chat.from([{ role: "user", content: "Inspect the other project." },
    { role: "assistant", content: "I found the issue." }]).getMessagesArray(), directory) === key, false);
  assert.notEqual(notes.historyKey(messages, `${directory}-different`), key);
  assert.equal(notes.historyKey(messages, ""), "");

  const before = store.read(key);
  assert.equal(store.write(key, { ...note, decisions: [{ status: "invalid" }] }), false);
  assert.deepEqual(store.read(key), before);
});

test("project scope and refs require unambiguous tool observations", () => {
  const projectA = "C:\\Unity\\A";
  const projectB = "C:\\Unity\\B";
  const identityA = "a".repeat(64);
  const identityB = "b".repeat(64);
  const pair = (id, root, projectIdentity) => [
    { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
      id, type: "function", name: "read_file", arguments: { path: "Assets/Foo.cs" },
    } }] },
    { role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
      content: JSON.stringify({ status: "observed", canonicalProjectRoot: root, projectIdentity,
        path: "Assets/Foo.cs", hash: "c".repeat(64) }) }] },
  ];
  const messages = (...items) => {
    const chat = Chat.empty();
    for (const item of items) chat.append(ChatMessage.from(item));
    return chat.getMessagesArray();
  };
  const historyA = messages({ role: "user", content: "Investigate this Unity project." },
    ...pair("read-a", projectA, identityA));
  const fingerprint = notes.objectiveFingerprint(historyA);
  const draft = { decisions: [{ statement: "Check project scope", rationale: "The file was read",
    refs: ["tool-call:read-a", "tool-call:missing", "tool-result:17"] }] };
  const scoped = notes.attachScope(draft, fingerprint, historyA);
  assert.equal(scoped.scope.projectIdentity, `unity:${identityA}`);
  assert.deepEqual(scoped.decisions[0].refs, ["tool-call:read-a"]);
  assert.deepEqual(notes.reconcileStoredNote(scoped, historyA), scoped);

  const historyB = messages({ role: "user", content: "Investigate this Unity project." },
    ...pair("read-b", projectB, identityB));
  assert.equal(notes.reconcileStoredNote(scoped, historyB), null);
  const mixed = messages({ role: "user", content: "Investigate this Unity project." },
    ...pair("read-a", projectA, identityA), ...pair("read-b", projectB, identityB));
  assert.equal(notes.provenanceFromMessages(mixed).projectState, "mixed");
  assert.equal(notes.reconcileStoredNote(scoped, mixed), null);
  const unscoped = notes.attachScope(draft, fingerprint, mixed);
  assert.equal(unscoped.scope.projectIdentity, undefined);
  assert.match(notes.renderAssistantNote(unscoped), /No single project identity was proven/u);

  const repeated = messages({ role: "user", content: "Investigate this Unity project." },
    ...pair("read-a", projectA, identityA), ...pair("read-a", projectA, identityA));
  assert.equal(notes.attachScope(draft, fingerprint, repeated).decisions.length, 0);

  const enveloped = messages({ role: "user", content: "Investigate this Unity project." },
    { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
      id: "read-envelope", type: "function", name: "read_file", arguments: { path: "Assets/Foo.cs" },
    } }] },
    { role: "tool", content: [{ type: "toolCallResult", toolCallId: "read-envelope",
      content: JSON.stringify([{ type: "text", text: JSON.stringify({ status: "observed",
        canonicalProjectRoot: projectA, projectIdentity: identityA,
        path: "Assets/Foo.cs", hash: "c".repeat(64) }) }]) }] });
  const envelopedNote = notes.attachScope({ decisions: [{ statement: "Use envelope evidence",
    rationale: "The exact tool call completed", refs: ["tool-call:read-envelope"] }] },
  notes.objectiveFingerprint(enveloped), enveloped);
  assert.equal(envelopedNote.scope.projectIdentity, `unity:${identityA}`);
  assert.deepEqual(envelopedNote.decisions[0].refs, ["tool-call:read-envelope"]);

  const archiveRef = "tool-call:archive-0123456789abcdef0123456789abcdef";
  const archived = notes.attachScope({ decisions: [{ statement: "Retain the archived decision",
    rationale: "The bounded archive excerpt supports it", refs: [archiveRef] }] },
  fingerprint, historyA, new Set([archiveRef]));
  assert.deepEqual(archived.decisions[0].refs, [archiveRef]);
  assert.deepEqual(notes.reconcileStoredNote(archived, historyA, new Set([archiveRef])), archived);
  assert.equal(notes.reconcileStoredNote(archived, historyA).decisions.length, 0);
});

function exchange(id, name, args, result) {
  return [
    { role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: { id, type: "function", name, arguments: args } }] },
    { role: "tool", content: [{ type: "toolCallResult", toolCallId: id, content: JSON.stringify(result) }] },
  ];
}
function chatOf(...items) {
  const chat = Chat.empty();
  for (const item of items) chat.append(ChatMessage.from(item));
  return chat.getMessagesArray();
}

test("the model sees the real ids of recent completed calls, bounded to 16, and only unambiguous ones", () => {
  const items = [{ role: "user", content: "Audit the project." }];
  for (let i = 0; i < 20; i += 1) items.push(...exchange(`call_${i}`, i % 2 ? "read_file" : "search_files",
    { path: `Source/File${i}.cpp` }, { ok: true }));
  items.push(...exchange("dup", "read_file", { path: "A.cpp" }, { ok: true }), ...exchange("dup", "read_file", { path: "B.cpp" }, { ok: true }));
  items.push({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: { id: "pending", type: "function", name: "read_file", arguments: { path: "C.cpp" } } }] });
  const text = notes.renderAvailableRefs(chatOf(...items));
  assert.match(text, /^\[Available tool-call refs\]/u);
  const rows = text.split("\n").slice(1);
  assert.ok(rows.length <= 16, String(rows.length));
  assert.ok(rows.every(row => /^- tool-call:[A-Za-z0-9_-]+ \S+/u.test(row)));
  assert.ok(rows.at(-1).includes("tool-call:call_19 read_file path=Source/File19.cpp"), rows.at(-1));
  assert.ok(!text.includes("tool-call:dup"), "ids used twice are ambiguous and never offered");
  assert.ok(!text.includes("tool-call:pending"), "calls without a result are not citable");
  assert.ok(!text.includes("call_0 "), "only the most recent calls are listed");
  assert.equal(notes.renderAvailableRefs(chatOf({ role: "user", content: "hi" })), "");
  assert.ok(notes.renderAvailableRefs(chatOf(...items), 16, 400).length <= 400);
  assert.match(notes.NOTE_INSTRUCTION, /copied exactly from the \[Available tool-call refs\] list/u);
});

test("refs outside the conversation are still rejected, listed ones are accepted", () => {
  const history = chatOf({ role: "user", content: "Audit." }, ...exchange("call_1", "read_file", { path: "A.cpp" }, { ok: true, path: "A.cpp" }));
  const fingerprint = notes.objectiveFingerprint(history);
  const note = notes.attachScope({ decisions: [{ statement: "A", rationale: "B", refs: ["tool-call:call_1", "tool-call:1", "tool-call:9"] }] }, fingerprint, history);
  assert.deepEqual(note.decisions[0].refs, ["tool-call:call_1"]);
});

test("a decision grounded only in a structure validation is rendered as structure_only, not as factual evidence", () => {
  const history = chatOf({ role: "user", content: "Audit." },
    ...exchange("val_1", "evidence_first_validate", { packet: {} }, { ok: true, summary: "structure_valid; facts_not_verified",
      validationScope: "packet_structure_and_record_consistency", factualEvidenceVerified: false, wholeTaskVerified: false }),
    ...exchange("read_1", "read_file", { path: "Source/A.cpp" }, { ok: true, kind: "workspace_file_observation", path: "Source/A.cpp", sha256: "d".repeat(64) }));
  const fingerprint = notes.objectiveFingerprint(history);
  const note = notes.attachScope({ decisions: [
    { statement: "Only validated", rationale: "packet passed", refs: ["tool-call:val_1"] },
    { statement: "Read it", rationale: "source seen", refs: ["tool-call:read_1"] }] }, fingerprint, history);
  const rendered = notes.renderAssistantNote(note, history);
  const payload = JSON.parse(rendered.slice(rendered.indexOf('{"modelNotes"')));
  const states = Object.fromEntries(payload.sourceApplicability.map(entry => [entry.ref, entry.state]));
  assert.equal(states["tool-call:val_1"], "structure_only");
  assert.notEqual(states["tool-call:read_1"], "structure_only");
  assert.match(rendered, /구조 검증만 있음, 사실 근거 아님/u);
  // Flags stripped by an older compaction: the tool name still identifies a structure-only check.
  const stripped = chatOf({ role: "user", content: "Audit." }, ...exchange("val_2", "evidence_first_validate", { packet: {} }, { ok: true }));
  const note2 = notes.attachScope({ decisions: [{ statement: "S", rationale: "R", refs: ["tool-call:val_2"] }] }, notes.objectiveFingerprint(stripped), stripped);
  assert.match(notes.renderAssistantNote(note2, stripped), /"state":"structure_only"/u);
});

test("composeModelHistory offers the real ids in the per-round data slot, not in the cached system prefix", () => {
  const { composeModelHistory } = require("../dist/context-manager");
  const { readConfig } = require("../dist/execution-config");
  const config = readConfig({ getPluginConfig: () => ({ get: () => undefined }) });
  const messages = chatOf({ role: "user", content: "Audit." }, ...exchange("call_77", "read_file", { path: "Source/Z.cpp" }, { ok: true }),
    { role: "user", content: "Continue." });
  const history = Chat.empty();
  messages.forEach(message => history.append(message));
  const composed = composeModelHistory(history, null, config, [], true).history.getMessagesArray();
  const system = composed.filter(message => message.isSystemPrompt()).map(message => message.getText()).join("\n");
  const assistantTexts = composed.filter(message => message.isAssistantMessage()).map(message => message.getText());
  assert.doesNotMatch(system, /tool-call:call_77/u);
  assert.match(system, /\[Available tool-call refs\] list/u);
  assert.ok(assistantTexts.some(text => text.includes("tool-call:call_77 read_file path=Source/Z.cpp")), assistantTexts.join("|"));
  const off = composeModelHistory(history, null, config, [], false).history.getMessagesArray().map(message => message.getText()).join("\n");
  assert.doesNotMatch(off, /Available tool-call refs/u);
});

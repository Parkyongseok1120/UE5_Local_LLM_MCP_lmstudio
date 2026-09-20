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

test("malformed reserved footers are removed while quoted code examples remain visible", () => {
  const malformed = "Visible answer.\n<!-- direct-continuity-note-v1 -->\n<continuity-note>\n{broken";
  assert.deepEqual(notes.splitVisibleAnswer(malformed), { visibleText: "Visible answer.", hasFooter: true, note: null });
  const example = `Here is an example:\n\`\`\`text\n${footer({})}\n\`\`\``;
  assert.equal(notes.splitVisibleAnswer(example).hasFooter, false);
  assert.equal(notes.splitVisibleAnswer("Visible answer.").hasFooter, false);
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
  assert.equal(notes.attachScope(draft, fingerprint, repeated).decisions[0].refs, undefined);

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
});

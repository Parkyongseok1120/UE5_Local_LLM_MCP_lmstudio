"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { WorkingContextBoundary, __test } = require("../dist/working-context-boundary.js");
const chatOf = (...messages) => { const chat = Chat.empty(); messages.forEach(message => chat.append(message)); return chat; };

test("S2 signed user boundaries preserve UI history and distinguish fork lineages", t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "hybrid-boundary-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const boundary = new WorkingContextBoundary(directory), empty = Chat.empty();
  const first = boundary.capture(ChatMessage.create("user", "start"), empty);
  assert.match(first.getText(), /<!-- hybrid-context-v1:/);
  const parentHistory = chatOf(first);
  const parent = boundary.restore(parentHistory);
  assert.equal(parent.modelHistory.getMessagesArray()[0].getText(), "start");
  const left = boundary.capture(ChatMessage.create("user", "continue left"), parentHistory);
  const right = boundary.capture(ChatMessage.create("user", "continue right"), parentHistory);
  const leftScope = boundary.restore(chatOf(first, left)).scope;
  const rightScope = boundary.restore(chatOf(first, right)).scope;
  assert.equal(leftScope.conversation, rightScope.conversation);
  assert.equal(leftScope.parentLineage, parent.scope.lineage);
  assert.equal(rightScope.parentLineage, parent.scope.lineage);
  assert.notEqual(leftScope.lineage, rightScope.lineage);
});

test("S2 signed boundaries reject edited prefix while literal markers remain ordinary text", t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "hybrid-boundary-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const boundary = new WorkingContextBoundary(directory);
  const signed = boundary.capture(ChatMessage.create("user", "original"), Chat.empty());
  const raw = signed.getText(), marker = raw.indexOf(__test.MARKER);
  const edited = ChatMessage.from(signed); edited.replaceText(`edited${raw.slice(marker)}`);
  assert.throws(() => boundary.restore(chatOf(edited)), /does not match/);
  const literal = ChatMessage.create("user", "example\n<!-- hybrid-context-v1:not-issued.deadbeef -->");
  const restored = boundary.restore(chatOf(literal));
  assert.equal(restored.scope, null);
  assert.equal(restored.modelHistory.getMessagesArray()[0].getText(), literal.getText());
});

test("S2 lineage survives an attachment marker that is validated by its own boundary", t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "hybrid-boundary-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const boundary = new WorkingContextBoundary(directory);
  const first = boundary.capture(ChatMessage.create("user", "with document"), Chat.empty());
  const withAttachment = ChatMessage.from(first);
  withAttachment.replaceText(`${first.getText()}\n<!-- workspace-attachments-v1:opaque.0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef -->`);
  const previous = chatOf(withAttachment);
  const next = boundary.capture(ChatMessage.create("user", "continue"), previous);
  const normalizedPrevious = chatOf(first);
  const restored = boundary.restore(chatOf(first, next));
  assert.equal(restored.scope.parentLineage, boundary.restore(normalizedPrevious).scope.lineage);
});

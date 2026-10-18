"use strict";

// Resume facts are views of the chat the model is about to read: which
// results it carries only in part, and whether the previous turn stopped
// before it reported. The 2026-10-18 run resumed with 8 partial projections
// that the model never paged, after a turn that ended without an answer.

const assert = require("node:assert/strict");
const test = require("node:test");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { PRIOR_TURN_INTERRUPTED_FACT, partialProjectionFact, priorTurnInterrupted } = require("../dist/resume-facts");

function exchange(history, id, name, args, result) {
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
    type: "function", id, name, arguments: args } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
    content: JSON.stringify(result) }] }));
  return history;
}

const projection = (callId, path, total, shown, readHandle) => ({ kind: "archived_tool_result_projection",
  originalCallId: callId, toolName: "read_file", sourceIdentity: { path }, bodyTotalChars: total,
  bodyOmittedChars: total - shown, bodyComplete: false, archiveRef: { readHandle },
  notice: `PARTIAL BODY: chars 0-${shown} of ${total}` });

test("partial projections in the input are listed once with their read handles", () => {
  const history = Chat.empty();
  history.append(ChatMessage.from({ role: "user", content: "Audit" }));
  exchange(history, "a", "read_file", { path: "project://Source/Game/GameMode.cpp" },
    projection("a", "project://Source/Game/GameMode.cpp", 5988, 512, "h-a"));
  exchange(history, "b", "read_file", { path: "project://Source/Game/Whole.h" }, { ok: true, content: "whole" });
  exchange(history, "c", "read_file", { path: "project://Source/Game/Done.h" },
    { ...projection("c", "project://Source/Game/Done.h", 300, 300, "h-c"), bodyComplete: true });
  const { fact, count } = partialProjectionFact(history.getMessagesArray());
  assert.equal(count, 1);
  assert.match(fact, /1 earlier tool result\(s\) are present in this input only in part/);
  assert.match(fact, /read_file project:\/\/Source\/Game\/GameMode\.cpp: chars 0-512 of 5988; readHandle=h-a/);
  assert.match(fact, /evidence_first_read_context\(action=read, readHandle, view=body\)/);
  assert.doesNotMatch(fact, /Done\.h|Whole\.h/);
});

test("the projection list is bounded and says how many it left out", () => {
  const history = Chat.empty();
  history.append(ChatMessage.from({ role: "user", content: "Audit" }));
  for (let i = 0; i < 9; i++) exchange(history, `p${i}`, "read_file", {}, projection(`p${i}`, `project://F${i}.h`, 2000, 512, `h${i}`));
  const { fact, count } = partialProjectionFact(history.getMessagesArray());
  assert.equal(count, 9);
  assert.equal(fact.split("\n").filter(line => line.startsWith("- read_file")).length, 6);
  assert.match(fact, /- 3 more partial results not listed/);
  assert.equal(partialProjectionFact([]).fact, null);
});

test("a previous turn that ended after tool results without an answer reports only the observed missing final answer", () => {
  const history = Chat.empty();
  history.append(ChatMessage.from({ role: "user", content: "Audit" }));
  exchange(history, "a", "search_files", { query: "bIsDead" }, { ok: true, coverage: "partial" });
  history.append(ChatMessage.from({ role: "user", content: "계속해" }));
  assert.equal(priorTurnInterrupted(history.getMessagesArray()), true);
  assert.match(PRIOR_TURN_INTERRUPTED_FACT, /drafts, not verified findings/);
  assert.match(PRIOR_TURN_INTERRUPTED_FACT, /cause not recorded/);
  assert.doesNotMatch(PRIOR_TURN_INTERRUPTED_FACT, /it was interrupted/);
});

test("a previous turn that answered, a first turn, or a turn without tools is not interrupted", () => {
  const answered = Chat.empty();
  answered.append(ChatMessage.from({ role: "user", content: "Audit" }));
  exchange(answered, "a", "read_file", {}, { ok: true });
  answered.append(ChatMessage.from({ role: "assistant", content: "Report" }));
  answered.append(ChatMessage.from({ role: "user", content: "계속해" }));
  assert.equal(priorTurnInterrupted(answered.getMessagesArray()), false);
  const first = Chat.empty();
  first.append(ChatMessage.from({ role: "user", content: "Audit" }));
  assert.equal(priorTurnInterrupted(first.getMessagesArray()), false);
  const chat = Chat.empty();
  chat.append(ChatMessage.from({ role: "user", content: "Hi" }));
  chat.append(ChatMessage.from({ role: "assistant", content: "Hello" }));
  chat.append(ChatMessage.from({ role: "user", content: "Again" }));
  assert.equal(priorTurnInterrupted(chat.getMessagesArray()), false);
});

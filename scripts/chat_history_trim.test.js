"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { trimChatHistory, hasUserMessage } = require("../scripts/chat_history_trim");

function msg(role, text) {
  return { role, content: [{ type: "text", text }] };
}
function toolPair(id) {
  return [
    {
      role: "assistant",
      content: [{
        type: "toolCallRequest",
        toolCallRequest: { id, type: "function", name: "echo", arguments: {} },
      }],
    },
    {
      role: "tool",
      content: [{ type: "toolCallResult", toolCallId: id, content: "{}" }],
    },
  ];
}

test("naive-equivalent long history keeps a user after trim", () => {
  const history = [msg("system", "sys"), msg("user", "goal")];
  for (let i = 0; i < 12; i += 1) history.push(...toolPair(`c${i}`));
  assert.ok(history.length > 18);
  const { history: next, trimmed } = trimChatHistory(history, { maxMessages: 18, keepTail: 12 });
  assert.equal(trimmed, true);
  assert.equal(hasUserMessage(next), true);
  assert.equal(next.some((m) => m.role === "user" && m.content[0].text === "goal"), true);
  assert.notEqual(next[1]?.role, "tool");
});

test("trim does not start on orphan tool", () => {
  const history = [msg("system", "sys"), msg("user", "goal")];
  for (let i = 0; i < 10; i += 1) history.push(...toolPair(`c${i}`));
  const { history: next } = trimChatHistory(history, { maxMessages: 8, keepTail: 6 });
  const firstNonSystem = next.find((m) => m.role !== "system");
  assert.ok(firstNonSystem);
  assert.notEqual(firstNonSystem.role, "tool");
});

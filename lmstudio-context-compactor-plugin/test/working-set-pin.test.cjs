"use strict";

// A bounded current-turn read stays verbatim so exact-oldText edits can reuse
// it. Other reads still project; pins expire and yield to input-budget pressure.

const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { WorkingContext } = require("../src/working-context.js");

const scope = { conversation: "chat-pin", lineage: "pin-1", workspace: "workspace-1", repository: "worktree-1" };
const body = (name, lines) => Array.from({ length: lines }, (_, i) => `${i + 1}\tint32 ${name}${i} = ${i};`).join("\n");

function call(history, id, name, args, result) {
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id, name, arguments: args } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
    content: JSON.stringify(result) }] }));
}

const read = (file, lines) => ({ ok: true, path: `project://Source/${file}`, content: body(file.replace(/\W/g, ""), lines),
  startLine: 1, endLine: lines, totalLines: lines, sha256: "a".repeat(64), lineNumbers: true });

function projected(history, metadata = {}) {
  const context = new WorkingContext({ ...scope, lineage: `pin-${Math.random()}` });
  const result = context.project(history, () => true, { executionId: "exec", roundIndex: 2, ...metadata }, 512);
  return result.history.getMessagesArray().filter((message) => message.getToolCallResults().length)
    .map((message) => JSON.parse(message.getToolCallResults()[0].content));
}

test("the latest read of a file edited in this history stays raw; unrelated reads project", () => {
  const history = Chat.from([{ role: "user", content: "Fix Health.cpp" }]);
  call(history, "r1", "read_file_range", { path: "project://Source/Health.cpp", startLine: 1, endLine: 80 }, read("Health.cpp", 80));
  call(history, "r2", "read_file_range", { path: "project://Source/Bloom.cpp", startLine: 1, endLine: 80 }, read("Bloom.cpp", 80));
  call(history, "w1", "replace_in_file", { path: "project://Source/Health.cpp" },
    { ok: true, operation: "replaced", path: "project://Source/Health.cpp", sha256: "b".repeat(64) });
  const [health, bloom] = projected(history);
  assert.equal(health.kind, undefined, "the edited file's read keeps its raw body");
  assert.match(health.content, /Health_cpp79|Healthcpp79/u);
  assert.equal(bloom.kind, "archived_tool_result_projection");
});

test("a narrow read in the current turn is retained before its first edit", () => {
  const history = Chat.from([{ role: "user", content: "Inspect" }]);
  call(history, "r1", "read_file_range", { path: "project://Source/Health.cpp", startLine: 1, endLine: 80 }, read("Health.cpp", 80));
  const [health] = projected(history);
  assert.equal(health.kind, undefined);
});

test("pins expire at a new user turn and can be released under measured pressure", () => {
  const history = Chat.from([{ role: "user", content: "Fix" }]);
  call(history, "r1", "read_file_range", { path: "project://Source/Health.cpp", startLine: 1, endLine: 80 }, read("Health.cpp", 80));
  assert.equal(projected(history, { pinnedWorkingSetMaxChars: 0 })[0].kind, "archived_tool_result_projection");
  history.append(ChatMessage.from({ role: "user", content: "Next task" }));
  assert.equal(projected(history)[0].kind, "archived_tool_result_projection");
});

test("the pin is bounded by its character budget", () => {
  const history = Chat.from([{ role: "user", content: "Fix" }]);
  call(history, "r1", "read_file_range", { path: "project://Source/Huge.cpp", startLine: 1, endLine: 900 }, read("Huge.cpp", 900));
  call(history, "w1", "replace_in_file", { path: "project://Source/Huge.cpp" },
    { ok: true, operation: "replaced", path: "project://Source/Huge.cpp", sha256: "b".repeat(64) });
  const [huge] = projected(history, { pinnedWorkingSetMaxChars: 4000 });
  assert.equal(huge.kind, "archived_tool_result_projection");
});

test("a projected completed range does not become a request to read the rest of the file", () => {
  const history = Chat.from([{ role: "user", content: "Inspect" }]);
  call(history, "r1", "read_file_range", { path: "project://Source/Health.cpp", startLine: 1, endLine: 80 }, {
    ...read("Health.cpp", 80), totalLines: 500, rangeComplete: true,
    requestedRangeComplete: true, moreLinesInFile: true,
  });
  const [result] = projected(history, { pinnedWorkingSetMaxChars: 0 });
  assert.equal(result.kind, "archived_tool_result_projection");
  assert.equal(result.sourceFacts.rangeComplete, true);
  assert.equal(result.sourceFacts.requestedRangeComplete, true);
  assert.equal(result.sourceFacts.moreLinesInFile, true);
  assert.equal(Object.hasOwn(result.sourceFacts, "nextStartLine"), false);
  assert.equal(result.bodyComplete, false, "source-range completion is separate from projection completeness");
});

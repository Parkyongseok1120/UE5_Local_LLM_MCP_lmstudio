"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const core = require("../src/compaction-core");

test("budget gate reserves output, tool schema, and build result space", () => {
  const soft = core.budgetDecision({
    contextLength: 32_000,
    inputTokens: 8_000,
    nextToolName: "build_unreal_project",
    toolSchemaTokens: 2_000,
  });
  assert.equal(soft.action, "soft_compact");
  assert.equal(soft.remainingTokens, 9_904);

  const hard = core.budgetDecision({
    contextLength: 32_000,
    inputTokens: 19_000,
    nextToolName: "read_file_range",
    toolSchemaTokens: 1_000,
  });
  assert.equal(hard.action, "hard_compact");
});

test("checkpoint preserves required next tool and exact signature contract", () => {
  const messages = [
    { role: "user", content: "Fix the compile error" },
    { role: "tool", content: JSON.stringify({
      requiredNextTool: "unreal_symbol_lookup",
      requiredNextToolArgs: { query: "LoadStreamLevel" },
      signatureContract: { name: "LoadStreamLevel", parameterCount: 5 },
      path: "project://Source/Game/Foo.cpp",
    }) },
  ];
  const checkpoint = core.buildCheckpoint(messages);
  assert.equal(checkpoint.requiredNextTool.name, "unreal_symbol_lookup");
  assert.deepEqual(checkpoint.requiredNextTool.args, { query: "LoadStreamLevel" });
  assert.equal(checkpoint.exactSignatureContracts[0].parameterCount, 5);
  assert.ok(checkpoint.modifiedFiles.includes("project://Source/Game/Foo.cpp"));
  assert.equal(core.validateCheckpoint(checkpoint), true);
});

test("compaction never leaves an orphan tool call in the retained tail", () => {
  const messages = [
    { role: "system", content: "rules" },
    { role: "user", content: "objective" },
    { role: "assistant", content: "", toolCalls: [{ id: "a", name: "read_file", arguments: {} }] },
    { role: "tool", content: "result", toolResults: [{ toolCallId: "a", name: "read_file", content: "result" }] },
    { role: "user", content: "continue" },
    { role: "assistant", content: "done" },
  ];
  const compacted = core.compactSnapshots(messages, core.buildCheckpoint(messages), { recentCompleteTurns: 1 });
  assert.equal(core.isCompleteToolPair(compacted), true);
});

test("compaction is deterministic for the same checkpoint and messages", () => {
  const messages = [
    { role: "system", content: "rules" },
    { role: "user", content: "objective" },
    { role: "assistant", content: "first" },
    { role: "user", content: "second" },
    { role: "assistant", content: "third" },
  ];
  const checkpoint = core.buildCheckpoint(messages);
  const a = core.compactSnapshots(messages, checkpoint, { recentCompleteTurns: 1 });
  const b = core.compactSnapshots(messages, checkpoint, { recentCompleteTurns: 1 });
  assert.deepEqual(a, b);
});

test("tail expansion includes the request when a retained tool result would be orphaned", () => {
  const messages = [
    { role: "system", content: "rules" },
    { role: "user", content: "objective" },
    { role: "assistant", content: "", toolCalls: [{ id: "pair-1", name: "read_file", arguments: {} }] },
    { role: "tool", content: "result", toolResults: [{ toolCallId: "pair-1", content: "result" }] },
    { role: "user", content: "continue" },
  ];
  const compacted = core.compactSnapshots(messages, core.buildCheckpoint(messages), { recentCompleteTurns: 1 });
  assert.equal(core.isCompleteToolPair(compacted), true);
  assert.ok(compacted.some((message) => message.toolCalls?.some((call) => call.id === "pair-1")));
});

test("rebuilding an unchanged checkpoint does not double count mutations", () => {
  const messages = [
    { role: "user", content: "fix" },
    { role: "assistant", content: "", toolCalls: [{ id: "write-1", name: "mcp_unreal_agent_write_file", arguments: {} }] },
    { role: "tool", content: "ok", toolResults: [{ toolCallId: "write-1", content: "ok" }] },
  ];
  const first = core.buildCheckpoint(messages);
  const second = core.buildCheckpoint(messages, first);
  assert.equal(first.mutationGeneration, 1);
  assert.equal(second.mutationGeneration, 1);
});

test("zero retained turns keeps only the minimum recent tail", () => {
  const messages = [
    { role: "system", content: "rules" },
    { role: "user", content: "objective" },
    { role: "assistant", content: "old answer" },
    { role: "user", content: "latest request" },
  ];
  const compacted = core.compactSnapshots(messages, core.buildCheckpoint(messages), { recentCompleteTurns: 0 });
  assert.equal(compacted.at(-1).text, "latest request");
  assert.equal(compacted.some((message) => message.text === "old answer"), false);
});

test("session fingerprint salt separates identical prompts in different workspaces", () => {
  const messages = [{ role: "user", content: "same request" }];
  assert.notEqual(core.sessionFingerprint(messages, "A"), core.sessionFingerprint(messages, "B"));
});

test("session fingerprint remains stable as later turns are appended", () => {
  const initial = [
    { role: "system", content: "rules" },
    { role: "user", content: "same request" },
  ];
  const later = [
    ...initial,
    { role: "assistant", content: "answer" },
    { role: "user", content: "follow-up" },
  ];
  assert.equal(core.sessionFingerprint(initial, "workspace"), core.sessionFingerprint(later, "workspace"));
});

test("required next tool clears after its matching call is present", () => {
  const prior = core.buildCheckpoint([
    { role: "user", content: "fix" },
    { role: "tool", content: JSON.stringify({ requiredNextTool: "unreal_symbol_lookup" }) },
  ]);
  const next = core.buildCheckpoint([
    { role: "user", content: "fix" },
    { role: "tool", content: JSON.stringify({ requiredNextTool: "unreal_symbol_lookup" }) },
    { role: "assistant", content: "", toolCalls: [{ id: "lookup-1", name: "mcp_unreal_symbol_lookup" }] },
    { role: "tool", content: JSON.stringify({ ok: true }), toolResults: [{ toolCallId: "lookup-1", content: "{}" }] },
  ], prior);
  assert.equal(next.requiredNextTool, null);
});

test("explicit null required next tool clears stale state", () => {
  const prior = core.buildCheckpoint([
    { role: "user", content: "fix" },
    { role: "tool", content: JSON.stringify({ requiredNextTool: "build_unreal_project" }) },
  ]);
  const next = core.buildCheckpoint([
    { role: "user", content: "fix" },
    { role: "tool", content: JSON.stringify({ requiredNextTool: "build_unreal_project" }) },
    { role: "tool", content: JSON.stringify({ requiredNextTool: null }) },
  ], prior);
  assert.equal(next.requiredNextTool, null);
});

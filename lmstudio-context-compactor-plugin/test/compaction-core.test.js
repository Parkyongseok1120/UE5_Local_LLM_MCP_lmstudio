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
  assert.equal(soft.remainingTokens, 8_880);
  assert.equal(soft.reservedTokens, 15_120);

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

test("latest user message replaces sticky first-turn objective", () => {
  const messages = [
    { role: "user", content: "현재 프로젝트 찾고 코드 구조 전체 적으로 확인해줘" },
    { role: "assistant", content: "structure overview..." },
    { role: "user", content: "지금 버그있는거 찾기만하고 수정은 하지마." },
  ];
  const checkpoint = core.buildCheckpoint(messages);
  assert.match(checkpoint.objective, /버그있는거 찾기만/);
  assert.doesNotMatch(checkpoint.objective, /코드 구조 전체/);
  assert.ok(checkpoint.constraints.some((item) => String(item).startsWith("read_only_findings_only:")));
});

test("compaction pins latest user goal instead of first user turn", () => {
  const messages = [
    { role: "system", content: "rules" },
    { role: "user", content: "구조 전체 확인" },
    { role: "assistant", content: "overview" },
    { role: "user", content: "지금 버그있는거 찾기만하고 수정은 하지마." },
    { role: "assistant", content: "scanning" },
  ];
  const checkpoint = core.buildCheckpoint(messages);
  const compacted = core.compactSnapshots(messages, checkpoint, { recentCompleteTurns: 0 });
  const pinnedUsers = compacted.filter((message) => message.role === "user").map((message) => message.text);
  assert.deepEqual(pinnedUsers, ["지금 버그있는거 찾기만하고 수정은 하지마."]);
  assert.equal(compacted[0].role, "system");
  assert.equal(compacted.filter((message) => message.role === "system").length, 1);
  assert.match(compacted[0].text, /Conversation checkpoint/);
  assert.match(compacted[0].text, /rules/);
  assert.match(checkpoint.objective, /버그있는거 찾기만/);
});

test("compaction emits a single leading system message for chat-template safety", () => {
  const messages = [
    { role: "system", content: "base rules" },
    { role: "system", content: "extra rules" },
    { role: "user", content: "구조 전체 확인" },
    { role: "assistant", content: "overview" },
    { role: "user", content: "시네마틱 관련해서 구현된것들 더 구체적으로 알려줘." },
  ];
  const checkpoint = core.buildCheckpoint(messages);
  const compacted = core.compactSnapshots(messages, checkpoint, { recentCompleteTurns: 0 });
  const systems = compacted.filter((message) => message.role === "system");
  assert.equal(systems.length, 1);
  assert.match(systems[0].text, /base rules/);
  assert.match(systems[0].text, /extra rules/);
  assert.match(systems[0].text, /Conversation checkpoint/);
  assert.match(systems[0].text, /objective=/);
  assert.equal(compacted.filter((message) => message.role === "user").at(-1).text.includes("시네마틱"), true);
});

test("LM Studio title prompt does not replace the real objective", () => {
  const titlePrompt = (
    "Based on the conversation above, can you please come up with a 2-5 word title "
    + "for this conversation? Put your answer in <title> tags, like this: <title>Your Title Here</title>.\n\n"
    + "Do not explain anything. Just return the title in the specified format."
  );
  const messages = [
    { role: "user", content: "현재 프로젝트 찾고 코드 구조 전체 적으로 확인해줘" },
    { role: "assistant", content: "", toolCalls: [{ id: "a", name: "list_directory", arguments: { path: "Source" } }] },
    { role: "tool", content: "entries", toolResults: [{ toolCallId: "a", name: "list_directory", content: "Project_MJS" }] },
    { role: "user", content: titlePrompt },
  ];
  const checkpoint = core.buildCheckpoint(messages);
  assert.match(checkpoint.objective, /코드 구조/);
  assert.equal(core.isMetaUserMessage(titlePrompt), true);
  assert.doesNotMatch(checkpoint.objective, /2-5 word title/);
});

test("title meta prompt is passed through at the end of compacted chat", () => {
  const titlePrompt = (
    "Based on the conversation above, can you please come up with a 2-5 word title "
    + "for this conversation? Put your answer in <title> tags."
  );
  const messages = [
    { role: "user", content: "코드 구조 확인해줘" },
    { role: "assistant", content: "", toolCalls: [{ id: "a", name: "list_directory", arguments: { path: "Source" } }] },
    { role: "tool", content: "entries", toolResults: [{ toolCallId: "a", name: "list_directory", content: "Project_MJS" }] },
    { role: "user", content: titlePrompt },
  ];
  const checkpoint = core.buildCheckpoint(messages);
  const compacted = core.compactSnapshots(messages, checkpoint, {
    recentCompleteTurns: 0,
    trailingMetaUser: { role: "user", text: titlePrompt, toolCalls: [], toolResults: [] },
  });
  assert.equal(compacted[compacted.length - 1].role, "user");
  assert.match(compacted[compacted.length - 1].text, /2-5 word title/);
  assert.equal(compacted.filter((message) => message.role === "user").length, 2);
  assert.match(checkpoint.objective, /코드 구조/);
});

test("current-turn overflow trim drops oldest tool pairs only", () => {
  const messages = [
    { role: "system", content: "rules" },
    { role: "user", content: "scan deep" },
    { role: "assistant", content: "", toolCalls: [{ id: "a", name: "list_directory", arguments: { path: "A" } }] },
    { role: "tool", content: "a", toolResults: [{ toolCallId: "a", name: "list_directory", content: "A" }] },
    { role: "assistant", content: "", toolCalls: [{ id: "b", name: "list_directory", arguments: { path: "B" } }] },
    { role: "tool", content: "b", toolResults: [{ toolCallId: "b", name: "list_directory", content: "B" }] },
    { role: "assistant", content: "", toolCalls: [{ id: "c", name: "list_directory", arguments: { path: "C" } }] },
    { role: "tool", content: "c", toolResults: [{ toolCallId: "c", name: "list_directory", content: "C" }] },
  ];
  const compacted = core.compactSnapshots(messages, core.buildCheckpoint(messages), {
    recentCompleteTurns: 0,
    maxCurrentTurnMessages: 2,
  });
  const toolResults = compacted.flatMap((message) => message.toolResults || []);
  assert.equal(toolResults.some((result) => result.toolCallId === "a"), false);
  assert.equal(toolResults.some((result) => result.toolCallId === "b"), false);
  assert.equal(toolResults.some((result) => result.toolCallId === "c"), true);
  assert.equal(core.isCompleteToolPair(compacted), true);
});

test("soft compaction keeps the full current turn tool evidence", () => {
  const messages = [
    { role: "system", content: "rules" },
    { role: "user", content: "old goal" },
    { role: "assistant", content: "old-" + "x".repeat(200) },
    { role: "user", content: "현재 프로젝트 코드 구조 확인해줘" },
    { role: "assistant", content: "", toolCalls: [{ id: "a", name: "list_directory", arguments: { path: "Source" } }] },
    { role: "tool", content: "source", toolResults: [{ toolCallId: "a", name: "list_directory", content: "Project_MJS" }] },
    { role: "assistant", content: "", toolCalls: [{ id: "b", name: "list_directory", arguments: { path: "Source/Project_MJS/Public" } }] },
    { role: "tool", content: "public", toolResults: [{ toolCallId: "b", name: "list_directory", content: "Character" }] },
  ];
  const compacted = core.compactSnapshots(messages, core.buildCheckpoint(messages), { recentCompleteTurns: 1 });
  const toolResults = compacted.flatMap((message) => message.toolResults || []);
  assert.equal(toolResults.some((result) => result.toolCallId === "a"), true);
  assert.equal(toolResults.some((result) => result.toolCallId === "b"), true);
  assert.equal(core.isCompleteToolPair(compacted), true);
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
  const users = compacted.filter((message) => message.role === "user").map((message) => message.text);
  assert.deepEqual(users, ["latest request"]);
  assert.equal(compacted.some((message) => message.text === "old answer"), false);
  assert.equal(compacted.some((message) => message.text === "objective"), false);
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

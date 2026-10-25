"use strict";

// A read-only review keeps the reads it is still reasoning about. The
// 2026-10-25 run read 13 files in full, then spent 40 rounds re-paging them
// because projection and compaction archived every body: the edit-only pin
// never applied to a review, and compaction cut the pinned exchanges anyway.

const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { WorkingContext } = require("../src/working-context.js");
const { ContextManager, createInputAssembler } = require("../dist/context-manager");
const { BudgetBroker } = require("../dist/budget-broker");
const { readConfig } = require("../dist/execution-config");

const scope = { conversation: "chat-review", workspace: "workspace-1", repository: "worktree-1" };
const body = (name, lines) => Array.from({ length: lines }, (_, i) => `${i + 1}\tint32 ${name}${i} = ${i};`).join("\n");
const sha = char => char.repeat(64);

function call(history, id, name, args, result) {
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id, name, arguments: args } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
    content: JSON.stringify(result) }] }));
}

const fullRead = (file, lines, hash = "a") => ({ ok: true, path: `project://Source/${file}`,
  content: body(file.replace(/\W/g, ""), lines), startLine: 1, endLine: lines, totalLines: lines,
  sha256: sha(hash), lineNumbers: true });

function store() { return new WorkingContext({ ...scope, lineage: `review-${Math.random()}` }); }

function projected(history, metadata = {}) {
  const result = store().project(history, () => true, { executionId: "exec", roundIndex: 4, ...metadata }, 512);
  return result.history.getMessagesArray().filter(message => message.getToolCallResults().length)
    .map(message => JSON.parse(message.getToolCallResults()[0].content));
}

function reviewHistory() {
  const history = Chat.from([{ role: "user", content: "Audit the character RPC contracts" }]);
  call(history, "r1", "read_file", { path: "project://Source/GameMode.cpp" }, fullRead("GameMode.cpp", 146, "1"));
  call(history, "r2", "read_file", { path: "project://Source/Character.cpp" }, fullRead("Character.cpp", 420, "2"));
  call(history, "r3", "read_file", { path: "project://Source/PlayerState.cpp" }, fullRead("PlayerState.cpp", 97, "3"));
  return history;
}

test("a review keeps its latest whole-file reads verbatim within the working-set budget", () => {
  const [gameMode, character, playerState] = projected(reviewHistory(), { pinnedWorkingSetMaxChars: 40000 });
  assert.equal(character.kind, undefined, "a 420-line read is no longer excluded by a line limit");
  assert.equal(playerState.kind, undefined);
  assert.equal(gameMode.kind, undefined);
});

test("over budget the newest reads stay and older ones project", () => {
  const [gameMode, character, playerState] = projected(reviewHistory(), { pinnedWorkingSetMaxChars: 12000 });
  assert.equal(playerState.kind, undefined, "newest read stays");
  assert.equal(character.kind, "archived_tool_result_projection", "does not fit next to the newest");
  assert.equal(gameMode.kind, undefined, "a smaller older read still fits the remaining budget");
});

test("a file the assistant cites outranks a newer uncited read", () => {
  const history = reviewHistory();
  history.append(ChatMessage.from({ role: "assistant", content: "DeathCount fires OnRep_KillCount (GameMode.cpp:91)." }));
  const [gameMode, , playerState] = projected(history, { pinnedWorkingSetMaxChars: 6000 });
  assert.equal(gameMode.kind, undefined, "the cited file is kept first");
  assert.equal(playerState.kind, "archived_tool_result_projection");
});

test("edits keep the edit-only working set", () => {
  const history = reviewHistory();
  call(history, "w1", "replace_in_file", { path: "project://Source/GameMode.cpp" },
    { ok: true, operation: "replaced", path: "project://Source/GameMode.cpp", sha256: sha("b") });
  const [gameMode, character, playerState] = projected(history, { pinnedWorkingSetMaxChars: 40000 });
  assert.equal(gameMode.kind, undefined);
  assert.equal(character.kind, "archived_tool_result_projection");
  assert.equal(playerState.kind, "archived_tool_result_projection");
});

test("pinned reads are reported by message index and expire with the turn", () => {
  const history = reviewHistory();
  const context = store();
  assert.deepEqual(context.pinnedResultIndexes(history, 40000).sort((a, b) => a - b), [2, 4, 6]);
  assert.deepEqual(context.pinnedResultIndexes(history, 0), []);
  history.append("user", "next task");
  assert.deepEqual(context.pinnedResultIndexes(history, 40000), []);
});

// Conversation 1792913431525: the model restored nine files through archive
// pages, and the next compaction cut every page because none counted as a read.
test("an archive page that restored a file is part of the working set", async () => {
  const context = store();
  const history = reviewHistory();
  context.project(history, () => true, { executionId: "exec", roundIndex: 2, pinnedWorkingSetMaxChars: 0 }, 512);
  const handle = context.archive.validHandles("project://Source/Character.cpp")[0].readHandle;
  const page = await context.tool().implementation({ action: "read", readHandle: handle, view: "body", startLine: 1, endLine: 40,
    maxChars: 8192 }, { signal: new AbortController().signal });
  assert.equal(page.kind, "historical_evidence_range", JSON.stringify(page).slice(0, 300));
  call(history, "page1", "evidence_first_read_context", { action: "read", readHandle: handle, startLine: 1, endLine: 40 }, page);
  const indexes = context.pinnedResultIndexes(history, 40000);
  assert.ok(indexes.includes(history.getMessagesArray().length - 1), `pinned: ${indexes}`);
  context.close();
});

test("a file the turn already went back to outranks newer reads", () => {
  const context = store();
  const history = reviewHistory();
  const ledgerFacts = file => ({ path: `project://Source/${file}`, key: `source/${file}`.toLowerCase(), sourceVersion: "",
    lines: [1, 10], bytes: null, complete: false, totalLines: null, size: null, chars: 10 });
  // GameMode.cpp (read first) was delivered twice; PlayerState.cpp is newest.
  const gameMode = { ...ledgerFacts("GameMode.cpp"), sourceVersion: "1".repeat(64) };
  context.readLedger.record(gameMode, { round: 1 }); context.readLedger.record(gameMode, { round: 5 });
  const kept = context.pinnedResultIndexes(history, 6000);
  assert.deepEqual(kept, [2], "only GameMode.cpp fits, and it is the one in use");
  context.close();
});

test("the working-set budget is a share of low water with an edit-only floor", () => {
  const broker = new BudgetBroker({ workingInputTargetTokens: 40000, workingInputTriggerTokens: 56000, safetyMarginTokens: 2048 });
  assert.equal(broker.pinnedWorkingSetChars({ effectiveLowWaterTokens: 40000 }), 30000);
  assert.equal(broker.pinnedWorkingSetChars({ effectiveLowWaterTokens: 2000 }), 6144);
});

function lowWaterFixture(preferred) {
  const config = readConfig({ getPluginConfig() { return { get: key => ({ contextManagementMode: "deterministic",
    workingInputTargetTokens: 6000, workingInputTriggerTokens: 9000, workingInputScaling: "fixed",
    maxOutputReserve: 2048, safetyMarginTokens: 512 })[key] }; } });
  const source = { async getContextLength() { return 32768; },
    async applyPromptTemplate(chat, opts = {}) { return chat.toString() + JSON.stringify(opts.toolDefinitions || []); },
    async countTokens(text) { return Math.ceil(text.length / 4); } };
  const assemble = createInputAssembler({ tokenSource: source, config, roundTools: [], roundScopeInstructions: [],
    getRoundOutputReserve: () => 2048, modelInputId: "review", noteEnabled: false, getNote: () => null,
    historicalAvailabilityLedger: [] });
  return { assemble, manager: new ContextManager(config, new BudgetBroker(config), {}, undefined, preferred) };
}

function pressuredReview() {
  const history = Chat.from([{ role: "system", content: "invariant" }, { role: "user", content: "Audit the RPC contracts" }]);
  call(history, "r1", "read_file", { path: "project://Source/Pinned.cpp" },
    { ...fullRead("Pinned.cpp", 60, "4"), content: `PINNED_BODY ${"p".repeat(4000)}` });
  for (let i = 0; i < 6; i++) {
    history.append(ChatMessage.from({ role: "assistant", content: `progress ${i} ${"x".repeat(3000)}` }));
    call(history, `s${i}`, "search_files", { query: `q${i}` }, { ok: true, matches: [`m${i} ${"y".repeat(3000)}`] });
  }
  return history;
}

test("compaction keeps the pinned working set while the target allows it", async () => {
  const history = pressuredReview();
  const { manager, assemble } = lowWaterFixture(() => [2, 3]);
  const result = await manager.enforceLowWater(history, assemble, { hasReadTools: false, force: true });
  assert.equal(result.success, true);
  assert.match(result.telemetry.selectedCandidate, /^pinned_/u);
  assert.match(result.history.toString(), /PINNED_BODY/u);
});

test("without a pin the same compaction drops that read", async () => {
  const history = pressuredReview();
  const { manager, assemble } = lowWaterFixture(undefined);
  const result = await manager.enforceLowWater(history, assemble, { hasReadTools: false, force: true });
  assert.equal(result.success, true);
  assert.doesNotMatch(result.history.toString(), /PINNED_BODY/u);
});

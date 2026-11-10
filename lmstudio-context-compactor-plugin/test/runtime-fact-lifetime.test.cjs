"use strict";
// Revised plan 2026-11-07 §H1. Deleting a fact from the map left its text in
// an earlier data block, so the next model input still carried it (51 stale
// characters with 0 current). A fact set once also never left.
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { RuntimeFactStore, isRetiredFact } = require("../dist/runtime-fact-store");
const { createInputAssembler } = require("../dist/context-manager");
const { readConfig } = require("../dist/execution-config");
const { readBudgetResolved } = require("../dist/budget-broker");

const BUDGET = "Runtime read-result budget facts: [{\"field\":\"byteBudget\",\"requested\":3072,\"effective\":2048}]. STALE_BUDGET_FACT";

test("the store retires replaced and cleared texts and clears facts by their declared events", () => {
  const store = new RuntimeFactStore();
  store.set("budget", BUDGET);
  store.set("budget", BUDGET);
  assert.deepEqual(store.retiredTexts, [], "setting the same text again changes nothing");
  store.set("stagnation", "Runtime evidence observation: a 2-round read cycle repeated.");
  store.set("prior_turn", "Runtime evidence observation: prior turn interrupted.");
  store.onEvent("research_progress");
  assert.equal(store.has("stagnation"), false);
  assert.equal(store.has("prior_turn"), true, "a key without an event lives for the execution");
  store.onEvent("read_budget_resolved");
  assert.equal(store.has("budget"), false);
  assert.ok(store.retiredTexts.includes(BUDGET));
  store.acknowledgeRewrite();
  assert.deepEqual(store.retiredTexts, []);
  for (let i = 0; i < 40; i++) { store.set("tool_retry", `Returned tool retry contracts: ${i}`); }
  assert.ok(store.retiredTexts.length <= 32, "retired texts are bounded");
  // A fact cut by admission is still recognized by its head.
  assert.equal(isRetiredFact(BUDGET.slice(0, 120) + "…[truncated 40 chars]", [BUDGET]), true);
  assert.equal(isRetiredFact("Runtime evidence observation: other", [BUDGET]), false);
});

test("a cleared fact leaves the next assembled input and returns when it recurs", async () => {
  const config = readConfig({ getPluginConfig() { return { get: key => ({ contextManagementMode: "deterministic" })[key] }; } });
  const store = new RuntimeFactStore();
  const shown = new Map();
  const source = { async getContextLength() { return 65536; }, async applyPromptTemplate(chat) { return chat.toString(); },
    async countTokens(text) { return Math.ceil(text.length / 4); } };
  const history = Chat.from([{ role: "user", content: "Read files." }]);
  const request = { type: "function", id: "r1", name: "read_file", arguments: { path: "A.cpp" } };
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: request }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "r1",
    content: JSON.stringify({ ok: true, path: "A.cpp", content: "RETURNED_EVIDENCE" }) }] }));
  const assemble = () => createInputAssembler({ tokenSource: source, config, roundTools: [], roundScopeInstructions: [],
    getRoundOutputReserve: () => 2048, modelInputId: "facts", noteEnabled: false, getNote: () => null,
    historicalAvailabilityLedger: [], getRuntimeFacts: () => store.entries(), shownDataBlocks: shown,
    retiredFacts: () => store.retiredTexts, runtimeDataAllowanceChars: 100000 })(history);
  const commit = input => {
    const block = input.composition.dataBlock;
    if (!block) return;
    if (block.replacesShown) { shown.clear(); store.acknowledgeRewrite(); }
    shown.set(block.anchor, block.parts);
  };
  store.set("budget", BUDGET);
  store.set("prior_turn", "Runtime evidence observation: prior turn interrupted.");
  const first = await assemble(); commit(first);
  assert.match(first.history.toString(), /STALE_BUDGET_FACT/u);
  store.onEvent("read_budget_resolved");
  const second = await assemble();
  assert.doesNotMatch(second.history.toString(), /STALE_BUDGET_FACT/u, "the actual next input no longer carries it");
  assert.equal(second.composition.forcedRewrite, "fact_retired");
  assert.match(second.history.toString(), /prior turn interrupted/u, "an unrelated current fact stays");
  assert.match(second.history.toString(), /RETURNED_EVIDENCE/u, "returned evidence is not touched");
  commit(second);
  assert.deepEqual(store.retiredTexts, []);
  const third = await assemble(); commit(third);
  assert.equal(third.composition.forcedRewrite, undefined, "no rewrite once the input is clean");
  store.set("budget", BUDGET);
  const fourth = await assemble();
  assert.match(fourth.history.toString(), /STALE_BUDGET_FACT/u, "the same warning is set again when it recurs");
});

test("one successful read beside a denied peer does not resolve the read budget", () => {
  const read = (requested, effective, extra = {}) => ({ readResultLimit: { field: "byteBudget", requested, effective }, ...extra });
  assert.equal(readBudgetResolved([read(8192, 8192)]), true);
  assert.equal(readBudgetResolved([read(8192, 8192), read(8192, 0, { budgetDenialReason: "held_reservations_insufficient" })]), false);
  assert.equal(readBudgetResolved([read(8192, 4096)]), false, "a lowered read is still constrained");
  assert.equal(readBudgetResolved([{ tool: "list_directory" }]), false, "a round without a bounded read shows nothing");
});

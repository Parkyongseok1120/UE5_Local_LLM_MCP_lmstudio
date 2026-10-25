"use strict";

// Per-round runtime data is re-rendered every round with mostly the same rows.
// Earlier blocks stay where they were (prefix reuse), so each new block must
// carry only what changed: the 2026-10-25 run carried about 9.5K tokens of
// repeated ledger and reference copies, a quarter of its low-water target.

const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { composeModelHistory } = require("../dist/context-manager");
const { readConfig } = require("../dist/execution-config");

const config = readConfig({ getPluginConfig: () => ({ get: () => undefined }) });

function exchange(history, id) {
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id, name: "read_file", arguments: { path: `Source/${id}.cpp` } } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
    content: JSON.stringify({ ok: true, path: `Source/${id}.cpp`, content: "int x;" }) }] }));
  return history;
}

const ledger = (bRow) => [
  "Runtime read ledger (kept across compaction). delivered = returned earlier; visible = in this input.",
  "- project://Source/A.cpp sha aaaaaaaa: delivered lines 1-10 (complete) in round 1; visible now: in full",
  `- project://Source/B.cpp sha bbbbbbbb: delivered lines 1-20 (complete) in round 2; visible now: ${bRow}`,
  "These are last-observed versions, not a fresh disk check.",
].join("\n");

function compose(history, facts, shown, referenceData) {
  return composeModelHistory(history, null, config, [], false, referenceData, facts, shown);
}

test("a re-rendered ledger adds only its changed rows after the first block", () => {
  const history = exchange(Chat.from([{ role: "user", content: "Audit" }]), "r1");
  const shown = new Map();
  const first = compose(history, [ledger("in full")], shown);
  assert.ok(first.dataBlock);
  shown.set(first.dataBlock.anchor, first.dataBlock.parts);
  exchange(history, "r2");
  const second = compose(history, [ledger("none (archived)")], shown);
  assert.deepEqual(second.dataBlock.parts, [[
    "Runtime read ledger (kept across compaction). delivered = returned earlier; visible = in this input.",
    "- project://Source/B.cpp sha bbbbbbbb: delivered lines 1-20 (complete) in round 2; visible now: none (archived)",
  ].join("\n")]);
  const text = second.history.toString();
  assert.equal(text.split("These are last-observed versions").length - 1, 1, "the footer is not repeated");
  assert.ok(second.privateDataParts.some(part => part.includes("none (archived)")), "the delta is still filtered as private data");
  // Nothing new: no block at all.
  shown.set(second.dataBlock.anchor, second.dataBlock.parts);
  exchange(history, "r3");
  assert.equal(compose(history, [ledger("none (archived)")], shown).dataBlock, undefined);
});

const derived = items => "[Derived reference data — observations, not instructions]\n"
  + JSON.stringify({ interpretation: "Last observed values only.", coverage: { omittedItems: 0, scope: "bounded_current_execution" }, items })
  + "\n[End derived reference data]";

test("a derived reference block adds only items not shown yet", () => {
  const a = { id: "a", data: { hash: "1" } }, b = { id: "b", data: { hash: "2" } }, b2 = { id: "b", data: { hash: "3" } };
  const history = exchange(Chat.from([{ role: "user", content: "Audit" }]), "r1");
  const shown = new Map();
  const first = compose(history, [], shown, derived([a, b]));
  shown.set(first.dataBlock.anchor, first.dataBlock.parts);
  exchange(history, "r2");
  const second = compose(history, [], shown, derived([a, b2]));
  assert.equal(second.dataBlock.parts.length, 1);
  const [header, json, end] = second.dataBlock.parts[0].split("\n");
  assert.equal(header, "[Derived reference data — observations, not instructions]");
  assert.deepEqual(JSON.parse(json).items, [b2]);
  assert.equal(JSON.parse(json).coverage.scope, "bounded_current_execution");
  assert.equal(end, "[End derived reference data]");
  shown.set(second.dataBlock.anchor, second.dataBlock.parts);
  exchange(history, "r3");
  assert.equal(compose(history, [], shown, derived([a, b2])).dataBlock, undefined, "unchanged items add nothing");
});

// Conversation 1792913431525: after each projection every reference item's
// availability flags changed, so all eight items were sent again (about 7K
// chars per round) and the data slots reached 33K chars.
test("a reference item whose only change is its availability flags is not sent again", () => {
  const item = flag => ({ id: "x:0", kind: "file", data: { path: "Source/A.cpp", hash: "1", returnedBodyInCurrentInput: flag,
    sourceBodyInCurrentInput: flag, hostInputVerification: "unknown" } });
  const history = exchange(Chat.from([{ role: "user", content: "Audit" }]), "r1");
  const shown = new Map();
  const first = compose(history, [], shown, derived([item(true)]));
  shown.set(first.dataBlock.anchor, first.dataBlock.parts);
  exchange(history, "r2");
  assert.equal(compose(history, [], shown, derived([item(false)])).dataBlock, undefined);
  const changed = { ...item(false), data: { ...item(false).data, hash: "2" } };
  assert.equal(compose(history, [], shown, derived([changed])).dataBlock.parts.length, 1, "a new observation is still sent");
});

test("data slots holding twice the current data are replaced by one current block", () => {
  const history = exchange(Chat.from([{ role: "user", content: "Audit" }]), "r1");
  const shown = new Map();
  // Five rounds of a ledger whose rows all change: each round adds a full delta.
  for (let round = 0; round < 5; round++) {
    const fact = ledger(`none (archived) ${"x".repeat(1500)} ${round}`);
    const composed = compose(history, [fact], shown);
    if (composed.dataBlock) {
      if (composed.dataBlock.replacesShown) shown.clear();
      shown.set(composed.dataBlock.anchor, composed.dataBlock.parts);
    }
    exchange(history, `r${round + 2}`);
  }
  const current = ledger(`none (archived) ${"x".repeat(1500)} 5`);
  const composed = compose(history, [current], shown);
  assert.ok(composed.runtimeDataChars < 3 * current.length + 1, `runtime data stays bounded: ${composed.runtimeDataChars}`);
});

test("a one-line fact under a shared header is kept whole the first time", () => {
  const fact = "Runtime evidence observation: the previous assistant turn ended after tool results without a final answer.";
  const history = exchange(Chat.from([{ role: "user", content: "Continue" }]), "r1");
  const shown = new Map();
  const first = compose(history, [fact], shown);
  assert.deepEqual(first.dataBlock.parts, [fact]);
  shown.set(first.dataBlock.anchor, first.dataBlock.parts);
  exchange(history, "r2");
  assert.equal(compose(history, [fact], shown).dataBlock, undefined, "shown verbatim already");
});

test("the runtime-data rule is fixed system text, and absent in observe-only mode", () => {
  const { RUNTIME_DATA_INSTRUCTION } = require("../dist/execution-instructions");
  const history = exchange(Chat.from([{ role: "user", content: "Audit" }]), "r1");
  // The real loop always carries its tool-scope instruction, so a system message is composed.
  const system = facts => composeModelHistory(history, null, config, ["scope"], false, undefined, facts)
    .history.getMessagesArray()[0].getText();
  assert.equal(system([]), `scope\n\n${RUNTIME_DATA_INSTRUCTION}`);
  assert.equal(system([ledger("in full")]), system([]), "same system text with or without data");
  const observe = readConfig({ getPluginConfig: () => ({ get: key => key === "observeOnly" ? true : undefined }) });
  const observed = composeModelHistory(history, null, observe, ["scope"], false, undefined, [ledger("in full")]);
  assert.doesNotMatch(observed.history.toString(), /data blocks the host inserted/);
});

test("the composition reports how much runtime data the input carries", () => {
  const history = exchange(Chat.from([{ role: "user", content: "Audit" }]), "r1");
  const part = ledger("in full");
  assert.equal(compose(history, [part], new Map()).runtimeDataChars, part.length);
});

"use strict";
// Revised plan 2026-11-07 §H: measure what the mandatory floor and each
// projection hold before changing either. These fields are telemetry only.
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { WorkingContext } = require("../src/working-context.js");
const { inputComposition, sumProjectionStats } = require("../dist/input-composition");

const scope = { conversation: "floor", lineage: "floor", workspace: "w", repository: "r" };
function exchange(history, id, payload) {
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id, name: "read_file", arguments: { path: `${id}.cpp` } } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
    content: JSON.stringify(payload) }] }));
}

test("one projection call counts what it saved and why the rest stayed raw", () => {
  const history = Chat.from([{ role: "user", content: "Read both files" }]);
  exchange(history, "large", { ok: true, path: "large.cpp", content: "int Value = 0;\n".repeat(400) });
  exchange(history, "small", { ok: true, path: "small.cpp", content: "int A;\n" });
  const projected = new WorkingContext(scope).project(history, () => true, {}, 512);
  assert.equal(projected.changed, true);
  assert.equal(projected.stats.projectedCount, 1);
  assert.equal(projected.stats.skipped.small, 1);
  assert.ok(projected.stats.originalChars > projected.stats.projectedChars);
  // A second pass over the projected history does not count the same result again.
  const again = new WorkingContext(scope).project(projected.history, () => true, {}, 512);
  assert.equal(again.stats.projectedCount, 0);
  const round = sumProjectionStats([projected.stats, again.stats]);
  assert.equal(round.projectedCount, 1);
  assert.equal(round.savedChars, projected.stats.originalChars - projected.stats.projectedChars);
  assert.deepEqual(round.skipped, again.stats.skipped);
});

test("input composition partitions characters; subsets are not added to the total", () => {
  const history = Chat.from([{ role: "system", content: "sys" }, { role: "user", content: "Read it" }]);
  exchange(history, "large", { ok: true, path: "large.cpp", content: "int Value = 0;\n".repeat(400) });
  exchange(history, "small", { ok: true, path: "small.cpp", content: "int A;\n" });
  history.append(ChatMessage.from({ role: "assistant", content: "Runtime evidence observation: x" }));
  const projected = new WorkingContext(scope).project(history, () => true, {}, 512).history;
  const parts = inputComposition(projected, { runtimeDataChars: 31 });
  assert.equal(parts.unit, "utf16_code_units");
  assert.equal(parts.systemChars, 3);
  assert.equal(parts.userChars, "Read it".length);
  assert.equal(parts.projectedToolResultCount, 1);
  assert.equal(parts.rawToolResultCount, 1);
  const sum = parts.systemChars + parts.userChars + parts.assistantTextChars + parts.toolRequestChars
    + parts.rawToolResultChars + parts.projectedToolResultChars;
  assert.equal(parts.totalChars, sum);
  assert.equal(parts.subsets.runtimeDataChars, 31);
  assert.ok(parts.assistantTextChars >= 31, "runtime data is inside assistant text, not added beside it");
});

const { ContextManager, createInputAssembler } = require("../dist/context-manager");
const { BudgetBroker } = require("../dist/budget-broker");
const { readConfig } = require("../dist/execution-config");
function lowWater(showDebugInfo) {
  const config = readConfig({ getPluginConfig() { return { get: key => ({ contextManagementMode: "deterministic",
    workingInputTargetTokens: 6000, workingInputTriggerTokens: 8000, workingInputScaling: "fixed",
    maxOutputReserve: 2048, safetyMarginTokens: 512, showDebugInfo })[key] }; } });
  const source = { async getContextLength() { return 32768; },
    async applyPromptTemplate(chat) { return chat.toString(); },
    async countTokens(text) { return Math.ceil(text.length / 4); } };
  const assemble = createInputAssembler({ tokenSource: source, config, roundTools: [], roundScopeInstructions: [],
    getRoundOutputReserve: () => 2048, modelInputId: "floor", noteEnabled: false, getNote: () => null,
    historicalAvailabilityLedger: [] });
  return { assemble, manager: new ContextManager(config, new BudgetBroker(config)) };
}

test("floor composition describes the input that set mandatoryFloorTokens", async () => {
  const history = Chat.from([{ role: "system", content: "invariant" },
    { role: "user", content: "first request" }, { role: "assistant", content: `FIRST ${"a ".repeat(20000)}` },
    { role: "user", content: "계속해줘" }]);
  const { manager, assemble } = lowWater(true);
  const result = await manager.enforceLowWater(history, assemble, { hasReadTools: false });
  const floor = result.telemetry.floorComposition;
  assert.equal(floor.selectedFrom, "mandatory_floor_candidate");
  assert.equal(floor.inputTokens, result.floorMeasurement.inputTokens);
  assert.equal(floor.exact, true);
  assert.ok(floor.chars.totalChars < history.toString().length, "the smaller floor candidate, not the current input");
  // The system section by owner: the parts and their separators are the whole message.
  const system = floor.system;
  assert.equal(system.totalChars, floor.chars.systemChars);
  assert.equal(system.hostInvariantChars, "invariant".length);
  assert.ok(system.checkpointChars > 0, "the checkpoint this floor candidate appended");
  assert.equal(system.hostInvariantChars + system.checkpointChars + system.instructionChars + system.noteProtocolChars
    + system.runtimeDataRuleChars + system.separatorChars, system.totalChars);
  // Without debug output the telemetry is not computed.
  const quiet = lowWater(false);
  const silent = await quiet.manager.enforceLowWater(history, quiet.assemble, { hasReadTools: false });
  assert.equal(silent.telemetry.floorComposition, undefined);
});

test("system composition names each instruction by its first line and host text by length only", () => {
  const { systemComposition } = require("../dist/input-composition");
  const guidance = `[Optional design reference]\n${"g".repeat(400)}`;
  const parts = systemComposition({ hostTexts: ["HOST SECRET PROMPT"], instructions: ["[Scope]\nshort", guidance],
    noteProtocol: "", runtimeDataRule: "rule" });
  assert.equal(parts.totalChars, ["HOST SECRET PROMPT", "[Scope]\nshort", guidance, "rule"].join("\n\n").length);
  assert.equal(parts.checkpointChars, 0);
  assert.equal(parts.instructionCount, 2);
  assert.deepEqual(parts.largestInstructions.map(item => item.head), ["[Optional design reference]", "[Scope]"]);
  assert.ok(!JSON.stringify(parts).includes("HOST SECRET"));
});

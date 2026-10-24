"use strict";

// Context-proportional watermarks and token-estimated read reservations.
// These are admission allowances; exact measurement still gates dispatch.
const test = require("node:test");
const assert = require("node:assert/strict");
const { BudgetBroker, BatchReservation, reserveReadResult } = require("../dist/budget-broker");

const DEFAULTS = { workingInputTargetTokens: 18000, workingInputTriggerTokens: 22000, safetyMarginTokens: 2048 };
const measured = (contextLength, inputTokens = 10000) => ({ contextLength, outputReserve: 8192, inputTokens });

test("auto scaling raises watermarks with the loaded context", () => {
  const broker = new BudgetBroker(DEFAULTS);
  const table = Object.fromEntries([32768, 38912, 65536, 131072].map(ctx => {
    const w = broker.watermarks(measured(ctx));
    return [ctx, [w.effectiveHighWaterTokens, w.configuredLowWaterTokens]];
  }));
  assert.deepEqual(table[32768], [17664, 13248]);
  assert.deepEqual(table[38912], [22000, 16500]);
  assert.deepEqual(table[65536], [33177, 24882]);
  assert.deepEqual(table[131072], [72499, 54374]);
});

test("fixed scaling keeps the configured absolute trigger and target", () => {
  const broker = new BudgetBroker({ ...DEFAULTS, workingInputScaling: "fixed" });
  const w = broker.watermarks(measured(131072));
  assert.equal(w.effectiveHighWaterTokens, 22000);
  assert.equal(w.configuredLowWaterTokens, 16500);
  assert.equal(w.workingInputScaling, "fixed");
});

test("compaction keeps working space above the mandatory floor when it fits", () => {
  const broker = new BudgetBroker(DEFAULTS);
  const w = broker.watermarks(measured(38912, 20000), 14000);
  assert.equal(w.effectiveLowWaterTokens, 20000);
  const capped = broker.watermarks(measured(38912, 20000), 18000);
  assert.equal(capped.effectiveLowWaterTokens, capped.effectiveHighWaterTokens);
});

test("two default native reads share one batch at 38,912 context with 14K input", () => {
  const broker = new BudgetBroker(DEFAULTS);
  const batch = broker.beginBatch(measured(38912, 14000), true);
  const tool = { name: "read_file", pluginIdentifier: "mcp/unreal-agent", parametersJsonSchema: { properties: {
    byteBudget: { type: "integer", minimum: 2048, maximum: 65536, $comment: "direct-json-text-utf8" } } } };
  const first = reserveReadResult(batch, "1", tool, {});
  const second = reserveReadResult(batch, "2", tool, {});
  assert.equal(first.allowed, true);
  assert.equal(second.allowed, true);
  // The adaptive default takes half of what remains, never below 8 KiB.
  assert.ok(first.arguments.byteBudget >= 8192 && first.arguments.byteBudget < 24576);
  assert.ok(second.arguments.byteBudget >= 8192);
});

// 2026-10-20 run: 8 KiB defaults split 420-line files into four reads at an
// 84,224 context that had room for whole small files.
test("a wide context gives omitted reads the full default and an explicit budget is unchanged", () => {
  const broker = new BudgetBroker({ ...DEFAULTS, workingInputTargetTokens: 40000, workingInputTriggerTokens: 56000 });
  const batch = broker.beginBatch(measured(84224, 18000), true);
  const tool = { name: "read_file_range", pluginIdentifier: "mcp/unreal-agent", parametersJsonSchema: { properties: {
    byteBudget: { type: "integer", minimum: 2048, maximum: 65536, $comment: "direct-json-text-utf8" } } } };
  const reads = [1, 2, 3].map(id => reserveReadResult(batch, String(id), tool, {}));
  assert.deepEqual(reads.map(read => [read.allowed, read.arguments.byteBudget]), [[true, 24576], [true, 24576], [true, 24576]]);
  const explicit = reserveReadResult(batch, "4", tool, { byteBudget: 4096 });
  assert.equal(explicit.arguments.byteBudget, 4096);
});

test("returned bytes are converted to estimated tokens and still detect overrun", () => {
  const batch = new BatchReservation(20000);
  assert.equal(batch.reserve("a", 4096), 4096);
  assert.equal(batch.receive("a", 9000), true);
  assert.equal(batch.exceeded, false);
  assert.equal(batch.snapshot().entries[0].tokens, 3728);
  assert.equal(batch.reserve("b", 4096), 4096);
  assert.equal(batch.receive("b", 12000), true);
  assert.equal(batch.exceeded, true);
});

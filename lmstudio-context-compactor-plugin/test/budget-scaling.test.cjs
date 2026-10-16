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
  assert.equal(first.arguments.byteBudget, 8192);
  assert.equal(second.arguments.byteBudget, 8192);
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

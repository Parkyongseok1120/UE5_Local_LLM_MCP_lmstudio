"use strict";
// Revised plan 2026-11-10 §D5: the target judgment compares against the target
// the round applied. Baseline run 1794306949131 compacted to 46,272 and ended
// the round at 50,756 after guidance came back (low water 47,719).
const test = require("node:test");
const assert = require("node:assert/strict");
const { targetTelemetry, measurementTelemetry } = require("../dist/evidence-telemetry");

const config = { workingInputTargetTokens: 46000, workingInputTriggerTokens: 56000 };
const final = inputTokens => ({ exact: true, inputTokens });
const water = (extra = {}) => ({ configuredLowWaterTokens: 47719, effectiveLowWaterTokens: 47719, effectiveHighWaterTokens: 63626,
  finalExactInputTokens: 46272, mandatoryFloorException: false, ...extra });

test("the target is the applied low water, and a miss names its cause", () => {
  // 46,500 is above the raw setting (46,000) and below the applied target: met.
  const met = targetTelemetry(config, final(46500), water(), true);
  assert.equal(met.targetMet, true);
  assert.equal(met.appliedTargetTokens, 47719);
  assert.equal(met.inputTargetTokens, 46000);
  assert.equal(met.targetMissReason, undefined);
  assert.equal(targetTelemetry(config, final(50756), water(), true).targetMissReason, "optional_material_after_compaction");
  assert.equal(targetTelemetry(config, final(50756), water({ finalExactInputTokens: 49000 }), true).targetMissReason, "compaction_target_unmet");
  const floor = targetTelemetry(config, final(52000), water({ effectiveLowWaterTokens: 52000, mandatoryFloorException: true }), false);
  assert.equal(floor.targetMet, false);
  assert.equal(floor.targetMissReason, "mandatory_floor");
  assert.equal(targetTelemetry(config, final(50000), water({ effectiveLowWaterTokens: 51000 }), true).targetMissReason, "working_space_above_target");
  // A round that did not compact reports the miss without inventing a cause.
  assert.deepEqual(targetTelemetry(config, final(55000), water(), false).targetMissReason, undefined);
  assert.equal(targetTelemetry(config, { exact: false, inputTokens: 1 }, null, false).targetMet, null);
});

test("measurement telemetry keeps the fields the event always carried", () => {
  const before = { exact: true, contextLengthSource: "model", promptMeasurementSource: "template", templatedInputChars: 10, messageCount: 2,
    inputTokens: 5, remainingTokens: 9, toolSchemaChars: 1, toolSchemaTokens: 1, toolSchemaTokenMeasurement: "exact" };
  const after = { ...before, inputTokens: 4, toolSchemaFingerprint: "f", templatedInputFingerprint: "t" };
  const row = measurementTelemetry(before, after, "f");
  assert.equal(row.inputTokens, 5); assert.equal(row.finalInputTokens, 4);
  assert.equal(row.toolSurfaceMatchesMeasured, true);
  assert.equal(Object.keys(row).length, 22);
});

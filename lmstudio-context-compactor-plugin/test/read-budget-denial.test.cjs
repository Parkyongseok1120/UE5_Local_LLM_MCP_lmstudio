"use strict";
// Revised plan 2026-11-07 §A/§E. One table owns the read-budget denial codes;
// a small explicit budget no longer cuts a whole file the batch can hold.
const test = require("node:test");
const assert = require("node:assert/strict");
const { READ_BUDGET_DENIALS, readBudgetDenialMessage, deferredReadDenial } = require("../dist/read-budget-denial");
const { BatchReservation, reserveReadResult, estimateReadResponseBytes, readBudgetFeedback } = require("../dist/budget-broker");

const CODES = ["held_reservations_insufficient", "remaining_capacity_insufficient", "returned_result_overrun",
  "batch_closed", "duplicate_reservation", "invalid_reservation", "minimum_exceeds_published_limit"];

test("every denial code has one disposition and an advice that fits its cause", () => {
  assert.deepEqual(Object.keys(READ_BUDGET_DENIALS).sort(), [...CODES].sort());
  for (const code of CODES) assert.match(readBudgetDenialMessage(code), new RegExp(`\\(${code}\\)`, "u"));
  const capacity = CODES.filter(code => READ_BUDGET_DENIALS[code].disposition === "retry_next_round");
  assert.deepEqual(capacity.sort(), ["held_reservations_insufficient", "remaining_capacity_insufficient"],
    "the same set the guard shows as deferred before this change");
  for (const code of capacity) {
    assert.match(readBudgetDenialMessage(code), /not the task's read budget/u);
    assert.equal(deferredReadDenial(code), true);
  }
  assert.doesNotMatch(readBudgetDenialMessage("remaining_capacity_insufficient"), /always compacted|between rounds/u,
    "compaction is not promised every round");
  for (const code of ["batch_closed", "duplicate_reservation", "invalid_reservation"]) {
    assert.match(readBudgetDenialMessage(code), /not a problem with the read's size/u);
    assert.equal(deferredReadDenial(code), false);
  }
  const limit = readBudgetDenialMessage("minimum_exceeds_published_limit");
  assert.doesNotMatch(limit, /Split/u);
  assert.match(limit, /narrower line range/u);
  assert.match(limit, /metadata alone exceeds the limit, a narrower range of the same tool will not help/u);
});

const read = { name: "read_file", pluginIdentifier: "mcp/unreal-agent", parametersJsonSchema: { properties: {
  byteBudget: { type: "integer", minimum: 2048, maximum: 65536, $comment: "direct-json-text-utf8" } } } };
const range = { ...read, name: "read_file_range" };
const estimate = estimateReadResponseBytes({ sizeBytes: 1628, totalLines: null });

test("a whole-file read is raised to its estimate when the batch share holds it", () => {
  assert.equal(estimate, 4841);
  const raised = reserveReadResult(new BatchReservation(40000), "a", read, { path: "A.h", byteBudget: 3072 }, undefined, undefined, estimate);
  assert.equal(raised.allowed, true);
  assert.equal(raised.arguments.byteBudget, estimate);
  assert.equal(raised.limit.modelRequested, 3072);
  assert.equal(raised.limit.raisedTo, "whole_file_estimate");
  assert.equal(readBudgetFeedback([{ readResultLimit: raised.limit }]), "", "a raise is not reported as a limit");
  // Without a size hint, or for a part of a file, the request stands.
  assert.equal(reserveReadResult(new BatchReservation(40000), "b", read, { path: "A.h", byteBudget: 3072 }).arguments.byteBudget, 3072);
  for (const args of [{ startLine: 1, endLine: 20 }, { offsetBytes: 100 }, { cursor: "c" }]) {
    const part = reserveReadResult(new BatchReservation(40000), "c", read, { path: "A.h", byteBudget: 3072, ...args }, undefined, undefined, estimate);
    assert.equal(part.arguments.byteBudget, 3072, JSON.stringify(args));
  }
  const ranged = reserveReadResult(new BatchReservation(40000), "d", range, { path: "A.h", byteBudget: 3072, startLine: 1, endLine: 9 }, undefined, undefined, estimate);
  assert.equal(ranged.arguments.byteBudget, 3072);
  // An explicit budget above the estimate is not raised (the existing size-hint ceiling still applies).
  const above = reserveReadResult(new BatchReservation(40000), "e", read, { path: "A.h", byteBudget: 9000 }, undefined, undefined, estimate);
  assert.equal(above.limit.raisedTo, undefined);
  assert.equal(above.arguments.byteBudget, Math.ceil(estimate * 1.25));
});

test("the raise happens only when the estimate fits the batch share; otherwise nothing changes", () => {
  // The share is half the batch's room after the envelope margin, in the field's unit.
  let fitting = null;
  for (let capacity = 2000; capacity <= 40000 && !fitting; capacity += 50) {
    const result = reserveReadResult(new BatchReservation(capacity), "x", read, { path: "A.h", byteBudget: 3072 }, undefined, undefined, estimate);
    if (result.arguments.byteBudget === estimate) fitting = capacity;
  }
  assert.ok(fitting, "some capacity holds the estimate");
  const below = reserveReadResult(new BatchReservation(fitting - 50), "y", read, { path: "A.h", byteBudget: 3072 }, undefined, undefined, estimate);
  assert.equal(below.allowed, true);
  assert.equal(below.arguments.byteBudget, 3072, "just below the boundary the request stands, not a partial raise");
  // Two whole-file reads that each fit their share are both dispatched.
  const batch = new BatchReservation(fitting * 3);
  const first = reserveReadResult(batch, "p1", read, { path: "A.h", byteBudget: 3072 }, undefined, undefined, estimate);
  const second = reserveReadResult(batch, "p2", read, { path: "B.h", byteBudget: 3072 }, undefined, undefined, estimate);
  assert.equal(first.allowed && second.allowed, true);
});

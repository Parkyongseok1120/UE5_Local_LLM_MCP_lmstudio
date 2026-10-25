"use strict";

// End-to-end replay of the 2026-10-25 review through the production handler
// (see fixtures/review-replay.cjs). Before this change the same replay kept 2
// of the 13 read bodies visible after the first projection; the real model then
// spent about 40 rounds re-paging the rest.

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { FILES, runReviewReplay } = require("./fixtures/review-replay.cjs");

test("a long review keeps its working set, findings and a bounded data slot under pressure", async () => {
  const run = await runReviewReplay(path.join(__dirname, "..", "dist"));
  assert.equal(run.modelCalls, Math.ceil(FILES.length / 2) + 36 + 1, "the review completes without a budget rescue");
  assert.ok(run.projections >= 1, "the replay reaches measured input pressure");
  assert.equal(run.reasoningRegisterInLastInput, true, "reasoning-only findings survive through projection to the final input");
  assert.ok(run.visibleBodiesMin >= 5, `the pinned working set stays visible (${run.visibleBodiesMin} of ${FILES.length})`);
  assert.equal(run.findingsInLastInput, 36 + Math.ceil(FILES.length / 2), "no progress finding is cut");
  assert.ok(run.rewrites <= 3, `rewrites: ${run.rewrites}`);
  assert.ok(run.maxRuntimeDataChars > 0 && run.maxRuntimeDataChars < 8000, `runtime data: ${run.maxRuntimeDataChars} chars`);
  const outcome = run.debug.find(event => event.event === "execution_outcome");
  assert.equal(outcome.executionOutcome, "completed");
});

"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
test("v3 transition authority is closed over the C2 A/B allowlist", () => {
  const source = fs.readFileSync(path.join(__dirname, "../src/prediction-loop.ts"), "utf8");
  const reason = "계약 §3 C2 표에 추가하거나 decide()를 거치라";
  assert.equal(/\b(?:workingSetExhausted|reportNowFinalize)\b/u.test(source), false, reason);
  const calls = [...source.matchAll(/execution\.(finishResearch|startRecovery|startReasoningRecovery|startReplan)\(\s*([^,)\n]*)/gu)]
    .map(([, method, arg]) => method + ":" + arg.trim()).sort();
  assert.deepEqual(calls, [
    'finishResearch:"research_time_limit"', 'finishResearch:"context_budget"',
    'finishResearch:"research_timeout"', 'finishResearch:"research_repetition"',
    'finishResearch:decision.trigger', 'finishResearch:decision.trigger',
    'finishResearch:"research_output_limit"', 'finishResearch:"report_review"',
    'finishResearch:"output_recovery"', 'finishResearch:"research_recovery_exhausted"',
    'startRecovery:"context_budget"', 'startReasoningRecovery:"reasoning_repetition"',
    'startReasoningRecovery:', 'startReplan:{',
  ].sort(), reason);
  assert.equal([...source.matchAll(/recoveryCoordinator\.decide\(/gu)].length, 1, reason);
});

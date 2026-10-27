"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { RecoveryCoordinator } = require("../dist/recovery-coordinator");
const { classifyObjective } = require("../dist/tool-capability-registry");
const base = () => ({ objective: classifyObjective("검토해줘"), verdict: { progressed: true, noNewEvidence: false,
  failedReads: 0, budgetDenied: [], mutationOutcomes: [] }, pressure: { workingSetExhausted: false, measuredStage: "final" },
  limits: { reached: false }, state: { reportNowTriggers: 0, narrowAttempted: false, mutationFocus: false } });
for (const [name, change, action, trigger] of [
  ["hard limit", x => { x.limits.reached = true; }, "finalize", "research_round_limit"],
  ["review pressure", x => { x.pressure.workingSetExhausted = true; }, "finalize", "context_working_set_exhausted"],
  ["mutation pressure", x => { x.objective = classifyObjective("수정해"); x.pressure.workingSetExhausted = true; }, "narrow_to_mutation", null],
  ["build pressure", x => { x.objective = classifyObjective("빌드해"); x.pressure.workingSetExhausted = true; }, "narrow_to_mutation", null],
  ["second mutation pressure", x => { x.objective = classifyObjective("수정해"); x.pressure.workingSetExhausted = true; x.state.narrowAttempted = true; }, "finalize", "mutation_blocked_by_context"],
  ["budget denial excludes stagnation", x => { x.verdict.budgetDenied = ["denied"]; x.state.reportNowTriggers = 2; }, "continue", null],
  ["review repeated no evidence", x => { x.state.reportNowTriggers = 2; }, "finalize", "research_no_new_evidence"],
  ["explain repeated no evidence", x => { x.objective = classifyObjective("hello"); x.state.reportNowTriggers = 2; }, "finalize", "research_no_new_evidence"],
  ["mutation repeated no evidence", x => { x.objective = classifyObjective("수정해"); x.state.reportNowTriggers = 2; }, "narrow_to_mutation", null],
  ["attempted mutation repeated no evidence", x => { x.objective = classifyObjective("수정해"); x.objective.mutationState = "attempted"; x.state.reportNowTriggers = 2; }, "finalize", "research_no_new_evidence"],
  ["second mutation no evidence", x => { x.objective = classifyObjective("수정해"); x.state.reportNowTriggers = 2; x.state.narrowAttempted = true; }, "finalize", "mutation_blocked_by_context"],
  ["focus result restores profile even under pressure", x => { x.objective = classifyObjective("수정해"); x.state.mutationFocus = true; x.state.narrowAttempted = true; x.verdict.mutationOutcomes = ["applied"]; x.pressure.workingSetExhausted = true; }, "continue", null],
  ["focus without mutation stops after one chance", x => { x.objective = classifyObjective("수정해"); x.state.mutationFocus = true; x.state.narrowAttempted = true; }, "finalize", "mutation_blocked_by_context"],
  ["ordinary progress", () => {}, "continue", null],
]) test("continuation policy: " + name, () => {
  const input = base(); change(input); const before = JSON.stringify(input);
  const coordinator = new RecoveryCoordinator();
  assert.deepEqual(coordinator.decide(input), { action, trigger });
  assert.equal(JSON.stringify(input), before, "decide is pure");
  assert.equal(coordinator.narrowAttempted, false);
});

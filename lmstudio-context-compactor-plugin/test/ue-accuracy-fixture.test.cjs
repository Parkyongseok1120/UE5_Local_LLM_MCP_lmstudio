"use strict";

// Integrity of the synthetic UE accuracy set (plan W-A1). The fixture module
// lives in scripts/ so the test runner never mistakes it for a test.
const test = require("node:test");
const assert = require("node:assert/strict");
const fixture = require("../scripts/ue-accuracy-fixture.cjs");

const gradable = fixture.CASES.filter(entry => entry.variant !== "scoring_rule");
const reference = entry => ({ label: entry.expected.label,
  severity: Array.isArray(entry.expected.severity) ? entry.expected.severity[0] : null,
  retractedPriorFinding: entry.expected.retractPriorFinding ? true : undefined });

test("pairs 1-6 have one normal and one defect case with the same question focus", () => {
  for (let pair = 1; pair <= 6; pair++) {
    const cases = fixture.CASES.filter(entry => entry.pair === pair);
    assert.deepEqual(cases.map(entry => entry.variant).sort(), ["D", "N"]);
    const [a, b] = cases.map(entry => entry.question.replace(entry.folder, "<folder>"));
    assert.equal(a, b, `pair ${pair} leaks the variant through its question`);
  }
  assert.ok(fixture.CASE_BY_ID.get("P7").expected.retractPriorFinding);
  assert.equal(fixture.CASE_BY_ID.get("P8").variant, "scoring_rule");
});

test("every case reads only its own files and every source anchor resolves", () => {
  const folders = new Set();
  for (const entry of gradable) {
    assert.ok(!folders.has(entry.folder)); folders.add(entry.folder);
    assert.ok(entry.files.length > 0);
    for (const evidence of entry.evidence.filter(item => item.kind === "fixture_source")) {
      assert.ok(entry.files.includes(evidence.path), `${entry.id} cites a file outside its case: ${evidence.path}`);
      assert.ok(fixture.FILES.get(evidence.path).content.includes(evidence.anchor), `${entry.id} anchor missing in ${evidence.path}`);
    }
    for (const evidence of entry.evidence.filter(item => item.kind === "engine_source")) {
      assert.match(evidence.sha256, /^[0-9a-f]{64}$/);
      assert.ok(evidence.lines[0] <= evidence.lines[1]);
    }
  }
});

test("source files do not reveal the answer key", () => {
  for (const file of fixture.files.filter(item => item.kind === "source")) {
    assert.doesNotMatch(file.content, /\b(?:defect|bug|wrong|dropped|not_defect|normal case)\b/iu, file.path);
    assert.doesNotMatch(file.path, /(?:normal|defect|bug)/iu);
  }
  const range = fixture.readPayload("project://Source/Case07/HitPawn.cpp", 2, 3);
  assert.equal(range.ok, true);
  assert.equal(range.endLine, 3);
  assert.equal(fixture.readPayload("Source/Missing.cpp").errorCode, "NOT_FOUND");
});

test("grading follows the plan's false-positive, partial and pair-7 rules", () => {
  const graded = gradable.map(entry => fixture.gradeCase(entry.id, reference(entry)));
  assert.equal(fixture.summarize(graded).correctness.pass, true);

  const allDefect = gradable.map(entry => fixture.gradeCase(entry.id, { label: "defect", severity: "P1" }));
  const flagged = fixture.summarize(allDefect).correctness;
  assert.equal(flagged.normalFalsePositives, 6);
  assert.equal(flagged.pair7P1Maintained, 1);
  assert.equal(flagged.pass, false);

  // Marking everything unknown avoids false positives but earns only partial credit.
  const unknown = gradable.map(entry => fixture.gradeCase(entry.id, { label: "needs_runtime_proof" }));
  const hedged = fixture.summarize(unknown).correctness;
  assert.equal(hedged.normalFalsePositives, 0);
  assert.equal(hedged.defectsDetected, 0);
  assert.equal(hedged.pass, false);
  assert.equal(fixture.gradeCase("P1-N", { label: "needs_runtime_proof" }).score, 0.5);

  const kept = fixture.gradeCase("P7", { label: "not_defect", retractedPriorFinding: true, reappearedAfterRetraction: true });
  assert.equal(kept.p1Maintained, true);
  const prompted = fixture.gradeCase("P7", { label: "not_defect", retractedPriorFinding: true, retractedAfterUserRebuttal: true });
  assert.equal(prompted.p1Maintained, false);
  assert.equal(prompted.selfCorrected, false);
});

test("report delivery is reported apart from correctness (pair 8)", () => {
  const wrong = gradable.map(entry => fixture.gradeCase(entry.id, { label: "defect", severity: "P1" }));
  const summary = fixture.summarize(wrong, { reportDelivered: true, reportCompleted: true, taskCompleted: false });
  assert.equal(summary.delivery.reportDelivered, true);
  assert.equal(summary.delivery.objectiveSatisfied, null);
  assert.equal(summary.correctness.pass, false);
  assert.throws(() => fixture.gradeCase("P8", {}), /Not a gradable case/);
});

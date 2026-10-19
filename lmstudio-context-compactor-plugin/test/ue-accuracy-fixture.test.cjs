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
const verified = { reviewer: 'fixture-test', notes: 'Test-only review observations.', causeCorrect: true, evidenceCorrect: true,
  retractedPriorFinding: true, retractedAfterUserRebuttal: false, reappearedAfterRetraction: false };

test("pairs 1-6 have one normal and one defect case with the same question focus", () => {
  for (let pair = 1; pair <= 6; pair++) {
    const cases = fixture.CASES.filter(entry => entry.pair === pair);
    assert.deepEqual(cases.map(entry => entry.variant).sort(), ["D", "N"]);
    const [a, b] = cases.map(entry => entry.question.replaceAll(entry.folder, "<folder>"));
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
  const graded = gradable.map(entry => fixture.gradeCase(entry.id, reference(entry), verified));
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

  const kept = fixture.gradeCase("P7", { label: "not_defect" }, { ...verified, reappearedAfterRetraction: true });
  assert.equal(kept.p1Maintained, true);
  const prompted = fixture.gradeCase("P7", { label: "not_defect" }, { ...verified, retractedAfterUserRebuttal: true });
  assert.equal(prompted.p1Maintained, false);
  assert.equal(prompted.selfCorrected, false);
});

test('correct labels cannot award cause/evidence/retraction credit to themselves', () => {
  const unreviewed = gradable.map(entry => fixture.gradeCase(entry.id, { ...reference(entry), ...verified }));
  const summary = fixture.summarize(unreviewed).correctness;
  assert.equal(summary.labelPass, true);
  assert.equal(summary.pass, null);
  assert.equal(summary.semanticReviewPending, 13);
  assert.equal(unreviewed.find(item => item.pair === 7).selfCorrected, null);
  assert.equal(fixture.gradeCase('P3-D', { label: 'defect', severity: 'P1' }, { ...verified, causeCorrect: false }).qualifiedCorrect, false);
  assert.equal(fixture.gradeCase('P4-D', { label: 'defect', severity: 'P1' }, verified).qualifiedCorrect, false);
});

test('presentation and lever setup are present on both sides without inventing an owner', () => {
  for (const folder of ['Case07', 'Case02']) {
    assert.match(fixture.FILES.get(`Source/${folder}/HitPlayerController.cpp`).content, /CreateDefaultSubobject<UHitFeedbackComponent>/u);
    assert.match(fixture.FILES.get(`Source/${folder}/HitFeedbackComponent.cpp`).content, /IsLocalController\(\)/u);
  }
  for (const folder of ['Case03', 'Case09']) {
    const pawn = fixture.FILES.get(`Source/${folder}/LeverPawn.cpp`).content;
    assert.ok(pawn.indexOf('FocusedLever = *It') < pawn.indexOf('if (FocusedLever)'));
    assert.match(fixture.FILES.get(`Source/${folder}/LeverActor.cpp`).content, /DOREPLIFETIME\(ALeverActor, bOpen\)/u);
    assert.match(fixture.FILES.get(`Source/${folder}/CaseContext.md`).content, /Owner unset/u);
  }
  const slice = fixture.readPayload('Source/Case07/HitPawn.cpp', 2, 3);
  assert.equal(slice.truncated, false);
  assert.equal(slice.nextStartLine, undefined);
});

test("report delivery is reported apart from correctness (pair 8)", () => {
  const wrong = gradable.map(entry => fixture.gradeCase(entry.id, { label: "defect", severity: "P1" }));
  const summary = fixture.summarize(wrong, { reportDelivered: true, reportCompleted: true, taskCompleted: false });
  assert.equal(summary.delivery.reportDelivered, true);
  assert.equal(summary.delivery.objectiveSatisfied, null);
  assert.equal(summary.correctness.pass, false);
  assert.throws(() => fixture.gradeCase("P8", {}), /Not a gradable case/);
});

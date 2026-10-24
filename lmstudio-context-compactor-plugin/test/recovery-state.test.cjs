"use strict";

// Harness state the model sees after a failure: repeated identical failures are
// named, withdrawn note items are not re-presented as decisions, and a
// structure-only packet check cannot leave "verified" wording unannotated.

const assert = require("node:assert/strict");
const test = require("node:test");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { ToolRoundStagnationDetector, messageEvidenceTelemetry } = require("../dist/evidence-telemetry");
const { partialSearchScopeNotice, verificationScopeNotice } = require("../dist/delivery-controller");
const notes = require("../src/continuity-model-notes.js");

function exchange(id, name, args, result, history = Chat.empty()) {
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
    type: "function", id, name, arguments: args } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
    content: JSON.stringify(result) }] }));
  return history;
}

const notFound = { ok: false, errorCode: "NOT_FOUND", message: "File not found: engine://Engine/x/A.cpp" };

test("an identical failing request repeated across rounds is reported once with its error code", () => {
  const detector = new ToolRoundStagnationDetector();
  const args = { path: "engine://Engine/x/A.cpp" };
  const first = detector.observe(exchange("a1", "read_file", args, notFound).getMessagesArray());
  assert.equal(first.repeatedFailure, null);
  const second = detector.observe(exchange("a2", "read_file", args, notFound).getMessagesArray());
  assert.deepEqual({ ...second.repeatedFailure, key: undefined },
    { key: undefined, toolName: "read_file", errorCode: "NOT_FOUND", count: 2, firstNotice: true });
  const third = detector.observe(exchange("a3", "read_file", args, notFound).getMessagesArray());
  assert.equal(third.repeatedFailure.count, 3);
  assert.equal(third.repeatedFailure.firstNotice, false);
});

test("a changed path after a failure is not a repeated failure", () => {
  const detector = new ToolRoundStagnationDetector();
  detector.observe(exchange("b1", "read_file", { path: "engine://Engine/x/A.cpp" }, notFound).getMessagesArray());
  const changed = detector.observe(exchange("b2", "read_file", { path: "engine://Engine/y/A.cpp" }, notFound).getMessagesArray());
  assert.equal(changed.repeatedFailure, null);
});

test("projection telemetry records partial bodies, their notice and read handle without body text", () => {
  const projection = { kind: "archived_tool_result_projection", viewMode: "body_first_bounded", bodyField: "content",
    bodyTotalChars: 9000, bodyOmittedChars: 8488, bodyComplete: false, notice: "PARTIAL BODY: chars 0-512 of 9000",
    archiveRef: { evidenceId: "ev_1", readHandle: "h_1", view: "body" }, excerpt: "SECRET BODY" };
  const [result] = messageEvidenceTelemetry(exchange("p1", "read_file", { path: "project://A.cpp" }, projection)
    .getMessagesArray()).results;
  assert.equal(result.bodyComplete, false);
  assert.equal(result.noticePresent, true);
  assert.equal(result.archiveRef.readHandle, "h_1");
  assert.doesNotMatch(JSON.stringify(result), /SECRET BODY/u);
});

function noteWith(items) {
  return { ...notes.validateDraftNote(items), scope: {} };
}

test("superseded and named-by-supersedes items move to withdrawn, not decisions", () => {
  const draft = notes.validateDraftNote({ decisions: [{ statement: "Super::TakeDamage always returns 0", rationale: "memory" }] });
  const oldId = draft.decisions[0].id;
  const note = noteWith({ decisions: [
    { id: oldId, statement: "Super::TakeDamage always returns 0", rationale: "memory", status: "superseded" },
    { statement: "AActor::TakeDamage returns the incoming amount", rationale: "Actor.cpp read", supersedes: [oldId] },
  ], openQuestions: [{ question: "Is PlayerState created on spawn?" }] });
  const rendered = notes.renderAssistantNote(note);
  const json = JSON.parse(rendered.slice(rendered.indexOf("{\"modelNotes\"")));
  assert.deepEqual(json.modelNotes.decisions.map(d => d.statement), ["AActor::TakeDamage returns the incoming amount"]);
  assert.equal(json.modelNotes.withdrawn.length, 1);
  assert.equal(json.modelNotes.withdrawn[0].text, "Super::TakeDamage always returns 0");
  assert.match(rendered, /Withdrawn items are listed only so they are not re-asserted/u);
  assert.equal(json.modelNotes.openQuestions.length, 1);
});

test("a note without withdrawn items renders unchanged", () => {
  const note = noteWith({ decisions: [{ statement: "Read Actor.cpp", rationale: "implementation" }] });
  const rendered = notes.renderAssistantNote(note);
  assert.doesNotMatch(rendered, /Withdrawn items/u);
  assert.equal(JSON.parse(rendered.slice(rendered.indexOf("{\"modelNotes\""))).modelNotes.withdrawn, undefined);
});

for (const lifecycle of ["status", "supersedes"]) test(`withdrawn rejections leave the current list via ${lifecycle}`, () => {
  const old = { id: "old_rejection", hypothesis: "Team assignment can follow spawn", reason: "assumed ordering",
    ...(lifecycle === "status" ? { status: "superseded" } : {}) };
  const note = noteWith({ rejectedHypotheses: [old], decisions: [{ statement: "Team assignment follows spawn here",
    rationale: "read implementation", ...(lifecycle === "supersedes" ? { supersedes: [old.id] } : {}) }] });
  const rendered = notes.renderAssistantNote(note);
  const view = JSON.parse(rendered.slice(rendered.indexOf('{"modelNotes"'))).modelNotes;
  assert.deepEqual(view.rejectedHypotheses, []);
  assert.equal(view.withdrawn[0].id, old.id);
  assert.equal(view.withdrawn[0].kind, "rejectedHypothesis");
  assert.equal(view.decisions.length, 1);
  assert.equal(note.rejectedHypotheses.length, 1, "rendering must not mutate the stored note");
});

const structureOnly = { ok: true, summary: "structure_valid; facts_not_verified",
  validationScope: "packet_structure_and_record_consistency", factualEvidenceVerified: false };

function turnWithValidation(result) {
  const history = Chat.empty();
  history.append(ChatMessage.from({ role: "user", content: "Audit" }));
  return exchange("v1", "evidence_first_validate", { mode: "audit" }, result, history);
}

test("verified wording after a structure-only check gets one scope footer", () => {
  const notice = verificationScopeNotice(turnWithValidation(structureOnly), "## P1 — 기능 결함 (검증 완료)");
  assert.match(notice, /형식과 기록 일관성만 확인/u);
});

test("no footer when the answer already states the limit, when no validation ran, or when facts were verified", () => {
  assert.equal(verificationScopeNotice(turnWithValidation(structureOnly), "P1 (형식 검사만 통과, 미검증)"), null);
  assert.equal(verificationScopeNotice(turnWithValidation(structureOnly), "원인을 정리했습니다."), null);
  const plain = Chat.empty();
  plain.append(ChatMessage.from({ role: "user", content: "Audit" }));
  assert.equal(verificationScopeNotice(plain, "검증 완료"), null);
  assert.equal(verificationScopeNotice(turnWithValidation({ ...structureOnly, factualEvidenceVerified: true }), "검증 완료"), null);
});

test("a validation from an earlier user turn does not annotate the current answer", () => {
  const history = turnWithValidation(structureOnly);
  history.append(ChatMessage.from({ role: "assistant", content: "earlier answer" }));
  history.append(ChatMessage.from({ role: "user", content: "Next question" }));
  assert.equal(verificationScopeNotice(history, "검증 완료"), null);
});

for (const answer of ["A: 검증 완료.\nB: 미검증.", "B is not verified; A is verified.",
  "B: 형식 검사만 통과했고 A: 검증 완료."])
  test(`another claim's limitation cannot suppress a verification notice: ${answer}`, () => {
    assert.ok(verificationScopeNotice(turnWithValidation(structureOnly), answer));
  });

// The 2026-10-24 audit called a structure-only pass "validation passed".
for (const answer of ["Good, the validation passed.", "The packet passed validation.", "P1 (검증 통과)", "근거 패킷이 검증을 통과함"])
  test(`passed-validation wording after a structure-only check gets the scope footer: ${answer}`, () => {
    assert.match(verificationScopeNotice(turnWithValidation(structureOnly), answer), /형식과 기록 일관성만 확인/u);
  });

for (const answer of ["Validation has not passed yet.", "검증을 통과하지 못했습니다.", "형식 검증만 통과했습니다."])
  test(`negated or structure-qualified passed wording needs no notice: ${answer}`, () => {
    assert.equal(verificationScopeNotice(turnWithValidation(structureOnly), answer), null);
  });

for (const answer of ["검증 완료가 아닙니다.", "사실 확인 완료는 아님.", "The claim is not yet verified.",
  "형식 검증만 완료했습니다.", "검증을 마치지 않았습니다."])
  test(`locally negated or structure-qualified wording needs no notice: ${answer}`, () => {
    assert.equal(verificationScopeNotice(turnWithValidation(structureOnly), answer), null);
  });

// The 2026-10-18 run: a budget-cut search omitted the one assignment, and the
// answer said the flag is set nowhere. The footer names the partial search.
const partialSearch = { ok: true, query: "bIsDead", coverage: "partial", truncated: true, omissionReason: "response_budget",
  matchesReturned: 16, matchesOmitted: 1, absenceVerified: false, nextCursor: "c1", results: [] };
function auditTurn() {
  const history = Chat.empty();
  history.append(ChatMessage.from({ role: "user", content: "Audit" }));
  return history;
}

test("an absence claim over a partial search gets one footer naming the search and its omitted matches", () => {
  const history = exchange("s1", "search_files", { path: "project://Source", query: "bIsDead" }, partialSearch, auditTurn());
  for (const answer of ["코드 전체 어디에도 bIsDead = true가 없습니다. P0 확정.", "bIsDead is never set, so the death loop is broken (P0 confirmed)."]) {
    const notice = partialSearchScopeNotice(history, answer);
    assert.match(notice, /search_files 'bIsDead'\(1건 생략\)/u, answer);
    assert.match(notice, /부재의 증거가 아니므로/u);
  }
});

test("no partial-search footer once the same search was paged to completion, or without absence wording", () => {
  let history = exchange("s1", "search_files", { path: "project://Source", query: "bIsDead" }, partialSearch, auditTurn());
  assert.equal(partialSearchScopeNotice(history, "bIsDead의 대입 위치를 정리했습니다."), null);
  history = exchange("s2", "search_files", { path: "project://Source", query: "bIsDead", cursor: "c1" },
    { ok: true, query: "bIsDead", coverage: "complete", truncated: false, results: [] }, history);
  assert.equal(partialSearchScopeNotice(history, "어디에도 없습니다."), null);
});

test("qualified absence wording, other turns and complete searches need no partial-search footer", () => {
  const history = exchange("s1", "search_files", { path: "project://Source", query: "bIsDead" }, partialSearch, auditTurn());
  assert.equal(partialSearchScopeNotice(history, "설정 위치는 아직 확인하지 못했습니다(미확인)."), null);
  const earlier = exchange("s1", "search_files", { path: "project://Source", query: "bIsDead" }, partialSearch, auditTurn());
  earlier.append(ChatMessage.from({ role: "assistant", content: "earlier" }));
  earlier.append(ChatMessage.from({ role: "user", content: "Next" }));
  assert.equal(partialSearchScopeNotice(earlier, "어디에도 없습니다."), null);
  const complete = exchange("s1", "search_files", { path: "project://Source", query: "x" },
    { ok: true, query: "x", coverage: "complete", truncated: false, results: [] }, auditTurn());
  assert.equal(partialSearchScopeNotice(complete, "x는 존재하지 않습니다."), null);
});

test("a coverage-limited symbol lookup without verified absence is named too", () => {
  const history = exchange("l1", "unreal_symbol_lookup", { query: "ACharacter::OnDeath", sourceMode: "engine_local" },
    { ok: true, status: "coverage_limited", query: "ACharacter::OnDeath", matchCount: 0,
      coverage: { completeness: "partial", apiAbsenceVerified: false } }, auditTurn());
  assert.match(partialSearchScopeNotice(history, "ACharacter::OnDeath는 존재하지 않습니다."),
    /unreal_symbol_lookup 'ACharacter::OnDeath'\(범위 제한\)/u);
});

test("a Unity search page cut short or stopped early is named too; a complete Unity search is not", () => {
  const unityPartial = { status: "observed", items: [{ path: "Assets/A.cs", line: 3, text: "x" }], total: 4, truncated: true,
    nextCursor: "c", incomplete: false, absenceVerified: false };
  const history = exchange("u1", "search_files", { path: "Assets", query: "isDead", content: true }, unityPartial, auditTurn());
  assert.match(partialSearchScopeNotice(history, "isDead는 어디에도 true로 설정되지 않는다."), /search_files 'isDead'\(3건 생략\)/u);
  const stopped = exchange("u2", "search_files", { path: "Assets", query: "isDead", content: true },
    { ...unityPartial, total: 1, truncated: false, nextCursor: null, incomplete: true }, auditTurn());
  assert.match(partialSearchScopeNotice(stopped, "존재하지 않는다."), /search_files 'isDead'\(부분 결과\)/u);
  const complete = exchange("u3", "search_files", { path: "Assets", query: "isDead", content: true },
    { ...unityPartial, total: 1, truncated: false, nextCursor: null, incomplete: false }, auditTurn());
  assert.equal(partialSearchScopeNotice(complete, "존재하지 않는다."), null);
});

"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { selectGuidanceIntent, selectGuidanceTopics } = require("../dist/guidance-topic-selection");
const { autoGuidanceCandidates, selectDesignGuidance, focusedGuidanceCandidates, designGuidanceMode } = require("../dist/design-guidance");
const { guidanceObjective } = require("../src/continuity-objectives.js");
const { emptyReferenceSnapshot } = require("../dist/reference-context");
const { bundledGuidanceSections } = require("../dist/generated-design-guidance");
const scope = { engine: "unreal", source: "config" };
const intent = text => selectGuidanceIntent({ text, reason: "user_objective_verified" });

test("auto classifies only explicit bounded task text, not quoted examples or ordinary nouns", () => {
  for (const [text, expected] of [
    ["멀티플레이 RPC 구현해 줘", "multiplayer"], ["SSOT 구조를 검토해 줘", "design"],
    ["타이머 해제 수명 문제를 조사", "lifecycle"], ["컴파일 오류 조사해", "debugging"],
    ["들여쓰기 정리해", "code-style"], ["Implement server authority", "multiplayer"],
    ["Review the API contract", "debugging"], ["아이템 정의를 구현해 줘", "core"],
    ["기존 UI 버튼을 복제하는 기능을 구현해 줘", "core"],
    ["멀티플레이 예시를 설명하고 버튼을 구현해 줘", "core"],
    ["```cpp\n// 멀티플레이 구현\n```\n확인", "core"], ["> SSOT 구조 개선\n계속", "core"],
    ["`RPC` 고쳐 줘", "core"], ["멀티플레이는 말고 들여쓰기 정리", "core"],
    ["SSOT 구조와 멀티플레이 구현", "multiplayer"], ["C:/RPC/Architecture.cpp 수정", "core"],
    ["구조, 수명, 멀티플레이 문제를 찾아줘", "core"],
    ["다음 코드 블록을 확인해 줘.\n~~~text\n```cpp\nImplement server authority\n```\n~~~", "core"],
    ["확인해 줘.\n````text\n```cpp\nImplement server authority\n```\n````", "core"],
  ]) assert.equal(intent(text).primary, expected, text);
  assert.equal(intent("참고 문서는 넣지 말고 코드를 확인").omitted, true);
  assert.equal(intent("참고 문서를 추가하지 마. 타이머 수명을 수정해 줘.").omitted, true);
  assert.equal(intent("Don't add references. Fix lifecycle cleanup.").omitted, true);
  assert.equal(intent("X".repeat(4001)).reason, "objective_truncated");
  assert.equal(designGuidanceMode("auto"), "auto");
  assert.deepEqual(selectDesignGuidance("auto", scope), []);
  assert.deepEqual(focusedGuidanceCandidates("auto", scope, emptyReferenceSnapshot()), []);
});

test("the live audit objective selects design and lifetime within the existing section and token limits", () => {
  const objective = "해당 프로젝트에서 책임, 수명, 미연결, 코어 이상, 등등의 문제점과 SSOT, SOLID 법칙을 위반하는 것들을 찾을것";
  const chosen = intent(objective);
  assert.equal(chosen.primary, "design"); assert.equal(chosen.secondary, "lifecycle");
  assert.equal(chosen.reason, "explicit_task_topics");
  const snapshot = emptyReferenceSnapshot(), selected = selectGuidanceTopics(chosen, snapshot);
  assert.equal(selected.supplement, "lifecycle");
  const candidates = autoGuidanceCandidates(selected.primary, selected.supplement, scope, snapshot);
  assert.ok(candidates.length <= 4);
  assert.ok(candidates[0].ids.some(id => id.startsWith("design/")));
  assert.ok(candidates[0].ids.some(id => bundledGuidanceSections.some(s => s.id === id
    && s.documentId !== "core" && s.topics.includes("lifecycle"))));
  for (const c of candidates) { assert.ok(c.ids.length <= 6); assert.ok(c.ids.includes("core/proof")); }
  snapshot.autoSignals = [{ signal: "diagnostic_present", observationIds: ["current-error"], order: "last_observation" }];
  const diagnostic = selectGuidanceTopics(chosen, snapshot);
  assert.equal(diagnostic.primary, "design"); assert.equal(diagnostic.supplement, "debugging");
  assert.ok(diagnostic.reasons.includes("explicit_secondary_deferred_for_diagnostic"));
});

test("objective owner supplies continuation; missing source or truncated history stays unknown", () => {
  const message = (role, text, index) => ({ role, text, index, toolRequests: [], toolResults: [] });
  const messages = [message("user", "멀티플레이 RPC 구현", 0), message("assistant", "구조 바꾸겠습니다", 1), message("user", "계속해", 2)];
  assert.equal(selectGuidanceIntent(guidanceObjective(messages)).primary, "multiplayer");
  const prior = { activeObjective: { text: "멀티플레이 RPC 구현", messageIndex: 0 } };
  assert.equal(guidanceObjective([message("user", "계속해", 1)], prior).reason, "objective_completeness_unknown");
  assert.equal(guidanceObjective([message("user", "RPC 구현 " + "x".repeat(4000), 0)]).reason, "objective_truncated");
});

test("only current auto observations add a supplement; stale manual signals cannot rank or reinsert diagnostics", () => {
  const snapshot = emptyReferenceSnapshot();
  snapshot.signals = ["diagnostic_present", "observed_source_changed"];
  snapshot.items = [{ id: "old", kind: "diagnostics", data: { diagnostics: ["STALE_ERROR"] } }];
  const primary = intent("SSOT 구조 검토");
  assert.equal(selectGuidanceTopics(primary, snapshot).supplement, undefined);
  snapshot.autoSignals = [{ signal: "compilation_pending", observationIds: ["new"], order: "last_observation" }];
  snapshot.items.push({ id: "new", kind: "operation", data: { operationStatus: "pending" } });
  const selected = selectGuidanceTopics(primary, snapshot);
  assert.equal(selected.primary, "design"); assert.equal(selected.supplement, "debugging");
  const candidates = autoGuidanceCandidates(selected.primary, selected.supplement, scope, snapshot);
  assert.ok(candidates.length <= 4);
  assert.ok(candidates[0].ids.includes("debugging/verification"));
  for (const c of candidates) {
    assert.ok(c.ids.length <= 6); assert.ok(c.ids.includes("core/proof"));
    assert.doesNotMatch(c.referenceData || "", /STALE_ERROR/);
    if (c.ids.some(id => id.startsWith("debugging/"))) assert.ok(c.ids.some(id => id.startsWith("design/")));
  }
  assert.deepEqual(candidates.at(-1).ids, ["core/proof"]);
  snapshot.autoSignals[0].order = "order_unknown";
  assert.equal(selectGuidanceTopics(primary, snapshot).supplement, undefined);
});

test("all activated contract packs carry the short common API, lifetime and completion criteria", () => {
  const { bundledGuidanceSections } = require("../dist/generated-design-guidance");
  const proof = bundledGuidanceSections.find(s => s.id === "core/proof");
  assert.ok(proof.text.length <= 900);
  for (const text of ["적용 버전", "종료·늦은 결과", "실제 성공 반환", "미확인", "추가 허가 조건"])
    assert.ok(proof.text.includes(text));
  for (const topic of ["design", "debugging", "code-style", "lifecycle", "multiplayer"])
    for (const c of autoGuidanceCandidates(topic, undefined, scope, emptyReferenceSnapshot()))
      assert.ok(c.instruction.includes(proof.text));
});

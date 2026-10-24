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

test('explicit API and macro tasks include an engine pack before optional common packs',()=>{
  for(const text of ['언리얼 API 제대로 확인해서 위의 계획대로 다시 해봐','UPROPERTY와 UFUNCTION 사용이 맞는지 확인해',
    'FORCEINLINE 필요한 함수인지 검토해','델리게이트 바인딩과 해제를 확인해','Enhanced Input API 구현해',
    'Review UFUNCTION specifiers','Check SerializeField usage']) assert.equal(intent(text).primary,'debugging',text);
  for(const engine of ['unreal','unity']) {
    const cs=autoGuidanceCandidates('debugging',undefined,{engine,source:'config'},emptyReferenceSnapshot());
    assert.ok(cs[0].ids.includes(engine === 'unreal' ? 'unreal/api' : 'unity/wiring'),cs[0].ids.join(','));
    assert.match(cs[0].instruction,engine === 'unreal' ? /UFUNCTION/ : /SerializeField/);
    assert.ok(cs.length<=7); cs.forEach(c=>assert.ok(c.ids.length<=6));
    assert.deepEqual(cs.at(-1).ids,['core/proof']);
  }
  assert.equal(intent('UFUNCTION이 뭔지 설명해').primary,'core');
  assert.equal(intent('> UFUNCTION 검토\n계속').primary,'core');
});

test('returned code context selects optional API guidance but never excludes user policy',()=>{
  const s=emptyReferenceSnapshot(); s.autoSignals=[{signal:'api_contract_observed',observationIds:['file'],order:'last_observation'}];
  assert.equal(selectGuidanceTopics(intent('캐릭터 기능 구현해'),s).supplement,'debugging');
  assert.equal(selectGuidanceTopics(intent('참고 문서를 넣지 마'),s).supplement,undefined);
  s.autoSignals[0].order='order_unknown';
  assert.equal(selectGuidanceTopics(intent('캐릭터 기능 구현해'),s).supplement,undefined);
});

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
    ["구조, 수명, 멀티플레이 문제를 찾아줘", "multiplayer"],
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

test("network work wording selects multiplayer; copies, examples, quotes and exclusions do not", () => {
  for (const text of ["네트워크 쪽 문제를 찾아줘", "RPC 호출 경로를 점검해", "리플리케이션 설정을 확인해",
    "Review the NetMulticast usage", "Check RepNotify handlers", "Fix the networking bug", "OnRep_Health 수정해"])
    assert.equal(intent(text).primary, "multiplayer", text);
  for (const text of ["UI 위젯을 복제하는 기능을 구현해", "네트워크 예시를 설명하고 버튼을 구현해", "`OnRep_Health` 확인",
    "네트워크 쪽은 말고 들여쓰기 정리", "Replicate the crash and fix it", "네트워크 쪽"])
    assert.equal(intent(text).primary, "core", text);
});

test("more than two explicit topics keep the first two in sentence then rule order", () => {
  // The cbc227f3 objective: network + design + lifecycle + API macros.
  const observed = intent("캐릭터 쪽과 네트워크 쪽에서 SSOT, SOLID, 계약, 책임, 수명관리, 또는 UE API 매크로가 잘못된 것들을 찾아서 보고해줘.");
  assert.deepEqual([observed.primary, observed.secondary, observed.reason], ["multiplayer", "design", "explicit_task_topics_limited"]);
  const candidates = autoGuidanceCandidates(observed.primary, observed.secondary, scope, emptyReferenceSnapshot());
  assert.ok(candidates[0].ids.includes("unreal-networking/authority"), candidates[0].ids.join(","));
  assert.match(candidates[0].instruction, /Server\/Client\/NetMulticast RPC/);
  for (const c of candidates) assert.ok(c.ids.length <= 6);
  const ordered = intent("들여쓰기 정리해. 구조 검토하고 타이머 수명 확인해");
  assert.deepEqual([ordered.primary, ordered.secondary, ordered.reason], ["code-style", "design", "explicit_task_topics_limited"]);
  assert.equal(intent("SSOT 구조와 멀티플레이 구현").reason, "explicit_task_topics");
});

test("an action-less correction is not inherited here; a returned API observation still backs debugging", () => {
  const followUp = intent("Die 없어.... 내부 APi에 없다고 그런거...");
  assert.deepEqual([followUp.primary, followUp.reason], ["core", "no_explicit_topic"]);
  const snapshot = emptyReferenceSnapshot();
  snapshot.autoSignals = [{ signal: "api_contract_observed", observationIds: ["header"], order: "last_observation" }];
  const selected = selectGuidanceTopics(followUp, snapshot);
  assert.deepEqual([selected.primary, selected.supplement], ["core", "debugging"]);
  const candidates = autoGuidanceCandidates(selected.primary, selected.supplement, scope, snapshot);
  assert.ok(candidates[0].ids.includes("unreal/api"), candidates[0].ids.join(","));
  assert.match(candidates[0].instruction, /Client RPC/);
});

test("the live audit objective selects design and lifetime within the existing section and token limits", () => {
  const objective = "해당 프로젝트에서 책임, 수명, 미연결, 코어 이상, 등등의 문제점과 SSOT, SOLID 법칙을 위반하는 것들을 찾을것";
  const chosen = intent(objective);
  assert.equal(chosen.primary, "design"); assert.equal(chosen.secondary, "lifecycle");
  assert.equal(chosen.reason, "explicit_task_topics");
  const snapshot = emptyReferenceSnapshot(), selected = selectGuidanceTopics(chosen, snapshot);
  assert.equal(selected.supplement, "lifecycle");
  const candidates = autoGuidanceCandidates(selected.primary, selected.supplement, scope, snapshot);
  assert.ok(candidates.length <= 5);
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
  assert.ok(candidates.length <= 5);
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

test("auto mode annotates same-major unvalidated engines and omits other majors or unknown versions", () => {
  const snapshot = version => { const s = emptyReferenceSnapshot(); s.applicability.engineVersion = version; return s; };
  const first = (version, mode = "lifecycle") => autoGuidanceCandidates(mode, undefined, scope, snapshot(version))[0];
  const annotated = first("5.3.0");
  assert.ok(annotated.ids.includes("unreal/lifetime"));
  assert.match(annotated.instruction, /\[unvalidated for unreal 5\.3\.0; validated on 5\.7, 5\.8\]/);
  assert.ok(annotated.omissionReasons.includes("contract_sections_engine_version_unvalidated_annotated"));
  for (const version of ["4.27.2", undefined]) {
    const omitted = first(version);
    assert.ok(!omitted.ids.includes("unreal/lifetime"), String(version));
    assert.ok(omitted.omissionReasons.some(r => /^contract_sections_(engine_version_unvalidated|version_unverified)$/.test(r)), String(version));
  }
  const unity = { engine: "unity", source: "config" };
  const unitySnapshot = version => { const s = emptyReferenceSnapshot(); s.applicability.engineVersion = version; return s; };
  const unityFirst = autoGuidanceCandidates("lifecycle", undefined, unity, unitySnapshot("6000.3.1f1"))[0];
  assert.ok(unityFirst.ids.includes("unity/lifetime"));
  assert.match(unityFirst.instruction, /\[unvalidated for unity 6000\.3\.1f1; validated on 6000\.0\]/);
});

test("a follow-up without its own topic keeps the previous request's explicit topics", () => {
  const { selectFollowUpGuidanceIntent } = require("../dist/guidance-topic-selection");
  const verified = text => ({ text, reason: "user_objective_verified" });
  const original = verified("캐릭터 쪽과 네트워크 쪽에서 SSOT, SOLID, 계약, 책임, 생명주기, 또는 UE API 매크로가 잘못된 것들을 찾아서 정리해줘.");
  const followUp = selectFollowUpGuidanceIntent(verified("Die 없어.... 엔진 APi에 없다고 그랬지..."), original);
  assert.equal(followUp.primary, "multiplayer");
  assert.equal(followUp.reason, "previous_request_topic");
  // The latest request's own topic, exclusion or omission wins.
  assert.equal(selectFollowUpGuidanceIntent(verified("코드 스타일 포맷 정리해줘"), original).primary, "code-style");
  assert.equal(selectFollowUpGuidanceIntent(verified("네트워크는 말고 다시 봐"), original).reason, "topic_exclusion_ambiguous");
  assert.equal(selectFollowUpGuidanceIntent(verified("지침은 넣지 말고 계속"), original).omitted, true);
  // Nothing to carry: no earlier request, or an earlier request without a topic.
  assert.equal(selectFollowUpGuidanceIntent(verified("Die 없어"), undefined).reason, "no_explicit_topic");
  assert.equal(selectFollowUpGuidanceIntent(verified("Die 없어"), verified("지금 프로젝트 어디야")).reason, "no_explicit_topic");
  assert.equal(selectFollowUpGuidanceIntent(verified("Die 없어"), { reason: "objective_truncated" }).reason, "no_explicit_topic");
});

test("returned RPC/replication source adds the multiplayer supplement; explicit topics and diagnostics keep priority", () => {
  const network = { signal: "network_contract_observed", observationIds: ["pawn"], order: "earlier_observation" };
  const api = { signal: "api_contract_observed", observationIds: ["pawn"], order: "last_observation" };
  const snapshot = signals => Object.assign(emptyReferenceSnapshot(), { autoSignals: signals });
  const core = { primary: "core", reason: "no_explicit_topic" };
  const chosen = selectGuidanceTopics(core, snapshot([network, api]));
  assert.equal(chosen.supplement, "multiplayer");
  assert.ok(chosen.reasons.includes("returned_network_contract_context"));
  assert.equal(selectGuidanceTopics(core, snapshot([api])).supplement, "debugging");
  assert.equal(selectGuidanceTopics({ primary: "design", secondary: "lifecycle", reason: "explicit_task_topics" },
    snapshot([network])).supplement, "lifecycle", "an explicit secondary topic wins");
  assert.equal(selectGuidanceTopics({ primary: "multiplayer", reason: "explicit_task_topic" }, snapshot([network])).supplement, undefined);
  const diagnostic = { signal: "diagnostic_present", observationIds: ["err"], order: "last_observation" };
  assert.equal(selectGuidanceTopics(core, snapshot([network, diagnostic])).supplement, "debugging");
  const candidates = autoGuidanceCandidates(chosen.primary, chosen.supplement, scope, emptyReferenceSnapshot());
  assert.ok(candidates[0].ids.some(id => id.startsWith("unreal-networking/")), "the Unreal networking contract is a candidate");
});

test("reserved engine contract sections get a data-free candidate before falling back to fewer sections", () => {
  const snapshot = emptyReferenceSnapshot();
  snapshot.applicability.engineVersion = "5.8"; snapshot.applicability.engineVersionSource = "installed_engine_source";
  snapshot.autoSignals = [{ signal: "network_contract_observed", observationIds: ["header"], order: "last_observation" }];
  snapshot.items = [{ id: "header", kind: "file", engine: "unreal", project: "C:/P/P.uproject", origin: {},
    data: { path: "Source/P/HitPawn.h", networkContractContext: "lexical_signal_only" } }];
  const selected = selectGuidanceTopics(intent("Does the hit marker appear for the player it is meant for?"), snapshot);
  assert.deepEqual([selected.primary, selected.supplement], ["core", "multiplayer"]);
  const candidates = autoGuidanceCandidates(selected.primary, selected.supplement, scope, snapshot);
  const withContract = candidates.filter(c => c.ids.includes("unreal-networking/engine-defaults"));
  assert.ok(withContract.some(c => c.referenceData) && withContract.some(c => !c.referenceData), candidates.map(c => c.ids.join(",")).join(" | "));
  const dataFree = candidates.findIndex(c => c.ids.includes("unreal-networking/engine-defaults") && !c.referenceData);
  assert.ok(dataFree < candidates.findIndex(c => !c.ids.includes("unreal-networking/engine-defaults")));
  assert.deepEqual(candidates.at(-1).ids, ["core/proof"]);
  assert.ok(candidates.length <= 5);
});

// The 2026-10-24 audit ran with only four sections under the 2,048-token
// default: the claim policies (unreal/verification) never reached the model,
// and the contract facts were blocked by an unverified engine version.
const AUDIT_REQUEST = '캐릭터 쪽과 네트워크 RPC 계약 쪽과 SSOT, SOLID, 계약, 책임, 수명관리, 또는 UE API 매크로가 잘못된 것들을 찾아서 보고해줘.';

test('an audit request reserves the engine claim policies and, once the version is known, the contract facts', () => {
  const selected = intent(AUDIT_REQUEST);
  assert.deepEqual([selected.primary, selected.secondary], ['multiplayer', 'design']);
  const unknown = autoGuidanceCandidates(selected.primary, selected.secondary, scope, emptyReferenceSnapshot());
  assert.ok(unknown[0].ids.includes('unreal/verification'), unknown[0].ids.join(','));
  assert.ok(unknown[0].ids.includes('unreal/responsibility'), 'the design supplement keeps its own section');
  const known = emptyReferenceSnapshot();
  Object.assign(known.applicability, { engineVersion: '5.7.4', engineVersionSource: 'installed_engine_source' });
  const cs = autoGuidanceCandidates(selected.primary, selected.secondary, scope, known);
  assert.ok(cs[0].ids.includes('unreal-networking/engine-defaults') && cs[0].ids.includes('unreal/verification'), cs[0].ids.join(','));
  assert.match(cs[0].instruction, /ue\.net\.client-rpc-callspace/);
  // 5.7 is a validated contract version, so the facts arrive without an unvalidated label.
  assert.doesNotMatch(cs[0].instruction, /unvalidated for unreal 5\.7\.4/);
  // Under a tight allowance the contract facts and claim policies outlast the
  // engine pack and supplement before selection falls back to the top pack.
  const reduced = cs.find(c => !c.ids.includes('unreal-networking/authority'));
  assert.deepEqual(reduced.ids, ['core/proof', 'multiplayer/authority', 'unreal-networking/engine-defaults', 'unreal/verification']);
  assert.ok(cs.some(c => c.ids.join(',') === 'core/proof,multiplayer/authority,unreal/verification'));
  assert.ok(cs.length <= 7);
});

test('the default guidance allowance fits the audit selection', () => {
  const { GUIDANCE_DEFAULTS } = require('../dist/config-defaults');
  assert.ok(GUIDANCE_DEFAULTS.maxTokens >= 6144);
});

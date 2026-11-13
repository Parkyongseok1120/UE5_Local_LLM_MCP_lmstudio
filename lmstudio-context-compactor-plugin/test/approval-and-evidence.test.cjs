"use strict";
// Conversation 1794469194476: "진행해" after a patch proposal ran as a review
// (12 reads, no edit); another "진행해" ran as a change but ended with example
// code and a confirmation question; the spawner default was "fixed" with a
// comment and reported 수정 완료. Approvals now bind to the accepted proposal,
// such an answer gets one return to the tools, and the turn's tool results are
// set against the answer's completion claims.
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { executionObjective } = require("../dist/tool-capability-registry");
const { ExecutionReturn, EXECUTION_RETURN_FACT, EXECUTION_RETURN_STATUS } = require("../dist/execution-return");
const { commentOnlyEdit, objectiveEvidence, objectiveEvidenceNotice, objectiveSatisfiedBy } = require("../dist/objective-evidence");
const { fakeController, handlePredictionLoop } = require("./helpers/prediction-loop-fixture.cjs");
const { pair, tool } = require("./helpers/v4.cjs");

const say = (role, text) => ChatMessage.create(role, text);
const objectiveFor = (request, proposal, approval) => executionObjective([say("user", request), say("assistant", proposal), say("user", approval)]);
const PROPOSAL = "## 수정 제안\n1. FSM 책임을 컴포넌트로 이전\n2. 스포너 기본 클래스 지정\n3. 공격 판정에 HasAuthority 검사\n1~3번을 패치하겠습니다. 진행할까요?";

test("a short approval runs the change the last answer proposed, and nothing wider", () => {
  const approved = objectiveFor("적 설계 검토해줘", PROPOSAL, "진행해");
  assert.equal(approved.kind, "mutate");
  assert.equal(approved.approvedItems.length, 3);
  assert.equal(objectiveFor("검토해줘", "수정 후 빌드까지 하겠습니다.", "ㅇㅇ 해줘").kind, "build");
  assert.equal(objectiveFor("review the enemy code", "I will fix items 1-2.", "go ahead").kind, "mutate");
  for (const [request, proposal, approval, why] of [
    ["적 설계 검토해줘", "다음 파일도 검토할까요?", "진행해", "a further-review offer stays a review"],
    ["적 설계 검토해줘", "1번을 수정하겠습니다. 아니면 더 검토할까요?", "진행해", "two offers leave the approval ambiguous"],
    ["코드 수정하지 말고 검토만 해줘", "1번을 수정하겠습니다.", "진행해", "an earlier no-change instruction stands"],
    ["적 설계 검토해줘", PROPOSAL, "아직 진행하지 마", "a declined approval"],
    ["적 설계 검토해줘", PROPOSAL, "진행해. 그런데 1번은 왜 문제인지 자세히 다시 설명해 줄 수 있을까요? 근거도 같이 부탁해요.", "a long message is not a short approval"],
  ]) assert.notEqual(objectiveFor(request, proposal, approval).kind, "mutate", why);
  assert.deepEqual(objectiveFor("수정 계획 세워봐", "계획: 1. A 2. B", "진행해").approvedItems, [], "an approved plan counts as an approval");
});

test("an approved change answered with text only gets one return to the tools", () => {
  const statuses = [], debug = [], facts = new Map();
  const host = { createStatus: state => statuses.push(state), debug: value => debug.push(value) };
  const store = { set: (key, text) => facts.set(key, text) };
  const approved = { kind: "mutate", requiredEffects: new Set(["read", "mutation"]), mutationState: "none", approvedItems: ["a"] };
  const eos = { finishReason: "eosFound" };
  const due = (objective, options = {}) => new ExecutionReturn(host, true, () => options.writable ?? true)
    .due(objective, options.captured || eos, options.answer || "예시 코드입니다. 적용할까요?", store, options.aborted || false);
  const once = new ExecutionReturn(host, true, () => true);
  assert.equal(once.due(approved, eos, "적용할까요?", store, false), true);
  assert.equal(once.due(approved, eos, "적용할까요?", store, false), false, "once per turn");
  assert.equal(facts.get("execution_return"), EXECUTION_RETURN_FACT);
  assert.equal(statuses[0].text, EXECUTION_RETURN_STATUS);
  assert.equal(debug[0].event, "execution_return_scheduled");
  assert.equal(due({ ...approved, approvedItems: undefined }), false, "a direct request may be answered in text");
  assert.equal(due({ ...approved, kind: "build" }), false);
  assert.equal(due({ ...approved, mutationState: "attempted" }), false);
  assert.equal(due(approved, { answer: "이미 적용되어 있어 변경이 필요 없습니다." }), false);
  assert.equal(due(approved, { writable: false }), false);
  assert.equal(due(approved, { aborted: true }), false);
  assert.equal(due(approved, { captured: { finishReason: "maxPredictedTokensReached" } }), false);
});

for (const answer of ["예시 코드는 다음과 같습니다.\n```cpp\nSpawnerClass = AEnemy::StaticClass();\n```\n이대로 적용할까요?", "1~3번은 이미 적용되어 있어 변경이 필요 없습니다."])
test(`after an approval, a text-only answer ${answer.startsWith("1~3") ? "reporting no change needed ends" : "returns once to the tools"}`, async () => {
  const history = Chat.from([{ role: "user", content: "적 설계 검토해줘" }, { role: "assistant", content: PROPOSAL }, { role: "user", content: "진행해" }]);
  let calls = 0, second;
  const model = { identifier: "approval-replay", async getContextLength() { return 65536; },
    async applyPromptTemplate(chat) { return chat.toString(); }, async countTokens() { return 1000; },
    async act(chat, tools, options) {
      calls++;
      if (calls === 2) second = { input: chat.toString(), tools: tools.map(t => t.name) };
      options.onPredictionCompleted({ stats: { stopReason: "eosFound" } });
      options.onMessage(ChatMessage.create("assistant", calls === 1 ? answer : "적용을 위해 파일을 먼저 확인하겠습니다."));
    } };
  const ctl = fakeController(history, model, { designGuidanceMode: "off" }, [tool("read_file"), tool("replace_in_file"), tool("write_file")]);
  await handlePredictionLoop(ctl);
  const noOp = answer.startsWith("1~3");
  assert.equal(calls, noOp ? 1 : 2);
  if (!noOp) {
    assert.ok(second.tools.includes("replace_in_file"), "the return has the mutation tools");
    assert.ok(second.input.includes(EXECUTION_RETURN_FACT), "and is told why");
    assert.ok(ctl.statuses.some(status => status.state?.text === EXECUTION_RETURN_STATUS));
  }
});

test("a comment-only edit is not a change, and the answer is held to what the tools did", () => {
  assert.equal(commentOnlyEdit("replace_in_file", { oldText: "SpawnClass = nullptr;", newText: "SpawnClass = nullptr; // = AEnemy::StaticClass();" }), true);
  assert.equal(commentOnlyEdit("replace_in_file", { oldText: "a = 1;", newText: "a = 2;" }), false);
  assert.equal(commentOnlyEdit("write_file", { content: "// all comment" }), false, "a new file is a change");
  assert.equal(commentOnlyEdit("apply_edit_bundle", { edits: [{ oldText: "x;", newText: "x; /* note */" }, { oldText: "y;", newText: "z;" }] }), false);
  const history = Chat.from([{ role: "user", content: "남은 것 다 수정해줘" }]);
  const add = (name, args, value, id) => { for (const message of pair(name, args, value, id)) history.append(message); };
  add("replace_in_file", { path: "project://Source/Game/SpawnerSubsystem.cpp", oldText: "Cls = nullptr;", newText: "Cls = nullptr; // AEnemy" }, { ok: true }, "e1");
  add("replace_in_file", { path: "project://Source/Game/Attack.cpp", oldText: "Sweep();", newText: "if (HasAuthority()) Sweep();" }, { ok: true }, "e2");
  add("build_unreal_project", {}, { ok: true, summary: "BUILD SUCCEEDED" }, "b1");
  const evidence = objectiveEvidence(history);
  assert.deepEqual(evidence, { level: "built", edits: 2, functionalEdits: 1, commentOnlyPaths: ["Source/Game/SpawnerSubsystem.cpp"],
    buildAfterLastEdit: "succeeded" });
  const mutate = { kind: "mutate", requiredEffects: new Set(["read", "mutation"]), mutationState: "applied" };
  assert.match(objectiveEvidenceNotice(mutate, evidence, "SpawnerSubsystem 기본값 수정 완료\nAttack 권한 검사 추가 완료"),
    /SpawnerSubsystem\.cpp: 주석·공백만 바뀌었습니다/u);
  assert.equal(objectiveEvidenceNotice(mutate, evidence, "Attack 권한 검사를 추가했습니다."), null, "no claim about the commented file");
  assert.equal(objectiveSatisfiedBy({ ...mutate, kind: "build" }, evidence), true, "a build request is settled by a build after the last edit");
  assert.equal(objectiveSatisfiedBy(mutate, evidence), null, "a change request is never settled by the evidence alone");
  add("replace_in_file", { path: "project://Source/Game/Attack.cpp", oldText: "if (HasAuthority()) Sweep();", newText: "Sweep2();" }, { ok: true }, "e3");
  assert.equal(objectiveEvidence(history).buildAfterLastEdit, "none");
  assert.match(objectiveEvidenceNotice(mutate, objectiveEvidence(history), "빌드 성공했습니다."), /마지막 수정 이후 성공한 빌드가 없습니다/u);
  const empty = objectiveEvidence(Chat.from([{ role: "user", content: "설명 작성해줘" }]));
  assert.equal(objectiveEvidenceNotice(mutate, empty, "작성 완료했습니다."), null, "a text deliverable is not a file change");
  assert.match(objectiveEvidenceNotice(mutate, empty, "세 파일을 수정했습니다."), /적용된 파일 수정이 없습니다/u);
});

test("only a matching build can close a compile-only request; changes plus builds stay open", () => {
  const { classifyObjective } = require("../dist/tool-capability-registry");
  const evidence = {edits:1, functionalEdits:1, commentOnlyPaths:[], buildAfterLastEdit:"succeeded",
    buildIdentity:{project:"C:/Games/A.uproject",target:"AEditor",configuration:"Development",platform:"Win64"}};
  const satisfied = request => objectiveSatisfiedBy(classifyObjective(request), evidence);
  for (const request of ["빌드해봐", "컴파일 오류 수정하고 빌드해", "fix compiler errors and build", "build AEditor Win64 Development"])
    assert.equal(satisfied(request), true, request);
  for (const request of ["P1 모든 사항 수정하고 빌드까지 해봐", "refactor then build", "실행해", "build BEditor Shipping", "build B.uproject", "build and benchmark"])
    assert.equal(satisfied(request), null, request);
  const objective = classifyObjective("P1 모든 사항 수정하고 빌드까지 해봐");
  assert.equal(objectiveEvidenceNotice(objective, evidence, "빌드 성공. 동작 미확인."), null);
  assert.match(objectiveEvidenceNotice(objective, evidence, "P1 전체 수정 완료"), /변경 전체/);
  const h = Chat.from([{role:"user",content:"빌드해봐"}]);
  for (const msg of pair("replace_in_file", {path:"project://A.cpp"}, {ok:true,canonicalProject:"C:/B/A.uproject"}, "change")) h.append(msg);
  for (const msg of pair("build_unreal_project", {}, {ok:true,project:{projectPath:"D:/Other/A.uproject"}}, "build")) h.append(msg);
  assert.equal(objectiveEvidence(h).buildAfterLastEdit, "none");
});

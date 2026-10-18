"use strict";

// Harness verification only: a scripted fake model drives the recovery
// harness so fault injection, recording and scoring can be checked. These
// results say nothing about any real model's recovery behaviour.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { ChatMessage } = require("@lmstudio/sdk");
const { runScenario } = require("../eval/recovery/recovery-harness.cjs");
const { scoreRun } = require("../eval/recovery/score-recovery.cjs");

const scenarios = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "eval", "recovery", "recovery-scenarios.json"), "utf8"));
const scenario = id => scenarios.scenarios.find(item => item.id === id);

function parsed(content) {
  return JSON.parse(content[0].text);
}

/** steps: (previousResult) => { tool, args } | { text } */
function scriptedModel(steps) {
  let step = 0, previous = null, calls = 0;
  return {
    identifier: "scripted-fake",
    async getContextLength() { return 65536; },
    async applyPromptTemplate(chat) { return chat.toString(); },
    async countTokens(text) { return Math.ceil(String(text).length / 4); },
    async act(_chat, tools, opts) {
      const action = steps[Math.min(step++, steps.length - 1)](previous);
      if (action.text !== undefined) {
        opts.onPredictionCompleted({ stats: { stopReason: action.stopReason || "eosFound",
          predictedTokensCount: action.stopReason === "maxPredictedTokensReached" ? 8192 : 20 } });
        opts.onMessage(ChatMessage.create("assistant", action.text));
        return;
      }
      const tool = tools.find(item => item.name === action.tool);
      assert.ok(tool, `tool ${action.tool} exposed`);
      const id = `call-${calls++}`;
      const content = await tool.implementation(action.args, {});
      previous = parsed(content);
      opts.onPredictionCompleted({ stats: { stopReason: "toolCalls" } });
      opts.onMessage(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
        toolCallRequest: { type: "function", id, name: action.tool, arguments: action.args } }] }));
      opts.onMessage(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
        content: JSON.stringify(previous) }] }));
      opts.onRoundEnd();
      throw opts.signal.reason;
    },
  };
}

const WRONG = "engine://Engine/Source/Runtime/Engine/Private/GameFramework/PlayerController.cpp";

test("wrong engine path: following the suggestion passes; repeating and claiming no source fails", async () => {
  const good = await runScenario(scenario("wrong_engine_path"), scenarios.fixture, scriptedModel([
    () => ({ tool: "read_file", args: { path: WRONG } }),
    previous => ({ tool: previous.suggestion.tool, args: previous.suggestion.args }),
    previous => ({ tool: "read_file", args: { path: previous.results[0].uri } }),
    () => ({ text: "APlayerController::PostLogin은 InitInputSystem()을 호출한다." }),
  ]));
  const goodScore = scoreRun(scenario("wrong_engine_path"), good);
  assert.equal(goodScore.status, "pass", JSON.stringify(goodScore.checks));
  assert.equal(good.calls[0].result.errorCode, "NOT_FOUND");
  assert.equal(good.calls[1].args.path, "engine://Engine/Source/Runtime/Engine");
  assert.ok(good.inputs.length >= 2 && good.debugEvents.some(e => e.event === "direct_round_observation"));

  const bad = await runScenario(scenario("wrong_engine_path"), scenarios.fixture, scriptedModel([
    () => ({ tool: "read_file", args: { path: WRONG } }),
    () => ({ tool: "read_file", args: { path: WRONG } }),
    () => ({ text: "엔진 Private 소스가 설치되지 않아 확인할 수 없습니다." }),
  ]));
  const badScore = scoreRun(scenario("wrong_engine_path"), bad);
  assert.equal(badScore.status, "fail");
  const failed = badScore.checks.filter(c => c.status === "fail").map(c => c.id);
  assert.ok(failed.includes("no_identical_repeat") && failed.some(id => id.startsWith("forbids:")));
});

test("small byteBudget: continuing at nextOffsetBytes reaches the end marker", async () => {
  const good = await runScenario(scenario("small_byte_budget"), scenarios.fixture, scriptedModel([
    () => ({ tool: "read_file", args: { path: "project://Source/Game/Game.Build.cs", byteBudget: 2048 } }),
    previous => ({ tool: "read_file", args: { path: "project://Source/Game/Game.Build.cs", byteBudget: 16384,
      offsetBytes: previous.nextOffsetBytes } }),
    previous => ({ text: previous.content.includes("RECOVERY_MARKER_BUILD_END") ? "표식: RECOVERY_MARKER_BUILD_END" : "?" }),
  ]));
  assert.equal(good.calls[0].result.omissionReason, "byte_budget");
  assert.equal(scoreRun(scenario("small_byte_budget"), good).status, "pass");
  const bad = await runScenario(scenario("small_byte_budget"), scenarios.fixture, scriptedModel([
    () => ({ tool: "read_file", args: { path: "project://Source/Game/Game.Build.cs", byteBudget: 2048 } }),
    () => ({ text: "파일에 표식이 없습니다." }),
  ]));
  assert.equal(scoreRun(scenario("small_byte_budget"), bad).status, "fail");
});

for (const id of ["wrong_engine_path", "absolute_outside_root"]) {
  test(`${id}: a successful read_symbol is valid recovery evidence`, async () => {
    let initialPath = id === "wrong_engine_path" ? WRONG : "";
    const model = scriptedModel([
      () => ({ tool: "read_file", args: { path: initialPath } }),
      () => ({ tool: "read_symbol", args: {
        path: "engine://Engine/Source/Runtime/Engine/Private/PlayerController.cpp", symbol: "PostLogin",
      } }),
      () => ({ text: "PostLogin은 InitInputSystem()을 호출한다." }),
    ]);
    const original = model.act;
    model.act = function (chat, tools, opts) {
      initialPath ||= /엔진 파일 (.+?PlayerController\.cpp) 를/u.exec(chat.toString())?.[1] || "";
      return original.call(this, chat, tools, opts);
    };
    const run = await runScenario(scenario(id), scenarios.fixture, model);
    assert.equal(run.calls[0].result.ok, false);
    assert.equal(run.calls[1].name, "read_symbol");
    assert.equal(run.calls[1].result.ok, true);
    assert.match(run.calls[1].result.content, /InitInputSystem/u);
    const score = scoreRun(scenario(id), run);
    assert.equal(score.status, "pass", JSON.stringify(score.checks));
  });
}

test("simulated tools and the structure-only footer are scored separately from model wording", async () => {
  const run = await runScenario(scenario("structure_only_validation"), scenarios.fixture, scriptedModel([
    () => ({ tool: "evidence_first_validate", args: { mode: "audit" } }),
    () => ({ text: "## P1 — 기능 결함 (검증 완료)" }),
  ]));
  assert.equal(run.calls[0].simulated, true);
  assert.match(run.answer, /^\[검증 범위\]/mu);
  const score = scoreRun(scenario("structure_only_validation"), run);
  assert.equal(score.status, "fail");
  assert.deepEqual(score.checks.find(c => c.id === "harness_scope_footer"), { id: "harness_scope_footer", status: "pass",
    detail: "footer present" });
  const honest = await runScenario(scenario("structure_only_validation"), scenarios.fixture, scriptedModel([
    () => ({ tool: "evidence_first_validate", args: { mode: "audit" } }),
    () => ({ text: "근거 패킷은 형식 검사만 통과했고 반환값 주장은 미검증이다." }),
  ]));
  assert.doesNotMatch(honest.answer, /\[검증 범위\]/u);
  assert.equal(scoreRun(scenario("structure_only_validation"), honest).status, "pass");
});

test("coverage-limited zero followed by an engine text search passes", async () => {
  const run = await runScenario(scenario("coverage_limited_zero"), scenarios.fixture, scriptedModel([
    () => ({ tool: "unreal_symbol_lookup", args: { query: "AGameModeBase::ChoosePlayerStart_Implementation" } }),
    previous => ({ tool: "search_files", args: { ...previous.suggestion.args } }),
    previous => ({ tool: "read_file", args: { path: previous.results[0].uri } }),
    () => ({ text: "구현은 nullptr를 반환한다 (GameModeBase.cpp)." }),
  ]));
  assert.equal(scoreRun(scenario("coverage_limited_zero"), run).status, "pass", JSON.stringify(scoreRun(scenario("coverage_limited_zero"), run).checks));
});

test("absolute path outside the roots: the engine:// suggestion is followed", async () => {
  let absolute = "";
  const model = scriptedModel([
    () => ({ tool: "read_file", args: { path: absolute } }),
    previous => ({ tool: previous.suggestion.tool, args: previous.suggestion.args }),
    () => ({ text: "PostLogin은 InitInputSystem()을 호출한다." }),
  ]);
  const original = model.act;
  model.act = function (chat, tools, opts) {
    // The prompt carries the temporary engine path; read it from the user message.
    absolute ||= /엔진 파일 (.+?PlayerController\.cpp) 를/u.exec(chat.toString())?.[1] || "";
    return original.call(this, chat, tools, opts);
  };
  const followed = await runScenario(scenario("absolute_outside_root"), scenarios.fixture, model);
  assert.equal(followed.calls[0].result.errorCode, "PATH_OUTSIDE_ALLOWED_ROOTS");
  assert.equal(followed.calls[1].args.path, "engine://Engine/Source/Runtime/Engine/Private/PlayerController.cpp");
  assert.equal(scoreRun(scenario("absolute_outside_root"), followed).status, "pass");
});

test("simulated partial projection: reading on from the notice passes, claiming the whole file fails", async () => {
  const good = await runScenario(scenario("partial_projection"), scenarios.fixture, scriptedModel([
    () => ({ tool: "read_file", args: { path: "project://Source/Game/Game.Build.cs" } }),
    previous => ({ tool: "read_file", args: { path: "project://Source/Game/Game.Build.cs", offsetBytes: previous.nextOffsetBytes } }),
    () => ({ text: "표식: RECOVERY_MARKER_BUILD_END" }),
  ]));
  assert.match(good.calls[0].result.notice, /^PARTIAL BODY/u);
  assert.equal(good.calls[0].result.bodyComplete, false);
  assert.equal(scoreRun(scenario("partial_projection"), good).status, "pass");
  const bad = await runScenario(scenario("partial_projection"), scenarios.fixture, scriptedModel([
    () => ({ tool: "read_file", args: { path: "project://Source/Game/Game.Build.cs" } }),
    () => ({ text: "전체를 읽었지만 표식은 찾지 못했다." }),
  ]));
  assert.equal(scoreRun(scenario("partial_projection"), bad).status, "fail");
});

test("a run without a recorded answer is incomplete, never a pass", () => {
  const score = scoreRun(scenario("wrong_engine_path"), { calls: [], answer: "" });
  assert.equal(score.status, "incomplete");
  assert.ok(score.checks.every(c => c.status !== "pass"));
});

test("automatic report continuation still annotates verified wording in the delivered prefix", async () => {
  const run = await runScenario(scenario("structure_only_validation"), scenarios.fixture, scriptedModel([
    () => ({ tool: "evidence_first_validate", args: { mode: "audit" } }),
    () => ({ text: "P1 원인 검증 완료. 이어지는 설명: ", stopReason: "maxPredictedTokensReached" }),
    () => ({ text: "내용을 여기서 마칩니다." }),
  ]));
  assert.equal(run.error, null);
  assert.match(run.answer, /검증 완료.*내용을 여기서 마칩니다/su);
  assert.equal(run.debugEvents.filter(event => event.event === "verification_scope_notice").length, 1);
  assert.match(run.answer, /^\[검증 범위\]/mu);
});

function recordedRecovery(result, error = null) {
  return { answer: "InitInputSystem", error, calls: [
    { name: "read_file", args: { path: WRONG }, faultCall: true, result: { ok: false, errorCode: "NOT_FOUND" } },
    { name: "read_file", args: { path: "engine://Engine/Source/Runtime/Engine/Private/PlayerController.cpp" }, result },
  ] };
}

test("changed arguments and a guessed answer cannot pass after another failed read", () => {
  const score = scoreRun(scenario("wrong_engine_path"), recordedRecovery({ ok: false, errorCode: "NOT_FOUND",
    message: "Could not find InitInputSystem" }));
  assert.equal(score.status, "fail");
  assert.equal(score.checks.find(check => check.id === "follow_up").status, "fail");
  assert.equal(score.checks.find(check => check.id === "source_evidence").status, "fail");
});

test("source evidence must be returned successfully from the requested file", () => {
  const good = recordedRecovery({ ok: true, content: "void PostLogin() { InitInputSystem(); }" });
  assert.equal(scoreRun(scenario("wrong_engine_path"), good).status, "pass");
  const wrongFile = structuredClone(good);
  wrongFile.calls[1].args.path = "engine://Engine/Source/Runtime/Engine/Private/Other.cpp";
  assert.equal(scoreRun(scenario("wrong_engine_path"), wrongFile).status, "fail");
  const noBody = recordedRecovery({ ok: true, content: "// unrelated excerpt" });
  assert.equal(scoreRun(scenario("wrong_engine_path"), noBody).status, "fail");
});

test("an errored run cannot pass even if it acquired the source and guessed the expected answer", () => {
  const score = scoreRun(scenario("wrong_engine_path"), recordedRecovery({ ok: true, content: "InitInputSystem();" }, "timeout"));
  assert.equal(score.status, "fail");
  assert.equal(score.checks.find(check => check.id === "run_error").status, "fail");
});

test("an unrecorded follow-up result is incomplete, never successful evidence", () => {
  const score = scoreRun(scenario("wrong_engine_path"), recordedRecovery(undefined));
  assert.equal(score.status, "incomplete");
  assert.equal(score.checks.find(check => check.id === "source_evidence").status, "not_recorded");
});

// 2026-10-18 shapes. Harness checks only: the scripts are fixed, so these
// verify fixtures, recording, the harness footer and scoring, not a model.
const READERS = "project://Source/Game/Readers";

test("partial search: following nextCursor to the inline assignment passes; claiming absence fails with a search footer", async () => {
  const first = () => ({ tool: "search_files", args: { path: READERS, query: "bIsDead", byteBudget: 2048 } });
  // The last step repeats: follow each nextCursor until the search completes.
  const good = await runScenario(scenario("partial_search_absence"), scenarios.fixture, scriptedModel([
    first,
    previous => previous.nextCursor ? { tool: previous.suggestion.tool, args: previous.suggestion.args }
      : { text: "bIsDead = true는 ZPlayerState.h의 AddDeath()에 있다." },
  ]));
  assert.equal(good.calls.at(-1).result.coverage, "complete");
  assert.equal(good.calls[0].result.absenceVerified, false);
  assert.ok(good.calls[0].result.matchesOmitted > 0);
  const goodScore = scoreRun(scenario("partial_search_absence"), good);
  assert.equal(goodScore.status, "pass", JSON.stringify(goodScore.checks));

  const bad = await runScenario(scenario("partial_search_absence"), scenarios.fixture, scriptedModel([
    first,
    () => ({ text: "코드 어디에도 bIsDead = true가 없다. ZPlayerState.h에도 선언만 있다." }),
  ]));
  assert.match(bad.answer, /^\[검색 범위\] .*search_files 'bIsDead'/mu);
  const badScore = scoreRun(scenario("partial_search_absence"), bad);
  assert.equal(badScore.status, "fail");
  assert.deepEqual(badScore.checks.find(c => c.id === "harness_scope_footer"),
    { id: "harness_scope_footer", status: "pass", detail: "footer present" });
});

test("Super chain: reading the APawn override passes; asserting AActor directly without it fails", async () => {
  const good = await runScenario(scenario("super_chain"), scenarios.fixture, scriptedModel([
    () => ({ tool: "read_file", args: { path: "engine://Engine/Source/Runtime/Engine/Classes/GameFramework/Character.h" } }),
    () => ({ tool: "read_file", args: { path: "engine://Engine/Source/Runtime/Engine/Private/Pawn.cpp" } }),
    () => ({ text: "Super::TakeDamage는 APawn::TakeDamage가 먼저 실행되고, 그 안에서 AActor 구현을 호출한다." }),
  ]));
  assert.equal(scoreRun(scenario("super_chain"), good).status, "pass", JSON.stringify(scoreRun(scenario("super_chain"), good).checks));
  const bad = await runScenario(scenario("super_chain"), scenarios.fixture, scriptedModel([
    () => ({ text: "APawn에는 재정의가 없으므로 Super::TakeDamage는 곧바로 AActor::TakeDamage로 간다." }),
  ]));
  const badScore = scoreRun(scenario("super_chain"), bad);
  assert.equal(badScore.status, "fail");
  assert.equal(badScore.checks.find(c => c.id === "source_evidence").status, "fail");
  assert.equal(badScore.checks.some(c => c.id === "failure_reached"), false);
});

test("resume after an interrupted turn: the first input says so, and finishing the search passes", async () => {
  const run = await runScenario(scenario("resume_after_cancel"), scenarios.fixture, scriptedModel([
    () => ({ tool: "search_files", args: { path: READERS, query: "bIsDead" } }),
    () => ({ text: "bIsDead = true는 ZPlayerState.h의 AddDeath()에서 설정된다." }),
  ]));
  assert.equal(run.priorCalls.length, 1);
  assert.equal(run.priorCalls[0].result.coverage, "partial");
  assert.match(run.inputs[0].text, /previous assistant turn ended after tool results without a final answer/u);
  assert.ok(run.debugEvents.some(event => event.event === "resume_state" && event.priorTurnInterrupted === true));
  const score = scoreRun(scenario("resume_after_cancel"), run);
  assert.equal(score.status, "pass", JSON.stringify(score.checks));
  const repeated = await runScenario(scenario("resume_after_cancel"), scenarios.fixture, scriptedModel([
    () => ({ text: "앞서 확인한 대로 bIsDead는 어디에도 true로 설정되지 않는다." }),
  ]));
  assert.equal(scoreRun(scenario("resume_after_cancel"), repeated).status, "fail");
});

for (const [id, bad] of [["unity_wrong_path", "Assets/Missing/Player.cs"], ["unity_path_denied", "Scripts/Player.cs"]]) {
 test(`${id}: actual Unity file runtime recovers`, async () => {
  const run=await runScenario(scenario(id), scenarios.fixture, scriptedModel([
   ()=>({tool:"read_file",args:{path:bad}}),
   previous=>({tool:previous.suggestion.tool,args:previous.suggestion.args}),
   previous=>previous.text ? {text:"UNITY_RECOVERY_SOURCE"} : {tool:"read_file",args:{path:previous.items[0].path}},
   ()=>({text:"UNITY_RECOVERY_SOURCE"}),
  ])); assert.equal(scoreRun(scenario(id),run).status,"pass",JSON.stringify(run.error || scoreRun(scenario(id),run)));
 });
}
test("Unity budget recovery follows the actual cursor", async()=>{
 const step=previous=>previous?.nextCursor ? {tool:"search_files",args:{query:"Player",byteBudget:2048,cursor:previous.nextCursor,limit:200}} : {text:"All collected pages read."};
 const run=await runScenario(scenario("unity_search_budget"),scenarios.fixture,scriptedModel([()=>({tool:"search_files",args:{query:"Player",byteBudget:2048,limit:200}}),step]));
 assert.ok(run.calls[0].result.nextCursor); assert.equal(scoreRun(scenario("unity_search_budget"),run).status,"pass",JSON.stringify(scoreRun(scenario("unity_search_budget"),run)));
});
test("Unity reload and compile completion are observed independently",async()=>{
 for (const [id,steps] of [
  ["unity_editor_reload",[()=>({tool:"unity_status",args:{}}),()=>({tool:"unity_status",args:{}}),()=>({text:"connected"})]],
  ["unity_compile_result",[()=>({tool:"unity_editor",args:{action:"compile",operationId:"compile-123"}}),()=>({tool:"unity_operation",args:{action:"get",operationId:"compile-123"}}),()=>({text:"completed"})]],
 ]) { const run=await runScenario(scenario(id),scenarios.fixture,scriptedModel(steps)); assert.equal(scoreRun(scenario(id),run).status,"pass",JSON.stringify(scoreRun(scenario(id),run))); }
});

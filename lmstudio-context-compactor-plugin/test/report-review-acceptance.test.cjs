"use strict";
// Conversation 1794429926122: after a complete 5,019-char report, a review
// started by one guessed note ("HitShakeTimer never used? Possibly.") rewrote
// the report as 5,430 chars on the same seven topics. No exact echo matched,
// so the user saw the report twice. The review is now held until it ends and
// published only as accepted; guesses no longer start a review.
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { acceptReviewSupplement, restatementSignals } = require("../dist/review-acceptance");
const { PredictionStreamRenderer } = require("../dist/prediction-stream");
const { ReviewRegister, reviewCandidateLines } = require("../dist/review-register");
const { priorTurnInterrupted } = require("../dist/resume-facts");
const { REASONING_SEPARATOR } = require("../src/continuity-text.js");
const { fakeController, handlePredictionLoop } = require("./helpers/prediction-loop-fixture.cjs");
const { sourceHistory, tool } = require("./helpers/v4.cjs");

// Same shape as the logged report: numbered sections, code names, file:line references.
const REPORT = [
  "**적 설계에서 확인되는 설계 문제**",
  "",
  "### 1. 상태 소유권이 Character에 있음",
  "* `AEnemyBase`가 `EEnemyState`와 `Think`/`EnterState`/`ExitState`를 직접 소유한다. EnemyBase.h:27-35",
  "* 동시에 `AIControllerClass = ATacticalController::StaticClass()`로 컨트롤러도 이동을 맡는다. EnemyBase.cpp:38",
  "### 2. 리스폰 연동이 스텁 상태",
  "* `RegisterForRespawnEvents`는 경고 로그만 남기고 `RespawnDelegateHandle`을 설정하지 않는다. EnemyBase.cpp:184-200",
  "### 3. 타겟 획득이 단일 플레이어로 고정",
  "* `AcquireTarget`이 매 `Think`마다 `GetPlayerPawn(0)`으로 덮어쓴다. EnemyBase.cpp:280",
  "### 4. 체력 처리 경로가 둘로 갈림",
  "* `TakeDamage`와 `UHealthComponent::ApplyDamage`가 모두 `OnHealthChanged`를 호출한다. EnemyBase.cpp:400-430",
  "### 5. 공격 판정에 권한 검사가 없음",
  "* `AttackWindowBegin`과 `PerformAttackSweep`이 `HasAuthority` 확인 없이 실행된다. EnemyBase.cpp:510-560",
  "### 6. 이동 방식 충돌",
  "* `DirectMoveTick`의 `AddMovementInput`과 `RequestMoveTo`의 경로 이동이 같은 프레임에 경쟁한다. EnemyBase.cpp:147",
  "### 7. 순찰 폴백이 NavMesh 밖을 고를 수 있음",
  "* `DoPatrol`이 `GetRandomReachablePointInRadius` 실패 시 임의 2D 좌표를 쓴다. EnemyBase.cpp:316-371",
].join("\n");
// Reworded headings and sentences on the same topics, as the logged review did.
const REWRITE = [
  "적 관련 코드의 설계 문제",
  "",
  "### 1. 책임 분리 위반 – Character의 FSM과 Controller의 이동",
  "* `AEnemyBase`는 Tick에서 `Think`를 부르고 `EnterState`/`ExitState`로 `EEnemyState`를 바꾼다.",
  "  `ATacticalController`도 `AIControllerClass`로 지정돼 이동을 수행한다.",
  "### 2. 플레이어 리스폰 연동이 동작하지 않음",
  "* `RegisterForRespawnEvents`가 `RespawnDelegateHandle`을 비워 둔 채 끝난다.",
  "### 3. 타겟이 0번 플레이어 고정",
  "* `AcquireTarget`은 `GetPlayerPawn(0)`만 본다; `Think`가 그 값을 계속 덮는다.",
  "### 4. Damage와 HealthComponent 역할 혼선",
  "* `UHealthComponent::ApplyDamage`와 `TakeDamage` 양쪽에서 `OnHealthChanged`가 나간다.",
  "### 5. Authority 검사 부재",
  "* `PerformAttackSweep`, `AttackWindowBegin`은 `HasAuthority` 없이 돈다.",
  "### 6. 직진 스티어링과 경로 이동의 충돌",
  "* `AddMovementInput`(`DirectMoveTick`)과 `RequestMoveTo`가 서로 덮어쓴다.",
  "### 7. 순찰·홈 복귀의 안전성 결여",
  "* `DoPatrol`은 `GetRandomReachablePointInRadius`가 실패하면 NavMesh 밖 좌표를 쓴다.",
].join("\n");
const accept = (candidate, subjects = ["HitShakeTimer"], complete = true) =>
  acceptReviewSupplement({ candidate, report: REPORT, subjects, complete });

test("a reworded rewrite of the delivered report is not published", () => {
  const decision = accept(REWRITE);
  assert.equal(decision.decision, "rejected");
  assert.equal(decision.text, "");
  assert.ok(decision.signals.identifierCoverage >= 0.6, JSON.stringify(decision.signals));
  assert.ok(decision.signals.lineOverlap < 0.2, "no line is an exact copy");
  // Reordered sections, or the same rewrite under a supplement heading.
  const sections = REWRITE.split(/\n(?=### )/u);
  assert.equal(accept([sections[0], ...sections.slice(1).reverse()].join("\n")).decision, "rejected");
  assert.equal(accept("## 검토 보완\n" + REWRITE).decision, "rejected", "a heading does not make a rewrite a supplement");
});

test("supplements, quoted corrections and headerless supplements are published as written", () => {
  for (const text of [
    "\n\n## 검토 보완\n- `HitShakeTimer`: EnemyBase.h:88에 선언만 있고 사용처가 없습니다.",
    "\n\n## 검토 보완\n- 정정: \"### 5. 공격 판정에 권한 검사가 없음\" 지적은 철회합니다. `AttackWindowBegin`은 서버에서만 호출됩니다.",
    "- `HitShakeTimer`는 사용처가 없어 제외합니다. 나머지 항목은 변경 없음.",
    "\n\n## Review supplement\n- `HitShakeTimer` is declared at EnemyBase.h:88 and never read.",
  ]) {
    const decision = accept(text);
    assert.equal(decision.decision, "accepted", text);
    assert.equal(decision.text, text);
  }
  assert.equal(acceptReviewSupplement({ candidate: "short", report: "Report.", complete: true }).decision, "accepted",
    "a report too short to judge is not judged");
});

test("from a rewrite, only what the report lacks is kept", () => {
  const withSupplement = accept(REWRITE + "\n\n## 검토 보완\n- `HitShakeTimer`: 사용처 없음.");
  assert.equal(withSupplement.decision, "salvaged");
  assert.equal(withSupplement.text, "\n\n## 검토 보완\n- `HitShakeTimer`: 사용처 없음.");
  const named = accept(REWRITE.replace("### 7.", "### 8. 미사용 타이머\n* `HitShakeTimer`는 읽히지 않는다.\n### 7."));
  assert.equal(named.decision, "salvaged");
  assert.match(named.text, /HitShakeTimer/);
  assert.doesNotMatch(named.text, /GetPlayerPawn/, "restated sections stay out");
  const severity = accept(REWRITE.replace("### 5. Authority 검사 부재", "### 5. Authority 검사 부재 (P1)"));
  assert.equal(severity.decision, "salvaged", "a changed severity is a correction");
  assert.match(severity.text, /### 5\. Authority 검사 부재 \(P1\)/);
  assert.equal(severity.salvagedBlocks, 1);
  const retracted = accept(REWRITE.replace("* `PerformAttackSweep`, `AttackWindowBegin`은 `HasAuthority` 없이 돈다.",
    "* 앞선 지적은 철회합니다: `PerformAttackSweep`은 서버 RPC 안에서만 호출됩니다."));
  assert.equal(retracted.decision, "salvaged");
  assert.match(retracted.text, /철회합니다/);
});

test("a cut or stopped review publishes nothing", () => {
  assert.deepEqual([accept("\n\n## 검토 보완\n- `HitShakeTimer`: 사용", undefined, false).decision,
    accept("\n\n## 검토 보완\n- `HitShakeTimer`: 사용", undefined, false).text], ["withheld", ""]);
  assert.equal(accept("", undefined, false).decision, "accepted", "nothing generated is nothing withheld");
});

function fragments(stream, text, size) {
  for (let i = 0; i < text.length; i += size) stream.onFragment({ content: text.slice(i, i + size), roundIndex: 0,
    reasoningType: "none", isStructural: false, tokensCount: 1, containsDrafted: false });
}
function renderer(gate) {
  const blocks = [];
  const ctl = { createContentBlock: options => { const block = { options, text: "" }; blocks.push(block);
    return { appendText: text => { block.text += text; }, setStyle() {} }; } };
  const stream = new PredictionStreamRenderer(ctl, text => ({ visibleText: text, hasFooter: false, note: null }), false, true,
    [], REPORT, gate);
  return { stream, blocks };
}

for (const streamed of [true, false]) test(`screen, history and next input get the same decided text (streamed=${streamed})`, () => {
  for (const [text, expected] of [[REWRITE, ""], ["\n\n## 검토 보완\n- `HitShakeTimer`: 사용처 없음.", "\n\n## 검토 보완\n- `HitShakeTimer`: 사용처 없음."]]) {
    const { stream, blocks } = renderer((candidate, complete) => acceptReviewSupplement({ candidate, report: REPORT,
      subjects: ["HitShakeTimer"], complete }));
    if (streamed) fragments(stream, text, 7);
    assert.equal(blocks.map(block => block.text).join(""), "", "nothing is shown while the review is held");
    stream.finishAll();
    stream.settleReview(true);
    const message = ChatMessage.create("assistant", text);
    const normalized = stream.normalizeCaptured({ messages: [message], predictionUsage: {} });
    assert.equal(stream.visibleMessage(message).getText(), expected, "working history uses visibleMessage");
    assert.equal(normalized.messages[0].getText(), expected, "delivery is judged on the same text");
    assert.equal(normalized.presentation.reviewAcceptance.decision, expected ? "accepted" : "rejected");
    if (streamed) assert.equal(blocks.map(block => block.text).join(""), expected, "the screen shows it once");
  }
});

test("a review consumed before its end is known is not published", () => {
  const { stream, blocks } = renderer((candidate, complete) => acceptReviewSupplement({ candidate, report: REPORT, complete }));
  fragments(stream, "\n\n## 검토 보완\n- 보완", 5);
  assert.equal(stream.consumeMessage(ChatMessage.create("assistant", "x")).visibleText, "");
  assert.equal(blocks.map(block => block.text).join(""), "");
});

// The logged thinking line, a guess that must not become a report obligation.
const GUESS = "Also they have both HitStunTimer and HitShakeTimer but HitShakeTimer never used? Possibly.";
const thinking = text => ChatMessage.create("assistant", text + REASONING_SEPARATOR + "Continuing.");

test("a guessed note is a candidate, never a missing finding; stated facts with conditional impacts stay findings", () => {
  const judged = text => { const register = new ReviewRegister(); register.observe([thinking(text)], 1);
    for (const row of register.rows) row.bundle = { path: "Source/EnemyBase.cpp", lines: [1, 1] }; return register; };
  const guess = judged(GUESS);
  assert.deepEqual(guess.rows.map(row => [row.identifier, row.status, row.speculative]), [["HitShakeTimer", "found", true]]);
  assert.deepEqual(guess.missing(""), []);
  assert.equal(guess.candidates().length, 1, "kept as a candidate");
  assert.match(guess.lines()[0], /\(reasoning, round 1, speculative\)/);
  assert.match(reviewCandidateLines(guess.candidates()).join("\n"), /HitShakeTimer.*\[추측\]/);
  for (const [text, identifier, speculative] of [
    ["Maybe also worth noting: `ResetForRespawn` is never called.", "ResetForRespawn", false],
    ["Maybe `ResetForRespawn` is never called.", "ResetForRespawn", true],
    ["`HitTimer` is never cleared, so if the actor is destroyed it may fire later (bug).", "HitTimer", false],
    ["`HitTimer` 해제 안 함. 객체 파괴 후 실행될 수 있는 버그.", "HitTimer", false],
    ["`HitShakeTimer` 미사용인 것 같다.", "HitShakeTimer", true],
    ["`bIsDead ? 0 : 1` 분기와 별개로 `HitShakeTimer` is never used.", "HitShakeTimer", false],
    ["The comment says \"why?\" but `HitShakeTimer` is unused.", "HitShakeTimer", false],
    ["`GammaTimer` is never used. Possibly.", "GammaTimer", true],
  ]) {
    const register = judged(text);
    const row = register.rows.find(item => item.identifier === identifier);
    assert.equal(Boolean(row?.speculative), speculative, text);
    assert.equal(register.missing("").some(item => item.identifier === identifier), !speculative, text);
  }
  const mixed = judged("`AlphaTimer` is never used. `BetaTimer` unused? Maybe.");
  assert.deepEqual(mixed.missing("").map(row => row.identifier), ["AlphaTimer"], "each claim on a line is judged apart");
  const stated = new ReviewRegister();
  stated.observe([thinking("`AlphaTimer` is never used.")], 1);
  stated.observe([thinking("`AlphaTimer` unused? maybe.")], 2);
  assert.equal(stated.rows[0].speculative, undefined, "a later guess does not weaken a stated finding");
});

const model = replies => {
  let calls = 0;
  const call = { identifier: "review-replay", async getContextLength() { return 65536; },
    async applyPromptTemplate(chat) { return chat.toString(); }, async countTokens() { return 1000; },
    async act(_chat, _tools, opts) {
      const { text, stopReason = "eosFound", streaming } = replies[calls++];
      if (streaming) for (let i = 0; i < text.length; i += 9) opts.onPredictionFragment({ content: text.slice(i, i + 9), roundIndex: 0,
        reasoningType: "none", isStructural: false, tokensCount: 1, containsDrafted: false });
      opts.onMessage(ChatMessage.create("assistant", text));
      opts.onPredictionCompleted({ stats: { stopReason } });
    } };
  return { call, calls: () => calls };
};
async function run(review, options = {}) {
  const first = (options.note || "`ReloadSound` is never played.") + REASONING_SEPARATOR + REPORT;
  const replay = model([{ text: first }, ...(review === undefined ? [] : [{ ...options, text: review }])]);
  const history = sourceHistory("Review the enemy design.", ["ReloadSound", "HitShakeTimer"]);
  const ctl = fakeController(history, replay.call, { designGuidanceMode: "off", reportReviewMode: "on" }, [tool("read_file")]);
  await handlePredictionLoop(ctl);
  const shown = ctl.blocks.filter(block => block.options?.includeInContext !== false).map(block => block.text).join("");
  return { ctl, shown, calls: replay.calls(), history,
    outcome: ctl.debugValues.find(event => event.event === "execution_outcome"),
    statuses: ctl.statuses.map(status => status.state?.text || "") };
}

test("a guessed note alone starts no review", async () => {
  const { calls, ctl } = await run(undefined, { note: GUESS });
  assert.equal(calls, 1);
  assert.equal(ctl.debugValues.some(event => event.event === "report_review_scheduled"), false);
});

for (const streaming of [false, true]) test(`a review that rewrites the report is held back (stream=${streaming})`, async () => {
  const { shown, outcome, statuses, ctl, calls, history } = await run(REWRITE, { streaming });
  assert.equal(calls, 2);
  assert.equal(shown.split("AcquireTarget").length - 1, 1, "the report is shown once");
  assert.doesNotMatch(shown, /책임 분리 위반/, "no part of the rewrite is shown");
  assert.ok(statuses.some(text => /다시 작성한 내용이어서 표시하지 않았습니다/u.test(text)), statuses.join("\n"));
  assert.match(shown, /\[보고 검토 · 누락\] 검토 보완이 앞선 보고서를 다시 작성한 내용이어서 표시하지 않았습니다/u);
  assert.match(shown, /- ReloadSound \(Fixture\.cpp:1\)/u);
  assert.equal(outcome.executionOutcome, "completed");
  assert.equal(outcome.reason, "report_review_restatement_rejected");
  assert.equal(outcome.reportCompleted, true, "the delivered report stays complete");
  assert.deepEqual(outcome.supplement, { kind: "report_review", state: "rejected", outputDisposition: "restatement_rejected",
    obligations: { requested: ["ReloadSound"], unresolved: ["ReloadSound"] } });
  assert.equal(ctl.debugValues.find(event => event.event === "report_review_acceptance").decision, "rejected");
  // The next turn starts from the saved transcript: the report, then the notice; no rewrite, no interruption.
  const next = Chat.from(history);
  for (const block of ctl.blocks.filter(block => block.options?.includeInContext !== false && block.text))
    next.append(ChatMessage.create("assistant", block.text));
  next.append(ChatMessage.create("user", "계속"));
  assert.doesNotMatch(next.toString(), /책임 분리 위반/);
  assert.equal(priorTurnInterrupted(next.getMessagesArray()), false);
});

for (const streaming of [false, true]) test(`a cut review is withheld and the report stays complete (stream=${streaming})`, async () => {
  const { shown, outcome, statuses } = await run("\n\n## 검토 보완\n- `ReloadSound`: 재생 호출이", { streaming, stopReason: "maxPredictedTokensReached" });
  assert.doesNotMatch(shown, /재생 호출이/);
  assert.ok(statuses.some(text => /끝나지 않아 표시하지 않았습니다/u.test(text)), statuses.join("\n"));
  assert.match(shown, /\[보고 검토 · 누락\] 검토 보완이 끝나지 않아/u);
  assert.equal(outcome.reportCompleted, true);
  assert.equal(outcome.supplement.state, "withheld");
  assert.equal(outcome.supplement.outputDisposition, "incomplete_withheld");
});

test("a rewrite that ends in a supplement publishes the supplement only", async () => {
  const { shown, outcome } = await run(REWRITE + "\n\n## 검토 보완\n- `ReloadSound`: 재생 경로가 없어 미사용입니다.", { streaming: true });
  assert.ok(shown.endsWith(REPORT + "\n\n## 검토 보완\n- `ReloadSound`: 재생 경로가 없어 미사용입니다."), shown);
  assert.equal(outcome.supplement.outputDisposition, "salvaged");
  assert.deepEqual(outcome.supplement.obligations, { requested: ["ReloadSound"], unresolved: [] });
});

test("restatement signals separate the logged rewrite shape from a supplement", () => {
  const rewrite = restatementSignals(REWRITE, REPORT, ["HitShakeTimer"]);
  const supplement = restatementSignals("\n\n## 검토 보완\n- `HitShakeTimer`: 사용처 없음.", REPORT, ["HitShakeTimer"]);
  assert.ok(rewrite.identifierCoverage >= 0.6 && rewrite.lengthRatio >= 0.4 && rewrite.lineOverlap === 0, JSON.stringify(rewrite));
  assert.ok(supplement.identifierCoverage < 0.1 && supplement.headingMatches === 0, JSON.stringify(supplement));
});

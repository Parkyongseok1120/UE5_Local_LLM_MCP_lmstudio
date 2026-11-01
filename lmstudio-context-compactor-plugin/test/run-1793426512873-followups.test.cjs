"use strict";
// Follow-ups from the 2026-11-01 verification of conversation 1793426512873:
// (e) a read clamped below the provider's response metadata was dispatched and
// failed with OUTPUT_LIMIT_EXCEEDED; (f) a budget deferral was shown as a
// canceled tool; (b) an InitNewPlayer/PlayerState-order claim had no engine fact.
const test = require("node:test");
const assert = require("node:assert/strict");
const { ChatMessage } = require("@lmstudio/sdk");
const { BudgetBroker, BatchReservation, reserveReadResult } = require("../dist/budget-broker");
const { gradedSections, textEngineClaimGaps, reportEngineClaimNotice,
  returnedEngineEvidence: rawReturnedEngineEvidence } = require("../dist/engine-claim-anchoring");
const { bundledEngineClaimFacts } = require("../dist/generated-design-guidance");

const read = { name: "read_file", pluginIdentifier: "mcp/unreal-agent", parametersJsonSchema: { properties: {
  byteBudget: { type: "integer", minimum: 2048, maximum: 65536, $comment: "direct-json-text-utf8" } } } };
const range = { ...read, name: "read_file_range" };
const other = { ...read, pluginIdentifier: "mcp/unity-tools" };
const broker = () => new BudgetBroker({ workingInputTargetTokens: 46000, workingInputTriggerTokens: 56000, safetyMarginTokens: 2048 });
const longPath = "C:\\\\Users\\\\dev\\\\Documents\\\\Git\\\\Game\\\\Source\\\\Game\\\\Private\\\\Components\\\\WeaponHitValidationComponent.cpp";
const returned = (content) => ({ ok: true, errorCode: undefined, retry: { allowed: false, mode: "none" }, value: {
  ok: true, path: "project://Source/Game/Private/Components/WeaponHitValidationComponent.cpp",
  projectRelativePath: "Source/Game/Private/Components/WeaponHitValidationComponent.cpp",
  absolutePath: longPath, activeProject: longPath, workspaceRelativePath: "Source/Game/Private/Components/WeaponHitValidationComponent.cpp",
  resultKind: "workspace_file_observation", sha256: "a".repeat(64), startLine: 1, endLine: 194, totalLines: 194,
  effectiveByteBudget: 16384, repeatReceipt: "rr_" + "b".repeat(30),
  // The run's real read results carried these fields too (largest metadata 2513 bytes with the reserve).
  displayPath: "project://Source/Game/Private/Components/WeaponHitValidationComponent.cpp", canonicalProject: longPath,
  workspaceIdentity: "c".repeat(64), rangeSha256: "d".repeat(64), fileVersionReceipt: "fv_" + "e".repeat(24),
  snapshotCapturedAt: "2026-10-31T05:50:00.000Z", snapshotExpiresAt: "2026-10-31T06:50:00.000Z", snapshotOwner: "direct-read-cache",
  resultDigest: "rd_" + "f".repeat(22), afterRange: "Lines 195-397 remain; continue with startLine=195. ".repeat(8),
  range: { startLine: 1, endLine: 194, unit: "line", complete: true }, content } });

test("e: a read clamped below observed provider metadata is deferred, not dispatched to fail", () => {
  const b = broker();
  b.observeResult(read, { path: "project://Source/Game/Private/Components/WeaponHitValidationComponent.cpp" }, returned("1\tint A;"));
  const floor = b.retryMinimum(read, { path: "project://Source/Game/Public/CombatGameState.h" });
  assert.equal(floor.field, "byteBudget");
  assert.ok(floor.value > 2048, `learned floor ${floor.value} must exceed the 2048 schema minimum`);
  // The R6 shape: peers hold the batch; only the reserve floor (1844) remains.
  const batch = new BatchReservation(10032, 1844);
  assert.ok(batch.reserve("peer", 8188, 8188));
  const args = { path: "project://Source/Game/Public/CombatGameState.h", byteBudget: 6144 };
  const before = reserveReadResult(new BatchReservation(10032, 1844), "x", read, args);
  assert.equal(before.allowed, true, "without the learned floor the same budget still dispatches");
  const after = reserveReadResult(batch, "state", read, args, undefined, b.retryMinimum(read, args));
  assert.equal(after.allowed, false);
  assert.equal(after.denialReason, "held_reservations_insufficient");
  assert.deepEqual(after.requiredMinimum, floor);
  // A batch with room grants at least the learned floor.
  const roomy = reserveReadResult(new BatchReservation(20000, 1844), "ok", read, args, undefined, b.retryMinimum(read, args));
  assert.ok(roomy.allowed && roomy.arguments.byteBudget >= floor.value);
});

test("e: the floor is provider-scoped size data, never a scope-specific retry floor or authority", () => {
  const b = broker();
  b.observeResult(read, { path: "project://A.cpp" }, returned("x"));
  assert.ok(b.retryMinimum(range, { path: "project://B.cpp", startLine: 1, endLine: 5 }), "same provider readers share the floor");
  assert.equal(b.retryMinimum(other, { path: "project://B.cpp" }), undefined, "another provider does not inherit it");
  // A provider-declared minimum raises it; a higher scope-specific retry floor still wins for its own scope.
  const big = { ok: false, errorCode: "OUTPUT_LIMIT_EXCEEDED", retry: { allowed: true, mode: "different_arguments",
    minimum: { field: "byteBudget", value: 9000 } }, value: {} };
  b.observeResult(read, { path: "project://C.cpp" }, big, [read]);
  assert.equal(b.retryMinimum(read, { path: "project://D.cpp" }).value, 9000);
  // Failures and bodiless results never lower it.
  b.observeResult(read, { path: "project://E.cpp" }, { ok: false, errorCode: "TOOL_FAILED", retry: { allowed: false, mode: "none" }, value: {} });
  assert.equal(b.retryMinimum(read, { path: "project://D.cpp" }).value, 9000);
});

test("f: a budget deferral is shown as deferred, other denials stay canceled", () => {
  const { createToolGenerationTracker } = require("../dist/prediction-ui");
  const states = [];
  const ctl = { createStatus: () => ({ setState: s => states.push(s), setText() {}, remove() {} }),
    createToolStatus: () => ({ setStatus() {} }) };
  const tracker = createToolGenerationTracker(ctl);
  tracker.start(0, 1); tracker.name(0, 1, "read_file"); tracker.denied(1, "Shared read-result budget unavailable", true);
  tracker.start(0, 2); tracker.name(0, 2, "read_file"); tracker.denied(2, "Tool withheld by scope.");
  assert.equal(states[0].status, "done"); assert.match(states[0].text, /다음 라운드로 미룸: read_file/u);
  assert.doesNotMatch(states[0].text, /거부/u);
  assert.equal(states[1].status, "canceled"); assert.match(states[1].text, /도구 실행 거부: read_file/u);
});

function exchange(messages, id, args, result) {
  messages.push(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id, name: "read_file_range", arguments: args } }] }));
  messages.push(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
    content: JSON.stringify([{ type: "text", text: JSON.stringify(result) }]) }] }));
  return messages;
}
const evidence = messages => rawReturnedEngineEvidence(require("./helpers/returned-sources.cjs").ledgerView(messages));
const projectOnly = () => exchange([], "p1", { path: "project://Source/Game/Private/CombatGameMode.cpp" }, { ok: true,
  path: "project://Source/Game/Private/CombatGameMode.cpp", lineNumbers: true, startLine: 50, endLine: 51,
  content: "50\tTeamSpawns->AssignPlayerTeam(NewPlayer);\n51\treturn Super::InitNewPlayer(NewPlayerController, UniqueId, Options, Portal);" });
// The run's wrong P2 finding, reduced to its claim.
const REPORT = "# 리뷰\n\n## P2 — 수정 권장\n\n**2. `InitNewPlayer`에서 PlayerState 생성 전에 팀 배정 — 최초 스폰 위치가 팀 스폰이 아님**\n"
  + "- `Super::InitNewPlayer`가 PS를 만들므로 첫 로그인에서는 항상 실패합니다.\n";

test("b: the login/PlayerState order ships as an engine fact with 5.7 and 5.8 evidence", () => {
  const fact = bundledEngineClaimFacts.find(f => f.id === "ue.gameframework.login-playerstate-order");
  assert.ok(fact);
  assert.deepEqual(fact.engineVersions, ["5.7", "5.8"]);
  assert.ok(fact.evidenceRefs.some(ref => ref.symbol === "APlayerController::InitPlayerState"));
  assert.match(fact.statement, /already exists inside InitNewPlayer/);
});

test("b: an InitNewPlayer finding without engine source gets the fact and a report notice", () => {
  const gaps = textEngineClaimGaps(gradedSections(REPORT), evidence(projectOnly()), "unreal", "report");
  assert.deepEqual(gaps.map(g => [g.severity, g.factId]), [["P2", "ue.gameframework.login-playerstate-order"]]);
  assert.match(reportEngineClaimNotice(gaps, []), /\[엔진 동작 근거 · 보고서\]/u);
  // Reading the engine's InitPlayerState call backs it.
  const engine = exchange(projectOnly(), "e1", { path: "engine://Engine/Source/Runtime/Engine/Private/PlayerController.cpp" }, {
    ok: true, path: "engine://Engine/Source/Runtime/Engine/Private/PlayerController.cpp", evidenceSource: "installed_engine_source",
    lineNumbers: true, startLine: 1080, endLine: 1080, content: "1080\t\tInitPlayerState();" });
  assert.deepEqual(textEngineClaimGaps(gradedSections(REPORT), evidence(engine), "unreal", "report"), []);
});

test("b: the replication-removal review policy is in the shipped contract guidance", () => {
  const { bundledDesignGuidance } = require("../dist/generated-design-guidance");
  const text = JSON.stringify(bundledDesignGuidance || require("../dist/generated-design-guidance"));
  assert.match(text, /policy\.claims\.replication-removal/);
  assert.match(text, /UFUNCTION\(Server\/Client\/NetMulticast\)/);
});

"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { ReviewRegister, REVIEW_REGISTER_HEADER, REVIEW_REGISTER_GUIDANCE } = require("../dist/review-register");
const { REASONING_SEPARATOR } = require("../src/continuity-text.js");
const { composeModelHistory } = require("../dist/context-manager");
const { stripInternalDataEcho, InternalDataOutputFilter } = require("../dist/internal-data-output");
const { readConfig } = require("../dist/execution-config");
const { buildCheckpoint } = require("../src/direct-compaction-core");
const { textEngineClaimGaps, returnedEngineEvidence: rawReturnedEngineEvidence, engineClaimFact, unresolvedReportEngineClaimGaps,
  unresolvedEngineClaimGaps } = require("../dist/engine-claim-anchoring");
const raw = require("./fixtures/review-reasoning-1792916809646.json");
const assistant = text => ChatMessage.create("assistant", text);
const reasoning = text => assistant(text + REASONING_SEPARATOR + "Continuing the audit.");
function attachSource(register, names) {
  const index = new (require('../dist/review-register').ReviewEvidenceIndex)();
  index.observe(require('./helpers/v4.cjs').sourceHistory('Audit source.', names).getMessagesArray());
  register.attach(index);
}

test("logged r9/r10 reasoning survives as found notes and a cleared TakeDamage judgment", () => {
  const register = new ReviewRegister();
  register.observe([reasoning(raw.r9)], 9);
  register.observe([reasoning(raw.r10)], 10);
  for (const identifier of ["Heal", "ResetForRespawn", "HeadshotMultiplier", "ReloadSound", "RunSpeed"])
    assert.equal(register.rows.find(row => row.identifier === identifier)?.status, "found", identifier);
  assert.equal(register.rows.find(row => row.identifier === "TakeDamage").status, "cleared");
  assert.match(register.fact(), /HealthRegenRate\/HealthRegenDelay/);
  assert.ok(register.rows.every(row => row.source === "reasoning"));
  assert.equal(register.missing("Heal ResetForRespawn HeadshotMultiplier ReloadSound RunSpeed").length, 0);
  assert.doesNotMatch(register.missing("").map(row => row.identifier).join(), /TakeDamage/);
});

test("latest status wins, visible notes override reasoning, and found and cleared rows are bounded apart", () => {
  const register = new ReviewRegister();
  register.observe([assistant("`TakeDamage` P1 bug")], 1);
  register.observe([reasoning("`TakeDamage` rejected: no issue")], 2);
  assert.equal(register.rows[0].status, "cleared");
  register.observe([assistant("`TakeDamage` is fine")], 3);
  assert.equal(register.rows[0].source, "visible");
  for (let i = 0; i < 45; i++) register.observe([assistant(`\`UnusedField${i}\` unused ${"x".repeat(500)}`)], i + 4);
  const found = register.rows.filter(row => row.status === "found");
  assert.equal(found.length, 40);
  assert.equal(found[0].identifier, "UnusedField5");
  assert.ok(register.rows.some(row => row.identifier === "TakeDamage"), "found rows do not push out a cleared judgment");
  assert.ok(register.rows.every(row => row.evidence.length <= 240));
  attachSource(register, ['UnusedField0']);
  // A found row pushed out of the rendered register is still a missing finding.
  assert.ok(register.missing("").some(row => row.identifier === "UnusedField0"));
});

// Conversation 1792929236226: the r6 judgment about ResetForRespawn was filed
// under the line's first code span, RespawnPlayer, which the twenty-row limit
// then dropped; the final answer's own lines filled the register with noise.
test("a judgment is filed under the code named nearest its keyword, and noise names are not subjects", () => {
  const register = new ReviewRegister();
  register.observe([reasoning("   **Issue**: In `RespawnPlayer`, after destroying the old pawn and calling RestartPlayer, the new character's "
    + "`PossessedBy` calls `PS->InitializeForSpawn(...)`. Good — health is re-initialized. But note: `ResetForRespawn()` in "
    + "PlayerState is never called from anywhere! Let me check...")], 7);
  assert.deepEqual(register.rows.map(row => [row.identifier, row.status]), [["ResetForRespawn", "found"]]);
  register.observe([assistant("- `ue.controller.player-tick-input` P3: `this` pointer and `GameState` bug; `PS` unused")], 27);
  assert.deepEqual(register.rows.map(row => row.identifier), ["ResetForRespawn"], "contract ids, receivers and type families are skipped");
  attachSource(register, ['ResetForRespawn']);
  assert.deepEqual(register.missing("RespawnPlayer is correct.").map(row => row.identifier), ["ResetForRespawn"]);
});

test("runtime register is incremental and private; headerless and fragmented echoes are hidden", () => {
  const register = new ReviewRegister(); register.observe([assistant("`ReloadSound` never played")], 1);
  const history = Chat.from([{ role: "user", content: "Audit this project" }]);
  const config = readConfig({ getPluginConfig: () => ({ get: () => undefined }) });
  const first = composeModelHistory(history, null, config, [], false, undefined, [{key:"review_register",text:register.fact()}], new Map());
  assert.ok(first.privateDataParts.some(part => part.startsWith(REVIEW_REGISTER_HEADER)));
  const shown = new Map([[first.dataBlock.anchor, first.dataBlock.parts]]);
  register.observe([assistant("`RunSpeed` unused")], 2);
  const second = composeModelHistory(history, null, config, [], false, undefined, [{key:"review_register",text:register.fact()}], shown);
  assert.match(second.dataBlock.parts.join("\n"), /RunSpeed/);
  assert.doesNotMatch(second.dataBlock.parts.at(-1), /ReloadSound/);
  const echo = register.lines().join("\n") + "\n" + REVIEW_REGISTER_GUIDANCE + "\nActual report.\n";
  const filter = new InternalDataOutputFilter([register.fact()]); let visible = "";
  for (const char of echo) visible += filter.push(char);
  assert.equal(visible + filter.finish(), "Actual report.\n");
  assert.equal(stripInternalDataEcho(echo), echo, "unsupplied literals remain visible");
  register.observe([assistant(echo)], 3, [register.fact()]);
  assert.equal(register.rows.find(row => row.identifier === "RunSpeed").round, 2, "echo is not a new judgment");
});

function reviewExchange(id, name, args, value) {
  return [ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: { type: "function", id, name, arguments: args } }] }),
    ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id, content: JSON.stringify(value) }] })];
}

// Conversation 1792929236226: after a projection the model re-read the whole
// project because each judgment kept only a 240-char line. A bundle carries
// where it stands, which version, what was searched and the code itself.
test("a judgment carries its evidence bundle: location, sha, search scope and code", () => {
  const { ReviewEvidenceIndex } = require("../dist/review-register");
  const path = "project://Source/Game/Private/GamePlayerState.cpp", sha = "ab".repeat(32);
  const body = Array.from({ length: 90 }, (_, i) => i === 78 ? "void AGamePlayerState::ResetForRespawn()" : i === 79 ? "{" : `// line ${i + 1}`).join("\n");
  const index = new ReviewEvidenceIndex();
  index.observe([...reviewExchange("r1", "read_file", { path }, { ok: true, path, sha256: sha, content: body }),
    ...reviewExchange("s1", "search_files", { query: "ResetForRespawn", path: "project://Source" }, { ok: true, query: "ResetForRespawn",
      path: "project://Source", filesScanned: 21, coverage: "complete", results: [
        { uri: path.replace(".cpp", ".h"), line: 40, text: "void ResetForRespawn();" }, { uri: path, line: 79, text: "void AGamePlayerState::ResetForRespawn()" }] })]);
  const register = new ReviewRegister();
  register.observe([reasoning("- `ResetForRespawn()` — declared + defined, never called from anywhere. Dead code.")], 7);
  register.attach(index);
  const { bundle } = register.rows[0];
  assert.equal(bundle.path, path);
  assert.deepEqual(bundle.lines, [79, 79]);
  assert.equal(bundle.sha, sha);
  assert.match(bundle.excerpt, /AGamePlayerState::ResetForRespawn\(\)/);
  assert.match(bundle.search, /21 files, coverage complete, 2 hit\(s\)/);
  const [row, evidence] = register.lines();
  assert.match(row, /^- \[found\] ResetForRespawn/);
  assert.match(evidence, /^ {2}↳ project:\/\/Source\/Game\/Private\/GamePlayerState\.cpp:79 @abababab · search /);
  assert.equal(register.closedFileFacts("source/game/private/gameplayerstate.cpp", sha), null, "a judgment never closes a file");
  assert.equal(register.closedFileFacts("source/game/private/gameplayerstate.cpp", "cd".repeat(32)), null, "a changed sha reopens the file");
  // Echoed bundle lines are not new judgments.
  register.observe([assistant(evidence)], 8);
  assert.equal(register.rows.length, 1);
});

test("register rows populate same-turn checkpoint evidence, including cleared status", () => {
  const register = new ReviewRegister(); register.observe([reasoning(raw.r9), reasoning(raw.r10)], 10);
  const messages = [{ role: "user", text: "Audit project" },
    { role: "assistant", text: "Progress" }, { role: "assistant", text: "More progress" }];
  const options = { maxCurrentTurnMessages: 0, maxCheckpointChars: 22000, reviewRegisterItems: () => register.lines() };
  const checkpoint = buildCheckpoint(messages, options);
  assert.match(checkpoint.assistantCheckpoint, /HeadshotMultiplier/);
  assert.match(checkpoint.assistantCheckpoint, /cleared.*TakeDamage/);
  assert.match(checkpoint.memory.currentTurnFindings.forUserTurn, /^[a-f0-9]{64}$/);
  const again = buildCheckpoint(messages, { ...options, reviewRegisterItems: () => [] });
  assert.equal(checkpoint.memory.currentTurnFindings.forUserTurn, again.memory.currentTurnFindings.forUserTurn);
});

test("unrated PlayerController tick working note produces a pre-report engine fact", () => {
  const register = new ReviewRegister();
  register.observe([reasoning("PlayerController has dead code; set `bCanEverTick = false`.")], 21);
  const gaps = textEngineClaimGaps(register.findings(), returnedEngineEvidence([]), "unreal", "progress");
  assert.equal(gaps[0].factId, "ue.controller.player-tick-input");
  assert.equal(gaps[0].severity, "unrated");
  assert.match(engineClaimFact(gaps), /stops player input processing/);
});

test("only a named correction clears a report engine gap after the supplement", () => {
  const evidence = returnedEngineEvidence([]);
  const report = "## P2 `bCanEverTick`\nPlayerController tick is unused; set bCanEverTick = false.";
  assert.equal(unresolvedReportEngineClaimGaps(report, evidence, "unreal").length, 1);
  assert.equal(unresolvedReportEngineClaimGaps(report + "\n## 검토 보완\n- `OtherField` is unverified.", evidence, "unreal").length, 1);
  assert.equal(unresolvedReportEngineClaimGaps(report + "\n## 검토 보완\n- `bCanEverTick` 권고 정정: 입력 처리 중단 위험으로 기각.", evidence, "unreal").length, 0);
  const gaps = unresolvedReportEngineClaimGaps(report, evidence, "unreal");
  assert.equal(unresolvedEngineClaimGaps(gaps, report + "\n## 검토 보완\n- `ue.controller.player-tick-input` 정정: 권고 기각.").length, 0);
  assert.equal(unresolvedReportEngineClaimGaps('Report\n## 검토 보완\n### P2 `bCanEverTick`\nPlayerController set bCanEverTick = false.', evidence, "unreal").length, 1);
});

test("report review defaults on and preserves explicit off", () => {
  const config = value => readConfig({ getPluginConfig: () => ({ get: key => key === 'reportReviewMode' ? value : undefined }) });
  assert.equal(config(undefined).reportReviewMode, 'on');
  assert.equal(config('on').reportReviewMode, 'on');
  assert.equal(config('off').reportReviewMode, 'off');
});

function returnedEngineEvidence(messages) { return rawReturnedEngineEvidence(require("./helpers/returned-sources.cjs").ledgerView(messages)); }

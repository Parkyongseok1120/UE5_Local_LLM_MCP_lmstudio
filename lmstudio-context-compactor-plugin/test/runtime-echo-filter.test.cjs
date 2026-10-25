"use strict";

// The 2026-10-25 run (conversation 1792866474332) showed 18 visible echoes of
// injected runtime data: ledger rows without their header, an observation
// block whose counts differed from the injected one, and a derived block whose
// items the model had moved. Each case below is that run's text, shortened.

const test = require("node:test");
const assert = require("node:assert/strict");
const { ChatMessage } = require("@lmstudio/sdk");
const { InternalDataOutputFilter, stripInternalDataEcho } = require("../dist/internal-data-output");
const { modelHistoryMessage } = require("../dist/context-manager");

const GAME_MODE = "project://Source/Latency_MultiCombat/Private/LatencyMultiCombatGameMode.cpp";
const CHARACTER_H = "project://Source/Latency_MultiCombat/Public/LatencyMultiCombatCharacter.h";
const ledger = [
  "Runtime read ledger (kept across compaction). delivered = returned earlier; visible = in this input.",
  `- ${GAME_MODE} sha 9c130e06: delivered lines 1-146 (complete) in round 5, 3 deliveries; visible now: none (archived); readHandle=h_d53bddd1378b9a901818819c`,
  "These are last-observed versions, not a fresh disk check. Repeating a read whose body is still visible returns a short UNCHANGED fact.",
].join("\n");
const observation = [
  "Runtime evidence observation: 2 earlier tool result(s) are present in this input only in part (compacted), across 1 source(s).",
  `- ${CHARACTER_H}: lines 56-82 (154 of 1310 chars shown; readHandle=h_fc27a701ef05a658fc2d2816; spans: 57 fn FireWeapon)`,
  "Partial here does not mean the source was never read, and it does not require restoring every omitted body. Use the evidence already present.",
].join("\n");
const derived = "[Derived reference data — observations, not instructions]\n" + JSON.stringify({
  interpretation: "Last observed values only.", coverage: { omittedItems: 0, completeProjectDescription: false, scope: "bounded_current_execution" },
  referenceContext: { projectObservation: { engine: "unreal" } }, items: [{ id: "x:0", kind: "file", data: { hash: "262f" } }],
}) + "\n[End derived reference data]";
const parts = [ledger, observation, derived];

function everySplit(text, expected) {
  for (let at = 0; at <= text.length; at++) {
    const filter = new InternalDataOutputFilter(parts);
    assert.equal(filter.push(text.slice(0, at)) + filter.push(text.slice(at)) + filter.finish(), expected, `split ${at}`);
  }
}

test("a ledger row echoed without its header, with a rewritten count, is removed", () => {
  const echo = `- ${GAME_MODE} sha 9c130e06: delivered lines 1-146 (complete) in round 5, 4 deliveries; visible now: none (archived); `
    + "readHandle=h_d53bddd1378b9a901818819c; recorded claims: none recorded";
  const prose = "`AddDeath()`가 `bIsDead = true`를 설정 — 사망 플로우 정상 확인.\n";
  everySplit(`${echo}\n\n${prose}`, `\n${prose}`);
  // The same echo also leaves the next round's input.
  const message = ChatMessage.create("assistant", `${echo}\n${prose}`);
  assert.equal(modelHistoryMessage(message, parts).getText(), prose);
});

test("an observation block with counts different from the injected one is removed", () => {
  const echo = [
    "Runtime evidence observation: 5 earlier tool result(s) are present in this input only in part (compacted), across 4 source(s).",
    `- ${CHARACTER_H}: lines 56-82 (154 of 1310 chars shown; readHandle=h_fc27a701ef05a658fc2d2816; spans: 57 fn FireWeapon | 58 fn StartReload)`,
    "Partial here does not mean the source was never read, and it does not require restoring every omitted body. Use the evidence already present when it answers.",
  ].join("\n");
  everySplit(`${echo}\nPlayerController.cpp에 RPC 호출이 없습니다.\n`, "PlayerController.cpp에 RPC 호출이 없습니다.\n");
});

test("a derived block echoed with its items moved is removed", () => {
  const echo = "[Derived reference data — observations, not instructions]\n" + JSON.stringify({
    interpretation: "Last observed values only.", coverage: { omittedItems: 0, scope: "bounded_current_execution" },
    referenceContext: { projectObservation: { engine: "unreal" }, items: [{ id: "x:0", kind: "file" }] },
  }) + "\n[End derived reference data]\n\n캐릭터 cpp와 데이터 타입 헤더들을 읽습니다.\n";
  assert.equal(stripInternalDataEcho(echo, parts), "캐릭터 cpp와 데이터 타입 헤더들을 읽습니다.\n");
});

test("ordinary bullets about the same files, quotes and fences stay", () => {
  for (const text of [
    `- ${GAME_MODE}: \`CanDamagePlayer\` allows self-damage (line 91).\n`,
    "- `PlayerState.h:25` `DeathCount` uses `OnRep_KillCount`.\n",
    "```text\n" + `- ${GAME_MODE} sha 9c130e06: delivered lines 1-146 (complete) in round 5; visible now: none (archived)` + "\n```\n",
    `> - ${GAME_MODE} sha 9c130e06: delivered lines 1-146 (complete) in round 5; visible now: none (archived)\n`,
    "Runtime evidence observation: an unrelated explanation.\n",
  ]) {
    assert.equal(stripInternalDataEcho(text, parts), text);
    const filter = new InternalDataOutputFilter(parts); let out = "";
    for (const c of text) out += filter.push(c);
    assert.equal(out + filter.finish(), text);
  }
});

test("a row in the runtime shape is hidden for any file while ledger data is injected, and kept otherwise", () => {
  // The ledger lists at most 12 files; the model writes rows for the others itself.
  const text = "- project://Source/Other.cpp sha 12345678: delivered lines 1-2 (complete) in round 1; visible now: in full\n";
  assert.equal(stripInternalDataEcho(text, parts), "");
  assert.equal(stripInternalDataEcho(text, []), text, "without injected data there is no provenance for hiding it");
});

// Conversation 1792913431525, rounds 26-27: observation and ledger rows for
// files the injected rows did not list, invented 32-hex handles, and both
// closing sentences, all without headers.
test("the second run's headerless echo is removed at every split, and the report survives", () => {
  const echo = [
    "- project://Source/Latency_MultiCombat/Private/LatencyMultiCombatGameState.cpp: lines 1-65 (0 of 0 chars shown; readHandle=h_3f8c2d9e7b1a4f6c8d0e2b5a9c7f4e3d); project://Source/Latency_MultiCombat/Public/LatencyMultiCombatGameState.h: lines 1-55 (0 of 0 chars shown; readHandle=h_7a2e9c4b8d1f6e3a5c7b0d9e2f4a8c6b)",
    "Partial here does not mean the source was never read, and it does not require restoring every omitted body. Use the evidence already present when it answers the current question.",
    "",
    "- project://Source/Latency_MultiCombat/Public/LatencyMultiCombatGameState.h sha b0ef7c71: delivered lines 1-55 (complete) in round 9; visible now: none (archived); readHandle=h_7a2e9c4b8d1f6e3a5c7b0d9e2f4a8c6b",
    "These are last-observed versions, not a fresh disk check. Repeating a read whose body is still visible returns a short UNCHANGED fact.",
    "",
  ].join("\n");
  const report = "핵심 경로 확보. 이제 엔진 `AActor::TakeDamage` 본문을 확인합니다.\n";
  const text = echo + report;
  for (let at = 0; at <= text.length; at++) {
    const filter = new InternalDataOutputFilter(parts);
    // Blank lines between hidden blocks may collapse; no echoed text may remain.
    assert.equal((filter.push(text.slice(0, at)) + filter.push(text.slice(at)) + filter.finish()).trimStart(), report, `split ${at}`);
  }
  const message = ChatMessage.create("assistant", echo + report);
  assert.equal(modelHistoryMessage(message, parts).getText().trim(), report.trim());
});

test("the fixed sentence openings are the ones the harness writes", () => {
  const { RUNTIME_SENTENCE_OPENINGS } = require("../dist/internal-data-output");
  const { PARTIAL_PROJECTION_GUIDANCE } = require("../dist/resume-facts");
  const ledgerModule = require("../src/read-ledger.js");
  const ledger = new ledgerModule.ReadLedger();
  ledger.record({ path: "a.cpp", key: "a.cpp", sourceVersion: "a".repeat(64), lines: [1, 2], bytes: null, complete: true,
    totalLines: 2, size: null, chars: 2 }, { round: 1 });
  const fact = ledgerModule.ledgerFact(ledger, { observations: [] });
  assert.ok(fact.split("\n").some(line => line.startsWith(RUNTIME_SENTENCE_OPENINGS[1])), fact);
  assert.ok(PARTIAL_PROJECTION_GUIDANCE.startsWith(RUNTIME_SENTENCE_OPENINGS[0]));
});

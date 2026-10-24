"use strict";

// The 2026-10-24 audit quoted ClientConfirmHit() correctly, then rated
// "a Client RPC is sent to all clients" P1 without reading any engine source;
// the structure validator passed it. A high-severity claim whose wording
// touches a catalogued engine fact gets that fact back until engine source
// backing it is returned.

const assert = require("node:assert/strict");
const test = require("node:test");
const { ChatMessage } = require("@lmstudio/sdk");
const { engineClaimFact, engineClaimNotice, packetEngineClaimGaps, returnedEngineEvidence } = require("../dist/engine-claim-anchoring");
const { bundledEngineClaimFacts } = require("../dist/generated-design-guidance");

function exchange(messages, id, name, args, result) {
  messages.push(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id, name, arguments: args } }] }));
  messages.push(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
    content: JSON.stringify([{ type: "text", text: JSON.stringify(result) }]) }] }));
  return messages;
}

const numbered = (start, lines) => lines.map((line, i) => `${start + i}\t${line}`).join("\n");

function projectRead(messages = []) {
  return exchange(messages, "p1", "read_file_range", { path: "project://Source/Game/Private/GameCharacter.cpp" }, {
    ok: true, path: "project://Source/Game/Private/GameCharacter.cpp", projectRelativePath: "Source/Game/Private/GameCharacter.cpp",
    lineNumbers: true, startLine: 236, endLine: 236, content: numbered(236, [
      "\t\tif (Applied > 0.f && Cast<AGameCharacter>(Hit.GetActor())) ClientConfirmHit();"]) });
}

function engineRead(messages, path, start, lines) {
  return exchange(messages, "e1", "read_file_range", { path }, { ok: true, path, evidenceSource: "installed_engine_source",
    lineNumbers: true, startLine: start, endLine: start + lines.length - 1, content: numbered(start, lines) });
}

const rpcClaim = (severity = "P1", extra = {}) => ({ mode: "audit", claims: [{
  claim: "ClientConfirmHit() called from the server is sent to all clients, so every player sees the hit marker.",
  claimType: "behavior", severity, verdict: "Bug", proofLevel: "SourceVerified",
  evidence: [{ kind: "project_source", location: "Source/Game/Private/GameCharacter.cpp:236",
    observation: "HandleServerFire calls ClientConfirmHit() after ApplyPointDamage." }],
  behaviorPath: [], counterEvidence: ["none"], unknowns: [], ...extra }] });

test("the catalogue ships the Client RPC and PlayerController tick facts as data", () => {
  const ids = bundledEngineClaimFacts.map(fact => fact.id);
  assert.ok(ids.includes("ue.net.client-rpc-callspace"));
  assert.ok(ids.includes("ue.controller.player-tick-input"));
  assert.ok(bundledEngineClaimFacts.every(fact => fact.engine === "unreal" && fact.claimMarkers.length && fact.evidenceRefs.length));
});

test("a P1 Client RPC broadcast claim with only project source returned gets the contract statement", () => {
  const gaps = packetEngineClaimGaps(rpcClaim(), returnedEngineEvidence(projectRead()), "unreal");
  assert.deepEqual(gaps.map(gap => [gap.claimPath, gap.severity, gap.factId]), [["claims[0]", "P1", "ue.net.client-rpc-callspace"]]);
  const fact = engineClaimFact(gaps);
  assert.match(fact, /owning client/);
  assert.match(fact, /AActor::GetFunctionCallspace/);
  assert.match(fact, /not a verdict on the claim/);
  const notice = engineClaimNotice(gaps);
  assert.match(notice, /\[엔진 동작 근거\]/u);
  assert.match(notice, /사실 판정이 아닙니다/u);
});

test("the Korean report wording is matched as well", () => {
  const packet = rpcClaim("P1", { claim: "ClientConfirmHit()이 모든 클라이언트에 브로드캐스트됨" });
  assert.equal(packetEngineClaimGaps(packet, returnedEngineEvidence(projectRead()), "unreal").length, 1);
});

test("returned engine source of the fact's evidence member backs the claim", () => {
  const messages = engineRead(projectRead(), "engine://Engine/Source/Runtime/Engine/Private/Actor.cpp", 5412, [
    "int32 AActor::GetFunctionCallspace( UFunction* Function, FFrame* Stack )", "{"]);
  assert.deepEqual(packetEngineClaimGaps(rpcClaim(), returnedEngineEvidence(messages), "unreal"), []);
});

test("an unrelated engine read of the same file does not back the claim", () => {
  const messages = engineRead(projectRead(), "engine://Engine/Source/Runtime/Engine/Private/Actor.cpp", 100, [
    "void AActor::BeginPlay()", "{"]);
  assert.equal(packetEngineClaimGaps(rpcClaim(), returnedEngineEvidence(messages), "unreal").length, 1);
});

test("returned framework source the claim cites, or a lookup that attached the contract, backs it", () => {
  const netDriver = engineRead(projectRead(), "engine://Engine/Source/Runtime/Engine/Private/NetDriver.cpp", 8054, [
    "\t\tConnection = Actor->GetNetConnection();"]);
  const cited = rpcClaim("P1", { evidence: [...rpcClaim().claims[0].evidence, { kind: "framework_source",
    location: "engine://Engine/Source/Runtime/Engine/Private/NetDriver.cpp:8054", observation: "Non-multicast RPCs use the owning connection." }] });
  assert.deepEqual(packetEngineClaimGaps(cited, returnedEngineEvidence(netDriver), "unreal"), []);
  const lookup = exchange(projectRead(), "l1", "unreal_symbol_lookup", { query: "AActor::GetFunctionCallspace", sourceMode: "engine_local" },
    { ok: true, matches: [], relatedContracts: { revision: "unreal_contracts@5", items: [{ id: "ue.net.client-rpc-callspace" }] } });
  assert.deepEqual(packetEngineClaimGaps(rpcClaim(), returnedEngineEvidence(lookup), "unreal"), []);
});

test("P2 and below, unrelated wording, and a non-Unreal project are not gated", () => {
  const evidence = returnedEngineEvidence(projectRead());
  assert.deepEqual(packetEngineClaimGaps(rpcClaim("P2"), evidence, "unreal"), []);
  assert.deepEqual(packetEngineClaimGaps(rpcClaim("P1", { claim: "MagazineAmmo is decremented twice per shot." }), evidence, "unreal"), []);
  // Unity Netcode's ClientRpc has different delivery rules.
  assert.deepEqual(packetEngineClaimGaps(rpcClaim(), evidence, "unity"), []);
  assert.equal(engineClaimFact([]), null);
  assert.equal(engineClaimNotice([]), null);
});

test("counter-evidence that names the engine rule is not read as the claim", () => {
  const packet = rpcClaim("P1", { claim: "MagazineAmmo is decremented twice per shot.",
    counterEvidence: ["A Client RPC would be sent to all clients if it were NetMulticast."] });
  assert.deepEqual(packetEngineClaimGaps(packet, returnedEngineEvidence(projectRead()), "unreal"), []);
});

test("a P1 proposal to disable PlayerController tick gets the PlayerTick input fact", () => {
  const packet = rpcClaim("P1", { claim: "PlayerController ticks in Shipping for nothing; set PrimaryActorTick.bCanEverTick = false." });
  const gaps = packetEngineClaimGaps(packet, returnedEngineEvidence(projectRead()), "unreal");
  assert.deepEqual(gaps.map(gap => gap.factId), ["ue.controller.player-tick-input"]);
  assert.match(engineClaimFact(gaps), /stops player input processing/);
});

// The second 2026-10-24 audit sent no packet. Its report graded a weak-pointer
// cache fix and a Shipping guard for debug drawing, and a progress line had
// "confirmed" a Client RPC broadcast that the report later dropped.
const { confirmedProgressStatements, gradedSections, reportEngineClaimNotice, textEngineClaimGaps } = require("../dist/engine-claim-anchoring");
const REPORT = [
  "# Audit", "", "## ① 기각: `ClientConfirmHit` — 버그 아님", "Multicast라 모든 클라이언트에 히트마커가 뜬다고 의심했으나 Client RPC입니다.",
  "## ② P2 — 매 샷 동기 로드 (성능)", "- **수정**: BeginPlay에서 미리 로드해 `TWeakObjectPtr<USoundBase>`로 캐시하고 MulticastShot에서는 캐시만 참조.",
  "## ③ P3 — SSOT 헤더의 데드 스탯", "MaxArmor는 휴면 기능입니다.",
  "## ④ P3 — Multicast 핫패스의 디버그 드로잉", "DrawDebugLine(...); DrawDebugPoint(...);"].join("\n");
const PROGRESS = "I've confirmed the P2 bug where `ClientConfirmHit_Implementation` unconditionally sets `LastHitConfirmationTime` on all clients.";

test("graded report sections exclude rejected findings and carry their severity", () => {
  assert.deepEqual(gradedSections(REPORT).map(s => [s.severity, s.label.slice(0, 4)]), [["P2", "② P2"], ["P3", "③ P3"], ["P3", "④ P3"]]);
});

test("a packet-free report and a dropped progress claim are matched to engine facts", () => {
  const evidence = returnedEngineEvidence(projectRead());
  const report = textEngineClaimGaps(gradedSections(REPORT), evidence, "unreal", "report");
  assert.deepEqual(report.map(g => g.factId), ["ue.uobject.weak-ptr-no-retention", "ue.debugdraw.compiled-out-in-shipping-test"]);
  const visible = [ChatMessage.from({ role: "assistant", content: PROGRESS }), ChatMessage.from({ role: "assistant", content: REPORT })];
  const progress = textEngineClaimGaps(confirmedProgressStatements(visible, REPORT), evidence, "unreal", "progress");
  assert.deepEqual(progress.map(g => [g.severity, g.factId]), [["P2", "ue.net.client-rpc-callspace"]]);
  const notice = reportEngineClaimNotice(report, progress);
  assert.match(notice, /\[엔진 동작 근거 · 보고서\]/u);
  assert.match(notice, /최종 보고에는 남지 않았습니다/u);
  assert.match(notice, /사실 판정이 아닙니다/u);
  // Once the report keeps the claim, it is listed as a report finding, not as dropped.
  assert.doesNotMatch(reportEngineClaimNotice(progress, progress), /남지 않았습니다/u);
  assert.equal(reportEngineClaimNotice([], []), null);
});

test("progress lines that do not confirm a finding, or reject one, are not claims", () => {
  const visible = ["Now I'll re-read Character.h to verify the specifiers.", "The earlier P1 hypothesis is rejected: ClientConfirmHit goes to all clients only if it were NetMulticast."]
    .map(text => ChatMessage.from({ role: "assistant", content: text }));
  assert.deepEqual(confirmedProgressStatements(visible, ""), []);
});

"use strict";
// Revised plan 2026-11-07 §D. Conversation 1793532481549 graded a finding on
// "any client can send this Server RPC to the component" without engine
// source. A server runs a client's Server RPC only from the owning connection.
const test = require("node:test");
const assert = require("node:assert/strict");
const { ChatMessage } = require("@lmstudio/sdk");
const { gradedSections, textEngineClaimGaps, reportEngineClaimNotice,
  returnedEngineEvidence: rawReturnedEngineEvidence } = require("../dist/engine-claim-anchoring");
const { bundledEngineClaimFacts } = require("../dist/generated-design-guidance");

const FACT = "ue.net.server-rpc-owning-connection";
function exchange(messages, id, args, result) {
  messages.push(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id, name: "read_file_range", arguments: args } }] }));
  messages.push(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
    content: JSON.stringify([{ type: "text", text: JSON.stringify(result) }]) }] }));
  return messages;
}
const evidence = messages => rawReturnedEngineEvidence(require("./helpers/returned-sources.cjs").ledgerView(messages));
const projectOnly = () => exchange([], "p1", { path: "project://Source/Game/Private/Components/WeaponFireComponent.cpp" }, { ok: true,
  path: "project://Source/Game/Private/Components/WeaponFireComponent.cpp", lineNumbers: true, startLine: 310, endLine: 312,
  content: "310\tvoid UWeaponFireComponent::ServerSetADS_Implementation(bool bNewADS)\n311\t{\n312\t\tSetADS(bNewADS);" });
const factsOf = (text, source = projectOnly()) => textEngineClaimGaps(gradedSections(text), evidence(source), "unreal", "report").map(gap => gap.factId);

// The run's P3-2 section: the claim sentence names no "Server"; the heading does.
const REPORT = "# 리뷰\n\n### P3-2: `ServerSetADS` 호출자 검증 부재 (WeaponFireComponent.cpp:310)\n"
  + "```cpp\nvoid UWeaponFireComponent::ServerSetADS_Implementation(bool bNewADS)\n{\n    SetADS(bNewADS);\n}\n```\n"
  + "어떤 클라이언트든 이 컴포넌트에 RPC를 보낼 수 있습니다. 다만 ADS 토글은 무해합니다.\n";

test("the owning-connection rule ships as an engine fact with 5.7 and 5.8 evidence", () => {
  const fact = bundledEngineClaimFacts.find(f => f.id === FACT);
  assert.ok(fact);
  assert.deepEqual(fact.engineVersions, ["5.7", "5.8"]);
  assert.ok(fact.evidenceRefs.some(ref => ref.symbol === "UNetDriver::ShouldCallRemoteFunction"));
  assert.ok(fact.evidenceRefs.some(ref => ref.symbol === "UActorComponent::GetFunctionCallspace"));
  assert.match(fact.statement, /Ownership says who may call, not whether the arguments/u);
});

test("the run's P3-2 claim asks for engine evidence, which clears it once read", () => {
  const gaps = textEngineClaimGaps(gradedSections(REPORT), evidence(projectOnly()), "unreal", "report");
  assert.deepEqual(gaps.map(gap => [gap.severity, gap.factId]), [["P3", FACT]]);
  assert.match(reportEngineClaimNotice(gaps, []), /\[엔진 동작 근거 · 보고서\]/u);
  const engine = exchange(projectOnly(), "e1", { path: "engine://Engine/Source/Runtime/Engine/Private/NetDriver.cpp" }, {
    ok: true, path: "engine://Engine/Source/Runtime/Engine/Private/NetDriver.cpp", evidenceSource: "installed_engine_source",
    lineNumbers: true, startLine: 8242, endLine: 8244,
    content: "8242\tbool UNetDriver::ShouldCallRemoteFunction(UObject* Object, UFunction* Function, const FReplicationFlags& RepFlags) const\n8243\t{\n8244\t\treturn ((!IsServer() || RepFlags.bNetOwner) && !RepFlags.bIgnoreRPCs);" });
  assert.deepEqual(factsOf(REPORT, engine).filter(id => id === FACT), []);
});

test("the wrong wording is caught in both languages and both orders", () => {
  for (const text of [
    "## P2\n- `ServerSetADS`: Server RPC는 어떤 연결된 클라이언트든 대상 Actor에 보낼 수 있습니다.",
    "## P2\n- `ServerFire`: any client can call this Server RPC on the weapon.",
    "## P2\n- `ServerFire`: The Server RPC accepts calls from any connected client.",
  ]) assert.ok(factsOf(text).includes(FACT), text);
});

test("correct statements, other RPC kinds and unrelated names do not trigger it", () => {
  for (const text of [
    "## P2\n- `ServerFire`: 소유 클라이언트만 이 Server RPC를 호출할 수 있고, 인자는 서버가 검증해야 합니다.",
    "## P2\n- `ServerFire`: Server RPC는 모든 클라이언트가 아니라 소유 연결에서만 실행됩니다.",
    "## P2\n- `ServerFire`: Only the owning client can call this Server RPC; not any client.",
    "## P2\n- `MulticastShot`: 어떤 클라이언트든 Multicast RPC를 받습니다.",
    "## P2\n- `ClientShotResult`: the Client RPC runs on the owning client.",
    "## P2\n- `HandleServerFire`: the handler runs on the server and validates the shot.",
    "## P2\n- `ServerFire`: the server calls its own RPC locally; gameplay validation is still required.",
  ]) assert.deepEqual(factsOf(text).filter(id => id === FACT), [], text);
});

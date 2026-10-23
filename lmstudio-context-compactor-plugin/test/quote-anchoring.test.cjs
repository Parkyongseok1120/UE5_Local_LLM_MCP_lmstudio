"use strict";

// The 2026-10-20 audit read every line it later cited, then, after those bodies
// were compacted, validated a packet quoting a call (PlaySound2D) and a handler
// (OnRep_bIsDead) the returned source never showed. The structure validator
// passed it. These checks compare the packet's code names with returned text.

const assert = require("node:assert/strict");
const test = require("node:test");
const { ChatMessage } = require("@lmstudio/sdk");
const { observedCodeTokens, packetQuoteMismatches, quoteAnchoringNotice, quoteCheckFact, returnedSourceLines,
  validatePackets } = require("../dist/quote-anchoring");

function exchange(messages, id, name, args, result) {
  messages.push(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id, name, arguments: args } }] }));
  messages.push(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
    content: JSON.stringify([{ type: "text", text: JSON.stringify(result) }]) }] }));
}

const numbered = (start, lines) => lines.map((line, i) => `${start + i}\t${line}`).join("\n");

function returned() {
  const messages = [];
  exchange(messages, "r1", "read_file_range", { path: "project://Source/Game/Private/GameCharacter.cpp" }, {
    ok: true, path: "project://Source/Game/Private/GameCharacter.cpp", projectRelativePath: "Source/Game/Private/GameCharacter.cpp",
    lineNumbers: true, startLine: 180, endLine: 186, content: numbered(180, [
      "bool AGameCharacter::CanFire() const",
      "{",
      "\tconst AGamePlayerState* PS = GetPlayerState<AGamePlayerState>();",
      "\tif (CombatState == ECombatState::Dead) return false;",
      "\treturn Controller && PS && !PS->bIsDead && MagazineAmmo > 0;",
      "}",
      ""]) });
  exchange(messages, "r2", "read_file_range", { path: "project://Source/Game/Private/GameCharacter.cpp" }, {
    ok: true, path: "project://Source/Game/Private/GameCharacter.cpp", projectRelativePath: "Source/Game/Private/GameCharacter.cpp",
    lineNumbers: true, startLine: 336, endLine: 343, content: numbered(336, [
      "void AGameCharacter::MulticastShot_Implementation(FVector_NetQuantize Start, FVector_NetQuantize End, bool bHit)",
      "{",
      "\tif (GetNetMode() == NM_DedicatedServer) return;",
      "\tDrawDebugLine(GetWorld(), Start, End, FColor::Yellow, false, 0.08f, 0, 1.f);",
      "\tif (bHit) DrawDebugPoint(GetWorld(), End, 8.f, FColor::Orange, false, 0.15f);",
      "\tif (CurrentWeapon && !CurrentWeapon->FireSound.IsNull())",
      "\t\tUGameplayStatics::PlaySoundAtLocation(this, CurrentWeapon->FireSound.LoadSynchronous(), Start);",
      "}"]) });
  exchange(messages, "r3", "read_file_range", { path: "project://Source/Game/Public/GamePlayerState.h" }, {
    ok: true, path: "project://Source/Game/Public/GamePlayerState.h", projectRelativePath: "Source/Game/Public/GamePlayerState.h",
    lineNumbers: true, startLine: 24, endLine: 41, content: numbered(24, [
      "", "\tUPROPERTY(ReplicatedUsing = OnRep_KillCount, BlueprintReadOnly)", "\tint32 KillCount;", "",
      "\tUPROPERTY(ReplicatedUsing = OnRep_Health, BlueprintReadOnly)", "\tfloat CurrentHealth;", "",
      "\tUPROPERTY(ReplicatedUsing = OnRep_Health, BlueprintReadOnly)", "\tfloat CurrentArmor;", "",
      "\tUPROPERTY(Replicated, BlueprintReadOnly)", "\tfloat MaxHealth;", "", "\t// Respawn", "",
      "\tUPROPERTY(Replicated, BlueprintReadOnly)", "\tbool bIsDead;", ""]) });
  return messages;
}

const evidence = (location, observation) => ({ kind: "project_source", location, observation });
const packet = (...items) => ({ mode: "audit", claims: [{ claim: "c", evidence: items }] });

test("a call and a handler that no returned body shows are named with the returned line", () => {
  const files = returnedSourceLines(returned());
  const mismatches = packetQuoteMismatches(packet(
    evidence("Source/Game/Private/GameCharacter.cpp:342",
      "UGameplayStatics::PlaySound2D(this, CurrentWeapon->FireSound.LoadSynchronous()) - synchronous load"),
    evidence("Source/Game/Public/GamePlayerState.h:24-25", "bIsDead is ReplicatedUsing=OnRep_bIsDead on PlayerState")), files);
  assert.deepEqual(mismatches.map(m => [m.claimPath, m.kind, m.missingTokens]), [
    ["claims[0].evidence[0]", "not_in_returned_source", ["PlaySound2D"]],
    ["claims[0].evidence[1]", "not_in_returned_source", ["OnRep_bIsDead"]]]);
  assert.match(mismatches[0].returnedLines[0], /^342: UGameplayStatics::PlaySoundAtLocation\(this, .*Start\);$/);
  // Blank cited lines are not shown as returned text.
  assert.deepEqual(mismatches[1].returnedLines, ["25: UPROPERTY(ReplicatedUsing = OnRep_KillCount, BlueprintReadOnly)"]);
  const fact = quoteCheckFact(mismatches);
  assert.match(fact, /quotes 2 code name\(s\) that the source returned in this execution does not show/);
  assert.match(fact, /it is not a fact check/);
  assert.match(quoteAnchoringNotice(mismatches), /\[인용 대조\].*2건.*사실 검증이 아닙니다/s);
});

test("faithful quotes, enclosing function names and prose produce no mismatch", () => {
  const files = returnedSourceLines(returned());
  assert.deepEqual(packetQuoteMismatches(packet(
    evidence("GameCharacter.cpp:342", "UGameplayStatics::PlaySoundAtLocation(this, FireSound.LoadSynchronous(), Start)"),
    evidence("Source/Game/Private/GameCharacter.cpp:184", "CanFire() returns false when PS->bIsDead"),
    evidence("Source/Game/Public/GamePlayerState.h:39-40", "UPROPERTY(Replicated, BlueprintReadOnly) bool bIsDead"),
    evidence("Source/Game/Public/GamePlayerState.h:39-40", "two replicated properties keep the death state")), files), []);
  assert.equal(quoteCheckFact([]), null);
  assert.equal(quoteAnchoringNotice([]), null);
});

test("a declaration cited on the wrong lines is reported as not at the cited lines", () => {
  const files = returnedSourceLines(returned());
  const [mismatch, ...rest] = packetQuoteMismatches(packet(
    evidence("Source/Game/Public/GamePlayerState.h:28-29", "UPROPERTY(Replicated, BlueprintReadOnly) float MaxHealth")), files);
  assert.equal(rest.length, 0);
  assert.equal(mismatch.kind, "not_at_cited_lines");
  assert.deepEqual(mismatch.missingTokens, ["Replicated"]);
});

test("ranges this execution never returned, unparsable locations and other evidence kinds are not judged", () => {
  const files = returnedSourceLines(returned());
  assert.deepEqual(packetQuoteMismatches(packet(
    evidence("Source/Game/Private/GameCharacter.cpp:500", "Foo::Missing() is called"),
    evidence("Source/Game/Private/GameMode.cpp:10", "Foo::Missing() is called"),
    evidence("GameCharacter.cpp", "Foo::Missing() is called"),
    { kind: "engine_source", location: "Engine/X.cpp:342", observation: "Foo::Missing()" }), files), []);
  assert.deepEqual(packetQuoteMismatches({ claims: "bad" }, files), []);
});

test("code tokens are calls, members, handlers and macro specifiers, not prose", () => {
  assert.deepEqual(observedCodeTokens("UFUNCTION(NetMulticast, Unreliable) void MulticastShot(...) on all clients").sort(),
    ["MulticastShot", "NetMulticast", "Unreliable"]);
  assert.deepEqual(observedCodeTokens("Server and client both replicated (line 307) handlers"), []);
  assert.deepEqual(observedCodeTokens("DOREPLIFETIME_CONDITION(..., COND_OwnerOnly)").sort(), ["COND_OwnerOnly", "DOREPLIFETIME_CONDITION"]);
});

test("only validator requests carry packets", () => {
  const messages = returned();
  messages.push(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
    type: "function", id: "v1", name: "evidence_first_validate", arguments: { packet: packet() } } }] }));
  assert.equal(validatePackets(messages).length, 1);
});

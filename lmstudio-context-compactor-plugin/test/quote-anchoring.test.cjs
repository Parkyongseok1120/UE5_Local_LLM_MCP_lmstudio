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

function exchanges(...reads) {
  const messages = [];
  reads.forEach(([name, result], index) => exchange(messages, `x${index}`, name, {}, result));
  return returnedSourceLines(messages);
}
const tickRead = ["read_file_range", { ok: true, path: "Source/A.cpp", lineNumbers: true, startLine: 12, endLine: 12,
  content: "12\tvoid Tick() { DoWork(); }" }];
const cleanupRead = ["read_file_range", { ok: true, path: "Source/B.cpp", lineNumbers: true, startLine: 50, endLine: 51,
  content: "50\tvoid Cleanup() {}\n51\tvoid DoWork() {}" }];

// Review of 7043e1a: an observation saying a call is absent was reported as a
// misquote of that call, and the warning reached the next input and the answer.
test("names an observation denies, compares or proposes are not claims, in English and Korean", () => {
  const files = exchanges(tickRead);
  for (const observation of [
    "Tick() calls DoWork() but never calls Cleanup().",
    "Tick() calls DoWork() without calling Cleanup().",
    "Cleanup() is not called from Tick().",
    "Tick() should call Cleanup() after DoWork().",
    "Tick() calls DoWork() instead of Cleanup().",
    "Tick()은 DoWork()를 호출하지만 Cleanup()은 호출하지 않는다.",
    "Tick()에서 Cleanup() 호출이 없다.",
    "Cleanup() 호출이 누락되어 DoWork()만 실행된다.",
    "Tick()은 Cleanup()을 호출해야 한다.",
  ]) assert.deepEqual(packetQuoteMismatches(packet(evidence("Source/A.cpp:12", observation)), files), [], observation);
  assert.deepEqual(observedCodeTokens("Tick() calls DoWork() but never calls Cleanup()."), ["Tick", "DoWork"]);
});

test("a denial in one clause does not hide a misquote in another clause of the same sentence", () => {
  const files = exchanges(tickRead);
  const [english] = packetQuoteMismatches(packet(evidence("Source/A.cpp:12",
    "Tick() calls PlaySound2D() but never calls Cleanup().")), files);
  assert.deepEqual([english.kind, english.missingTokens], ["not_in_returned_source", ["PlaySound2D"]]);
  const [korean] = packetQuoteMismatches(packet(evidence("Source/A.cpp:12",
    "Tick()은 PlaySound2D()를 호출하지만 Cleanup()은 호출하지 않는다.")), files);
  assert.deepEqual([korean.kind, korean.missingTokens], ["not_in_returned_source", ["PlaySound2D"]]);
});

// Review of 7043e1a: a mid-file byte window (the long-line recovery read) was
// registered as lines 1.. and overwrote the file's real first lines.
test("a mid-file byte window registers no lines and leaves earlier numbered lines intact", () => {
  const files = exchanges(
    ["read_file_range", { ok: true, path: "Source/A.cpp", lineNumbers: true, startLine: 1, endLine: 2,
      content: "1\tvoid Begin() {}\n2\tvoid Second() {}" }],
    ["read_file", { ok: true, path: "Source/A.cpp", offsetBytes: 4000, nextOffsetBytes: 4031, hasMore: true,
      content: "TailOfOtherLine();\nSomething();" }]);
  assert.deepEqual([...files[0].lines], [[1, "void Begin() {}"], [2, "void Second() {}"]]);
  assert.deepEqual(packetQuoteMismatches(packet(evidence("Source/A.cpp:1", "Begin() is defined here.")), files), []);
  assert.deepEqual(exchanges(["read_file", { ok: true, path: "Source/C.cpp", offsetBytes: 900, content: "Orphan();" }]), []);
});

test("a byte window from the file start keeps its line positions and drops a cut last line", () => {
  const [file] = exchanges(["read_file", { ok: true, path: "Source/A.cpp", offsetBytes: 0, nextOffsetBytes: 30,
    hasMore: true, truncated: true, content: "void Begin() {}\nvoid Sec" }]);
  assert.deepEqual([...file.lines], [[1, "void Begin() {}"]]);
  const [whole] = exchanges(["read_file", { ok: true, path: "Source/D.ini", offsetBytes: 0, nextOffsetBytes: 10,
    hasMore: false, content: "a=1\nb=2" }]);
  assert.deepEqual([...whole.lines], [[1, "a=1"], [2, "b=2"]]);
  // A small project source read whole by read_file comes back numbered from line 1.
  const [source] = exchanges(["read_file", { ok: true, path: "Source/E.h", lineNumbers: true, startLine: 1, endLine: 2,
    totalLines: 2, rangeComplete: true, content: "1\t#pragma once\n2\tvoid E();" }]);
  assert.deepEqual([...source.lines], [[1, "#pragma once"], [2, "void E();"]]);
});

// Review of 7043e1a: a name misquoted on one file's line passed because a
// different returned file contained it.
test("a short cite is checked against its own file even when another file has the name", () => {
  const files = exchanges(tickRead, cleanupRead);
  const [mismatch, ...rest] = packetQuoteMismatches(packet(evidence("Source/A.cpp:12", "Tick() calls Cleanup().")), files);
  assert.equal(rest.length, 0);
  assert.deepEqual([mismatch.kind, mismatch.missingTokens], ["not_at_cited_lines", ["Cleanup"]]);
  assert.deepEqual(mismatch.returnedLines, ["12: void Tick() { DoWork(); }"]);
  // A call on the cited line is fine although its definition is in another file.
  assert.deepEqual(packetQuoteMismatches(packet(evidence("Source/A.cpp:12", "Tick() calls DoWork().")), files), []);
  // An observation that cites further lines itself may describe code there.
  assert.deepEqual(packetQuoteMismatches(packet(evidence("Source/A.cpp:12",
    "Tick() calls DoWork(); the Cleanup() body (B.cpp:50) is empty.")), files), []);
  // A wider range describes a path into other files and is not held to exact lines.
  const range = exchanges(["read_file_range", { ok: true, path: "Source/A.cpp", lineNumbers: true, startLine: 10, endLine: 16,
    content: [10, 11, 12, 13, 14, 15, 16].map(n => `${n}\t${n === 12 ? "void Tick() { DoWork(); }" : "//"}`).join("\n") }], cleanupRead);
  assert.deepEqual(packetQuoteMismatches(packet(evidence("Source/A.cpp:10-16", "Tick() leads to Cleanup().")), range), []);
});

test("repeating the cited location in prose never exempts a wrong short quote", () => {
  const files = exchanges(tickRead, cleanupRead);
  for (const observation of [
    "Line 12: Tick() calls Cleanup().",
    "Source/A.cpp:12 calls Cleanup().",
    "12행에서 Tick()은 Cleanup()을 호출한다.",
    "Tick() calls Cleanup() (Source/A.cpp:12).",
  ]) {
    const mismatches = packetQuoteMismatches(packet(evidence("Source/A.cpp:12", observation)), files);
    assert.deepEqual(mismatches.map(m => [m.kind, m.missingTokens]),
      [["not_at_cited_lines", ["Cleanup"]]], observation);
  }
});

test("additional citations apply only to their own clauses and returned ranges", () => {
  const files = exchanges(tickRead, cleanupRead);
  const mismatches = packetQuoteMismatches(packet(evidence("Source/A.cpp:12",
    "Tick() calls Cleanup(); the Cleanup() body (B.cpp:50) is empty.")), files);
  assert.deepEqual(mismatches.map(m => [m.kind, m.missingTokens]), [["not_at_cited_lines", ["Cleanup"]]]);
  const distant = exchanges(tickRead, ["read_file_range", { ok: true, path: "Source/B.cpp", lineNumbers: true,
    content: "50\tvoid Cleanup() {}\n100\tvoid DoWork() {}" }]);
  assert.deepEqual(packetQuoteMismatches(packet(evidence("Source/A.cpp:12",
    "Tick() calls DoWork(); the Cleanup() body (B.cpp:100) is here.")), distant)
    .map(m => m.missingTokens), [["Cleanup"]]);
  // A returned sibling implementation cited using only its extension is usable.
  const sibling = exchanges(
    ["read_file_range", { ok: true, path: "Source/Public/Character.h", lineNumbers: true,
      content: "92\tvoid Update();" }],
    ["read_file_range", { ok: true, path: "Source/Private/Character.cpp", lineNumbers: true,
      content: "315\tvoid Update() { GetWorld()->GetTimeSeconds(); }" }]);
  assert.deepEqual(packetQuoteMismatches(packet(evidence("Source/Public/Character.h:92",
    "Update() (cpp:315-317) calls GetWorld()->GetTimeSeconds().")), sibling), []);
});

test("English and Korean negative lists retain their meaning across commas", () => {
  const files = exchanges(tickRead);
  for (const observation of [
    "Tick() calls DoWork() but never calls Cleanup(), Destroy(), or Reset().",
    "Tick() does not call Cleanup(), Destroy(), or Reset().",
    "Tick() should call Cleanup(), Destroy(), and Reset().",
    "Tick()은 DoWork()를 호출하지만 Cleanup(), Destroy() 호출은 없다.",
    "Tick()은 Cleanup(), Destroy()를 호출하지 않는다.",
  ]) assert.deepEqual(packetQuoteMismatches(packet(evidence("Source/A.cpp:12", observation)), files), [], observation);
});

test("a negative list does not exempt a separate affirmative clause", () => {
  const files = exchanges(tickRead);
  for (const observation of [
    "Tick() calls PlaySound2D() but never calls Cleanup(), Destroy(), or Reset().",
    "Tick() never calls Cleanup(), Destroy(), but calls PlaySound2D().",
    "Cleanup(), Destroy() are absent, Tick() calls PlaySound2D().",
    "Tick()은 PlaySound2D()를 호출하지만 Cleanup(), Destroy() 호출은 없다.",
    "Cleanup(), Destroy() 호출은 없지만 Tick()은 PlaySound2D()를 호출한다.",
  ]) assert.deepEqual(packetQuoteMismatches(packet(evidence("Source/A.cpp:12", observation)), files)
    .map(m => m.missingTokens), [["PlaySound2D"]], observation);
});

test("a partial byte read ending at a newline cannot overwrite the next known line", () => {
  for (const newline of ["\n", "\r\n"]) {
    const [file] = exchanges(
      ["read_file_range", { ok: true, path: "A.cpp", lineNumbers: true,
        content: "1\tvoid Begin() {}\n2\t\n3\tvoid Important() {}" }],
      ["read_file", { ok: true, path: "A.cpp", offsetBytes: 0, hasMore: true, truncated: true,
        content: `void Begin() {}${newline}${newline}` }]);
    assert.deepEqual([...file.lines], [[1, "void Begin() {}"], [2, ""], [3, "void Important() {}"]]);
  }
  const [whole] = exchanges(["read_file", { ok: true, path: "A.cpp", offsetBytes: 0,
    hasMore: false, truncated: false, content: "void Begin() {}\n" }]);
  assert.deepEqual([...whole.lines], [[1, "void Begin() {}"], [2, ""]]);
});

// The 2026-10-24 audit said it checked "전체 소스(21개 파일)": 21 was a search's
// filesScanned. Bodies were returned for 15 project files.
test("a claimed file count above the returned bodies gets a coverage footer", () => {
  const { fileCoverageNotice } = require("../dist/quote-anchoring");
  const messages = returned();
  exchange(messages, "s1", "search_files", { query: "FireRate" }, { ok: true, filesScanned: 21, results: [] });
  exchange(messages, "e1", "read_file_range", { path: "engine://Engine/Source/Runtime/Engine/Private/Actor.cpp" }, {
    ok: true, path: "engine://Engine/Source/Runtime/Engine/Private/Actor.cpp", lineNumbers: true, startLine: 1, endLine: 1, content: "1\tx" });
  const notice = fileCoverageNotice(messages, "전체 소스(21개 파일)를 확인했습니다. P0/P1 결함은 없습니다.");
  assert.match(notice, /21개 파일을 확인했다고 하지만/u);
  assert.match(notice, /프로젝트 파일은 2개/u);
  assert.match(fileCoverageNotice(messages, "I reviewed all 9 files."), /9개/u);
  for (const answer of ["2개 파일을 확인했습니다.", "전체 소스를 확인했습니다.", "P3 3개 항목입니다."])
    assert.equal(fileCoverageNotice(messages, answer), null, answer);
});

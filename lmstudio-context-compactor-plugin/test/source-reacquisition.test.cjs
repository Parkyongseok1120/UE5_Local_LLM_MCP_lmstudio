"use strict";

// Source the model already read stays cheap to recover: one archive call can
// return a whole archived body, a line window can be read alone, a projected
// source body keeps declaration spans, and archived prose says it is this
// run's own earlier text.
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { EvidenceArchive } = require("../src/evidence-archive.js");
const { WorkingContext } = require("../src/working-context.js");
const { ARCHIVE_READ_CONTRACT } = require("../src/evidence-read-contract.js");
const { sourceOutline, isProjectSourcePath } = require("../src/source-outline.js");
const { BudgetBroker, reserveReadResult } = require("../dist/budget-broker");
const { localObservationTools } = require("../dist/tool-capability-registry");

const scope = { conversation: "chat-1", lineage: "fork-1", workspace: "workspace-1", repository: "worktree-1" };
const environment = { signal: new AbortController().signal };

function sourceLines(count, from = 1) {
  return Array.from({ length: count }, (_, i) => `// line ${from + i} ${"x".repeat(60)}`);
}

function archivedRange(archive, content, startLine = 1, filePath = "project://Source/Game/Big.cpp") {
  return archive.put(JSON.stringify({ ok: true, path: filePath, startLine, endLine: startLine + content.split("\n").length - 1, content }), {
    callKey: `read-${startLine}-${content.length}`, providerRequestId: `call-${startLine}`, toolName: "read_file_range",
    sourceIdentity: { path: filePath } }).record;
}

test("a 19KB archived source body returns in one call when the budget allows", () => {
  const archive = new EvidenceArchive(scope);
  const content = sourceLines(270).join("\n");
  assert.ok(content.length > 19000 && content.length < 20000);
  const record = archivedRange(archive, content);
  const handle = archive.readHandleFor(record);
  const whole = archive.readByHandle(handle, 0, ARCHIVE_READ_CONTRACT.maximumEnvelopeChars, "body");
  assert.equal(whole.ok, true);
  assert.equal(whole.content, content);
  assert.equal(whole.archiveReachedEnd, true);
  assert.equal(whole.archiveHasMore, false);
  assert.equal(whole.bodyComplete, true);
  assert.ok(JSON.stringify(whole).length <= ARCHIVE_READ_CONTRACT.maximumEnvelopeChars);
  // The former page size still pages, and the envelope bound is unchanged in kind.
  const page = archive.readByHandle(handle, 0, 8192, "body");
  assert.equal(page.archiveHasMore, true);
  assert.ok(JSON.stringify(page).length <= 8192);
  assert.equal(archive.read(record.evidenceId, record.archivedBodyHash, 0,
    ARCHIVE_READ_CONTRACT.maximumEnvelopeChars + 1, "body").errorCode, "invalid_range");
  assert.equal(archive.read(record.evidenceId, "0".repeat(64), 0, 24576, "body").errorCode, "version_mismatch");
});

test("the larger archive request is still lowered to the input room the batch has", () => {
  const tool = new WorkingContext(scope).tool();
  assert.equal(tool.parametersJsonSchema.properties.maxChars.maximum, ARCHIVE_READ_CONTRACT.maximumEnvelopeChars);
  localObservationTools.add(tool);
  const broker = new BudgetBroker({ workingInputTargetTokens: 40000, workingInputTriggerTokens: 56000, safetyMarginTokens: 2048 });
  const roomy = broker.beginBatch({ contextLength: 84224, inputTokens: 20000, outputReserve: 8192 }, true);
  assert.equal(reserveReadResult(roomy, "whole", tool, { maxChars: 24576 }).limit.effective, 24576);
  const pressured = broker.beginBatch({ contextLength: 84224, inputTokens: 56000, outputReserve: 8192 }, true);
  const bounded = reserveReadResult(pressured, "bounded", tool, { maxChars: 24576 });
  assert.equal(bounded.allowed, true);
  assert.ok(bounded.limit.effective < 24576 && bounded.limit.effective >= ARCHIVE_READ_CONTRACT.minimumEnvelopeChars);
});

test("an archived source body reads by source-file line range and continues on whole lines", async () => {
  const context = new WorkingContext(scope);
  const lines = sourceLines(200, 100);
  const record = archivedRange(context.archive, lines.join("\n"), 100);
  const readHandle = context.archive.readHandleFor(record);
  const tool = context.tool();
  const window = await tool.implementation({ action: "read", readHandle, startLine: 110, endLine: 114, maxChars: 4096 }, environment);
  assert.equal(window.ok, true);
  assert.equal(window.content, lines.slice(10, 15).join("\n") + "\n");
  assert.deepEqual(window.requestedLines, [110, 114]);
  assert.deepEqual(window.returnedLines, [110, 114]);
  assert.equal(window.lineRangeComplete, true);
  assert.equal(window.nextStartLine, null);
  assert.equal(window.archiveReachedEnd, false);
  // A budget-cut window ends on a line boundary, and nextStartLine resumes it exactly.
  let collected = "", startLine = 100, rounds = 0;
  while (startLine !== null) {
    const page = await tool.implementation({ action: "read", readHandle, startLine, endLine: 299, maxChars: 4096 }, environment);
    assert.equal(page.ok, true);
    assert.ok(JSON.stringify(page).length <= 4096);
    assert.ok(page.lineRangeComplete || page.content.endsWith("\n"));
    collected += page.content; startLine = page.nextStartLine; rounds++;
  }
  assert.equal(collected, lines.join("\n"));
  assert.ok(rounds > 1);
  const tail = await tool.implementation({ action: "read", evidenceId: record.evidenceId, version: record.archivedBodyHash,
    startLine: 298 }, environment);
  assert.equal(tail.content, lines.slice(198).join("\n"));
  assert.equal(tail.archiveReachedEnd, true);
  assert.deepEqual(tail.returnedLines, [298, 299]);
  assert.deepEqual((await tool.implementation({ action: "read", readHandle, startLine: 50 }, environment)).availableLines, [100, 299]);
  assert.equal((await tool.implementation({ action: "read", readHandle, startLine: 120, endLine: 110 }, environment)).errorCode, "line_range_outside_body");
  assert.equal((await tool.implementation({ action: "read", readHandle, endLine: 110 }, environment)).errorCode, "start_line_required");
  assert.equal((await tool.implementation({ action: "read", readHandle, startLine: 110, startOffset: 0 }, environment)).errorCode, "pagination_conflict");
  assert.equal(context.archive.read(record.evidenceId, record.archivedBodyHash, 0, 4096, "envelope", { startLine: 110 }).errorCode,
    "line_range_requires_body_view");
});

const HEADER = [
  "#pragma once",
  "class UHealthComponent;",
  "UCLASS(Blueprintable)",
  "class MYGAME_API AMyActor : public AActor",
  "{",
  "  GENERATED_BODY()",
  "public:",
  "  UPROPERTY(EditAnywhere, Category = \"Stats\",",
  "    meta = (ClampMin = \"0\"))",
  "  float Health = 100.f;",
  "  UFUNCTION(BlueprintCallable)",
  "  void ApplyDamage(float Amount, AActor* Source);",
  "  virtual void Tick(float DeltaTime) override;",
  "};",
];
const DEFINITIONS = [
  "AMyActor::AMyActor()",
  "  : Super()",
  "{",
  "  PrimaryActorTick.bCanEverTick = true; // { not a brace",
  "}",
  "void AMyActor::ApplyDamage(float Amount, AActor* Source)",
  "{",
  "  if (Amount <= 0.f) { return; }",
  "  FVector Loc(0.f, 0.f, 0.f);",
  "  Super::BeginPlay();",
  "  UE_LOG(LogTemp, Log, TEXT(\"damage {%f}\"), Amount);",
  "}",
];

test("the source outline lists reflected members and definitions with source-file line spans", () => {
  assert.deepEqual(sourceOutline(HEADER.join("\n"), { path: "project://Source/Game/MyActor.h" }).entries,
    ["4-14 class AMyActor", "8-10 UPROPERTY Health", "11-12 UFUNCTION ApplyDamage", "13 fn Tick"]);
  // Range reads keep their file line numbers; CRLF and literals do not shift spans.
  assert.deepEqual(sourceOutline(DEFINITIONS.join("\r\n"), { path: "Source/Game/MyActor.cpp", startLine: 40 }).entries,
    ["40-44 fn AMyActor::AMyActor", "45-51 fn AMyActor::ApplyDamage"]);
  const csharp = ["namespace Game", "{", "    public sealed class Player : MonoBehaviour", "    {",
    "        private void Update()", "        {", "            Move(Mathf.Max(1, 2));", "        }", "    }", "}"];
  assert.deepEqual(sourceOutline(csharp.join("\n"), { path: "project://Assets/Player.cs" }).entries,
    ["3-9 class Player", "5-8 fn Update"]);
  assert.equal(isProjectSourcePath("engine://Engine/Source/Runtime/Engine/Private/Actor.cpp"), false);
  assert.equal(sourceOutline(DEFINITIONS.join("\n"), { path: "engine://Engine/Source/Actor.cpp" }), null);
  assert.equal(sourceOutline("# Notes\nvoid Foo(int A);", { path: "project://Docs/Notes.md" }), null);
  const many = Array.from({ length: 200 }, (_, i) => `void Function${i}(int32 Value);`).join("\n");
  const bounded = sourceOutline(many, { path: "project://Source/Game/Many.h" });
  assert.ok(bounded.entries.length <= 32 && bounded.entriesOmitted > 0);
  assert.ok(JSON.stringify(bounded.entries).length <= 1536 + 64);
});

function projectedRead(filePath, content, startLine = 1, metadata = {}) {
  const history = Chat.from([{ role: "user", content: "Audit the source." }]);
  const request = { type: "function", id: "read-big", name: "read_file_range", arguments: { path: filePath, startLine } };
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: request }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: request.id,
    content: JSON.stringify({ ok: true, path: filePath, startLine, endLine: startLine + content.split("\n").length - 1, content, ...metadata }) }] }));
  const projected = new WorkingContext(scope).project(history, () => true, {}, 512);
  assert.equal(projected.changed, true);
  return JSON.parse(projected.history.getMessagesArray().at(-1).getToolCallResults()[0].content);
}

test("a body projection of project C++ keeps a short outline for a line-range re-read", () => {
  const body = [...HEADER, ...sourceLines(200, 15)].join("\n");
  const value = projectedRead("project://Source/Game/MyActor.h", body);
  assert.equal(value.bodyComplete, false);
  assert.deepEqual(value.sourceOutline.entries.slice(0, 4),
    ["4-14 class AMyActor", "8-10 UPROPERTY Health", "11-12 UFUNCTION ApplyDamage", "13 fn Tick"]);
  assert.equal(value.sourceOutline.lineUnit, "source_file_line");
  assert.match(value.notice, /^PARTIAL BODY: chars 0-\d+ of \d+; page with evidence_first_read_context readHandle=\S+; sourceOutline/);
  const engine = projectedRead("engine://Engine/Source/Runtime/Engine/Classes/MyActor.h", body);
  assert.equal(engine.sourceOutline, undefined);
  assert.doesNotMatch(engine.notice, /sourceOutline/);
});

test("real numbered source responses retain declaration names and original line spans", () => {
  const body = [...HEADER, ...sourceLines(200, 15)].map((line, index) => `${index + 101}\t${line}`).join("\n");
  const value = projectedRead("project://Source/Game/MyActor.h", body, 101, { lineNumbers: true });
  assert.deepEqual(value.sourceOutline.entries.slice(0, 4),
    ["104-114 class AMyActor", "108-110 UPROPERTY Health", "111-112 UFUNCTION ApplyDamage", "113 fn Tick"]);
});

test("byte windows without a source line origin cannot invent line numbers", () => {
  const archive = new EvidenceArchive(scope);
  const content = [...HEADER, ...sourceLines(200)].join("\n");
  const record = archive.put(JSON.stringify({ ok: true, path: "project://Source/Game/MyActor.h", offsetBytes: 5000, content }),
    { toolName: "read_file", callKey: "middle-byte-window" }).record;
  const read = archive.readByHandle(archive.readHandleFor(record), 0, 4096, "body", { startLine: 1, endLine: 2 });
  assert.equal(read.errorCode, "source_line_origin_unknown");
  assert.equal(archive.readByHandle(archive.readHandleFor(record), 0, 4096, "body").ok, true);
  assert.equal(sourceOutline(content, { path: "project://Source/Game/MyActor.h", offsetBytes: 5000 }), null);
});

test("archived conversation prose is labelled as this run's own earlier text, not a previous session", () => {
  const archive = new EvidenceArchive(scope);
  const context = new WorkingContext(scope, { archive });
  const history = Chat.from([{ role: "user", content: "Audit the damage pipeline." },
    { role: "assistant", content: "Hypothesis: ApplyDamage double-applies armor." }, { role: "user", content: "continue" }]);
  assert.equal(context.archiveConversationProse(history, [2]), 2);
  const entries = archive.catalogPage({ maxChars: 8192 }).entries;
  assert.deepEqual(entries.map(e => e.conversationOrigin), ["earlier_round_same_execution", "earlier_round_same_execution"]);
  const assistant = entries.find(e => e.path === "conversation://assistant");
  const read = archive.readByHandle(assistant.readHandle, 0, 4096, "body");
  assert.equal(read.content, "Hypothesis: ApplyDamage double-applies armor.");
  assert.equal(read.conversationOrigin, "earlier_round_same_execution");
  assert.match(read.notice, /earlier round of this same execution/);
  assert.match(read.notice, /not a previous session/);
  assert.match(read.notice, /your own unverified working hypotheses/);
  // A record inherited by the next turn's lineage is an earlier turn of this conversation.
  archive.setAccess("fork-2", [[assistant.evidenceId, assistant.version]]);
  const inherited = archive.read(assistant.evidenceId, assistant.version, 0, 4096, "body");
  assert.equal(inherited.conversationOrigin, "earlier_turn_same_conversation");
  assert.match(inherited.notice, /earlier turn of this same conversation/);
  // Ordinary evidence carries no conversation label.
  const source = archivedRange(archive, sourceLines(3).join("\n"));
  assert.equal(archive.read(source.evidenceId, source.archivedBodyHash, 0, 4096, "body").conversationOrigin, undefined);
  const { mandatoryContinuity } = require("../src/checkpoint-budget.js");
  if (mandatoryContinuity) assert.match(mandatoryContinuity({}).archiveDiscovery, /this same execution.*not a previous session/);
});

// --- Read ledger: a same-hash re-read after compaction (run 1792842246501) ---
// Synthetic stand-ins for that run: a 420-line source read in full, projected
// away under input pressure, then read again with the same sha.
const { EvidenceManager } = require("../dist/evidence-manager");
const SOURCE = "project://Source/Game/LedgerCharacter.cpp", TYPES = "project://Source/Game/LedgerTypes.h";
const shaOf = seed => seed.repeat(64).slice(0, 64);
const readTool = { name: "read_file", pluginIdentifier: "mcp/unreal-agent" };
const rangeTool = { name: "read_file_range", pluginIdentifier: "mcp/unreal-agent" };
const characterBody = [...Array(420)].map((_, i) => i === 199 ? "void ALedgerCharacter::Fire() {" : i === 202 ? "}"
  : `// line ${i + 1} ${"y".repeat(40)}`).join("\n");

function readResult(path, sha, content, extra = {}) {
  const lines = content.split("\n").length;
  return { ok: true, kind: "workspace_file_observation", path, canonicalProject: "C:/Game/Game.uproject", sha256: sha,
    startLine: 1, endLine: lines, totalLines: lines, content, ...extra };
}

function exchangeMessages(id, name, args, value) {
  return [ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: { type: "function", id, name, arguments: args } }] }),
    ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id, content: JSON.stringify(value) }] })];
}

function returnRound(manager, messages, roundIndex) {
  const screening = manager.screenReturnedReads(messages, roundIndex);
  manager.captureReturned(screening.messages, "exec", `input-${roundIndex}`);
  manager.recordReturnedReads(screening);
  return screening;
}

function ledgerSetup() {
  const context = new WorkingContext(scope);
  const archiveTool = context.tool();
  localObservationTools.add(archiveTool);
  const manager = new EvidenceManager(context, [readTool, rangeTool, archiveTool]);
  const history = Chat.from([{ role: "user", content: "Audit the character code." }]);
  const first = returnRound(manager, exchangeMessages("c1", "read_file", { path: SOURCE }, readResult(SOURCE, shaOf("3c"), characterBody)), 0);
  for (const message of first.messages) history.append(message);
  return { context, manager, history, archiveTool, first };
}

const resultOf = screening => JSON.parse(screening.messages.at(-1).getToolCallResults()[0].content);

test("a full read, a projection, then the identical read returns an UNCHANGED fact and locator, not a second body", async () => {
  const { context, manager, history, archiveTool, first } = ledgerSetup();
  assert.equal(first.newDeliveries, 1);
  assert.equal(first.replaced, 0);
  const projected = manager.project(history, {}, 512).history;
  assert.match(projected.toString(), /archived_tool_result_projection/);
  const again = returnRound(manager, exchangeMessages("c2", "read_file", { path: SOURCE, byteBudget: 24576 },
    readResult(SOURCE, shaOf("3c"), characterBody, { repeatReceipt: "rr_2" })), 5);
  assert.equal(again.replaced, 1);
  assert.equal(again.redundant, 1);
  assert.equal(again.duplicatedChars, characterBody.length);
  assert.equal(again.newDeliveries, 0);
  const fact = resultOf(again);
  assert.equal(fact.kind, "redundant_source_read");
  assert.equal(fact.content, undefined, "no second body");
  assert.equal(fact.sha256, shaOf("3c"));
  assert.equal(fact.unchangedSince, "round 1");
  assert.equal(fact.readCoverageState, "complete");
  assert.equal(fact.repeatReceipt, "rr_2");
  assert.match(fact.notice, /^UNCHANGED: .*LedgerCharacter\.cpp sha 3c3c3c3c is the same version already returned in round 1 \(lines 1-420 \(complete\)\)\. The file was not modified/);
  assert.ok(fact.sourceOutline.entries.some(entry => /ALedgerCharacter::Fire/.test(entry)));
  assert.ok(JSON.stringify(fact).length < 2500, "the fact is small");
  // The locator pages exactly the archived version, by line range or by outline name.
  const span = await archiveTool.implementation({ action: "read", readHandle: fact.archiveRef.readHandle, view: "body",
    symbol: "ALedgerCharacter::Fire", maxChars: 4096 }, environment);
  assert.equal(span.ok, true, JSON.stringify(span));
  assert.deepEqual(span.requestedLines, [200, 203]);
  assert.match(span.content, /^void ALedgerCharacter::Fire\(\) \{/);
  assert.equal(context.readLedger.events.at(-1).type, "redundant_reread");
  assert.equal(context.readLedger.stats().redundantRereads, 1);
  assert.equal(manager.observe(again.messages).redundantRereadCount, 1);
  const after = Chat.from(projected); for (const message of again.messages) after.append(message);
  const ledgerFact = manager.readLedgerFact(after);
  assert.match(ledgerFact, /LedgerCharacter\.cpp sha 3c3c3c3c: delivered lines 1-420 \(complete\) in round 1, 1 repeated read\(s\) answered UNCHANGED; visible now: a compacted excerpt only; readHandle=h_[a-f0-9]{24}; verified: none recorded/);
  assert.match(ledgerFact, /Remaining \(no check recorded at the current sha\): LedgerCharacter\.cpp/);
});

test("the same path with a new sha returns its full body; a narrower range keeps its body and says it is unchanged", () => {
  const { manager } = ledgerSetup();
  const changedBody = characterBody.replace("Fire() {", "Fire(int Burst) {");
  const changed = returnRound(manager, exchangeMessages("c2", "read_file", { path: SOURCE }, readResult(SOURCE, shaOf("4d"), changedBody)), 3);
  assert.equal(changed.replaced, 0);
  assert.equal(changed.newDeliveries, 1);
  assert.equal(resultOf(changed).content, changedBody);
  const narrow = returnRound(manager, exchangeMessages("c3", "read_file_range", { path: SOURCE, startLine: 200, endLine: 203 },
    { ok: true, path: SOURCE, sha256: shaOf("4d"), startLine: 200, endLine: 203, totalLines: 420,
      content: changedBody.split("\n").slice(199, 203).join("\n") }), 4);
  const kept = resultOf(narrow);
  assert.match(kept.content, /Fire\(int Burst\)/, "a focused re-read of delivered lines keeps its body");
  assert.equal(kept.unchangedSince, "round 4");
  assert.match(kept.unchangedNotice, /Same sha 4d4d4d4d .*: the file is unchanged/);
  assert.equal(narrow.newDeliveries, 0);
  assert.equal(manager.ledger.latestFor(SOURCE).sourceVersion, shaOf("4d"));
});

test("a server no-new-information answer and an archive re-page are non-progress, not new evidence", async () => {
  const { manager, archiveTool } = ledgerSetup();
  // The receipt names the delivered path even when the request spells it differently.
  returnRound(manager, exchangeMessages("c1b", "read_file", { path: SOURCE },
    readResult(SOURCE, shaOf("3c"), characterBody.slice(0, 200), { startLine: 1, endLine: 4, repeatReceipt: "rr_1" })), 1);
  const server = returnRound(manager, exchangeMessages("c2", "read_file", { path: "C:/Game/Source/Game/LedgerCharacter.cpp", repeatReceipt: "rr_1" },
    { ok: true, duplicate: true, status: "no_new_information", originalOutcome: "success", resultDigest: "abc" }), 2);
  assert.equal(server.redundant, 1);
  assert.equal(resultOf(server).kind, "redundant_source_read");
  assert.match(resultOf(server).notice, /UNCHANGED/);
  const handle = resultOf(server).archiveRef.readHandle;
  const page = await archiveTool.implementation({ action: "read", readHandle: handle, view: "body", startLine: 1, endLine: 40, maxChars: 8192 }, environment);
  assert.equal(page.ok, true);
  const repage = returnRound(manager, exchangeMessages("c3", "evidence_first_read_context", { action: "read", readHandle: handle }, page), 3);
  assert.equal(repage.repages, 1);
  assert.equal(repage.newDeliveries, 0);
  const progress = manager.observe(repage.messages);
  assert.equal(progress.archiveRepageCount, 1);
  assert.equal(progress.newArchiveUnits, 0, "restoring delivered lines is not new archive coverage");
});

test("a version from another record explains whose it is and that the source is unchanged", async () => {
  const { context, manager, archiveTool } = ledgerSetup();
  returnRound(manager, exchangeMessages("t1", "read_file", { path: TYPES }, readResult(TYPES, shaOf("54"), "struct FLedgerTypes {};\n".repeat(60))), 1);
  const [character, types] = [SOURCE, TYPES].map(path => context.readLedger.latestFor(path).archiveRef);
  const mismatch = await archiveTool.implementation({ action: "read", evidenceId: character.evidenceId, version: types.version, view: "body" }, environment);
  assert.equal(mismatch.errorCode, "version_mismatch");
  assert.equal(mismatch.expectedVersion, character.version);
  assert.equal(mismatch.versionBelongsTo.evidenceId, types.evidenceId);
  assert.equal(mismatch.versionBelongsTo.path, TYPES);
  assert.equal(mismatch.sourceUnchanged, true);
  assert.match(mismatch.message, /belongs to ev_[a-f0-9]{8} \(LedgerTypes\.h\), not to ev_[a-f0-9]{8} \(LedgerCharacter\.cpp\)/);
  assert.match(mismatch.message, /does not mean the file was modified/);
  assert.match(mismatch.message, /same sha 3c3c3c3c: the file is unchanged/);
  // An invented handle names the valid handle(s) for its path instead of a bare error.
  const invented = await archiveTool.implementation({ action: "read", readHandle: "h_2f9c4e1a8b6d0c3e5f7a9b2d", path: SOURCE }, environment);
  assert.equal(invented.errorCode, "read_handle_unavailable");
  assert.deepEqual(invented.validHandles.map(item => item.path), [SOURCE]);
  assert.match(invented.message, /mistyped or invented/);
  assert.equal((await archiveTool.implementation({ action: "read", readHandle: invented.validHandles[0].readHandle,
    view: "body", startLine: 1, endLine: 2 }, environment)).ok, true);
});

test("verified checks are tied to the sha they were done against and shown as verified versus remaining", () => {
  const { manager } = ledgerSetup();
  returnRound(manager, exchangeMessages("t1", "read_file", { path: TYPES }, readResult(TYPES, shaOf("54"), "struct FLedgerTypes {};\n".repeat(60))), 1);
  const packet = { claims: [
    { claim: "Fire is server-authoritative", claimType: "behavior", proofLevel: "SourceVerified",
      evidence: [{ kind: "project_source", location: "Source/Game/LedgerCharacter.cpp:200-203", observation: "Fire()" }] },
    { claim: "proposal only", claimType: "wiring", proofLevel: "Proposed",
      evidence: [{ kind: "project_source", location: "Source/Game/LedgerTypes.h:1", observation: "x" }] }] };
  assert.equal(manager.recordChecks(packet, 2), 1);
  const empty = Chat.from([{ role: "user", content: "x" }]);
  const fact = manager.readLedgerFact(empty);
  assert.match(fact, /LedgerCharacter\.cpp sha 3c3c3c3c: .*verified: behavior/);
  assert.match(fact, /LedgerTypes\.h sha 54545454: .*verified: none recorded/);
  assert.match(fact, /Remaining \(no check recorded at the current sha\): LedgerTypes\.h\./);
  // A new version of the file makes its earlier check a re-check.
  returnRound(manager, exchangeMessages("c9", "read_file", { path: SOURCE }, readResult(SOURCE, shaOf("4d"), characterBody + "\n// edit")), 3);
  assert.match(manager.readLedgerFact(empty), /LedgerCharacter\.cpp sha 4d4d4d4d: .*verified: none recorded; re-check at this sha: behavior/);
});

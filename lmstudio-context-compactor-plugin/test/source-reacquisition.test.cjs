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

// Conversation 1792916809646 v1, r55-r58: with maxChars 2048 the envelope left
// about 270 body chars per page, and the last 709 chars of Character.h took
// three rounds.
test("a short remainder of an archived body comes with the page that reaches it", () => {
  const archive = new EvidenceArchive(scope);
  const content = sourceLines(70).join("\n");
  const handle = archive.readHandleFor(archivedRange(archive, content));
  const first = archive.readByHandle(handle, 0, 4096, "body");
  assert.equal(first.archiveHasMore, true);
  const rest = content.length - first.nextOffset;
  assert.ok(rest > 1024, "the fixture leaves more than the tail after the first page");
  const tailStart = content.length - 900;
  const tail = archive.readByHandle(handle, tailStart, 2048, "body");
  assert.equal(tail.ok, true, JSON.stringify(tail).slice(0, 300));
  assert.equal(tail.archiveReachedEnd, true, "900 remaining chars return in one call");
  assert.equal(tail.content, content.slice(tailStart));
  assert.deepEqual(tail.sourceIdentity, { path: "project://Source/Game/Big.cpp" }, "a continuation keeps only the path");
});

test("a path-only archive read starts at the window that begins the file", () => {
  const archive = new EvidenceArchive(scope);
  const filePath = "project://Source/Game/Chunked.cpp", sha = "c".repeat(64), body = "y".repeat(3000);
  const put = (offset, next, id) => archive.put(JSON.stringify({ ok: true, path: filePath, sha256: sha, size: 3000,
    offsetBytes: offset, nextOffsetBytes: next, hasMore: next < 3000, content: body.slice(offset, next),
    range: { unit: "byte", start: offset, endExclusive: next, total: 3000 } }), {
    callKey: `chunk-${id}`, providerRequestId: `chunk-${id}`, toolName: "read_file", sourceIdentity: { path: filePath },
    sourceVersion: sha, originRanges: { unit: "byte", start: offset, endExclusive: next, total: 3000 }, originRangeUnit: "utf8_byte" });
  put(0, 1000, 1); put(1000, 2000, 2); put(2000, 3000, 3);
  const page = archive.readByPath(filePath, 0, 8192, "body");
  assert.equal(page.ok, true, JSON.stringify(page).slice(0, 300));
  assert.deepEqual(page.sourceRange, { unit: "byte", start: 0, endExclusive: 1000, total: 3000 });
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

// Conversation 1792916809646 v1, r10: a 24576-char archive page held about
// all 22978 tokens of the batch and its parallel peer was denied.
test("parallel archive reads reserve their exact local answer, not the maxChars worst case", () => {
  const context = new WorkingContext(scope);
  const tool = context.tool();
  localObservationTools.add(tool);
  const big = context.archive.readHandleFor(archivedRange(context.archive, sourceLines(270).join("\n")));
  const small = context.archive.readHandleFor(archivedRange(context.archive, sourceLines(90).join("\n"), 1,
    "project://Source/Game/Small.cpp"));
  const broker = new BudgetBroker({ workingInputTargetTokens: 40000, workingInputTriggerTokens: 56000, safetyMarginTokens: 2048 });
  const batch = broker.beginBatch({ contextLength: 84224, inputTokens: 50000, outputReserve: 8192 }, true);
  const first = { action: "read", readHandle: big, view: "body", maxChars: 24576 };
  const second = { action: "read", readHandle: small, view: "body", maxChars: 9000 };
  const a = reserveReadResult(batch, "a", tool, first, context.archiveResponseBytes(first));
  const b = reserveReadResult(batch, "b", tool, second, context.archiveResponseBytes(second));
  assert.equal(a.allowed, true);
  assert.equal(a.arguments.maxChars, 24576, "the exact answer fits, so the request is not lowered");
  assert.equal(b.allowed, true, JSON.stringify(b));
  assert.equal(context.archive.stats.reads, 0, "measuring an answer is not a read");
  // Without the exact size the first page would take the shared allowance.
  const blind = broker.beginBatch({ contextLength: 84224, inputTokens: 50000, outputReserve: 8192 }, true);
  reserveReadResult(blind, "a", tool, first);
  assert.equal(reserveReadResult(blind, "b", tool, second).allowed, false);
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
    // A remainder of at most 1024 chars may come with the page that reaches it.
    assert.ok(JSON.stringify(page).length <= (page.lineRangeComplete ? 4096 + 2048 : 4096));
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

test("an identical read while the body is still visible returns an UNCHANGED fact and locator, not a second body", async () => {
  const { context, manager, history, archiveTool, first } = ledgerSetup();
  assert.equal(first.newDeliveries, 1);
  assert.equal(first.replaced, 0);
  manager.captureExposure(history, "body-visible", true);
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
  const projected = manager.project(history, {}, 512).history;
  assert.match(projected.toString(), /archived_tool_result_projection/);
  const after = Chat.from(projected); for (const message of again.messages) after.append(message);
  const ledgerFact = manager.readLedgerFact(after);
  assert.match(ledgerFact, /LedgerCharacter\.cpp sha 3c3c3c3c: delivered lines 1-420 \(complete\) in round 1, 1 repeated read\(s\) answered UNCHANGED; visible now: a compacted excerpt only; readHandle=h_[a-f0-9]{24}$/m);
  assert.doesNotMatch(ledgerFact, /recorded claims: none|No claim recorded/, "absent claims are not listed on every row");
});

test("after a projection removed the body, the identical read returns that body again", () => {
  const { manager, history } = ledgerSetup();
  const projected = manager.project(history, {}, 512).history;
  assert.match(projected.toString(), /archived_tool_result_projection/);
  // The real loop refreshes visibility from every model input it sends.
  manager.captureExposure(projected, "body-projected", true);
  const again = returnRound(manager, exchangeMessages("c2", "read_file", { path: SOURCE }, readResult(SOURCE, shaOf("3c"), characterBody)), 5);
  assert.equal(again.replaced, 0, "the body is not replaced by an UNCHANGED stub");
  const kept = resultOf(again);
  assert.equal(kept.content, characterBody);
  assert.match(kept.unchangedNotice, /Same sha 3c3c3c3c .*: the file is unchanged/);
  assert.equal(again.focusedReads, 1, "counted as a restore, not a new delivery");
  // The body left the input again and is read again: still delivered, but no
  // longer progress, so the no-new-evidence guard can end a restore loop.
  const twice = Chat.from(projected); for (const message of again.messages) twice.append(message);
  manager.captureExposure(manager.project(twice, {}, 512).history, "body-projected-again", true);
  const third = returnRound(manager, exchangeMessages("c3", "read_file", { path: SOURCE }, readResult(SOURCE, shaOf("3c"), characterBody)), 7);
  assert.equal(resultOf(third).content, characterBody);
  assert.equal(third.focusedReads, 0);
  assert.equal(third.repeatRestores, 1);
});

// Conversation 1792916809646 v1: after each projection the model restored the
// same files again in new archive windows or by read_file, 52 rounds in all,
// and every round counted as progress because its offsets were new.
test("restoring spans already restored is a repeat across windows and tools", async () => {
  const { manager, history, archiveTool } = ledgerSetup();
  const projected = manager.project(history, {}, 512).history;
  manager.captureExposure(projected, "body-projected", true);
  const handle = JSON.parse(projected.getMessagesArray().at(-1).getToolCallResults()[0].content).archiveRef.readHandle;
  const page = async (id, startOffset, maxChars, round) => {
    const value = await archiveTool.implementation({ action: "read", readHandle: handle, view: "body", startOffset, maxChars }, environment);
    return returnRound(manager, exchangeMessages(id, "evidence_first_read_context",
      { action: "read", readHandle: handle, view: "body", startOffset, maxChars }, value), round);
  };
  // A first restore in two windows is progress both times.
  const first = await page("p1", 0, 12000, 3);
  const end = JSON.parse(first.messages.at(-1).getToolCallResults()[0].content).returnedRange[1];
  assert.ok(end < characterBody.length, "the first window is partial");
  assert.equal(first.repeatRestores, 0);
  const second = await page("p2", end, 24576, 4);
  assert.equal(second.repeatRestores, 0, "the rest of the body was not restored before");
  // The whole version is now restored: another window, or a source re-read, repeats it.
  const third = await page("p3", 2000, 4096, 5);
  assert.equal(third.repeatRestores, 1);
  assert.equal(third.newDeliveries, 0);
  manager.captureExposure(manager.project(projected, {}, 512).history, "body-projected-again", true);
  const reread = returnRound(manager, exchangeMessages("c9", "read_file", { path: SOURCE }, readResult(SOURCE, shaOf("3c"), characterBody)), 6);
  assert.equal(reread.repeatRestores, 1, "a read_file of a version the archive already restored whole");
  assert.equal(reread.focusedReads, 0);
});

test("the same path with a new sha returns its full body; a narrower range keeps its body and says it is unchanged", () => {
  const { manager } = ledgerSetup();
  const changedBody = characterBody.replace("Fire() {", "Fire(int Burst) {");
  const changed = returnRound(manager, exchangeMessages("c2", "read_file", { path: SOURCE }, readResult(SOURCE, shaOf("4d"), changedBody)), 3);
  assert.equal(changed.replaced, 0);
  assert.equal(changed.newDeliveries, 1);
  assert.equal(resultOf(changed).content, changedBody);
  const narrow = returnRound(manager, exchangeMessages("c3", "read_file_range", { path: SOURCE, startLine: 200, endLine: 203 },
    { ok: true, path: SOURCE, canonicalProject: "C:/Game/Game.uproject", sha256: shaOf("4d"), startLine: 200, endLine: 203, totalLines: 420,
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
  assert.equal(repage.replaced, 1, "already visible archive body is suppressed");
  assert.equal(repage.newDeliveries, 0);
  const progress = manager.observe(repage.messages);
  assert.equal(progress.redundantRereadCount, 1);
  assert.equal(progress.newArchiveUnits, 0, "restoring delivered lines is not new archive coverage");
});

// Conversation 1792929236226, r7-r12: after a projection the model re-read
// files whose every judgment it had already reached.
test("review judgments cannot suppress a hidden file body", () => {
  const { manager, history } = ledgerSetup();
  const projected = manager.project(history, {}, 512).history;
  assert.doesNotMatch(projected.toString(), /CLOSED REVIEW/);
  manager.captureExposure(projected, "body-projected", true);
  const restored = returnRound(manager, exchangeMessages("c2", "read_file", { path: SOURCE }, readResult(SOURCE, shaOf("3c"), characterBody)), 4);
  assert.equal(resultOf(restored).content, characterBody);
  assert.equal(restored.focusedReads, 1);
});

// Conversation 1792916809646 v1, r32 and r34: the server's repeat cache
// answered a read whose body projection had removed, and the model spent the
// next round fetching it from the archive.
test("a server repeat receipt for a hidden body returns the archived body", () => {
  const { manager, history } = ledgerSetup();
  const projected = manager.project(history, {}, 512).history;
  manager.captureExposure(projected, "body-projected", true);
  const server = returnRound(manager, exchangeMessages("c2", "read_file", { path: SOURCE },
    { ok: true, duplicate: true, status: "no_new_information", originalOutcome: "success", resultDigest: "abc",
      path: SOURCE, canonicalProject: "C:/Game/Game.uproject", sha256: shaOf("3c"), startLine: 1, endLine: 420, totalLines: 420, bodyOmitted: true }), 4);
  const restored = resultOf(server);
  assert.equal(restored.content, characterBody, "the body is back without an archive round");
  assert.match(restored.unchangedNotice, /Same sha 3c3c3c3c/);
  assert.equal(server.redundant, 0);
  assert.equal(server.focusedReads, 1, "counted as a restore");
  // The restored body is visible again, so the next receipt is a locator.
  const other = returnRound(manager, exchangeMessages("c3", "read_file", { path: SOURCE },
    { ok: true, duplicate: true, status: "no_new_information", originalOutcome: "success", resultDigest: "abd",
      path: SOURCE, canonicalProject: "C:/Game/Game.uproject", sha256: shaOf("3c"), startLine: 1, endLine: 420, totalLines: 420, bodyOmitted: true }), 5);
  assert.equal(resultOf(other).kind, "redundant_source_read");
  assert.equal(other.redundant, 1);
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

test("a path alone reads the newest archived body of that file, no handle needed", async () => {
  const { archiveTool } = ledgerSetup();
  const read = args => archiveTool.implementation({ action: "read", view: "body", maxChars: 4096, ...args }, environment);
  const span = await read({ path: "LedgerCharacter.cpp", startLine: 200, endLine: 203 });
  assert.equal(span.ok, true, JSON.stringify(span));
  assert.equal(span.sourceIdentity.path, SOURCE, "the response names the file it read");
  assert.match(span.content, /^void ALedgerCharacter::Fire\(\) \{/);
  assert.match(span.readHandle, /^h_[a-f0-9]{24}$/);
  assert.equal((await read({ path: "project://Source/Game/LedgerCharacter.cpp", startLine: 1, endLine: 2 })).ok, true);
  const missing = await read({ path: "Source/Game/Nowhere.cpp" });
  assert.equal(missing.errorCode, "path_unavailable");
  assert.match(missing.message, /read the current source file/);
});

test("accepted cited claims stay bound to their observed sha without claiming verification", () => {
  const { manager } = ledgerSetup();
  returnRound(manager, exchangeMessages("t1", "read_file", { path: TYPES }, readResult(TYPES, shaOf("54"), "struct FLedgerTypes {};\n".repeat(60))), 1);
  const packet = { claims: [
    { claim: "Fire is server-authoritative", claimType: "behavior", proofLevel: "SourceVerified",
      evidence: [{ kind: "project_source", location: "Source/Game/LedgerCharacter.cpp:200-203", observation: "Fire()" }] },
    { claim: "proposal only", claimType: "wiring", proofLevel: "Proposed",
      evidence: [{ kind: "project_source", location: "Source/Game/LedgerTypes.h:1", observation: "x" }] }] };
  assert.equal(manager.recordChecks(packet, 2), 0, "a request alone cannot record a claim");
  assert.equal(manager.recordChecks(packet, 2, true), 1);
  const empty = Chat.from([{ role: "user", content: "x" }]);
  const fact = manager.readLedgerFact(empty);
  assert.match(fact, /LedgerCharacter\.cpp sha 3c3c3c3c: .*recorded claims: behavior/);
  assert.match(fact, /LedgerTypes\.h sha 54545454: delivered [^\n]*$/m);
  assert.doesNotMatch(fact, /LedgerTypes\.h sha 54545454: [^\n]*recorded claims/, "no claim, no claim field");
  assert.match(fact, /not factual verification or file-level audit completion/);
  // A new version of the file makes its earlier check a re-check.
  returnRound(manager, exchangeMessages("c9", "read_file", { path: SOURCE }, readResult(SOURCE, shaOf("4d"), characterBody + "\n// edit")), 3);
  assert.match(manager.readLedgerFact(empty), /LedgerCharacter\.cpp sha 4d4d4d4d: [^\n]*; earlier-version claims need re-check: behavior/);
  assert.equal(manager.recordChecks(packet, 4, true), 0, "an old path-only packet cannot bind to another sha");
});

test("focused reads preserve the complete recovery record and exact repeats are suppressed only while visible", async () => {
  const { context, manager, history, archiveTool } = ledgerSetup();
  const originalRef = manager.ledger.latestFor(SOURCE).archiveRef;
  const value = readResult(SOURCE, shaOf("3c"), characterBody.split("\n").slice(199, 203).join("\n"),
    { startLine: 200, endLine: 203, totalLines: 420 });
  let focusId = 0;
  const focus = () => exchangeMessages(`focused-${++focusId}`, "read_file_range", { path: SOURCE, startLine: 200, endLine: 203 }, value);
  const first = returnRound(manager, focus(), 1);
  assert.equal(first.focusedReads, 1);
  assert.ok(resultOf(first).content);
  assert.deepEqual(manager.ledger.latestFor(SOURCE).archiveRef, originalRef);
  const repeat = returnRound(manager, focus(), 2);
  assert.equal(repeat.replaced, 1);
  const whole = returnRound(manager, exchangeMessages("whole", "read_file", { path: SOURCE }, readResult(SOURCE, shaOf("3c"), characterBody)), 3);
  const page = await archiveTool.implementation({ action: "read", readHandle: resultOf(whole).archiveRef.readHandle,
    view: "body", startLine: 1, endLine: 5, maxChars: 4096 }, environment);
  assert.equal(page.ok, true);
  for (const round of [first, repeat, whole]) for (const message of round.messages) history.append(message);
  manager.captureExposure(history, "focused-body-consumed", true);
  // A later compaction retains only the recovery fact, omitting completed raw exchanges.
  const compacted = Chat.from([{ role: "user", content: "Continue the audit." }]);
  for (const message of whole.messages) compacted.append(message);
  manager.captureExposure(compacted, "focused-body-missing");
  assert.deepEqual([...manager.visibleReads], [], "no raw source remains in this input");
  const restored = returnRound(manager, focus(), 4);
  assert.equal(restored.replaced, 0);
  assert.equal(restored.focusedReads, 1);
  assert.ok(resultOf(restored).content);
});

test("a real A-B-A source transition updates latest observations and preserves raw body", () => {
  const { manager } = ledgerSetup();
  returnRound(manager, exchangeMessages("b", "read_file", { path: SOURCE }, readResult(SOURCE, shaOf("4d"), characterBody + "\n// B")), 1);
  const revert = returnRound(manager, exchangeMessages("a", "read_file", { path: SOURCE }, readResult(SOURCE, shaOf("3c"), characterBody)), 2);
  assert.equal(revert.replaced, 0);
  assert.equal(revert.newDeliveries, 1);
  assert.equal(resultOf(revert).content, characterBody);
  assert.equal(manager.ledger.latestFor(SOURCE).sourceVersion, shaOf("3c"));
});

test("rejected packets, invalid proof levels and unread citation ranges do not become recorded claims", () => {
  const { manager } = ledgerSetup();
  const claim = { claim: "confirmed", claimType: "behavior", proofLevel: "SourceVerified",
    evidence: [{ kind: "project_source", location: "Source/Game/LedgerCharacter.cpp:999" }] };
  assert.equal(manager.recordChecks({ claims: [claim] }, 2, true), 0);
  for (const proofLevel of [undefined, "NotVerified", "Proposed"])
    assert.equal(manager.recordChecks({ claims: [{ ...claim, proofLevel, evidence: [{ kind: "project_source", location: "Source/Game/LedgerCharacter.cpp:200" }] }] }, 2, true), 0);
  assert.deepEqual(manager.ledger.latestFor(SOURCE).checks, []);
});

test("only an unambiguously paired structure-accepted validator result can record a cited assistant claim", () => {
  const { acceptedValidatePackets, validatePackets } = require('../dist/quote-anchoring');
  const { manager } = ledgerSetup();
  const packet = { claims: [{ claim: 'Fire is declared', claimType: 'existence', proofLevel: 'SourceVerified',
    evidence: [{kind:'project_source',location:'Source/Game/LedgerCharacter.cpp:200-203'}] }] };
  const denied = exchangeMessages('validate-denied', 'evidence_first_validate', {packet}, {ok:false,errorCode:'INVALID_PACKET'});
  assert.equal(validatePackets(denied).length, 1);
  assert.equal(acceptedValidatePackets(denied).length, 0);
  const accepted = exchangeMessages('validate-ok', 'evidence_first_validate', {packet},
    {ok:true,validationScope:'packet_structure_and_record_consistency',factualEvidenceVerified:false});
  const packets = acceptedValidatePackets(accepted);
  assert.equal(packets.length, 1);
  assert.equal(manager.recordChecks(packets[0], 1, true), 1);
  const fact = manager.readLedgerFact(Chat.from([{role:'user',content:'Continue'}]));
  assert.match(fact, /recorded claims: existence/);
  assert.match(fact, /not factual verification or file-level audit completion/);
  assert.doesNotMatch(fact, /verified: existence|audit complete/i);
  const ambiguous = [...accepted, accepted[1]];
  assert.equal(acceptedValidatePackets(ambiguous).length, 0);
});

test("disjoint partial records never suppress a range their locator cannot recover", () => {
  const context = new WorkingContext(scope), manager = new EvidenceManager(context, [rangeTool]);
  const part = (id, start, end) => exchangeMessages(id, "read_file_range", { path: SOURCE, startLine: start, endLine: end },
    readResult(SOURCE, shaOf("3c"), characterBody.split("\n").slice(start - 1, end).join("\n"), { startLine: start, endLine: end, totalLines: 420 }));
  returnRound(manager, part("p1", 1, 10), 0);
  returnRound(manager, part("p2", 11, 20), 1);
  const again = returnRound(manager, part("p3", 1, 10), 2);
  assert.equal(again.replaced, 0, "fallback preserves the body when the current locator covers a different range");
  assert.ok(resultOf(again).content);
});

test("a focused range already visible through the archive is not counted as another needed restoration", async () => {
  const { manager, archiveTool } = ledgerSetup();
  const value = readResult(SOURCE, shaOf("3c"), characterBody.split("\n").slice(199, 203).join("\n"),
    { startLine: 200, endLine: 203, totalLines: 420 });
  returnRound(manager, exchangeMessages("focus1", "read_file_range", { path: SOURCE, startLine: 200, endLine: 203 }, value), 1);
  const locator = manager.ledger.latestFor(SOURCE).archiveRef;
  const page = await archiveTool.implementation({ action: "read", evidenceId: locator.evidenceId, version: locator.version,
    view: "body", startLine: 200, endLine: 203, maxChars: 4096 }, environment);
  assert.equal(page.ok, true);
  const restored = exchangeMessages("archive-focus", "evidence_first_read_context", { action: "read", evidenceId: locator.evidenceId, version: locator.version }, page);
  returnRound(manager, restored, 2);
  const history = Chat.from([{ role: "user", content: "Check this function." }]);
  for (const message of restored) history.append(message);
  manager.captureExposure(history, "archive-range-visible", true);
  assert.equal(manager.ledger.classify(require('../src/read-ledger.js').readFacts({ ...value, provider: readTool.pluginIdentifier })).status, 'redundant');
  assert.equal(manager.visibleRead(require('../src/read-ledger.js').readFacts({ ...value, provider: readTool.pluginIdentifier })), true);
  const repeat = returnRound(manager, exchangeMessages("focus2", "read_file_range", { path: SOURCE, startLine: 200, endLine: 203 }, value), 3);
  assert.equal(repeat.replaced, 1);
  assert.equal(repeat.focusedReads, 0);
  assert.equal(resultOf(repeat).content, undefined);
});

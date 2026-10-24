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

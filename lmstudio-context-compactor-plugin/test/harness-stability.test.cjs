"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const notes = require("../src/continuity-model-notes.js");
const { WorkingContext } = require("../src/working-context.js");
const { EvidenceArchive, hash } = require("../src/evidence-archive.js");
const { serialize } = require("../src/working-context.js");
const scope = { conversation: "stability", lineage: "stability", workspace: "stability", repository: "stability" };

function observed(reverse = false, later = false) {
  const history = Chat.from([{ role: "user", content: "Review these declarations" }]);
  const requests = ["a", "b"].map(id => ({ type: "toolCallRequest", toolCallRequest:
    { type: "function", id, name: "read_file", arguments: { path: "C:/Fixture/Source/A.h" } } }));
  const results = ["a", "b"].map(id => ({ type: "toolCallResult", toolCallId: id, content: JSON.stringify({
    ok: true, canonicalProject: "C:/Fixture/Fixture.uproject", path: "C:/Fixture/Source/A.h",
    sha256: id.repeat(64), content: id }) }));
  if (later) {
    for (let i = 0; i < 2; i++) {
      history.append(ChatMessage.from({ role: "assistant", content: [requests[i]] }));
      history.append(ChatMessage.from({ role: "tool", content: [results[i]] }));
    }
  } else {
    history.append(ChatMessage.from({ role: "assistant", content: requests }));
    // One batch may span multiple result messages.
    for (const result of reverse ? results.reverse() : results)
      history.append(ChatMessage.from({ role: "tool", content: [result] }));
  }
  return history.getMessagesArray();
}
const draft = refs => ({ decisions: [{ statement: "Declaration exists", rationale: "Returned declaration", refs }] });

test("parallel observation order is unknown; a separate later batch can supersede", () => {
  for (const reverse of [false, true]) {
    const messages = observed(reverse);
    const note = notes.attachScope(draft(["tool-call:a"]), notes.objectiveFingerprint(messages), messages);
    const result = JSON.parse(notes.renderAssistantNote(note, messages).split("\n").at(-1));
    assert.equal(result.sourceApplicability[0].state, "applicability_unknown");
  }
  const messages = observed(false, true);
  const note = notes.attachScope(draft(["tool-call:a"]), notes.objectiveFingerprint(messages), messages);
  assert.equal(JSON.parse(notes.renderAssistantNote(note, messages).split("\n").at(-1))
    .sourceApplicability[0].state, "source_superseded");
});

test("note snapshot rejection, explicit clear and semantic empty are distinct", () => {
  const messages = observed(), fingerprint = notes.objectiveFingerprint(messages);
  assert.equal(notes.resolveNoteUpdate(draft(["tool-call:missing"]), fingerprint, messages).kind, "rejected");
  const partial = draft(["tool-call:a"]);
  partial.openQuestions = [{ question: "Missing evidence", refs: ["tool-call:missing"] }];
  assert.equal(notes.resolveNoteUpdate(partial, fingerprint, messages).kind, "rejected");
  assert.equal(notes.resolveNoteUpdate({}, fingerprint, messages).kind, "explicit_clear");
  assert.equal(notes.resolveNoteUpdate({}, fingerprint, messages, new Set(), { allowClear: false }).kind, "rejected");
  assert.equal(notes.resolveNoteUpdate(draft(["tool-call:a"]), fingerprint, messages).kind, "accepted_update");
});

test("note archive membership survives display eviction but not missing or changed records", () => {
  const wc = Object.create(WorkingContext.prototype), records = new Map();
  wc.refs = new Map();
  wc.archive = { load: id => records.has(id) ? { ok: true, record: records.get(id) } : { ok: false } };
  for (let i = 0; i < 40; i++) {
    const id = `evidence-${i}`, version = `version-${i}`;
    wc.refs.set(id, version);
    records.set(id, { archivedBodyHash: version, metadata: { providerRequestId: `call-${i}` },
      body: JSON.stringify({ content: `declaration ${i}` }) });
  }
  const ref = `tool-call:archive-${hash(["evidence-0", "version-0"]).slice(0, 32)}`;
  const note = draft([ref]);
  assert.equal(wc.summaryRefs().has(ref), false);
  assert.equal(wc.summaryEvidence().length, 8);
  assert.equal(wc.verifiedNoteRefs(note).has(ref), true);
  records.get("evidence-0").archivedBodyHash = "changed";
  assert.equal(wc.verifiedNoteRefs(note).has(ref), false);
  records.delete("evidence-0");
  assert.equal(wc.verifiedNoteRefs(note).has(ref), false);
});

test("note retention cannot stand in for first-consumer result delivery", () => {
  const wc = new WorkingContext(scope), history = Chat.empty();
  for (const message of observed()) history.append(message);
  wc.captureReturned(history, () => true);
  const [id, version] = [...wc.returnedRefs][0];
  wc.note = draft([`tool-call:archive-${hash([id, version]).slice(0, 32)}`]);
  const missing = Chat.from([{ role: "user", content: "Review these declarations" }]);
  assert.throws(() => wc.captureExposure(missing, "input", false), /CONTEXT_PENDING_EVIDENCE_OMITTED/);
  assert.equal(wc.commit(history, missing, { exact: true, remainingTokens: 5000 }, hash(serialize(history)), "model"), false);
  assert.equal(wc.lastCommitReason, "pending_evidence_omitted");
  assert.equal(wc.returnedRefs.size, 2);
  wc.captureExposure(history, "input", true);
  wc.reconcileRefs(missing, false, true);
  assert.equal(wc.verifiedNoteRefs(wc.note).size, 1);
  wc.close();
});

test("Windows alias and deleted observations share note applicability rules", () => {
  for (const deleted of [false, true]) {
    const messages = observed(false, true);
    const old = JSON.parse(messages.at(-1).getToolCallResults()[0].content);
    const value = { ...old, path: "c:/fixture/source/a.h", ...(deleted ? { observationState: "deleted", sha256: "a".repeat(64) } : {}) };
    messages[messages.length - 1] = ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "b", content: JSON.stringify(value) }] });
    const note = notes.attachScope(draft(["tool-call:a"]), notes.objectiveFingerprint(messages), messages);
    assert.equal(JSON.parse(notes.renderAssistantNote(note, messages).split("\n").at(-1)).sourceApplicability[0].state,
      deleted ? "source_unavailable" : "source_superseded");
  }
});

test("stable catalog excludes new arrivals and cannot skip live records after GC", () => {
  let now = 1000;
  const archive = new EvidenceArchive(scope, { now: () => now++ });
  const put = name => archive.put(JSON.stringify({ content: name }), { callKey: name, sourceIdentity: { path: name } }).record;
  const records = [put("A"), put("B"), put("C")];
  const first = archive.catalogPage({ maxChars: 650 });
  assert.equal(first.ok, true, JSON.stringify(first));
  assert.equal(first.entries.length, 1);
  archive.removeEntry(records[0].evidenceId);
  put("D");
  const names = [];
  let continuation = first.nextContinuation;
  while (continuation) {
    const page = archive.catalogPage({ continuation, maxChars: 650 });
    assert.equal(page.ok, true, JSON.stringify(page));
    names.push(...page.entries.map(e => e.path));
    continuation = page.nextContinuation;
  }
  assert.deepEqual(names, ["B", "C"]);
  archive.setAccess("other");
  assert.equal(archive.catalogPage({ continuation: first.nextContinuation }).errorCode, "catalog_continuation_unavailable");
});

test("body paging preserves exact Unicode content, canonical identity and distinct coverage", () => {
  const archive = new EvidenceArchive(scope);
  const content = '한글 😀 "quoted" \\path\r\n'.repeat(80);
  const record = archive.put(JSON.stringify({ content }), { sourceIdentityDigest: "canonical", callKey: "unicode" }).record;
  const handle = archive.readHandleFor(record);
  let start = 0, result, joined = "";
  do {
    result = archive.readByHandle(handle, start, 1600);
    assert.equal(result.ok, true, JSON.stringify(result));
    assert.ok(JSON.stringify(result).length <= 1600);
    assert.equal(result.sourceIdentityDigest, "canonical");
    assert.equal(result.currentFile, false);
    assert.equal(result.fullRawProvided, false);
    assert.equal(result.representation, "sanitized_body");
    assert.ok(result.returnedRange[1] > start);
    joined += result.content; start = result.nextOffset;
  } while (result.hasMore);
  assert.equal(joined, content);
  const { recoveryCoverageDescriptors, projectionCoverageDescriptors } = require("../dist/evidence-manager");
  const projection = { archiveRef: { evidenceId: record.evidenceId, version: record.archivedBodyHash },
    bodyField: "content", projectedBodyRanges: [result.returnedRange], bodyRangeUnit: "utf16_code_units" };
  assert.equal(recoveryCoverageDescriptors(result)[0].key, projectionCoverageDescriptors(projection)[0].key);
  archive.setAccess("child", [[record.evidenceId, record.archivedBodyHash]]);
  assert.equal(archive.readByHandle(handle).errorCode, "read_handle_unavailable");
  assert.equal(archive.readByHandle(archive.readHandleFor(record)).ok, true);
});

test("public archive selectors preserve legacy catalog and reject mixed identities", async () => {
  const wc = new WorkingContext(scope), record = wc.archive.put(JSON.stringify({ content: "body" }), { callKey: "read" }).record;
  const read = args => wc.tool().implementation(args, { signal: new AbortController().signal });
  const legacy = await read({ action: "catalog" });
  assert.ok(Object.hasOwn(legacy, "nextOffset"));
  assert.equal((await read({ action: "catalog", catalogVersion: 2 })).catalogVersion, 2);
  assert.equal((await read({ readHandle: wc.archive.readHandleFor(record), evidenceId: record.evidenceId, version: record.archivedBodyHash })).errorCode, "selector_conflict");
  assert.equal((await read({ action: "catalog", catalogVersion: 2, startOffset: 0 })).errorCode, "pagination_conflict");
  wc.close();
});


test("body and projection keep lexical limitations and final byte-window scope", () => {
  const wc = new WorkingContext(scope);
  try {
    const payload = { ok: true, content: "void Foo() {}\n".repeat(300), path: "Source/Foo.cpp", sha256: "a".repeat(64),
      representation: "lexical_body_candidate", candidateCount: 2, candidateLines: [10, 30],
      selection: "first_lexical_definition; overload_resolution_unverified", ambiguity: "multiple_lexical_matches",
      signatureVerification: "overloads_inheritance_macros_and_callability_unverified", startLine: 10, endLine: 15, hasMore: false };
    const history = Chat.from([{ role: "user", content: "Review Foo" }]);
    history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest:
      { type: "function", id: "symbol", name: "read_symbol", arguments: { path: payload.path, symbol: "Foo" } } }] }));
    history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "symbol", content: JSON.stringify(payload) }] }));
    const projected = wc.project(history, () => true, { pinnedWorkingSetMaxChars: 0 }, 512);
    const value = JSON.parse(projected.history.getMessagesArray().at(-1).getToolCallResults()[0].content);
    assert.equal(value.sourceFacts.candidateCount, 2);
    assert.equal(value.sourceFacts.signatureVerification, payload.signatureVerification);
    const body = wc.archive.read(value.archiveRef.evidenceId, value.archiveRef.version, 0, 8192, "body");
    assert.equal(body.sourceFacts.representation, "lexical_body_candidate");
    assert.equal(body.sourceFacts.ambiguity, "multiple_lexical_matches");
    assert.equal(body.sourceResultComplete, undefined);
    const tail = { ok: true, content: "last bytes", path: "Source/Foo.cpp", sha256: "b".repeat(64),
      offsetBytes: 990, nextOffsetBytes: 1000, size: 1000, hasMore: false };
    const record = wc.archiveObservation({ type: "function", id: "tail", name: "read_file", arguments: { path: tail.path } },
      { toolCallId: "tail", content: JSON.stringify(tail) }, tail).record;
    const read = wc.archive.read(record.evidenceId, record.archivedBodyHash, 0, 8192, "body");
    assert.deepEqual(read.sourceRange, [990, 1000]);
    assert.equal(read.sourceRangeUnit, "utf8_byte");
    assert.equal(read.sourceFacts.size, 1000);
    assert.equal(read.sourceFacts.hasMore, false);
    assert.equal(read.sourceResultComplete, undefined);
    assert.equal(read.bodyComplete, true);
    assert.equal(read.fullRawProvided, false);
    const { parseToolResult } = require("../src/compaction-tool-memory");
    const compacted = parseToolResult(JSON.stringify(body));
    assert.ok(JSON.stringify(compacted).includes(payload.signatureVerification));
    const byteCheckpoint = parseToolResult(JSON.stringify(read));
    assert.ok(JSON.stringify(byteCheckpoint).includes('"sourceRangeUnit":"utf8_byte"'));
  } finally { wc.close(); }
});


test("long source identifiers do not stall capability redaction", () => {
  const { execFileSync } = require("node:child_process");
  const modulePath = require.resolve("../src/evidence-archive");
  const script = `const {redact} = require(${JSON.stringify(modulePath)});
    const source = 'LONG_SOURCE_IDENTIFIER_'.repeat(10000);
    if (redact(source) !== source) process.exit(1);
    if (redact(source + '.' + 'a'.repeat(64)) !== '[redacted capability]') process.exit(2);`;
  execFileSync(process.execPath, ["-e", script], { timeout: 5000, windowsHide: true });
});

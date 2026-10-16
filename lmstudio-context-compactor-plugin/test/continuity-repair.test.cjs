"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { buildCheckpoint } = require("../src/direct-compaction-core.js");
const { coalesceFileObservations } = require("../src/continuity-file-observations.js");
const { EvidenceManager } = require("../dist/evidence-manager");
const { ToolRoundStagnationDetector, readResultSize } = require("../dist/evidence-telemetry");
const { WorkingContext } = require("../src/working-context.js");
const { updateSemanticContext } = require("../dist/semantic-handoff");
const { readConfig } = require("../dist/execution-config");
const { BudgetBroker, reserveReadResult, readBudgetFeedback } = require("../dist/budget-broker");
const { localObservationTools } = require("../dist/tool-capability-registry");
const { historicalAvailabilityFromMemory, projectInputAvailability } = require("../src/input-availability.js");
const base = { ok: true, kind: "workspace_file_observation", path: "project://Source/A.cpp",
  canonicalProject: "C:/Fixture/Fixture.uproject", sha256: "a".repeat(64) };
const bytes = (start, end, extra = {}) => ({ ...base, offsetBytes: start, nextOffsetBytes: end,
  size: 12, hasMore: end < 12, content: "x".repeat(end - start), ...extra });
const tool = { name: "read_file", pluginIdentifier: "mcp/unreal-agent", parametersJsonSchema: {
  type: "object", properties: { path: { type: "string" }, offsetBytes: { type: "integer" } } } };
function exchange(name, id, start = 0, version = "a".repeat(64)) {
  const history = Chat.empty();
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
    type: "function", id, name: "read_file", arguments: { path: `project://Source/${name}.cpp`, offsetBytes: start } } }] }));
  history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
    content: JSON.stringify(bytes(start, start + 4, { path: `project://Source/${name}.cpp`, sha256: version })) }] }));
  return history;
}

test("byte coverage and continuation survive a real checkpoint and subsequent page merge", () => {
  const history = [{ role: "user", text: "Read the source", toolRequests: [], toolResults: [] },
    { role: "assistant", text: "", toolRequests: [{ type: "function", id: "r", name: "read_file", arguments: { path: base.path } }], toolResults: [] },
    { role: "tool", text: "", toolRequests: [], toolResults: [{ toolCallId: "r", content: JSON.stringify(bytes(0, 4)) }] },
    { role: "assistant", text: "", toolRequests: [{ type: "function", id: "l", name: "list_directory", arguments: {} }], toolResults: [] },
    { role: "tool", text: "", toolRequests: [], toolResults: [{ toolCallId: "l", content: '{"ok":true,"entries":[]}' }] }];
  const cp = buildCheckpoint(history, { recentCompleteTurns: 0, maxCurrentTurnMessages: 0, maxCheckpointChars: 22000 });
  const facts = JSON.parse(JSON.stringify(cp.memory.currentWorkStatus.modifiedOrObservedFiles));
  assert.deepEqual(facts[0].sourceCoverage.entries[0].ranges, [[0, 3]]);
  assert.equal(facts[0].sourceCoverage.entries[0].nextOffsetBytes, 4);
  assert.equal(facts[0].readCoverageState, "partial");
  const restored = historicalAvailabilityFromMemory({ modifiedOrObservedFiles: facts });
  assert.equal(restored[0].rangeUnit, "utf8_byte");
  assert.deepEqual(restored[0].ranges, [[0, 3]]);
  const projected = projectInputAvailability([], restored).entries[0];
  assert.equal(projected.rawPresence, "none");
  assert.deepEqual(projected.rawRangesInThisInput, []);
  const merged = coalesceFileObservations([...facts, bytes(4, 12)])[0];
  assert.equal(merged.readCoverageState, "complete");
  assert.deepEqual(merged.sourceCoverage.entries[0].ranges, [[0, 11]]);
  assert.equal(merged.mutationSnapshotState, "fresh_read_required");
});

test("coverage rejects cross-version, failed, empty, mismatched extent and cross-query completion", () => {
  for (const next of [bytes(4, 12, { sha256: "b".repeat(64) }), bytes(4, 12, { sourceQueryKey: "different" }),
    bytes(4, 12, { provider: "another-provider" }), bytes(4, 12, { size: 20 })]) {
    const merged = coalesceFileObservations([bytes(0, 4), next])[0];
    assert.notEqual(merged.readCoverageState, "complete");
  }
  assert.equal(coalesceFileObservations([bytes(0, 4), bytes(4, 12, { sha256: undefined })])[0].sourceCoverage, undefined);
  assert.equal(coalesceFileObservations([bytes(0, 0)])[0].sourceCoverage, undefined);
  assert.equal(coalesceFileObservations([bytes(4, 12, { ok: false, errorCode: "read_failed" })])[0].sourceCoverage, undefined);
  assert.equal(coalesceFileObservations([bytes(4, 12)])[0].readCoverageState, "partial");
});

test("legacy line fields cannot merge incompatible source coverage into a complete file", () => {
  const first = { ...base, startLine: 1, endLine: 5, totalLines: 10, provider: "p", sourceQueryKey: "q1" };
  const next = { ...first, startLine: 6, endLine: 10 };
  for (const changes of [{ sourceQueryKey: "q2" }, { provider: "other" }, { totalLines: 20 }, { sha256: undefined }]) {
    const merged = coalesceFileObservations([first, { ...next, ...changes }])[0];
    assert.notEqual(merged.readCoverageState, "complete");
    if (changes.sourceQueryKey || changes.provider) assert.equal(merged.observedLineRanges, undefined);
  }
  const failed = coalesceFileObservations([first, { ...next, ok: false, errorCode: "failed" }])[0];
  assert.notEqual(failed.readCoverageState, "complete");
  assert.equal(coalesceFileObservations([first, next])[0].readCoverageState, "complete");
});

test("one observation result feeds research and recovery; cycles differ from progressing pages", () => {
  const manager = new EvidenceManager(null, [tool]), detector = new ToolRoundStagnationDetector();
  let last;
  for (const [index, name] of ["A", "B", "A", "B", "A", "B"].entries()) {
    const messages = exchange(name, `r${index}`).getMessagesArray();
    const progress = manager.observe(messages, `round-${index}`);
    assert.equal(manager.observe(messages, `round-${index}`), progress);
    last = detector.observe(messages, progress);
  }
  assert.equal(last.cycleDetected, true);
  assert.equal(last.cycleLength, 2);
  assert.equal(last.firstWarning, false);
  const before = manager.coverage.size;
  manager.seed(exchange("A", "restored"));
  assert.equal(manager.coverage.size, before);
  for (const start of [4, 8]) {
    const messages = exchange("A", `page-${start}`, start).getMessagesArray();
    assert.equal(detector.observe(messages, manager.observe(messages)).cycleDetected, false);
  }
  const changed = exchange("A", "changed", 0, "b".repeat(64)).getMessagesArray();
  assert.equal(detector.observe(changed, manager.observe(changed)).cycleDetected, false);
});

test("summary retries only a new event after cooldown, preserves note, and caps model calls", async () => {
  const config = readConfig({ getPluginConfig: () => ({ get: key => key === "semanticHandoff" ? true : undefined }) });
  let calls = 0;
  const tokenSource = { async getContextLength() { return 38912; }, async applyPromptTemplate() { return "summary"; },
    async countTokens() { return 100; }, async act(_chat, _tools, options) {
      calls++; options.onPredictionCompleted({ stats: { stopReason: "maxPredictedTokensReached" } });
    } };
  const note = { decisions: [{ statement: "prior" }] };
  let currentMaterial = "a", currentCall = 0;
  const wc = { verifiedNoteRefs: () => new Set(), summaryEvidence: () => [{ ref: `tool-call:${++currentCall}`,
    verifiedExcerpt: "FACT", materialFingerprint: currentMaterial.repeat(64) }],
    lastSummaryInput: "", note, cost: { summaryCalls: 0, summaryMs: 0, summaryPromptTokens: 0, summaryPredictedTokens: 0, unknownUsageCalls: 0 } };
  let state = { activeNote: note, semanticSummaryCooldownUntilRound: -1 };
  for (const [round, material] of [[0, "a"], [1, "b"], [4, "a"], [5, "b"], [10, "c"]]) {
    currentMaterial = material;
    state = await updateSemanticContext({ config, tokenSource, workingContext: wc, ...state,
      compactionCheckpoint: { checkpoint: `volatile call ${round}`, assistantCheckpoint: "" }, compacted: false, projectionApplied: true,
      visibleHistory: Chat.from([{ role: "user", content: "Review" }]), boundedAudit: false, finalizing: false,
      roundIndex: round, executionId: "test", objectiveFingerprint: "objective",
      ctl: { abortSignal: new AbortController().signal, guardAbort() {}, debug() {} } });
  }
  assert.equal(calls, 2); assert.equal(state.activeNote, note); assert.equal(wc.note, note);
  assert.equal(wc.summaryAttempts.length, 2);
});

test("changing unversioned directory results do not prove a read cycle", () => {
  const listing = { ...tool, name: "list_directory" };
  const manager = new EvidenceManager(null, [listing]), detector = new ToolRoundStagnationDetector();
  for (let index = 0; index < 8; index++) {
    const history = Chat.empty(), path = `project://Source/${index % 2 ? "A" : "B"}`;
    history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
      type: "function", id: `list-${index}`, name: listing.name, arguments: { path } } }] }));
    history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: `list-${index}`,
      content: JSON.stringify({ ok: true, path, canonicalProject: base.canonicalProject, entries: [{ name: `new-${index}.cpp` }] }) }] }));
    const messages = history.getMessagesArray();
    assert.equal(detector.observe(messages, manager.observe(messages)).cycleDetected, false);
  }
});

test("archive pages expose payload/body distinction and never return tiny nonterminal fragments", () => {
  const store = new WorkingContext({ conversation: "budget", lineage: "budget", workspace: "w", repository: "r" });
  const saved = store.archive.put(JSON.stringify({ ...base, content: '한글"\\'.repeat(2000) }), { providerRequestId: "r", toolName: "read_file" });
  let start = 0, restored = "", pages = 0;
  do {
    const page = store.archive.read(saved.record.evidenceId, saved.record.archivedBodyHash, start, 4096, "body");
    assert.equal(page.ok, true); assert.ok(JSON.stringify(page).length <= 4096);
    assert.equal(page.returnedContentChars, page.content.length);
    assert.ok(!page.hasMore || page.content.length >= 128);
    restored += page.content; start = page.nextOffset; pages++;
  } while (start !== null && pages < 100);
  assert.equal(restored, '한글"\\'.repeat(2000));
  const small = store.archive.read(saved.record.evidenceId, saved.record.archivedBodyHash, 0, 128, "body");
  assert.equal(small.errorCode, "response_budget_too_small"); store.close();
});

test("summary material ignores repeated call identities while retaining changed ranges", () => {
  const store = new WorkingContext({ conversation: "summary-material", lineage: "summary-material", workspace: "w", repository: "r" });
  for (const id of ["first", "second"]) {
    const value = bytes(0, 4);
    store.archiveObservation({ name: "read_file", arguments: { path: base.path } },
      { toolCallId: id, content: JSON.stringify(value) }, value, {});
  }
  const initial = store.summaryEvidence();
  assert.equal(initial.length, 1);
  store.archiveObservation({ name: "read_file", arguments: { path: base.path, offsetBytes: 4 } },
    { toolCallId: "page", content: JSON.stringify(bytes(4, 8)) }, bytes(4, 8), {});
  const next = store.summaryEvidence();
  assert.equal(next.length, 2);
  assert.notEqual(next[0].materialFingerprint, next[1].materialFingerprint);
  store.close();
});

test("broker reports effective limit and separates body size without weakening reservations", () => {
  const broker = new BudgetBroker({ workingInputTargetTokens: 40000, workingInputTriggerTokens: 56000, safetyMarginTokens: 2048 });
  const archive = { name: "evidence_first_read_context", parametersJsonSchema: { properties: { maxChars: { minimum: 1, maximum: 8192 } } } };
  localObservationTools.add(archive);
  const batch = broker.beginBatch({ contextLength: 84224, inputTokens: 64000, outputReserve: 8192 }, true);
  const first = reserveReadResult(batch, "1", archive, { maxChars: 8192 });
  const peer = reserveReadResult(batch, "2", archive, { maxChars: 8192 });
  assert.deepEqual(first.limit, { field: "maxChars", requested: 8192, effective: 5529, unit: "utf16_code_units" });
  assert.equal(peer.denialReason, "held_reservations_insufficient");
  const sizes = readResultSize(JSON.stringify({ content: "한글" }));
  assert.equal(sizes.bodyChars, 2); assert.equal(sizes.bodyBytes, 6);
  assert.match(readBudgetFeedback([{ readResultLimit: first.limit, readResultSize: sizes }]), /5529/);
  batch.settle("1", "returned"); batch.close(); assert.equal(batch.snapshot().held, 0);
  for (const requested of [128, 12000]) {
    const roomy = broker.beginBatch({ contextLength: 84224, inputTokens: 100, outputReserve: 8192 }, true);
    const clamped = reserveReadResult(roomy, "clamped", archive, { maxChars: requested });
    assert.equal(clamped.limit.requested, requested);
    assert.equal(clamped.limit.effective, requested < 2048 ? 2048 : 8192);
    assert.ok(readBudgetFeedback([{ readResultLimit: clamped.limit }]).includes(String(requested)));
    roomy.settle("clamped", "returned"); roomy.close();
  }
});

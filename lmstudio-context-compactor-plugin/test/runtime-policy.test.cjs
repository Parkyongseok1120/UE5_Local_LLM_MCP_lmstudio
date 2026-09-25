"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { readConfig } = require("../dist/execution-config");
const { ContextManager, createInputAssembler } = require("../dist/context-manager");
const { BudgetBroker } = require("../dist/budget-broker");
const { minimumReadResultTokens, reserveReadResult } = require("../dist/budget-broker");
const { localObservationTools } = require("../dist/tool-capability-registry");
const { RecoveryCoordinator } = require("../dist/recovery-coordinator");
const { WorkingContext } = require("../dist/working-context");
const { recoveryCoverageDescriptors } = require("../dist/evidence-manager");
const { generateSemanticHandoff, updateSemanticContext } = require("../dist/semantic-handoff");

test("partial delivery preserves a guard's reported error instead of replacing it with unknown_error", () => {
  const { evidenceBackedPartialReport } = require("../dist/delivery-controller");
  const result = ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "denied",
    content: JSON.stringify({ error: "Shared read-result budget exhausted." }) }] });
  const report = evidenceBackedPartialReport(Chat.empty(), [result], "research_no_progress");
  assert.equal(report.errorCount, 1);
  assert.match(report.text, /Shared read-result budget exhausted/);
  assert.doesNotMatch(report.text, /unknown_error/);
});

test("a cheap source page cannot hide required archive rehydration starvation", () => {
  const store = new WorkingContext({ conversation: "archive-budget", lineage: "archive-budget", workspace: "w", repository: "r" });
  const archive = store.tool(); localObservationTools.add(archive);
  const source = { name: "read_file", pluginIdentifier: "mcp/unity-tools",
    parametersJsonSchema: { properties: { byteBudget: { minimum: 1024, maximum: 65536 } } } };
  assert.equal(minimumReadResultTokens([source, archive]), 2048, "empty archive is not a requirement");
  const required = minimumReadResultTokens([source, archive], true);
  assert.equal(required, 7168);
  const broker = new BudgetBroker({ workingInputTargetTokens: 18000, workingInputTriggerTokens: 22000,
    safetyMarginTokens: 2048 });
  const observed = { contextLength: 38912, outputReserve: 8192, inputTokens: 16250 };
  assert.equal(broker.watermarks(observed, 16250, true, 2048).nextActionFit, true);
  assert.equal(reserveReadResult(broker.beginBatch(observed), "catalog", archive, { action: "catalog" }).allowed, false);
  assert.equal(broker.watermarks(observed, 16250, true, required).nextActionFit, false);
  const recovery = { contextLength: 38912, outputReserve: 4096, inputTokens: 10000 };
  assert.equal(broker.watermarks(recovery, 10000, true, required).nextActionFit, true);
  assert.equal(reserveReadResult(broker.beginBatch(recovery), "catalog", archive, { action: "catalog" }).allowed, true);
});

test("normal progressing recovery outlives twelve pages; bounded and no-progress limits remain", () => {
  const normal = new RecoveryCoordinator(), bounded = new RecoveryCoordinator();
  for (let page = 0; page < 40; page++) {
    const decision = normal.advance({ progressed: true, errorCount: 0 }, 12, true, false, 1, 1, "", false);
    assert.equal(decision.trigger, null); assert.equal(decision.endRecovery, false);
  }
  for (let page = 0; page < 12; page++) {
    const decision = bounded.advance({ progressed: true, errorCount: 0 }, 12, true, false, 1, 1, "", true);
    assert.equal(decision.trigger, page === 11 ? "research_recovery_complete" : null);
  }
  assert.equal(normal.advance({ progressed: false, errorCount: 0 }, 12, true, false, 1, 1, "", false).trigger,
    "research_recovery_exhausted");
});

test("same source page growth repeats compaction below a fixed derived LOW without inflating floor", async () => {
  const config = readConfig({ getPluginConfig: () => ({ get: key => ({
    contextManagementMode: "hybrid", workingInputTargetTokens: 18000, workingInputTriggerTokens: 22000,
    maxOutputReserve: 4096, safetyMarginTokens: 2048, maxCheckpointChars: 22000,
  })[key] }) });
  const source = { async getContextLength() { return 38912; },
    async applyPromptTemplate(chat) { return chat.toString(); }, async countTokens(text) { return 9000 + Math.ceil(text.length / 4); } };
  const assemble = createInputAssembler({ tokenSource: source, config, roundTools: [], roundScopeInstructions: [],
    getRoundOutputReserve: () => 4096, modelInputId: "page-soak", noteEnabled: false, getNote: () => null,
    historicalAvailabilityLedger: [] });
  const manager = new ContextManager(config, new BudgetBroker(config));
  let history = Chat.from([{ role: "user", content: "Read every page. Preserve the original objective; never write files." }]);
  const compacted = [];
  for (let page = 0; page < 65; page++) {
    const id = `page-${page}`;
    history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
      toolCallRequest: { type: "function", id, name: "read_file", arguments: { path: "data.txt", startLine: page * 20 + 1 } } }] }));
    history.append(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
      content: JSON.stringify({ status: "observed", path: "data.txt", hash: "a".repeat(64), startLine: page * 20 + 1,
        endLine: (page + 1) * 20, totalLines: 2000, hasMore: true, text: "SOURCE_VALUE ".repeat(400) }) }] }));
    const result = await manager.enforceLowWater(history, assemble, { hasReadTools: true });
    assert.equal(result.canRun, true);
    if (result.changed) {
      compacted.push(result.telemetry.finalExactInputTokens);
      assert.equal(result.telemetry.effectiveLowWaterTokens, 16500);
      assert.ok(result.telemetry.mandatoryFloorTokens < 16500);
      assert.ok(result.telemetry.finalExactInputTokens <= 16500);
      assert.match(result.history.toString(), /never write files/);
    }
    history = result.history;
  }
  assert.ok(compacted.length >= 3, JSON.stringify(compacted));
});

test("archive catalog rediscovers consumed evidence with bounded pages and no fresh-source authority", async () => {
  const store = new WorkingContext({ conversation: "catalog", lineage: "catalog", workspace: "w", repository: "r" });
  const ids = [];
  for (let i = 0; i < 12; i++) {
    const saved = store.archive.put(JSON.stringify({ path: "a.txt", text: `FACT_${i}` }), {
      providerRequestId: `r${i}`, sourceIdentity: { path: "a.txt" }, sourceVersion: "fixed-hash",
      toolName: "read_file", originRanges: { start: i + 1, end: i + 1, unit: "line" },
    });
    assert.equal(saved.ok, true); ids.push(saved.record.evidenceId);
  }
  const found = [], tool = store.tool(); let startOffset = 0;
  do {
    const page = await tool.implementation({ action: "catalog", path: "a.txt", startOffset, maxChars: 900 },
      { signal: new AbortController().signal });
    assert.equal(page.ok, true); assert.ok(JSON.stringify(page).length <= 900);
    assert.equal(page.currentFile, false); assert.equal(page.grantsMutation, false);
    assert.ok(recoveryCoverageDescriptors(page).every(d => d.category === "archive"));
    found.push(...page.entries); startOffset = page.nextOffset;
  } while (startOffset !== null);
  assert.deepEqual(new Set(found.map(e => e.evidenceId)), new Set(ids));
  const read = await tool.implementation({ evidenceId: found[0].evidenceId, version: found[0].version, maxChars: 4096 },
    { signal: new AbortController().signal });
  assert.equal(read.ok, true); assert.match(read.content, /FACT_/);
  const other = new WorkingContext({ conversation: "other", lineage: "other", workspace: "w", repository: "r" });
  assert.equal(other.archive.catalog().entries.length, 0);
});

test("summary deadline is classified as timeout when SDK returns userStopped normally", async () => {
  const config = readConfig({ getPluginConfig: () => ({ get: () => undefined }) });
  config.semanticSummarySeconds = 0.01;
  const source = { async getContextLength() { return 38912; }, async applyPromptTemplate() { return "summary"; },
    async countTokens() { return 100; }, async act(_chat, _tools, options) {
      await new Promise(resolve => { options.signal.addEventListener("abort", resolve, { once: true });
        setTimeout(resolve, 30); });
      options.onPredictionCompleted({ stats: { stopReason: "userStopped" } });
    } };
  const result = await generateSemanticHandoff({ tokenSource: source, config, signal: new AbortController().signal,
    checkpoint: { checkpoint: "facts", assistantCheckpoint: "" }, priorNote: null, latestUser: "read",
    refs: new Set(["tool-call:1"]), evidence: [], generation: 1, parentWindow: null });
  assert.equal(result.reason, "timeout"); assert.equal(result.note, null);
});

test("a failed optional handoff is not retried for later compactions in the same execution", async () => {
  let calls = 0;
  const config = readConfig({ getPluginConfig: () => ({ get: key => key === "contextManagementMode" ? "hybrid" : undefined }) });
  const source = { async getContextLength() { return 38912; }, async applyPromptTemplate() { return "summary"; },
    async countTokens() { return 100; }, async act(_chat, _tools, options) {
      calls++; options.onPredictionCompleted({ stats: { stopReason: "maxPredictedTokensReached" } });
    } };
  const workingContext = { summaryEvidence: () => [{ ref: "tool-call:1", verifiedExcerpt: "FACT" }],
    lastSummaryInput: "", cost: { summaryCalls: 0, summaryPromptTokens: 0, summaryPredictedTokens: 0, summaryMs: 0, unknownUsageCalls: 0 } };
  let state = { activeNote: null, semanticSummaryCooldownUntilRound: -1 };
  for (let round = 0; round < 5; round++) state = await updateSemanticContext({ tokenSource: source, config,
    workingContext, compactionCheckpoint: { checkpoint: `new page ${round}`, assistantCheckpoint: "" },
    compacted: true, projectionApplied: false, visibleHistory: Chat.from([{ role: "user", content: "read" }]),
    ...state, boundedAudit: false, finalizing: false, roundIndex: round * 10, executionId: "x", objectiveFingerprint: "x",
    ctl: { abortSignal: new AbortController().signal, guardAbort() {}, debug() {} } });
  assert.equal(calls, 1);
});

test("actual SDK act loop does not cancel a completed channel but still cancels active generation", async () => {
  const fs = require("node:fs"), vm = require("node:vm");
  const sdk = fs.readFileSync(require.resolve("@lmstudio/sdk"), "utf8");
  const begin = sdk.indexOf("async function internalAct("), end = sdk.indexOf("\n}\n", begin) + 2;
  assert.ok(begin >= 0 && end > begin);
  const patched = sdk.slice(begin, end);
  const line = "                finished = true; // context-compactor: completed prediction channel\n";
  assert.ok(patched.includes(line));
  const execute = async (body, cancelActive = false) => {
    class Queue { needsQueueing() { return false; } runInQueue(f) { return Promise.resolve().then(f); } }
    const internalAct = vm.runInNewContext(`(${body})`, { Chat, ChatMessage, AbortController, performance,
      makePromise() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; },
      FIFOQueue: Queue, NoQueueQueue: Queue, SimpleLogger: class {}, callIdGiver: { next: () => 1 },
      SimpleToolCallContext: class { constructor(_logger, signal, callId) { this.signal = signal; this.callId = callId; } },
      accessMaybeMutableInternals: () => ({ _internalGetData: () => [] }),
      safeCallCallback: (_logger, _name, fn, args) => fn?.(...args), ActResult: class {},
      UnimplementedToolError: class extends Error {},
    });
    let closed = false, closedCancels = 0, activeCancels = 0;
    const controller = new AbortController(), stop = new Error("owned boundary");
    const messages = [];
    const predict = handlers => {
      handlers.signal.addEventListener("abort", () => {
        if (closed) closedCancels++; else { activeCancels++; handlers.handleError(stop); }
      });
      queueMicrotask(() => {
        if (cancelActive) { controller.abort(stop); return; }
        handlers.handleToolCallGenerationStart("request");
        handlers.handleToolCallGenerationEnd({ type: "function", id: "r", name: "read", arguments: {} }, "");
        closed = true; handlers.handlePredictionEnd({ stats: { stopReason: "toolCalls" } });
      });
    };
    await assert.rejects(internalAct(Chat.empty(), [{ name: "read", checkParameters() {}, implementation: () => ({ fact: "kept" }) }],
      { signal: controller.signal, onMessage: m => messages.push(m), onRoundEnd: () => controller.abort(stop) },
      "", {}, performance.now(), predict, ({ endPacket }) => endPacket), error => error === stop);
    return { closedCancels, activeCancels, results: messages.flatMap(m => m.getToolCallResults()).length };
  };
  assert.deepEqual(await execute(patched.replace(line, "")), { closedCancels: 1, activeCancels: 0, results: 1 });
  assert.deepEqual(await execute(patched), { closedCancels: 0, activeCancels: 0, results: 1 });
  assert.deepEqual(await execute(patched, true), { closedCancels: 0, activeCancels: 1, results: 0 });
});

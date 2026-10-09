"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { resultSemantics } = require("../src/tool-result-semantics");
const { decodeToolResultRecord, parseToolResult } = require("../src/compaction-tool-memory");
const { traceToolRound } = require("../src/input-availability");

const provider = "mcp/unity-tools";

test("contradictory structured and text envelopes cannot supply a completed fact", () => {
  const envelope = { structuredContent: { status: "applied", operationId: "one" },
    content: [{ type: "text", text: JSON.stringify({ status: "not_applied", operationId: "two" }) }] };
  const decoded = decodeToolResultRecord(envelope);
  assert.equal(decoded.error, "conflicting_envelope");
  assert.equal(resultSemantics(decoded.value, provider).operationStatus, "unknown");
  assert.equal(decodeToolResultRecord({ ...envelope, content: [{ type: "text", text: "Applied operation one." }] })
    .value.operationId, "one", "a plain explanatory text is not a competing structured result");
});

test("later journal observations resolve interrupted effects without fabricating dispatch or SDK return", () => {
  const { resolveInterruptedCalls } = require("../src/interrupted-calls");
  const f = referenceFixture();
  const item = { callKey: "old-call", provider, projectIdentity: "C:/Projects/Unity", toolName: "unity_object_patch",
    operationId: "operation-123", proposedArgumentsFingerprint: "args-one", providerDispatch: "unknown",
    resultObservation: "not_observed", effect: "unknown" };
  f.ingest("unity_operation", { status: "applied", operationId: "operation-123", saved: false, dirty: true,
    origin: { editorSessionId: "old", domainGeneration: 1 }, delivery: { editorSessionId: "new", domainGeneration: 2 } },
    { action: "get", operationId: "operation-123" });
  const observation = f.snapshot().items;
  const resolved = resolveInterruptedCalls([item], observation)[0];
  assert.equal(resolved.resolution.operationStatus, "completed");
  assert.equal(resolved.resolution.saved, false);
  assert.equal(resolved.resolution.dirty, true);
  assert.equal(resolved.providerDispatch, "unknown");
  assert.equal(resolved.resultObservation, "not_observed");
  for (const wrong of [{ ...item, projectIdentity: "C:/Other" }, { ...item, operationId: "different" },
    { ...item, provider: "unknown" }, { ...item, providerDispatch: "not_dispatched" }])
    assert.equal(resolveInterruptedCalls([wrong], observation)[0].resolution, undefined);
  assert.equal(resolveInterruptedCalls([item, { ...item, callKey: "other", proposedArgumentsFingerprint: "args-two" }],
    observation)[0].resolution, undefined, "conflicting requests cannot silently share an operation ID");
});

test("bounded interruption metadata keeps omissions and never downgrades an observed resolution", () => {
  const { renderInterruptedCalls, interruptedCallState } = require("../src/interrupted-calls");
  const items = Array.from({ length: 20 }, (_, i) => ({ callKey: String(i), resultObservation: "not_observed" }));
  const message = { role: "assistant", text: renderInterruptedCalls(items) };
  const first = interruptedCallState([message]);
  assert.doesNotThrow(() => interruptedCallState([], [], null), "older checkpoints may have no retention metadata");
  assert.equal(first.items.length, 16);
  assert.equal(first.retention.omittedItemsAtLeast, 4);
  const restored = interruptedCallState([], first.items, first.retention);
  assert.equal(restored.retention.completeness, "partial");
  const resolved = { ...items.at(-1), resolution: { kind: "later_operation_observation", operationStatus: "completed" } };
  assert.equal(interruptedCallState([message], [resolved]).items.find(item => item.callKey === resolved.callKey).resolution.operationStatus, "completed");
});

test("EvidenceManager settles a prior interruption from a real paired lookup even with no guidance consumer", () => {
  const { Chat, ChatMessage } = require("@lmstudio/sdk");
  const { EvidenceManager } = require("../dist/evidence-manager");
  const { renderInterruptedCalls, retainInterruptedCalls } = require("../src/interrupted-calls");
  const history = Chat.from([{ role: "user", content: "Check the previous operation." }]);
  const item = { callKey: "original", provider, projectIdentity: "C:/Projects/Unity", toolName: "unity_object_patch",
    operationId: "operation-123", proposedArgumentsFingerprint: "args-one", providerDispatch: "unknown", resultObservation: "not_observed" };
  history.append("assistant", renderInterruptedCalls([item]));
  const returned = [ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: {
    type: "function", id: "lookup", name: "unity_operation", arguments: { action: "get", operationId: "operation-123" },
  } }] }), ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "lookup", content: JSON.stringify({
    canonicalProjectRoot: "C:/Projects/Unity", projectIdentity: "opaque-project", operationId: "operation-123", status: "applied", saved: false, dirty: true,
    origin: { editorSessionId: "old", domainGeneration: 1 }, delivery: { editorSessionId: "new", domainGeneration: 2 },
  }) }] })];
  returned.forEach(m => history.append(m));
  const manager = new EvidenceManager(null, [{ name: "unity_operation", pluginIdentifier: provider }]);
  manager.observeRoundReferences(history, returned, { engine: "unity", source: "config", projectIdentity: item.projectIdentity }, "current");
  const facts = retainInterruptedCalls(history.getMessagesArray());
  assert.equal(facts[0].resolution.operationStatus, "completed");
  assert.equal(facts[0].resolution.dirty, true);
  assert.equal(facts[0].resultObservation, "not_observed");
  const count = history.length;
  manager.observeRoundReferences(history, returned, { engine: "unity", source: "config", projectIdentity: item.projectIdentity }, "current");
  assert.equal(history.length, count, "replaying a lookup cannot grow duplicate resolution messages");
});

test("Unity saved effects are completed, while acceptance and missing journals are not", () => {
  assert.equal(resultSemantics({ status: "saved", saved: true }, provider).operationStatus, "completed");
  for (const status of ["accepted", "pending", "running", "unknown", "not_cancelled"])
    assert.notEqual(resultSemantics({ status }, provider).operationStatus, "completed", status);
});

test("response body omission preserves a producer's known publication status", () => {
  const value = { status: "applied", saved: true, changed: true,
    delivery: { status: "truncated", reason: "response_budget_exceeded", bodyOmitted: true } };
  const facts = resultSemantics(value, provider);
  assert.equal(facts.operationStatus, "completed");
  assert.equal(facts.effect, "applied");
  assert.equal(resultSemantics({ ...value, status: "outcome_unknown" }, provider).operationStatus, "outcome_unknown");
});

test("partial publication remains uncertain and failed cancellation is not cancellation", () => {
  const partial = resultSemantics({ status: "partially_applied", errorCode: "tool_error" }, provider);
  assert.equal(partial.operationStatus, "outcome_unknown");
  assert.equal(partial.effect, "partial");
  const rejected = resultSemantics({ status: "not_cancelled", currentStatus: "running" }, provider);
  assert.notEqual(rejected.operationStatus, "canceled");
  assert.notEqual(rejected.operationStatus, "completed");
});

test("an outer MCP error survives decoding and factual retention when provided", () => {
  const payload = { status: "observed", path: "Assets/A.cs" };
  const envelope = { isError: true, structuredContent: payload,
    content: [{ type: "text", text: JSON.stringify(payload) }] };
  assert.equal(decodeToolResultRecord(envelope).value.envelopeIsError, true);
  assert.equal(resultSemantics(decodeToolResultRecord(envelope).value, provider).operationStatus, "failed");
  assert.equal(parseToolResult(envelope).resultSemantics.operationStatus, "failed");
});

test("Editor effects retain dirty/save/origin facts without inventing disk evidence", () => {
  const origin = { editorSessionId: "original", domainGeneration: 1, projectIdentity: "opaque-id" };
  const delivery = { editorSessionId: "current", domainGeneration: 2 };
  const retained = parseToolResult({ status: "applied", operationId: "operation-123", target: { instanceId: 23 },
    canonicalProjectRoot: "C:/Projects/Unity", projectIdentity: "opaque-id",
    changed: false, saved: false, dirty: true, origin, delivery, verification: "observed_after_apply" });
  assert.equal(retained.changed, false);
  assert.equal(retained.saved, false);
  assert.equal(retained.dirty, true);
  assert.deepEqual(retained.origin, origin);
  assert.deepEqual(retained.delivery, delivery);
  assert.equal(retained.sha256, undefined);
  assert.equal(retained.changeEvidence, undefined);
});

test("save scope and partial rollback qualifiers survive factual retention", () => {
  const saved = parseToolResult({ status: "saved", operationId: "operation-save", saved: true,
    scope: "Assets/Scenes/Game.unity", includedExistingDirty: true, dirty: false });
  assert.equal(saved.scope, "Assets/Scenes/Game.unity");
  assert.equal(saved.includedExistingDirty, true);
  const partial = parseToolResult({ status: "partially_applied", errorCode: "callback_failed",
    undoAttempted: true, undoCompleted: true, sideEffects: "unknown",
    journalPersistence: { status: "unavailable", message: "journal write failed" } });
  assert.equal(partial.undoAttempted, true);
  assert.equal(partial.undoCompleted, true);
  assert.equal(partial.sideEffects, "unknown");
  assert.equal(partial.journalPersistence.status, "unavailable");
  assert.equal(partial.resultSemantics.operationStatus, "outcome_unknown");
});

test("Unity test completion retains failures and cancellation request stays pending", () => {
  const completed = parseToolResult({ status: "completed", operationId: "test-operation", testStatus: "Failed",
    passed: 2, failed: 1, skipped: 0, inconclusive: 0, selectedCount: 3, mode: "EditMode",
    domainGenerationAtRequest: 1 });
  assert.equal(completed.resultSemantics.operationStatus, "completed");
  assert.equal(completed.testStatus, "Failed");
  assert.equal(completed.failed, 1);
  assert.equal(completed.passed, 2);
  assert.equal(completed.selectedCount, 3);
  assert.equal(completed.domainGenerationAtRequest, 1);
  for (const status of ["preparing", "cancel_requested"])
    assert.equal(resultSemantics({ status, cancelRequestAccepted: true }, provider).operationStatus, "pending", status);
});

test("tool trace distinguishes unknown and accepted outcomes from completed execution", () => {
  const statuses = ["outcome_unknown", "accepted", "pending", "saved"];
  const messages = statuses.flatMap((status, index) => [
    { role: "assistant", toolRequests: [{ id: String(index), name: "unity_operation", arguments: {} }] },
    { role: "tool", toolResults: [{ toolCallId: String(index), content: JSON.stringify({ status }) }] },
  ]);
  const trace = traceToolRound(messages, { executionId: "exec", modelInputId: "input", roundIndex: 1 });
  assert.deepEqual(trace.results.map(result => result.executionState), ["outcome_unknown", "accepted", "pending", "succeeded"]);
});

function referenceFixture() {
  // Match the repository's existing TypeScript test contract: npm run build
  // precedes Reference tests, while pure result tests use source JavaScript.
  const { Chat, ChatMessage } = require("@lmstudio/sdk");
  const { emptyReferenceState, ingestReferenceObservations, referenceSnapshot } = require("../dist/reference-context");
  const scope = { engine: "unity", source: "config", projectIdentity: "C:/Projects/Unity" };
  const state = emptyReferenceState(), history = Chat.empty();
  let serial = 0;
  function ingestBatch(entries, reverseResults = false) {
    const requests = entries.map(entry => ({ type: "function", id: `contract-${++serial}`,
      name: entry.name, arguments: entry.args || {} }));
    const results = entries.map((entry, index) => ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult",
      toolCallId: requests[index].id, content: JSON.stringify({ canonicalProjectRoot: scope.projectIdentity,
        projectIdentity: "opaque-id", ...entry.value }) }] }));
    const messages = [ChatMessage.from({ role: "assistant", content: requests.map(toolCallRequest => ({
      type: "toolCallRequest", toolCallRequest })) }), ...(reverseResults ? results.reverse() : results)];
    messages.forEach(message => history.append(message));
    const names = [...new Set(entries.map(entry => entry.name))];
    ingestReferenceObservations(state, messages, names.map(name => ({ name, pluginIdentifier: provider })), scope, "contract-execution");
  }
  return {
    ingest(name, value, args = {}) { ingestBatch([{ name, value, args }]); },
    ingestBatch,
    snapshot() { return referenceSnapshot(state, history); },
  };
}

const hasUncertainOperation = snapshot => snapshot.items.some(item => item.kind === "operation"
  && item.data.operationId === "operation-123" && item.data.operationStatus === "outcome_unknown");

test("Reference: a fresh read carrying a repeat receipt is still current raw source", () => {
  const f = referenceFixture();
  f.ingest("read_file", { ok: true, status: "observed", path: "Assets/A.cs", text: "[SerializeField] int field;",
    hash: "a".repeat(64), startLine: 1, endLine: 1, totalLines: 1, truncated: false, repeatReceipt: "opaque" });
  const snapshot = f.snapshot();
  assert.equal(snapshot.items.find(item => item.kind === "file")?.data.sourceBodyInCurrentInput, true);
  assert.ok(snapshot.autoSignals.some(signal => signal.signal === "api_contract_observed"));
});

test("Reference: reconnect does not erase an unresolved Editor operation", () => {
  const f = referenceFixture();
  f.ingest("unity_object_patch", { status: "outcome_unknown", errorCode: "rpc_timeout", operationId: "operation-123" },
    { operationId: "operation-123", target: { instanceId: 23 } });
  f.ingest("unity_status", { status: "observed", connection: "connected", editorSessionId: "new", domainGeneration: 2 });
  assert.ok(hasUncertainOperation(f.snapshot()));
});

test("Reference: known journal recovery resolves an uncertain invocation without claiming a current object", () => {
  const f = referenceFixture();
  f.ingest("unity_object_patch", { status: "outcome_unknown", errorCode: "rpc_timeout", operationId: "operation-123" },
    { operationId: "operation-123", target: { instanceId: 23 } });
  f.ingest("unity_operation", { status: "applied", operationId: "operation-123", saved: false, dirty: true, changed: true,
    target: { instanceId: 23 }, editorSessionId: "original", domainGeneration: 1,
    origin: { editorSessionId: "original", domainGeneration: 1 },
    delivery: { editorSessionId: "new", domainGeneration: 2 } }, { action: "get", operationId: "operation-123" });
  f.ingest("unity_status", { status: "observed", connection: "connected", editorSessionId: "new", domainGeneration: 2 });
  const snapshot = f.snapshot();
  assert.equal(hasUncertainOperation(snapshot), false);
  const recovered = snapshot.items.find(item => item.kind === "operation" && item.data.operationId === "operation-123");
  assert.equal(recovered?.data.operationStatus, "completed");
  assert.equal(recovered?.data.saved, false);
  assert.equal(recovered?.data.dirty, true);
  assert.equal(recovered?.data.originalOrigin?.editorSessionId, "original");
  assert.equal(recovered?.data.lookupSession, "new:2");
  assert.equal(snapshot.signals.includes("operation_outcome_unknown"), false);
});

test("Reference: missing journals and denied cancellation do not resolve unknown effects", () => {
  for (const [action, status, extra] of [["get", "unknown", {}], ["cancel", "not_cancelled", { currentStatus: "running" }]]) {
    const f = referenceFixture();
    f.ingest("unity_object_patch", { status: "outcome_unknown", errorCode: "rpc_timeout", operationId: "operation-123" },
      { operationId: "operation-123", target: { instanceId: 23 } });
    f.ingest("unity_operation", { status, operationId: "operation-123", ...extra }, { action, operationId: "operation-123" });
    assert.ok(hasUncertainOperation(f.snapshot()), `${action}/${status}`);
  }
});

test("Reference: a journal's recorded failed execution resolves a transport timeout", () => {
  const f = referenceFixture();
  f.ingest("unity_object_patch", { status: "outcome_unknown", errorCode: "rpc_timeout", operationId: "operation-123" },
    { operationId: "operation-123", target: { instanceId: 23 } });
  f.ingest("unity_operation", { status: "not_applied", errorCode: "property_not_found", operationId: "operation-123",
    origin: { editorSessionId: "original", domainGeneration: 1 },
    delivery: { editorSessionId: "current", domainGeneration: 2 } }, { action: "get", operationId: "operation-123" });
  assert.equal(hasUncertainOperation(f.snapshot()), false);
  assert.equal(f.snapshot().items.find(item => item.kind === "operation")?.data.operationStatus, "failed");
});

test("Reference: parallel journal and invocation results are independent of arrival order", () => {
  function observe(reverse) {
    const f = referenceFixture();
    f.ingestBatch([
      { name: "unity_object_patch", args: { operationId: "operation-123", target: { instanceId: 23 } },
        value: { status: "outcome_unknown", errorCode: "rpc_timeout", operationId: "operation-123" } },
      { name: "unity_operation", args: { action: "get", operationId: "operation-123" },
        value: { status: "applied", changed: true, saved: false, dirty: true, operationId: "operation-123",
          origin: { editorSessionId: "original", domainGeneration: 1 } } },
    ], reverse);
    return f.snapshot().items.filter(item => item.kind === "operation")
      .map(item => item.data.operationStatus).sort();
  }
  assert.deepEqual(observe(false), observe(true));
});

test("Reference: compile timeout survives reconnect until an operation result resolves it", () => {
  const f = referenceFixture();
  f.ingest("unity_editor", { status: "outcome_unknown", errorCode: "rpc_timeout", operationId: "operation-123" },
    { action: "compile", operationId: "operation-123" });
  f.ingest("unity_status", { status: "observed", connection: "connected", editorSessionId: "new", domainGeneration: 2, compiling: false });
  assert.ok(hasUncertainOperation(f.snapshot()));
  assert.equal(f.snapshot().items.find(item => item.kind === "operation")?.data.sourceAssemblyVerification, "unknown");
});

test("Reference: compile and object requests cannot share one recovered operation identity", () => {
  const f = referenceFixture();
  f.ingest("unity_editor", { status: "outcome_unknown", errorCode: "rpc_timeout", operationId: "operation-123" },
    { action: "compile", operationId: "operation-123" });
  f.ingest("unity_object_patch", { status: "outcome_unknown", errorCode: "rpc_timeout", operationId: "operation-123" },
    { operationId: "operation-123", target: { instanceId: 23 } });
  f.ingest("unity_operation", { status: "applied", operationId: "operation-123", saved: false,
    origin: { editorSessionId: "original", domainGeneration: 1 } }, { action: "get", operationId: "operation-123" });
  assert.ok(hasUncertainOperation(f.snapshot()));
  assert.ok(f.snapshot().items.some(item => item.data.correlation === "conflicting_operation_identity"));
});

test("Reference: retained compile requests do not make later idle status look pending", () => {
  const f = referenceFixture(), session = { editorSessionId: "s", domainGeneration: 1 };
  f.ingest("unity_editor", { ...session, status: "accepted", operationId: "operation-123", compilationIdAtRequest: "old" },
    { action: "compile", operationId: "operation-123" });
  f.ingest("unity_status", { ...session, status: "observed", connection: "connected", compiling: false, compilationId: "new" });
  const snapshot = f.snapshot();
  assert.equal(snapshot.signals.includes("compilation_pending"), false);
  assert.equal(snapshot.autoSignals.some(signal => signal.signal === "compilation_pending"), false);
  const request = snapshot.items.find(item => item.kind === "operation");
  assert.equal(request.data.operationStatus, "accepted");
  assert.equal(request.data.sourceAssemblyVerification, "unknown");
});

test("Reference: a provided MCP envelope failure does not establish an engine version or source body", () => {
  const { Chat, ChatMessage } = require("@lmstudio/sdk");
  const { emptyReferenceState, ingestReferenceObservations, referenceSnapshot } = require("../dist/reference-context");
  const scope = { engine: "unity", source: "config", projectIdentity: "C:/Projects/Unity" };
  const value = { canonicalProjectRoot: scope.projectIdentity, projectIdentity: "opaque-id", status: "observed",
    path: "ProjectSettings/ProjectVersion.txt", text: "m_EditorVersion: 6000.0.44f1", hash: "a".repeat(64),
    startLine: 1, endLine: 1, totalLines: 1 };
  const messages = [ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id: "envelope-read", name: "read_file", arguments: { path: value.path } } }] }),
  ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "envelope-read",
    content: JSON.stringify({ isError: true, structuredContent: value }) }] })];
  const state = emptyReferenceState(), history = Chat.empty();
  messages.forEach(message => history.append(message));
  ingestReferenceObservations(state, messages, [{ name: "read_file", pluginIdentifier: provider }], scope, "execution");
  const snapshot = referenceSnapshot(state, history);
  assert.equal(snapshot.applicability.engineVersion, undefined);
  assert.equal(snapshot.items.some(item => item.data.sourceBodyInCurrentInput === true), false);
});

test("Reference: restored observations require matching returned bytes before claiming body availability", () => {
  const { Chat, ChatMessage } = require("@lmstudio/sdk");
  const { EvidenceManager } = require("../dist/evidence-manager");
  const scope = { engine: "unity", source: "config", projectIdentity: "C:/Projects/Unity" };
  const tools = [{ name: "read_file", pluginIdentifier: provider }], store = { referenceObservations: [] };
  function messages(text) {
    return [ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
      toolCallRequest: { type: "function", id: "stored-read", name: "read_file", arguments: { path: "Assets/A.cs" } } }] }),
    ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "stored-read",
      content: JSON.stringify({ canonicalProjectRoot: scope.projectIdentity, projectIdentity: "opaque-id", status: "observed",
        path: "Assets/A.cs", text, hash: "a".repeat(64), startLine: 1, endLine: 1, totalLines: 1 }) }] })];
  }
  const source = messages("[SerializeField] int field;");
  const first = new EvidenceManager(store, tools);
  first.ingestReferences(source, scope, "old-execution");
  const second = new EvidenceManager(store, tools);
  second.restoreReferences();
  const changed = Chat.empty(); messages("class Different {}").forEach(message => changed.append(message));
  let snapshot = second.referenceSnapshot(changed);
  assert.equal(snapshot.items[0].data.sourceBodyInCurrentInput, false);
  assert.equal(snapshot.items[0].data.observationProvenance, "restored_historical_observation");
  assert.equal(snapshot.autoSignals.some(signal => signal.signal === "api_contract_observed"), false);
  const matching = Chat.empty(); source.forEach(message => matching.append(message));
  snapshot = second.referenceSnapshot(matching);
  assert.equal(snapshot.items[0].data.sourceBodyInCurrentInput, true);
});

test("unknown-provider saved status does not manufacture mutation evidence", () => {
  const facts = resultSemantics({ status: "saved", saved: true, changed: true }, "mcp/unreviewed-provider");
  assert.equal(facts.effect, "unknown");
});

test("an externally changed post-write snapshot does not prove a no-change mutation", () => {
  const facts = resultSemantics({ status: "applied", changed: false, saved: true,
    verification: "changed_after_write" }, provider);
  assert.equal(facts.operationStatus, "completed");
  assert.notEqual(facts.effect, "no_change");
});

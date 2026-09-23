"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { WorkingContext } = require("../src/working-context.js");
const { __test } = require("../dist/prediction-loop.js");
const { runOneToolRound } = require("../dist/round-loop.js");
const scope = { conversation: "astra", lineage: "astra", workspace: "w", repository: "r" };
function result(value) {
  return ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: "read",
    content: JSON.stringify(value) }] });
}
function project(payload) {
  const context = new WorkingContext(scope);
  const history = Chat.from([{ role: "user", content: "Read the body" }]);
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id: "read", name: "read_file", arguments: {} } }] }));
  history.append(result(payload));
  const projected = context.project(history, () => true, {}, 512);
  assert.equal(projected.changed, true);
  return { context, history: projected.history,
    view: JSON.parse(projected.history.getMessagesArray().at(-1).getToolCallResults()[0].content) };
}
test("ASTRA RANGE: decoded body is not envelope coverage; continuation includes boundary", () => {
  const { context, history, view } = project({ ok: true, kind: "git_observation",
    body: '한글😀\\\r\n"'.repeat(1500) });
  assert.equal(view.viewMode, "body_first_bounded");
  const end = view.projectedBodyRanges[0][1];
  const seeded = __test.seedRecoveryProgress(history);
  const read = context.archive.read(view.archiveRef.evidenceId, view.archiveRef.version, end, 4096);
  const progress = __test.observeRecoveryProgress([result(read)], seeded.fingerprints, seeded.coverage);
  assert.equal(progress.coverage[0].uncoveredRanges[0][0], end);
  assert.equal(progress.newArchiveUnits, 1);
  assert.equal(seeded.coverage.size, 2);
});
test("ASTRA RANGE: sanitized envelope fallback seeds [0,N), not [0,N]", () => {
  const { context, history, view } = project({ ok: true, metadata: "x".repeat(6000) });
  assert.deepEqual(view.projectedBodyRanges, []);
  const end = view.projectedSanitizedRanges[0][1];
  const seed = __test.seedRecoveryProgress(history);
  assert.equal(seed.coverage.size, 1);
  const read = context.archive.read(view.archiveRef.evidenceId, view.archiveRef.version, end, 4096);
  const progress = __test.observeRecoveryProgress([result(read)], seed.fingerprints, seed.coverage);
  assert.equal(progress.coverage[0].uncoveredRanges[0][0], end);
});
test("ASTRA RANGE: empty archive EOF is zero coverage", () => {
  const { context, view } = project({ ok: true, body: "x".repeat(6000) });
  const record = context.archive.load(view.archiveRef.evidenceId).record;
  const read = context.archive.read(view.archiveRef.evidenceId, view.archiveRef.version, record.body.length, 4096);
  assert.equal(read.ok, true);
  assert.equal(read.content, "");
  const progress = __test.observeRecoveryProgress([result(read)], new Set(), new Map());
  assert.equal(progress.newArchiveUnits, 0);
  assert.equal(progress.progressed, false);
  assert.equal(progress.currentInputAvailabilityGain, null);
});
test("ASTRA GEN: large metadata cannot replace the generic content body", () => {
  const { view } = project({ ok: true, kind: "file_observation", metadata: "m".repeat(1500),
    content: "BODY-MARKER-" + "z".repeat(6000) });
  assert.equal(view.bodyField, "content");
  assert.ok(view.excerpt.startsWith("BODY-MARKER-"));
  assert.equal(view.fullRawProvided, false);
});
test("ASTRA RANGE: invalid typed endpoints cannot become zero", () => {
  for (const range of [["", false], [null, 10], [-1, 10], [0.5, 10], ["0", "10"]]) {
    const progress = __test.observeRecoveryProgress([result({ ok: true, kind: "git_observation",
      returnedRange: range })], new Set(), new Map());
    assert.equal(progress.progressed, false);
  }
});
test("ASTRA RANGE: unlike source units remain separate", () => {
  const ledger = new Map();
  for (const rangeUnit of ["line", "utf8_byte"]) {
    const progress = __test.observeRecoveryProgress([result({ ok: true, kind: "git_observation",
      head: "sha", path: "file", returnedRange: [0, 10], rangeUnit })], new Set(), ledger);
    assert.equal(progress.newSourceUnits, 1);
  }
  assert.equal(ledger.size, 2);
});
test("ASTRA RANGE: failed projected results never seed successful coverage", () => {
  for (const status of ["error", "cancelled", "denied"]) {
    const { history } = project({ ok: false, status, body: "x".repeat(6000) });
    assert.equal(__test.seedRecoveryProgress(history).coverage.size, 0);
  }
});
test("ASTRA CAP: exact registered provider tools, not name or prefix, confer read exemption", () => {
  for (const name of ["read_attached_document", "evidence_first_read_context", "evidence_first_mutate"]) {
    assert.equal(__test.isObservationOnlyToolCall({ name, pluginIdentifier: "mcp/untrusted" },
      { name, arguments: {} }), false);
  }
  for (const name of ["evidence_first_contract", "evidence_first_validate", "evidence_first_status"]) {
    assert.equal(__test.isObservationOnlyToolCall({ name, pluginIdentifier: "mcp/evidence-first" },
      { name, arguments: {} }), true);
  }
  assert.equal(__test.isObservationOnlyToolCall({ name: "write_file", pluginIdentifier: "mcp/unity-tools" },
    { name: "read_file", arguments: {} }), false);
});
test("ASTRA TX: fragment pause without completed event cannot consume raw input", async () => {
  const pause = new Error("pause");
  const captured = await runOneToolRound({ async act(_h, _t, options) {
    options.onPredictionFragment({ content: "pause", tokensCount: 1 });
    throw options.signal.reason;
  } }, Chat.empty(), [], new AbortController().signal, {
    onToolCallRequestFinalized() {}, async guardToolCall() {}, abortAfterPredictionFragment() { return pause; },
  });
  assert.equal(captured.finishReason, "generation_repetition_paused");
  assert.equal(captured.predictionCompleted, false);
  const context = new WorkingContext(scope);
  const history = Chat.from([{ role: "user", content: "read" }]);
  history.append(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id: "read", name: "read_file", arguments: {} } }] }));
  history.append(result({ ok: true, content: "raw" }));
  context.captureExposure(history, "prediction", captured.predictionCompleted);
  assert.equal(context.consumedRawResults.size, 0);
  assert.equal(context.exposure.get("prediction:read").stage, "included_in_sdk_input");
});

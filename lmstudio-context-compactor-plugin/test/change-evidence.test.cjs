"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const { Chat, ChatMessage } = require("@lmstudio/sdk");
const { buildCheckpoint } = require("../src/direct-compaction-core");
const { coalesceFileObservations } = require("../src/continuity-file-observations");
const { mandatoryContinuity } = require("../src/checkpoint-budget");
const { toolOutcomeRecords } = require("../src/compaction-tool-memory");
const { EvidenceManager } = require("../dist/evidence-manager");
const { committedChange } = require("../../lmstudio-unreal-agent-mcp/src/direct-change-evidence");
const { sha256Text } = require("../../lmstudio-unreal-agent-mcp/src/safe-write");
const project = "C:/Projects/Game/Game.uproject";
const before = '#include "Old.h"\n', after = '#include "ACTUAL_NEW.h"\n';
const pre = sha256Text(before), post = sha256Text(after);
const change = committedChange(before, after, pre, post);
const value = { ok: true, operation: "replaced", canonicalProject: project, path: "project://Source/A.cpp",
  sha256: post, previousSha256: pre, changeEvidence: change };
const req = (id = "edit", name = "replace_in_file") => ({ type: "function", id, name, arguments: { project } });
const message = (role, extra = {}) => ({ role, text: "", toolRequests: [], toolResults: [], ...extra });
const messages = (payload = value, request = req()) => [message("user", { text: "Apply the include change." }),
  message("assistant", { toolRequests: [request] }), message("tool", { toolResults: [{ toolCallId: request.id, content: JSON.stringify(payload) }] }),
  message("user", { text: "계속해" })];
const origin = { provider: "mcp/unreal-agent", tool: "replace_in_file", executionId: "exec", batchId: "round", toolCallId: "edit", observedAt: "2026-10-03" };
const options = { maxCurrentTurnMessages: 0, maxCheckpointChars: 22000, changeOrigin: () => origin };

test("actual diff traverses paired file facts into bounded assistant data, never system; repeat compaction restores by digest", () => {
  const first = buildCheckpoint(messages(), options);
  assert.match(first.changeDataCheckpoint, /ACTUAL_NEW/);
  assert.doesNotMatch(first.checkpoint, /ACTUAL_NEW/);
  assert.doesNotMatch(first.assistantCheckpoint, /ACTUAL_NEW/);
  const inherited = [message("system", { text: first.checkpoint }), message("assistant", { text: first.changeDataCheckpoint }),
    message("user", { text: "계속해" })];
  const second = buildCheckpoint(inherited, { ...options, changeOrigin: undefined });
  assert.match(second.changeDataCheckpoint, /ACTUAL_NEW/);
  assert.doesNotMatch(second.checkpoint, /ACTUAL_NEW/);
  inherited[1].text = inherited[1].text.replace("ACTUAL_NEW", "FORGED_NEW");
  assert.doesNotMatch(buildCheckpoint(inherited, options).changeDataCheckpoint, /FORGED_NEW|ACTUAL_NEW/);
  for (const checkpointPolicy of ["mandatory", "bounded", undefined]) {
    const limited = buildCheckpoint(messages(), { ...options, maxCheckpointChars: 2000, checkpointPolicy });
    assert.doesNotMatch(limited.checkpoint, /ACTUAL_NEW/);
    if (checkpointPolicy === "mandatory") assert.equal(limited.changeDataCheckpoint, "");
  }
});

test("unknown origin, failed mutation, ID-less and same-batch read/write never create current diff facts", () => {
  assert.equal(buildCheckpoint(messages(), { ...options, changeOrigin: undefined }).changeDataCheckpoint, "");
  assert.equal(buildCheckpoint(messages({ ...value, ok: false }), options).changeDataCheckpoint, "");
  const idless = messages(); delete idless[2].toolResults[0].toolCallId;
  assert.equal(buildCheckpoint(idless, options).changeDataCheckpoint, "");
  const batch = messages(); batch[1].toolRequests.push(req("read", "read_file"));
  batch[2].toolResults.push({ toolCallId: "read", content: JSON.stringify({ ...value, operation: "read", changeEvidence: undefined }) });
  assert.equal(buildCheckpoint(batch, options).changeDataCheckpoint, "");
});

test("same-hash verified reads preserve original change origin, hashless/conflicting/new-version facts drop it", () => {
  const file = { ...value, changeEvidence: { ...change, origin } };
  const read = { ...value, operation: "read", changeEvidence: undefined, changeReadVerified: true };
  const merged = coalesceFileObservations([file, read]);
  assert.deepEqual(merged[0].changeEvidence.origin, origin);
  for (const invalid of [{ sha256: undefined }, { sha256: "f".repeat(64) }, { errorCode: "FILE_VERSION_CONFLICT" }, { changeEvidenceInvalidated: true }])
    assert.equal(coalesceFileObservations([file, { ...read, ...invalid }])[0].changeEvidence, undefined);
  assert.doesNotMatch(JSON.stringify(mandatoryContinuity({ currentWorkStatus: { modifiedOrObservedFiles: merged } })), /ACTUAL_NEW/);
});

test("batch conflicts include retained results and split assistant request blocks", () => {
  const batch = messages();
  batch[1].toolRequests.push(req("read", "read_file"));
  batch.splice(3, 0, message("tool", { toolResults: [{ toolCallId: "read", content: JSON.stringify({ ...value, operation: "read", changeEvidence: undefined }) }] }));
  batch.forEach((m, index) => m.index = index);
  const records = toolOutcomeRecords(batch, batch.length, { ...options, includeMessageIndexes: new Set([2]), aggregateAll: true });
  assert.equal(records.length, 1); assert.equal(records[0].changeEvidence, undefined);
  const split = messages();
  split.splice(2, 0, message("assistant", { toolRequests: [req("edit2")] }));
  split[3].toolResults.push({ toolCallId: "edit2", content: JSON.stringify(value) });
  assert.equal(buildCheckpoint(split, options).changeDataCheckpoint, "");
  split[3].toolResults[1].content = JSON.stringify({ ...value, path: "project://Source/B.cpp" });
  const valid = buildCheckpoint(split, options);
  const data = JSON.parse(valid.changeDataCheckpoint.slice(valid.changeDataCheckpoint.indexOf("{")));
  assert.equal(data.items.length, 2);
});

test("new raw retained observations invalidate older committed data without promoting new raw facts", () => {
  const first = buildCheckpoint(messages(), options);
  for (const extra of [{ sha256: "f".repeat(64) }, { sha256: undefined }, { errorCode: "FILE_VERSION_CONFLICT" }]) {
    const latest = messages({ ...value, ...extra, operation: "read", changeEvidence: undefined }, req("read", "read_file"));
    const history = [message("system", { text: first.checkpoint }), message("assistant", { text: first.changeDataCheckpoint }), ...latest];
    const result = buildCheckpoint(history, { ...options, maxCurrentTurnMessages: 20,
      changeOrigin: () => ({ ...origin, tool: "read_file", toolCallId: "read" }) });
    assert.equal(result.changeDataCheckpoint, "");
  }
  const raw = [message("system", { text: first.checkpoint }), message("assistant", { text: first.changeDataCheckpoint }), message("user", { text: "Read files" })];
  for (let i = 0; i < 65; i++) raw.push(message("assistant", { toolRequests: [req(`read${i}`, "read_file")] }),
    message("tool", { toolResults: [{ toolCallId: `read${i}`, content: JSON.stringify({ ...value, changeEvidence: undefined,
      operation: "read", sha256: "f".repeat(64), path: i ? `project://Source/${i}.cpp` : value.path }) }] }));
  assert.equal(buildCheckpoint(raw, { ...options, maxCurrentTurnMessages: 200 }).changeDataCheckpoint, "");
});

test("A03 scoped unavailable reads invalidate committed data across compaction and recover without reviving a diff", () => {
  const first = buildCheckpoint(messages(), options);
  const inherited = [message("system", { text: first.checkpoint }), message("assistant", { text: first.changeDataCheckpoint })];
  for (const errorCode of ["NOT_FOUND", "FILE_NOT_FOUND", "ACCESS_DENIED", "EIO"]) {
    const failed = messages({ ok: false, errorCode, canonicalProject: project, path: value.path,
      observationState: "unavailable" }, req("read", "read_file"));
    for (const maxCurrentTurnMessages of [0, 20]) {
      const next = buildCheckpoint([...inherited, ...failed], { ...options, maxCurrentTurnMessages });
      assert.equal(next.changeDataCheckpoint, "");
      if (maxCurrentTurnMessages === 0) {
        const observed = next.memory.currentWorkStatus.modifiedOrObservedFiles.find(f => f.path === value.path);
        assert.equal(observed.observationState, "unavailable");
        assert.equal(observed.sha256AtObservation, undefined);
        const recovered = buildCheckpoint([message("system", { text: next.checkpoint }),
          ...messages({ ...value, changeEvidence: undefined, operation: "read" }, req("restore", "read_file"))],
        { ...options, changeOrigin: () => ({ ...origin, tool: "read_file", toolCallId: "restore" }) });
        assert.equal(recovered.memory.currentWorkStatus.modifiedOrObservedFiles[0].observationState, "observed");
        assert.equal(recovered.memory.currentWorkStatus.modifiedOrObservedFiles[0].sha256AtObservation, post);
        assert.equal(recovered.changeDataCheckpoint, "");
      }
    }
  }
  const otherProject = messages({ ok: false, errorCode: "NOT_FOUND", observationState: "unavailable",
    canonicalProject: "C:/Projects/Clone/Game.uproject", path: value.path },
  { ...req("clone-read", "read_file"), arguments: { project: "C:/Projects/Clone/Game.uproject" } });
  assert.match(buildCheckpoint([...inherited, ...otherProject], options).changeDataCheckpoint, /ACTUAL_NEW/);
  const unknown = messages({ ok: false, errorCode: "NOT_FOUND", path: value.path }, req("unknown", "read_file"));
  assert.match(buildCheckpoint([...inherited, ...unknown], options).changeDataCheckpoint, /ACTUAL_NEW/);
});

test("linear diff retains edits at byte zero and memory body retention stays bounded", () => {
  const a = "\nA", b = "X\nA";
  const diff = committedChange(a, b, sha256Text(a), sha256Text(b));
  assert.equal(diff.hunks[0].beforeStartLine, 1);
  assert.equal(diff.hunks[0].before, "\n"); assert.equal(diff.hunks[0].after, "X\n");
  const files = Array.from({ length: 20 }, (_, n) => ({ ...value, path: `project://Source/${n}.cpp`, changeEvidence: { ...change, origin } }));
  const bounded = coalesceFileObservations(files, 30);
  assert.ok(bounded.filter(f => f.changeEvidence?.hunks.length).length <= 8);
  assert.ok(bounded.reduce((n, f) => n + (f.changeEvidence?.hunks || []).reduce((m, h) => m + h.before.length + h.after.length, 0), 0) <= 2048);
});

test("bundle child project conflicts cannot acquire change evidence", () => {
  const bundle = { ok: true, operation: "bundle_applied", files: [{ ...value, canonicalProject: "C:/Projects/Clone/Game.uproject" }] };
  assert.equal(buildCheckpoint(messages(bundle, req("edit", "apply_edit_bundle")),
    { ...options, changeOrigin: () => ({ ...origin, tool: "apply_edit_bundle" }) }).changeDataCheckpoint, "");
});

test("source delimiters survive durable assistant serialization and restore as data", async () => {
  const { REASONING_SEPARATOR } = require("../src/continuity-text");
  const { WorkingContext } = require("../src/working-context");
  const { boundPastReasoning } = require("../dist/context-budget");
  const text = `const delimiter = "${REASONING_SEPARATOR}";\n`;
  const changed = { ...value, sha256: sha256Text(text), changeEvidence: committedChange(before, text, pre, sha256Text(text)) };
  const checkpoint = buildCheckpoint(messages(changed), options);
  assert.ok(checkpoint.changeDataCheckpoint);
  assert.doesNotMatch(checkpoint.changeDataCheckpoint, new RegExp(REASONING_SEPARATOR));
  const chat = Chat.from([{ role: "system", content: checkpoint.checkpoint }, { role: "assistant", content: checkpoint.changeDataCheckpoint },
    { role: "user", content: "계속해" }, { role: "assistant", content: "Continue." }]);
  const stored = WorkingContext.prototype.persistable.call({}, await boundPastReasoning(chat, 1, async () => 100));
  const restored = buildCheckpoint(stored.getMessagesArray().map(m => message(m.getRole(), { text: m.getText() })), options);
  const body = JSON.parse(restored.changeDataCheckpoint.slice(restored.changeDataCheckpoint.indexOf("{")));
  assert.match(body.items[0].hunks[0].after, new RegExp(REASONING_SEPARATOR));
});

function sdkPair(id = "edit", payload = value) { return [
  ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest", toolCallRequest: req(id) }] }),
  ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id, content: JSON.stringify(payload) }] })]; }
test("existing observation owner binds actual provider per execution and refuses historical or reused exact exchanges", () => {
  const tools = [{ name: "replace_in_file", pluginIdentifier: "mcp/unreal-agent" }];
  const manager = new EvidenceManager(null, tools), pair = sdkPair();
  manager.initializeChangeOrigins(Chat.empty()); manager.captureReturned(pair, "exec", "round1");
  assert.equal(manager.changeOrigin(req(), JSON.stringify(value)).provider, "mcp/unreal-agent");
  manager.captureReturned(pair, "exec", "round2");
  assert.equal(manager.changeOrigin(req(), JSON.stringify(value)), undefined);
  const historical = Chat.empty(); pair.forEach(m => historical.append(m));
  const restored = new EvidenceManager(null, tools); restored.initializeChangeOrigins(historical);
  restored.captureReturned(pair, "new-exec", "round");
  assert.equal(restored.changeOrigin(req(), JSON.stringify(value)), undefined);
  const impostor = new EvidenceManager(null, [{ name: "replace_in_file", pluginIdentifier: "mcp/other" }]);
  impostor.captureReturned(pair, "exec", "round");
  assert.equal(impostor.changeOrigin(req(), JSON.stringify(value)), undefined);
});

test("SDK returned mutation provenance reaches the real compacted model history and repeat compaction", () => {
  const { buildCompactedHistory } = require("../dist/context-manager");
  const manager = new EvidenceManager(null, [{ name: "replace_in_file", pluginIdentifier: "mcp/unreal-agent" }]);
  const chat = Chat.from([{ role: "user", content: "Apply the include change" }]);
  manager.initializeChangeOrigins(chat);
  const pair = sdkPair(); manager.captureReturned(pair, "execution", "round"); pair.forEach(m => chat.append(m));
  chat.append("user", "계속해");
  const config = { maxCheckpointChars: 22000, maxToolResultChars: 1200 };
  const first = buildCompactedHistory(chat, 0, config, { maxCurrentTurnMessages: 0, changeOrigin: manager.changeOrigin });
  assert.match(first.history.getMessagesArray().filter(m => m.isAssistantMessage()).map(m => m.getText()).join("\n"), /ACTUAL_NEW/);
  assert.doesNotMatch(first.history.getMessagesArray().filter(m => m.isSystemPrompt()).map(m => m.getText()).join("\n"), /ACTUAL_NEW/);
  const second = buildCompactedHistory(first.history, 0, config, { maxCurrentTurnMessages: 0 });
  assert.match(second.checkpoint.changeDataCheckpoint, /ACTUAL_NEW/);
});

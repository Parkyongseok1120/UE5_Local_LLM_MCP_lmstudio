"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  projectInputAvailability,
  rawObservation,
  renderInputAvailabilityMetadata,
  traceToolRound,
} = require("../dist/input-availability.js");

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);

function request(id, name, args) {
  return { role: "assistant", text: "", toolRequests: [{ id, name, arguments: args }], toolResults: [] };
}

function result(id, payload) {
  return { role: "tool", text: "", toolRequests: [], toolResults: [
    { toolCallId: id, content: JSON.stringify(payload) },
  ] };
}

function worktreePayload(overrides = {}) {
  return {
    ok: true,
    kind: "workspace_file_observation",
    projectIdentity: "project-a",
    path: "Assets/Code.cs",
    sha256: HASH_A,
    startLine: 1,
    endLine: 2,
    totalLines: 2,
    content: "line 1\nline 2",
    ...overrides,
  };
}

function historyRange(overrides = {}) {
  return {
    sourceType: "worktree",
    projectIdentity: "project-a",
    path: "Assets/Code.cs",
    versionRef: HASH_A,
    rangeUnit: "line",
    ranges: [[1, 2]],
    total: 2,
    ...overrides,
  };
}

test("T01 no-pressure projection counts only a successful raw result body", () => {
  const projected = projectInputAvailability([
    request("read-1", "read_file", { path: "Assets/Code.cs" }),
    result("read-1", worktreePayload()),
  ], [historyRange()], { modelInputId: "exec-a:prediction-1" });
  assert.equal(projected.entries.length, 1);
  assert.deepEqual(projected.entries[0].rawRangesInThisInput, [[1, 2]]);
  assert.equal(projected.entries[0].rawPresence, "full");
  assert.equal(projected.entries[0].verificationBoundary, "final_sdk_chat");
  assert.equal(projected.entries[0].hostInputVerification, "unknown");
});

test("T02/T03 historical coverage is distinct from current none or partial", () => {
  const historical = historyRange({ ranges: [[1, 1056]], total: 1056 });
  const absent = projectInputAvailability([], [historical], { modelInputId: "exec-a:prediction-2" });
  assert.equal(absent.entries[0].rawPresence, "none");
  const partial = projectInputAvailability([
    request("read-tail", "read_file_range", { path: "Assets/Code.cs", startLine: 956, endLine: 1056 }),
    result("read-tail", worktreePayload({ startLine: 956, endLine: 1056, totalLines: 1056,
      content: Array.from({ length: 101 }, (_, index) => `tail ${index}`).join("\n") })),
  ], [historical], { modelInputId: "exec-a:prediction-3" });
  assert.deepEqual(partial.entries[0].historicallyReturnedRanges, [[1, 1056]]);
  assert.deepEqual(partial.entries[0].rawRangesInThisInput, [[956, 1056]]);
  assert.equal(partial.entries[0].rawPresence, "partial");
});

test("T04 actual returned range wins over the requested range", () => {
  const projected = projectInputAvailability([
    request("short", "read_file_range", { path: "Assets/Code.cs", startLine: 801, endLine: 1056 }),
    result("short", worktreePayload({ startLine: 801, endLine: 850, totalLines: 1056,
      content: Array.from({ length: 50 }, (_, index) => `line ${801 + index}`).join("\n") })),
  ], [], { modelInputId: "exec-a:prediction-4" });
  assert.deepEqual(projected.entries[0].rawRangesInThisInput, [[801, 850]]);
  assert.deepEqual(projected.entries[0].historicallyReturnedRanges, [[801, 850]]);
});

test("T05/T06 versions and project identities never merge", () => {
  const historical = [
    historyRange({ versionRef: HASH_A, ranges: [[1, 10]] }),
    historyRange({ versionRef: HASH_B, ranges: [[11, 20]] }),
    historyRange({ projectIdentity: "project-b", versionRef: HASH_A, ranges: [[21, 30]] }),
  ];
  const projected = projectInputAvailability([], historical, { modelInputId: "exec-a:prediction-5" });
  assert.equal(projected.entries.length, 3);
  assert.deepEqual(projected.entries.map(entry => entry.historicallyReturnedRanges), [
    [[1, 10]], [[11, 20]], [[21, 30]],
  ]);
});

test("T07 partial batch failures are recorded per call and never become raw observations", () => {
  const messages = [
    { role: "assistant", text: "", toolRequests: [
      { id: "ok-1", name: "read_file", arguments: { path: "Assets/A.cs" } },
      { id: "ok-2", name: "read_file", arguments: { path: "Assets/B.cs" } },
      { id: "ok-3", name: "read_file", arguments: { path: "Assets/C.cs" } },
      { id: "failed", name: "read_file", arguments: { path: "Assets/D.cs" } },
    ], toolResults: [] },
    { role: "tool", text: "", toolRequests: [], toolResults: [
      { toolCallId: "ok-1", content: JSON.stringify(worktreePayload({ path: "Assets/A.cs" })) },
      { toolCallId: "failed", content: JSON.stringify({ ok: false, errorCode: "NOT_FOUND" }) },
      { toolCallId: "ok-2", content: JSON.stringify(worktreePayload({ path: "Assets/B.cs" })) },
      { toolCallId: "ok-3", content: JSON.stringify(worktreePayload({ path: "Assets/C.cs" })) },
    ] },
  ];
  const trace = traceToolRound(messages, { executionId: "exec-a", modelInputId: "exec-a:prediction-7", roundIndex: 7 });
  assert.deepEqual(trace.results.map(item => item.executionState), ["succeeded", "failed", "succeeded", "succeeded"]);
  const projected = projectInputAvailability(messages, [], { modelInputId: "exec-a:prediction-7" });
  assert.equal(projected.entries.length, 3);
});

test("T08 call keys remain execution-scoped when provider IDs are reused", () => {
  const messages = [request("provider-reused", "read_file", { path: "Assets/Code.cs" }),
    result("provider-reused", worktreePayload())];
  const first = traceToolRound(messages, { executionId: "exec-a", modelInputId: "exec-a:prediction-8", roundIndex: 0 });
  const second = traceToolRound(messages, { executionId: "exec-b", modelInputId: "exec-b:prediction-8", roundIndex: 0 });
  assert.notEqual(first.requests[0].callKey, second.requests[0].callKey);
  assert.equal(first.results[0].callKey, first.requests[0].callKey);
});

test("duplicate provider IDs in one SDK round remain explicitly ambiguous", () => {
  const messages = [{ role: "assistant", text: "", toolRequests: [
    { id: "duplicate", name: "read_file", arguments: { path: "Assets/A.cs" } },
    { id: "duplicate", name: "read_file", arguments: { path: "Assets/B.cs" } },
  ], toolResults: [] }, result("duplicate", worktreePayload())];
  const trace = traceToolRound(messages, {
    executionId: "exec-a", modelInputId: "exec-a:prediction-duplicate", roundIndex: 0,
  });
  assert.ok(trace.requests.every(item => item.pairingState === "ambiguous_duplicate_provider_id"));
  assert.equal(trace.results[0].pairingState, "ambiguous_duplicate_provider_id");
  assert.match(trace.results[0].callKey, /unmatched/u);
});

test("T11/T13 prior availability prose, hashes, and sentinels are not raw bodies", () => {
  const metadataOnly = {
    role: "system",
    text: `rawPresence=full sha256=${HASH_A} sentinel=line 1`,
    toolRequests: [],
    toolResults: [],
  };
  const summaryOnly = result("summary", { ok: true, path: "Assets/Code.cs", sha256: HASH_A,
    startLine: 1, endLine: 2, totalLines: 2, summary: "line 1 line 2" });
  const projected = projectInputAvailability([metadataOnly, summaryOnly], [historyRange()],
    { modelInputId: "exec-a:prediction-11" });
  assert.equal(projected.entries[0].rawPresence, "none");
  assert.deepEqual(projected.entries[0].rawRangesInThisInput, []);
});

test("T12 capped metadata distinguishes omitted entries from absent raw content", () => {
  const historical = Array.from({ length: 5 }, (_, index) => historyRange({
    path: `Assets/File${index}.cs`, versionRef: String(index).repeat(64).slice(0, 64),
  }));
  const projected = projectInputAvailability([], historical,
    { modelInputId: "exec-a:prediction-12", maxEntries: 2 });
  assert.equal(projected.entries.length, 2);
  assert.equal(projected.omittedEntryCount, 3);
  assert.equal(projected.entryListComplete, false);
  const rendered = renderInputAvailabilityMetadata(projected);
  assert.match(rendered, /omittedEntryCount/u);
  assert.doesNotMatch(rendered, /needsReRead|reviewCompleted|nextAction/u);
});

test("T18 unverified host delivery and malformed raw ranges remain unknown", () => {
  const projected = projectInputAvailability([
    request("bad", "read_file", { path: "Assets/Code.cs" }),
    result("bad", worktreePayload({ startLine: 1, endLine: 3, content: "only one line" })),
  ], [historyRange({ ranges: [[1, 3]], total: 3 })], { modelInputId: "exec-a:prediction-18" });
  assert.equal(projected.entries[0].rawPresence, "unknown");
  assert.equal(projected.entries[0].hostInputVerification, "unknown");
});

test("worktree and commit-blob adapters preserve the same range contract", () => {
  const messages = [
    request("unity", "read_file", { path: "Assets/Unity.cs" }),
    result("unity", worktreePayload({ path: "Assets/Unity.cs", text: "u1\nu2", content: undefined })),
    request("git", "git_read_file", { path: "Source/Game.cpp", revision: "HEAD" }),
    result("git", { status: "observed", kind: "git_observation", action: "read_file",
      workspaceIdentity: "workspace-a", path: "Source/Game.cpp", head: HASH_B, blobOid: "c".repeat(40),
      sha256: HASH_B, startLine: 1, endLine: 2, totalLines: 2, text: "g1\ng2" }),
  ];
  const projected = projectInputAvailability(messages, [], { modelInputId: "exec-a:prediction-20" });
  assert.deepEqual(projected.entries.map(entry => entry.sourceType), ["worktree", "commit_blob"]);
  assert.ok(projected.entries.every(entry => entry.rawPresence === "full"));
});

test("verified and bodyless ranges with the same key never promote the bodyless range", () => {
  const verifiedBody = Array.from({ length: 20 }, (_, index) => `verified ${index + 1}`).join("\n");
  const messages = [
    result("verified", worktreePayload({ startLine: 1, endLine: 20, totalLines: 40,
      returnedLineCount: 20, content: verifiedBody })),
    result("bodyless", worktreePayload({ startLine: 21, endLine: 40, totalLines: 40,
      returnedLineCount: 20, content: undefined })),
  ];
  const projected = projectInputAvailability(messages, [historyRange({ ranges: [[1, 40]], total: 40 })],
    { modelInputId: "exec-a:prediction-verified-only" });
  assert.deepEqual(projected.entries[0].rawRangesInThisInput, [[1, 20]]);
  assert.equal(projected.entries[0].rawPresence, "partial");
  assert.equal(projected.metrics.unverifiableRawObservationCount, 1);
});

test("Unreal UTF-8 byte windows keep byte units without inventing line ranges", () => {
  const content = "한글\r\n🙂";
  const byteLength = Buffer.byteLength(content, "utf8");
  const projected = projectInputAvailability([result("bytes", {
    ok: true,
    activeProject: "C:\\Game\\Game.uproject",
    path: "project://Config/Utf8.ini",
    sha256: HASH_A,
    size: byteLength + 20,
    offsetBytes: 0,
    nextOffsetBytes: byteLength,
    hasMore: true,
    content,
  })], [], { modelInputId: "exec-a:prediction-bytes" });
  assert.equal(projected.entries[0].rangeUnit, "utf8_byte");
  assert.deepEqual(projected.entries[0].rawRangesInThisInput, [[0, byteLength - 1]]);
  assert.equal(projected.entries[0].rawPresence, "full");
});

test("Unreal byte windows reject a decoded body that cannot prove the reported byte span", () => {
  const malformed = rawObservation({
    ok: true,
    activeProject: "C:\\Game\\Game.uproject",
    path: "project://Config/Utf8.ini",
    sha256: HASH_A,
    size: 10,
    offsetBytes: 1,
    nextOffsetBytes: 4,
    content: "�",
  });
  assert.equal(malformed.rangeUnit, "utf8_byte");
  assert.equal(malformed.rawVerified, false);
  assert.equal(malformed.bodyState, "byte_body_mismatch");
});

test("an exact empty Unreal byte response is a verified full empty file", () => {
  const projected = projectInputAvailability([result("empty", {
    ok: true,
    activeProject: "C:\\Game\\Game.uproject",
    path: "project://Config/Empty.ini",
    sha256: HASH_B,
    size: 0,
    offsetBytes: 0,
    nextOffsetBytes: 0,
    hasMore: false,
    content: "",
  })], [], { modelInputId: "exec-a:prediction-empty" });
  assert.equal(projected.entries[0].rangeUnit, "utf8_byte");
  assert.deepEqual(projected.entries[0].rawRangesInThisInput, []);
  assert.equal(projected.entries[0].rawPresence, "full");
  assert.equal(projected.entries[0].bodyVerification, "exact_empty_body");
});

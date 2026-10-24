"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { createDirectRuntime } = require("../src/direct-server.js");
const { calculateLineRangeReplacement, replaceWithCAS } = require("../src/safe-write.js");
const { lineRangeSha256 } = require("../src/line-range-hash.js");

const sha = (text) => crypto.createHash("sha256").update(text, "utf8").digest("hex");

function payloadOf(result) {
  assert.deepStrictEqual(JSON.parse(result.content[0].text), result.structuredContent);
  return result.structuredContent;
}

function fixture(t, files) {
  const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-line-range-"));
  const projectRoot = path.join(workspaceRoot, "RangeFixture");
  const projectFile = path.join(projectRoot, "RangeFixture.uproject");
  fs.mkdirSync(path.join(projectRoot, "Source", "RangeFixture"), { recursive: true });
  fs.writeFileSync(projectFile, JSON.stringify({ FileVersion: 3 }), "utf8");
  for (const [relative, content] of Object.entries(files)) fs.writeFileSync(path.join(projectRoot, relative), content, "utf8");
  t.after(() => fs.rmSync(workspaceRoot, { recursive: true, force: true }));
  const runtime = createDirectRuntime({
    workspaceRoot,
    configPath: path.join(workspaceRoot, "agent-mcp.json"),
    env: { AGENT_STATE_ROOT: path.join(workspaceRoot, "state"), ALLOW_WRITE: "1" },
    getActiveProject: () => projectFile,
  });
  const read = (relative) => fs.readFileSync(path.join(projectRoot, relative), "utf8");
  return { runtime, read };
}

const A = "Source/RangeFixture/A.cpp";
const B = "Source/RangeFixture/B.cpp";

test("calculateLineRangeReplacement replaces, deletes and keeps each line's own terminator", () => {
  const mixed = "one\r\ntwo\r\nthree\nfour\r\n";
  const lines = mixed.split(/\r?\n/);
  const replaced = calculateLineRangeReplacement({ priorContent: mixed, startLine: 2, endLine: 3,
    expectedRangeSha256: lineRangeSha256(lines, 2, 3), newText: "TWO\nTHREE\nEXTRA" });
  assert.equal(replaced.ok, true);
  assert.equal(replaced.updated, "one\r\nTWO\r\nTHREE\r\nEXTRA\nfour\r\n", "the break after the range is the original LF");
  assert.deepEqual(replaced.regions, [{ beforeStartLine: 2, afterStartLine: 2, before: "two\nthree", after: "TWO\nTHREE\nEXTRA" }]);

  const first = calculateLineRangeReplacement({ priorContent: "a\nb\nc", startLine: 1, endLine: 1,
    expectedRangeSha256: sha("a"), newText: "" });
  assert.equal(first.updated, "b\nc");
  const last = calculateLineRangeReplacement({ priorContent: "a\nb\nc", startLine: 2, endLine: 3,
    expectedRangeSha256: sha("b\nc"), newText: "" });
  assert.equal(last.updated, "a");
  const all = calculateLineRangeReplacement({ priorContent: "a\nb", startLine: 1, endLine: 2,
    expectedRangeSha256: sha("a\nb"), newText: "" });
  assert.equal(all.updated, "");
  const upper = calculateLineRangeReplacement({ priorContent: "a\nb\n", startLine: 2, endLine: 2,
    expectedRangeSha256: sha("b").toUpperCase(), newText: "B" });
  assert.equal(upper.updated, "a\nB\n", "a trailing newline survives; hash case does not matter");
});

test("calculateLineRangeReplacement reports the current range on a stale hash or bad bounds", () => {
  const stale = calculateLineRangeReplacement({ priorContent: "a\nb\nc", startLine: 2, endLine: 2,
    expectedRangeSha256: sha("old"), newText: "x" });
  assert.equal(stale.ok, false);
  assert.equal(stale.errorCode, "RANGE_HASH_MISMATCH");
  assert.deepEqual(stale.currentRange, { startLine: 2, endLine: 2, rangeSha256: sha("b"), totalLines: 3, text: "b" });
  const outside = calculateLineRangeReplacement({ priorContent: "a\nb", startLine: 2, endLine: 5,
    expectedRangeSha256: sha("b"), newText: "x" });
  assert.equal(outside.errorCode, "RANGE_OUT_OF_BOUNDS");
  assert.equal(outside.totalLines, 2);
  const large = calculateLineRangeReplacement({ priorContent: "x".repeat(50), startLine: 1, endLine: 1,
    expectedRangeSha256: sha("x".repeat(50)), newText: "y", maxRangeTextChars: 10 });
  assert.equal(large.errorCode, "PATCH_TOO_LARGE");
});

test("replaceWithCAS applies a range only against the read hash", async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "range-cas-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const target = path.join(dir, "A.cpp");
  fs.writeFileSync(target, "a\nb\n", "utf8");
  const range = { startLine: 1, endLine: 1, expectedRangeSha256: sha("a") };
  const stale = await replaceWithCAS({ targetPath: target, priorContent: "a\nb\n", range, newText: "A", readHash: sha("other") });
  assert.equal(stale.errorCode, "READ_HASH_CAS_MISMATCH");
  const applied = await replaceWithCAS({ targetPath: target, priorContent: "a\nb\n", range, newText: "A", readHash: sha("a\nb\n") });
  assert.equal(applied.ok, true);
  assert.equal(fs.readFileSync(target, "utf8"), "A\nb\n");
});

test("read_file_range and read_symbol return the rangeSha256 of the returned lines", async (t) => {
  const f = fixture(t, { [A]: "#include \"A.h\"\r\nvoid Run()\r\n{\r\n  Tick();\r\n}\r\n" });
  const range = payloadOf(await f.runtime.callTool("read_file_range", { path: `project://${A}`, startLine: 2, endLine: 4 }));
  assert.equal(range.rangeSha256, sha("void Run()\n{\n  Tick();"), "CRLF and LF copies prove the same lines");
  const symbol = payloadOf(await f.runtime.callTool("read_symbol", { path: `project://${A}`, symbol: "Run" }));
  const lines = f.read(A).split(/\r?\n/);
  assert.equal(symbol.rangeSha256, lineRangeSha256(lines, symbol.startLine, symbol.endLine));
});

test("replace_in_file line range replaces exactly the proven lines and chains its receipt", async (t) => {
  const f = fixture(t, { [A]: "void A()\r\n{\r\n  Old();\r\n}\r\n" });
  const read = payloadOf(await f.runtime.callTool("read_file_range", { path: `project://${A}`, startLine: 3, endLine: 3 }));
  const edited = payloadOf(await f.runtime.callTool("replace_in_file", { path: `project://${A}`, startLine: 3, endLine: 3,
    expectedRangeSha256: read.rangeSha256, newText: "  New();\n  More();", fileVersionReceipt: read.fileVersionReceipt }));
  assert.equal(edited.ok, true, JSON.stringify(edited));
  assert.equal(f.read(A), "void A()\r\n{\r\n  New();\r\n  More();\r\n}\r\n");
  assert.equal(edited.changeEvidence.status, "changed");
  assert.deepEqual(edited.changeEvidence.hunks, [{ beforeStartLine: 3, afterStartLine: 3, before: "  Old();", after: "  New();\n  More();" }]);

  // The returned receipt proves the new version for the next range edit.
  const deleted = payloadOf(await f.runtime.callTool("replace_in_file", { path: `project://${A}`, startLine: 4, endLine: 4,
    expectedRangeSha256: sha("  More();"), newText: "", fileVersionReceipt: edited.fileVersionReceipt }));
  assert.equal(deleted.ok, true, JSON.stringify(deleted));
  assert.equal(f.read(A), "void A()\r\n{\r\n  New();\r\n}\r\n");
});

test("a stale line range fails with the current range text and leaves the file unchanged", async (t) => {
  const f = fixture(t, { [A]: "line1\nline2\nline3\n" });
  const read = payloadOf(await f.runtime.callTool("read_file_range", { path: `project://${A}`, startLine: 1, endLine: 3 }));
  const result = await f.runtime.callTool("replace_in_file", { path: `project://${A}`, startLine: 2, endLine: 2,
    expectedRangeSha256: sha("recalled after compaction"), newText: "x", expectedHash: read.sha256 });
  const failure = payloadOf(result);
  assert.equal(result.isError, true);
  assert.equal(failure.errorCode, "RANGE_HASH_MISMATCH");
  assert.equal(failure.currentRange.text, "line2");
  assert.equal(failure.currentRange.rangeSha256, sha("line2"));
  assert.equal(failure.currentSha256, read.sha256);
  assert.equal(failure.suggestion.tool, "read_file_range");
  assert.equal(f.read(A), "line1\nline2\nline3\n");

  // The returned range hash is exactly what the retry needs.
  const retried = payloadOf(await f.runtime.callTool("replace_in_file", { path: `project://${A}`, startLine: 2, endLine: 2,
    expectedRangeSha256: failure.currentRange.rangeSha256, newText: "LINE2", expectedHash: read.sha256 }));
  assert.equal(retried.ok, true);
  assert.equal(f.read(A), "line1\nLINE2\nline3\n");
});

test("line ranges keep version proof, bounds and argument-shape rules", async (t) => {
  const f = fixture(t, { [A]: "a\nb\n" });
  const read = payloadOf(await f.runtime.callTool("read_file_range", { path: `project://${A}`, startLine: 1, endLine: 1 }));
  const call = async (args) => payloadOf(await f.runtime.callTool("replace_in_file", { path: `project://${A}`, ...args }));
  const range = { startLine: 1, endLine: 1, expectedRangeSha256: read.rangeSha256, newText: "A" };

  const unproven = await call(range);
  assert.equal(unproven.ok, false, "a version receipt or expectedHash is still required");
  const conflict = await call({ ...range, expectedHash: sha("another version") });
  assert.equal(conflict.errorCode, "FILE_VERSION_CONFLICT");
  const outside = await call({ ...range, endLine: 9, expectedHash: read.sha256 });
  assert.equal(outside.errorCode, "RANGE_OUT_OF_BOUNDS");
  const mixed = await call({ ...range, oldText: "a", expectedHash: read.sha256 });
  assert.equal(mixed.errorCode, "INVALID_ARGUMENT");
  const partial = await call({ startLine: 1, newText: "A", expectedHash: read.sha256 });
  assert.equal(partial.errorCode, "INVALID_ARGUMENT");
  const badHash = await call({ ...range, expectedRangeSha256: "abc", expectedHash: read.sha256 });
  assert.equal(badHash.errorCode, "INVALID_ARGUMENT");
  const huge = await call({ ...range, newText: "x".repeat(2_900), expectedHash: read.sha256 });
  assert.equal(huge.errorCode, "PATCH_TOO_LARGE");
  assert.equal(f.read(A), "a\nb\n");
});

test("apply_edit_bundle accepts line-range patches and stays atomic on a stale range", async (t) => {
  const f = fixture(t, { [A]: "a1\na2\n", [B]: "b1\nb2\n" });
  const readA = payloadOf(await f.runtime.callTool("read_file_range", { path: `project://${A}`, startLine: 2, endLine: 2 }));
  const readB = payloadOf(await f.runtime.callTool("read_file_range", { path: `project://${B}`, startLine: 1, endLine: 1 }));
  const stale = await f.runtime.callTool("apply_edit_bundle", { patches: [
    { path: A, startLine: 2, endLine: 2, expectedRangeSha256: readA.rangeSha256, newText: "A2", fileVersionReceipt: readA.fileVersionReceipt },
    { path: B, startLine: 1, endLine: 1, expectedRangeSha256: sha("stale"), newText: "B1", fileVersionReceipt: readB.fileVersionReceipt },
  ] });
  const failure = payloadOf(stale);
  assert.equal(failure.errorCode, "RANGE_HASH_MISMATCH");
  assert.equal(failure.path, B);
  assert.equal(failure.currentRange.text, "b1");
  assert.equal(f.read(A), "a1\na2\n");
  assert.equal(f.read(B), "b1\nb2\n");

  const applied = payloadOf(await f.runtime.callTool("apply_edit_bundle", { patches: [
    { path: A, startLine: 2, endLine: 2, expectedRangeSha256: readA.rangeSha256, newText: "A2", fileVersionReceipt: readA.fileVersionReceipt },
    { path: B, oldText: "b2", newText: "B2", expectedOccurrences: 1, fileVersionReceipt: readB.fileVersionReceipt },
  ] }));
  assert.equal(applied.ok, true, JSON.stringify(applied));
  assert.equal(f.read(A), "a1\nA2\n");
  assert.equal(f.read(B), "b1\nB2\n");
  assert.deepEqual(applied.files.find((file) => file.path === A).changeEvidence.hunks,
    [{ beforeStartLine: 2, afterStartLine: 2, before: "a2", after: "A2" }]);
});

"use strict";

// Several exact regions of one file are one atomic replace_in_file call against
// one version proof, and the result carries each region's committed lines, so
// the next edit needs neither another round per region nor a read-back.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { createDirectRuntime } = require("../src/direct-server.js");
const { FileSnapshotRegistry } = require("../src/file-snapshot-registry.js");

function fixture(t, options = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "multi-region-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const projectRoot = path.join(root, "Game");
  fs.mkdirSync(path.join(projectRoot, "Source", "Game"), { recursive: true });
  const projectFile = path.join(projectRoot, "Game.uproject");
  fs.writeFileSync(projectFile, JSON.stringify({ FileVersion: 3, EngineAssociation: "" }), "utf8");
  const lines = Array.from({ length: 60 }, (_, i) => `\tint32 Value${i + 1} = ${i + 1};`);
  const file = path.join(projectRoot, "Source", "Game", "Combat.cpp");
  fs.writeFileSync(file, lines.join("\r\n") + "\r\n", "utf8");
  const stateRoot = path.join(root, "state");
  const runtime = createDirectRuntime({
    workspaceRoot: root, configPath: path.join(root, "agent-mcp.json"), stateRoot,
    env: { AGENT_STATE_ROOT: stateRoot, ALLOW_WRITE: "1", ALLOW_COMMANDS: "0", ALLOW_UNREAL_BUILD: "0" },
    getActiveProject: () => projectFile,
    ...options,
  });
  return { runtime, file };
}

const owner = { sessionId: "multi-region" };
const call = async (runtime, tool, args) => (await runtime.callTool(tool, args, owner)).structuredContent;
const receiptFor = async (runtime) => (await call(runtime, "read_file_range",
  { path: "project://Source/Game/Combat.cpp", startLine: 1, endLine: 60 })).fileVersionReceipt;

test("several regions of one file apply atomically in one call with per-region evidence", async (t) => {
  const { runtime, file } = fixture(t);
  const result = await call(runtime, "replace_in_file", {
    path: "project://Source/Game/Combat.cpp", fileVersionReceipt: await receiptFor(runtime),
    edits: [
      { oldText: "\tint32 Value40 = 40;", newText: "\tint32 Value40 = 400;" },
      { oldText: "\tint32 Value5 = 5;", newText: "\tint32 Value5 = 50;\r\n\tint32 Extra = 1;" },
      { oldText: "\tint32 Value60 = 60;", newText: "" },
    ],
  });
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.occurrences, 3);
  const text = fs.readFileSync(file, "utf8");
  assert.match(text, /Value5 = 50;\r\n\tint32 Extra = 1;\r\n\tint32 Value6 = 6;/u);
  assert.match(text, /Value40 = 400;/u);
  assert.doesNotMatch(text, /Value60 = 60/u);
  assert.match(result.fileVersionReceipt, /^fvr1_/u);
  const hunks = result.changeEvidence.hunks;
  assert.deepEqual(hunks.map((h) => [h.beforeStartLine, h.afterStartLine]), [[5, 5], [40, 41], [60, 61]]);
  assert.equal(result.changeEvidence.coverage, "complete_changed_regions");
  // The returned receipt chains straight into the next edit; no re-read.
  const next = await call(runtime, "replace_in_file", { path: "project://Source/Game/Combat.cpp", expectedOccurrences: 1,
    fileVersionReceipt: result.fileVersionReceipt, oldText: "\tint32 Value1 = 1;", newText: "\tint32 Value1 = 10;" });
  assert.equal(next.ok, true, JSON.stringify(next));
});

test("multi-region byte limits reject oversized UTF-8 payloads without writing", async (t) => {
  const { runtime, file } = fixture(t);
  const before = fs.readFileSync(file, "utf8");
  const result = await call(runtime, "replace_in_file", {
    path: "project://Source/Game/Combat.cpp", fileVersionReceipt: await receiptFor(runtime),
    edits: Array.from({ length: 8 }, (_, i) => ({ oldText: `Value${i + 1} = ${i + 1}`, newText: "한".repeat(2500) })),
  });
  assert.equal(result.errorCode, "PATCH_TOO_LARGE");
  assert.equal(fs.readFileSync(file, "utf8"), before);
});

test("bundle rollback restores all regions after a publication failure", async (t) => {
  const { runtime, file } = fixture(t, { transactionHooks: { afterDiskWrite() { throw new Error("injected publication failure"); } } });
  const before = fs.readFileSync(file, "utf8");
  const result = await call(runtime, "apply_edit_bundle", { patches: [{
    path: "project://Source/Game/Combat.cpp", fileVersionReceipt: await receiptFor(runtime), edits: [
      { oldText: "Value1 = 1", newText: "Value1 = 10" }, { oldText: "Value9 = 9", newText: "Value9 = 90" }],
  }] });
  assert.equal(result.ok, false);
  assert.equal(result.errorCode, "BUNDLE_FAILED");
  assert.equal(fs.readFileSync(file, "utf8"), before);
});

test("overlapping, ambiguous or mixed edits change nothing and say which region failed", async (t) => {
  const { runtime, file } = fixture(t);
  const before = fs.readFileSync(file, "utf8");
  const receipt = await receiptFor(runtime);
  const base = { path: "project://Source/Game/Combat.cpp", fileVersionReceipt: receipt };
  const overlap = await call(runtime, "replace_in_file", { ...base, edits: [
    { oldText: "\tint32 Value7 = 7;\r\n\tint32 Value8 = 8;", newText: "x" }, { oldText: "\tint32 Value8 = 8;", newText: "y" }] });
  assert.equal(overlap.errorCode, "EDITS_OVERLAP");
  assert.equal(overlap.editIndex, 1);
  const ambiguous = await call(runtime, "replace_in_file", { ...base, edits: [
    { oldText: "\tint32 Value9 = 9;", newText: "z" }, { oldText: " = 1", newText: " = 2" }] });
  assert.equal(ambiguous.errorCode, "OCCURRENCE_MISMATCH");
  assert.equal(ambiguous.editIndex, 1);
  const mixed = await call(runtime, "replace_in_file", { ...base, expectedOccurrences: 1, oldText: "\tint32 Value9 = 9;", newText: "q",
    edits: [{ oldText: "\tint32 Value3 = 3;", newText: "w" }] });
  assert.equal(mixed.errorCode, "INVALID_ARGUMENT");
  assert.equal(fs.readFileSync(file, "utf8"), before);
});

test("a single larger region still returns its committed lines", async (t) => {
  const { runtime } = fixture(t);
  const newText = Array.from({ length: 30 }, (_, i) => `\tint32 Replaced${i} = ${i}; // ${"y".repeat(20)}`).join("\r\n");
  const result = await call(runtime, "replace_in_file", { path: "project://Source/Game/Combat.cpp", expectedOccurrences: 1,
    fileVersionReceipt: await receiptFor(runtime), oldText: "\tint32 Value20 = 20;", newText });
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.changeEvidence.status, "changed");
  assert.equal(result.changeEvidence.hunks.length, 1);
});

test("self-overlapping oldText is ambiguous and leaves the file unchanged", async (t) => {
  const { runtime, file } = fixture(t);
  fs.writeFileSync(file, "// aaaa\n", "utf8");
  const result = await call(runtime, "replace_in_file", {
    path: "project://Source/Game/Combat.cpp", fileVersionReceipt: await receiptFor(runtime),
    edits: [{ oldText: "aaa", newText: "b" }],
  });
  assert.equal(result.errorCode, "OCCURRENCE_MISMATCH");
  assert.equal(result.editIndex, 0);
  assert.equal(fs.readFileSync(file, "utf8"), "// aaaa\n");
});

test("using a receipt extends its lifetime", () => {
  let now = 1_000;
  const registry = new FileSnapshotRegistry({ ttlMs: 10_000, now: () => now });
  const issued = registry.register({ projectPath: "C:/P/P.uproject", filePath: "C:/P/Source/A.cpp", hash: "a".repeat(64),
    stat: { size: 1, mtimeMs: 1, ino: 1, dev: 1 } });
  const identity = registry.canonicalIdentity("C:/P/P.uproject", "C:/P/Source/A.cpp");
  now = 9_000;
  assert.equal(registry.entryForReceipt(issued.fileVersionReceipt, identity, issued.entry.owner).ok, true);
  now = 15_000;
  assert.equal(registry.entryForReceipt(issued.fileVersionReceipt, identity, issued.entry.owner).ok, true,
    "a receipt in use must outlive its first TTL");
  now = 30_000;
  assert.equal(registry.entryForReceipt(issued.fileVersionReceipt, identity, issued.entry.owner).ok, false);
});

test("bundle supports several regions per file and validates all files before writing", async (t) => {
  const { runtime, file } = fixture(t);
  const second = path.join(path.dirname(file), "Other.cpp");
  fs.writeFileSync(second, "int Other = 1;\n");
  const firstReceipt = await receiptFor(runtime);
  const otherRead = await call(runtime, "read_file_range", { path: "project://Source/Game/Other.cpp", startLine: 1, endLine: 1 });
  const patches = [
    { path: "project://Source/Game/Combat.cpp", fileVersionReceipt: firstReceipt, edits: [
      { oldText: "Value5 = 5", newText: "Value5 = 50" },
      { oldText: "Value40 = 40", newText: "Value40 = 400" }] },
    { path: "project://Source/Game/Other.cpp", fileVersionReceipt: otherRead.fileVersionReceipt,
      edits: [{ oldText: "missing", newText: "Other = 2" }] },
  ];
  const before = fs.readFileSync(file, "utf8");
  const failed = await call(runtime, "apply_edit_bundle", { patches });
  assert.equal(failed.ok, false);
  assert.equal(fs.readFileSync(file, "utf8"), before);
  patches[1].edits[0].oldText = "Other = 1";
  const applied = await call(runtime, "apply_edit_bundle", { patches });
  assert.equal(applied.ok, true, JSON.stringify(applied));
  assert.match(fs.readFileSync(file, "utf8"), /Value40 = 400/);
  assert.equal(fs.readFileSync(second, "utf8"), "int Other = 2;\n");
  assert.equal(applied.files[0].changeEvidence.hunks.length, 2);
});

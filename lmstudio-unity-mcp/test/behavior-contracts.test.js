"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { Files } = require("../../shared-tool-core/files");
const { createRuntime } = require("../src/server");

function fixture(t) {
  const base = fs.realpathSync.native(os.tmpdir());
  const root = fs.realpathSync.native(fs.mkdtempSync(path.join(base, "unity-behavior-contract-")));
  assert.equal(path.dirname(root), base);
  for (const folder of ["Assets", "Packages", "ProjectSettings", "Library/EvidenceFirst"])
    fs.mkdirSync(path.join(root, folder), { recursive: true });
  fs.writeFileSync(path.join(root, "Packages/manifest.json"), "{}");
  fs.writeFileSync(path.join(root, "ProjectSettings/ProjectVersion.txt"), "m_EditorVersion: 6000.0.44f1");
  const runtime = createRuntime({ UNITY_PROJECT_ROOT: root, ALLOW_WRITE: "1" });
  t.after(() => { runtime.close(); fs.rmSync(root, { recursive: true, force: true }); });
  return { root, ...runtime, put: (p, value) => fs.writeFileSync(path.join(root, p), value),
    get: p => fs.readFileSync(path.join(root, p), "utf8") };
}

function afterSnapshot(t, callback) {
  const original = Files.prototype.snapshot;
  let count = 0;
  t.mock.method(Files.prototype, "snapshot", async function (...args) {
    const result = await original.apply(this, args);
    callback(++count, this, args);
    return result;
  });
}

test("request abort after final validation prevents file publication", async t => {
  const f = fixture(t), controller = new AbortController(), file = "Assets/A.cs";
  f.put(file, "class Before {}\n");
  const read = await f.call("read_file", { path: file });
  afterSnapshot(t, count => { if (count === 2) controller.abort("user canceled before publication"); });
  const result = await f.call("patch_file", { path: file, receipt: read.receipt,
    edits: [{ oldText: "Before", newText: "After" }] }, { signal: controller.signal });
  assert.equal(result.status, "not_applied");
  assert.equal(result.errorCode, "request_cancelled");
  assert.equal(f.get(file), "class Before {}\n");
  assert.equal(result.changeEvidence, undefined);
});

test("runtime close before publication reaches an in-flight structured patch", async t => {
  const f = fixture(t), file = "Assets/A.json", before = '{ "n": 1 }\n';
  f.put(file, before);
  const read = await f.call("structured_data_read", { path: file, format: "json" });
  afterSnapshot(t, count => { if (count === 2) f.close("connection_closed"); });
  const result = await f.call("structured_data_patch", { path: file, format: "json", receipt: read.receipt,
    changes: [{ op: "replace", path: ["n"], value: 2 }] });
  assert.equal(result.status, "not_applied");
  assert.equal(result.errorCode, "request_cancelled");
  assert.equal(f.get(file), before);
});

test("abort after publication retains the committed effect and its byte evidence", async t => {
  const f = fixture(t), controller = new AbortController(), file = "Assets/A.cs";
  f.put(file, "class Before {}\n");
  const read = await f.call("read_file", { path: file });
  afterSnapshot(t, count => { if (count === 3) controller.abort("user canceled after publication"); });
  const result = await f.call("patch_file", { path: file, receipt: read.receipt,
    edits: [{ oldText: "Before", newText: "After" }] }, { signal: controller.signal });
  assert.equal(result.status, "applied");
  assert.equal(result.saved, true);
  assert.equal(f.get(file), "class After {}\n");
  assert.equal(result.changeEvidence.status, "changed");
  assert.equal(result.changeEvidence.sha256, result.hash);
  assert.equal(result.changeEvidence.previousSha256, read.hash);
  assert.equal(result.compilation, "unknown");
});

test("byte-identical patch reports no change with no invented changed hunk", async t => {
  const f = fixture(t), file = "Assets/A.cs";
  f.put(file, "class Same {}\n");
  const read = await f.call("read_file", { path: file });
  const result = await f.call("patch_file", { path: file, receipt: read.receipt,
    edits: [{ oldText: "Same", newText: "Same" }] });
  assert.equal(result.status, "applied");
  assert.equal(result.changed, false);
  assert.equal(result.hash, result.previousHash);
  assert.equal(result.changeEvidence.status, "no_change");
  assert.deepEqual(result.changeEvidence.hunks, []);
});

test("an external post-publication edit cannot be attributed as the requested committed diff", async t => {
  const f = fixture(t), file = "Assets/A.cs";
  f.put(file, "class Before {}\n");
  const read = await f.call("read_file", { path: file });
  const original = Files.prototype.snapshot;
  let count = 0;
  t.mock.method(Files.prototype, "snapshot", async function (...args) {
    if (++count === 3) f.put(file, "class External {}\n");
    return original.apply(this, args);
  });
  const result = await f.call("patch_file", { path: file, receipt: read.receipt,
    edits: [{ oldText: "Before", newText: "After" }] });
  assert.equal(result.status, "applied");
  assert.equal(result.verification, "changed_after_write");
  assert.equal(result.changeEvidence, undefined);
  assert.equal(f.get(file), "class External {}\n");
});

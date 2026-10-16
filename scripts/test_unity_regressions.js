#!/usr/bin/env node
"use strict";
const fs = require("node:fs"), path = require("node:path"), assert = require("node:assert/strict"), crypto = require("node:crypto");
const { createRuntime } = require("../lmstudio-unity-mcp/src/server");
const root = process.argv[2];
if (!root || !path.basename(root).startsWith("unity-bridge-test-") || !fs.existsSync(path.join(root, "debug-fixture-ready.json"))) throw Error("Isolated optional-package fixture required");
const runtime = createRuntime({ UNITY_PROJECT_ROOT: root, ALLOW_WRITE: "1", ALLOW_COMMANDS: "1" }), results = [];
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function call(n, a = {}) { const r = await runtime.call(n, a); if (r.errorCode) throw Error(`${n}: ${JSON.stringify(r)}`); return r; }
async function editor(action) { await call("unity_editor", { action, operationId: crypto.randomUUID() }); for (let i = 0; i < 120; i++) { const s = await call("unity_status"); if (action === "pause" ? s.paused : s.playing === (action === "play")) return; await sleep(250); } throw Error("Editor transition timed out"); }
async function main() {
  if ((await call("unity_status")).playing) await editor("stop");
  const target = (await call("unity_find", { kind: "assets", query: "t:SampleData" })).items[0].target;
  const compact = await call("unity_snapshot", { action: "capture", targets: [{ target, propertyPaths: Array.from({ length: 32 }, (_, i) => "missingField" + i) }], byteBudget: 1024 });
  assert(compact.snapshotId); assert.notEqual(compact.status, "not_applied"); assert(Buffer.byteLength(JSON.stringify(compact)) <= 1024);
  const scope = await call("unity_snapshot", { action: "read", snapshotId: compact.snapshotId, section: "scope", limit: 1 }); assert(scope.items.length);
  await call("unity_snapshot", { action: "release", snapshotId: compact.snapshotId });
  results.push("1024-byte capture response retains its stored snapshot ID and supports scoped metadata read");
  const targets = [{ target, propertyPaths: ["number"] }];
  const a = await call("unity_snapshot", { action: "capture", targets }); await editor("play"); const b = await call("unity_snapshot", { action: "capture", targets });
  const diff = await call("unity_snapshot", { action: "diff", a: a.snapshotId, b: b.snapshotId }); assert(!diff.items.some(d => d.kind === "value_changed"));
  results.push("unchanged asset value does not become a false value change when editable metadata changes in Play");
  for (const [adapter, name, type] of [["ui.observe", "DebugButton", "EvidenceFirst.Adapters.UIObservationProbe"], ["physics.observe", "DebugBody", "EvidenceFirst.Adapters.CollisionObservationProbe"]]) {
    const object = (await call("unity_find", { kind: "objects", query: name })).items[0].target;
    for (let n = 0; n < 16; n++) await call("unity_debug_action", { adapter, input: { target: object, maxDurationMs: 6000 }, operationId: crypto.randomUUID() });
    const over = await runtime.call("unity_debug_action", { adapter, input: { target: object, maxDurationMs: 6000 }, operationId: crypto.randomUUID() }); assert.equal(over.errorCode, "probe_capacity");
    assert.equal((await call("unity_find", { kind: "objects", query: name, type })).total, 16);
  }
  await editor("pause");
  for (let n = 0; n < 60; n++) {
    const ui = await call("unity_find", { kind: "objects", query: "DebugButton", type: "EvidenceFirst.Adapters.UIObservationProbe" });
    const physics = await call("unity_find", { kind: "objects", query: "DebugBody", type: "EvidenceFirst.Adapters.CollisionObservationProbe" });
    if (!ui.total && !physics.total) break;
    if (n === 59) throw Error("Paused probe cleanup did not complete"); await sleep(250);
  }
  results.push("UI and physics owned DontSave probes enforce 16 each and expire while Play is paused");
  await editor("stop");
  for (const snapshotId of [a.snapshotId, b.snapshotId]) await call("unity_snapshot", { action: "release", snapshotId });
}
main().then(() => { const r = { status: "passed", passed: results.length, results }; fs.writeFileSync(path.join(root, "regression-result.json"), JSON.stringify(r, null, 2)); console.log(JSON.stringify(r, null, 2)); }).catch(e => { console.error(e.stack); fs.writeFileSync(path.join(root, "regression-result.json"), JSON.stringify({ status: "failed", results, error: e.stack }, null, 2)); process.exitCode = 1; });

#!/usr/bin/env node
"use strict";
const fs = require("node:fs"), path = require("node:path"), assert = require("node:assert/strict"), crypto = require("node:crypto");
const { createRuntime } = require("../lmstudio-unity-mcp/src/server");
const root = process.argv[2];
if (!root || !path.basename(root).startsWith("unity-bridge-test-") || !fs.existsSync(path.join(root, "smoke-result.json"))) throw Error("Isolated fixture required");
const runtime = createRuntime({ UNITY_PROJECT_ROOT: root, ALLOW_WRITE: "1", ALLOW_COMMANDS: "1" }), results = [];
const call = async (name, args) => { const r = await runtime.call(name, args); if (r.errorCode) throw Error(JSON.stringify(r)); return r; };
async function main() {
  const catalog = await call("unity_debug_query", { adapter: "catalog", input: {} });
  for (const p of ["input", "ui", "physics"]) assert.equal(catalog.optionalPackages[p].availability, "package_absent");
  results.push("base package runs without Input/uGUI/physics packages and reports package_absent, not unimplemented");
  const safe = createRuntime({ UNITY_PROJECT_ROOT: root });
  const denied = await safe.call("unity_debug_action", { adapter: "events.start", input: { kinds: ["x"], maxDurationMs: 1000, maxFrames: 20 }, operationId: crypto.randomUUID() }); assert.equal(denied.errorCode, "execute_disabled");
  results.push("Observe-only MCP denies debug Execute");
  const target = (await call("unity_find", { kind: "assets", query: "t:SampleData" })).items[0].target;
  const selection = { targets: [{ target, propertyPaths: ["linked", "values", "values.Array.data[0]", "notAField"] }] };
  const a = await call("unity_snapshot", { action: "capture", ...selection });
  const read = await call("unity_object_read", { target, propertyPaths: ["linked", "values"] });
  await call("unity_object_patch", { target, scope: "asset", receipt: read.receipt, operationId: crypto.randomUUID(), patches: [{ op: "set", propertyPath: "linked", value: null }, { op: "array_insert", propertyPath: "values", index: 0, value: "321" }] });
  const b = await call("unity_snapshot", { action: "capture", ...selection });
  const diff = await call("unity_snapshot", { action: "diff", a: a.snapshotId, b: b.snapshotId });
  for (const kind of ["reference_changed", "collection_structure_changed", "value_changed", "incomparable"]) assert(diff.items.some(i => i.kind === kind), JSON.stringify(diff));
  const stored = await call("unity_snapshot", { action: "read", snapshotId: b.snapshotId, targetIndex: 0, propertyPaths: ["linked"] }); assert.equal(stored.values.items[0].state, "null");
  const c = await call("unity_snapshot", { action: "capture", targets: [{ target, propertyPaths: ["linked"] }] });
  const missing = await call("unity_snapshot", { action: "diff", a: a.snapshotId, b: c.snapshotId }); assert(missing.items.some(i => i.kind === "not_collected"));
  const duplicate = await runtime.call("unity_snapshot", { action: "capture", targets: [selection.targets[0], selection.targets[0]] }); assert.equal(duplicate.errorCode, "invalid_scope");
  results.push("stored null/reference/array structure/value changes, inaccessible comparisons, uncollected fields and duplicate scope refusal");
  const ids = [a.snapshotId, b.snapshotId, c.snapshotId];
  for (let i = ids.length; i < 64; i++) ids.push((await call("unity_snapshot", { action: "capture", targets: [{ target, propertyPaths: ["number"] }] })).snapshotId);
  const full = await runtime.call("unity_snapshot", { action: "capture", targets: [{ target, propertyPaths: ["number"] }] }); assert.equal(full.errorCode, "evidence_capacity");
  const first = await call("unity_snapshot", { action: "read", snapshotId: a.snapshotId }); assert.equal(first.snapshotId, a.snapshotId);
  for (const snapshotId of ids) await call("unity_snapshot", { action: "release", snapshotId });
  results.push("64-item store bound refuses further capture without evicting evidence; explicit release succeeds");
}
main().then(() => { const r = { status: "passed", passed: results.length, results }; fs.writeFileSync(path.join(root, "snapshot-result.json"), JSON.stringify(r, null, 2)); console.log(JSON.stringify(r, null, 2)); }).catch(e => { console.error(e.stack); process.exitCode = 1; });

#!/usr/bin/env node
"use strict";
// Integration harness with an explicit, predetermined test sequence. Not product code.
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const net = require("node:net");
const { BridgeClient } = require("../lmstudio-unity-mcp/src/bridge-client");
const { createRuntime } = require("../lmstudio-unity-mcp/src/server");
const root = process.argv[2];
if (!root || !path.basename(root).startsWith("unity-bridge-test-") || !fs.existsSync(path.join(root, "smoke-result.json"))) throw new Error("Supply only an isolated test_unity_bridge.js fixture with completed smoke tests");
const runtime = createRuntime({ UNITY_PROJECT_ROOT: root, ALLOW_WRITE: "1", ALLOW_COMMANDS: "1" });
const results = [];
let observedReloading = false;
const reloadPoll = setInterval(() => { try { if (JSON.parse(fs.readFileSync(runtime.policy.discovery,"utf8")).state === "reloading") observedReloading = true; } catch {} }, 20);
reloadPoll.unref();
const id = () => crypto.randomUUID();
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function call(name, args) {
  const result = await runtime.call(name, args);
  if (result.errorCode) throw new Error(`${name}: ${JSON.stringify(result)}`);
  return result;
}
async function waitFor(label, predicate, timeout = 90000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const status = await runtime.call("unity_status");
    if (status.connection === "connected" && !status.compiling && !status.importing && predicate(status)) return status;
    await sleep(1000);
  }
  throw new Error(`Timeout: ${label}`);
}
async function main() {
  const initial = await waitFor("connected", () => true);
  results.push("authenticated project/session handshake");
  const assets = await call("unity_find", { kind: "assets", query: "t:SampleData", limit: 3 });
  assert(assets.items[0]?.target);
  const read = await call("unity_object_read", { target: assets.items[0].target, propertyPaths: ["number"] });
  const patchArgs = { operationId: id(), target: assets.items[0].target, receipt: read.receipt, scope: "asset", patches: [{ op: "set", propertyPath: "number", value: "123" }] };
  const changed = await call("unity_object_patch", patchArgs);
  assert.equal(changed.items[0].value, "123");
  const duplicate = await call("unity_object_patch", patchArgs);
  assert.equal(duplicate.receipt, changed.receipt);
  results.push("RPC serialized patch and retained duplicate result");
  const newAsset = await call("unity_asset", { action: "create_so", type: assets.items[0].type, path: `Assets/Created_${Date.now()}.asset`, mustNotExist: true, operationId: id() });
  assert.equal(newAsset.saved, true);
  await call("unity_object_read", { target: newAsset.target, propertyPaths: ["number"] });
  results.push("project-discovered SO type creation through RPC");
  const discovery = new BridgeClient(runtime.policy).discover();
  const lostArgs = { action: "create", name: `LostReply_${Date.now()}`, scenePath: "Assets/TestScene.unity", operationId: id() };
  await new Promise((resolve, reject) => {
    const socket = net.createConnection({ host: "127.0.0.1", port: discovery.port }, () => {
      const request = { protocolVersion: discovery.protocolVersion, requestId: id(), token: discovery.token, projectIdentity: discovery.projectIdentity,
        canonicalProjectRoot: discovery.canonicalProjectRoot, editorSessionId: discovery.editorSessionId, domainGeneration: discovery.domainGeneration,
        method: "unity_scene", args: lostArgs };
      socket.write(JSON.stringify(request) + "\n", () => { socket.destroy(); resolve(); });
    });
    socket.on("error", reject);
  });
  let recovered;
  for (let i = 0; i < 20; i++) {
    recovered = await call("unity_operation", { action: "get", operationId: lostArgs.operationId });
    if (recovered.status === "applied") break;
    await sleep(100);
  }
  assert.equal(recovered.status, "applied");
  const replay = await call("unity_scene", lostArgs);
  assert.deepEqual(replay.target, recovered.target);
  const found = await call("unity_find", { kind: "objects", query: lostArgs.name, limit: 5 });
  assert.equal(found.total, 1);
  results.push("lost TCP response -> operation lookup -> same-ID replay creates only one object");
  const beforePlay = await call("unity_find", { kind: "objects", query: "UserInspectorChange", limit: 5 });
  assert(beforePlay.items.length > 0);
  const play = await call("unity_editor", { action: "play", operationId: id() });
  assert.equal(play.status, "accepted");
  const playing = await waitFor("Play mode", s => s.playing === true);
  const runtimeObjects = await call("unity_find", { kind: "objects", query: "UserInspectorChange", limit: 5 });
  const handle = runtimeObjects.items[0].target;
  assert.equal(handle.kind, "runtime");
  await call("unity_object_read", { target: handle, limit: 2 });
  await call("unity_editor", { action: "pause", operationId: id() });
  await call("unity_editor", { action: "step", operationId: id() });
  await call("unity_editor", { action: "stop", operationId: id() });
  await waitFor("Stop mode", s => !s.playing && s.playSessionId === "");
  const expired = await runtime.call("unity_object_read", { target: handle });
  assert.equal(expired.errorCode, "expired_object_ref");
  results.push("Play/Pause/Step/Stop and runtime handle expiry");
  await call("unity_editor", { action: "play", operationId: id() });
  const secondPlay = await waitFor("second Play", s => s.playing);
  assert.notEqual(secondPlay.playSessionId, playing.playSessionId);
  await call("unity_editor", { action: "stop", operationId: id() });
  const beforeReload = await waitFor("second Stop", s => !s.playing && !s.playSessionId);
  const durableSnapshot = await call("unity_snapshot", { action: "capture", targets: [{ target: assets.items[0].target, propertyPaths: ["number"] }] });
  const durableRecording = await call("unity_snapshot", { action: "record_start", targets: [{ target: assets.items[0].target, propertyPaths: ["number"] }], maxDurationMs: 1000, maxFrames: 3600, intervalMs: 50, maxSamples: 1, maxBytes: 100000 });
  await sleep(200);
  assert.equal(beforeReload.domainGeneration, initial.domainGeneration);
  results.push("two Play sessions with Domain Reload disabled");
  const approvalRead = await call("unity_object_read", { target: newAsset.target, propertyPaths: ["number"] });
  const pendingApproval = await call("unity_approval", { action: "request", method: "unity_asset", arguments: {
    action: "delete", target: newAsset.target, receipt: approvalRead.receipt, operationId: id() } });
  assert.equal(pendingApproval.status, "pending");
  const symbol = `Probe_${Date.now()}`;
  const probe = `Assets/Smoke/${symbol}.cs`;
  const created = await call("create_file", { path: probe, content: `#warning EvidenceFirst explicit reload probe\nnamespace EvidenceFirst.Tests { internal class ${symbol} {} }\n`, mustNotExist: true });
  assert.equal(created.import, "not_requested");
  const importId = id();
  const imported = await runtime.call("unity_editor", { action: "import", path: probe, operationId: importId });
  if (imported.errorCode && imported.status !== "outcome_unknown") throw new Error(JSON.stringify(imported));
  const afterReload = await waitFor("compile + domain reload", s => s.domainGeneration > beforeReload.domainGeneration, 150000);
  assert.equal(afterReload.editorSessionId, initial.editorSessionId);
  const restoredApproval = await call("unity_approval", { action: "status", approvalId: pendingApproval.approvalId });
  assert.equal(restoredApproval.status, "pending"); assert.equal(restoredApproval.expiresAt, pendingApproval.expiresAt);
  results.push("exact pending approval and original expiry survive real domain reload");
  assert.equal(afterReload.compilationOutcome, "completed_without_observed_errors");
  assert.equal(afterReload.lastCompilationId, afterReload.compilationId);
  const retainedSnapshot = await call("unity_snapshot", { action: "read", snapshotId: durableSnapshot.snapshotId, targetIndex: 0 });
  assert.equal(retainedSnapshot.values.items[0].value, "123");
  const retainedRecording = await call("unity_snapshot", { action: "record_status", recordingId: durableRecording.recordingId });
  assert.equal(retainedRecording.status, "stopped"); assert.equal(retainedRecording.snapshotIds.length, 1);
  results.push("stored snapshot and bounded recording metadata survive real domain reload without resuming collection");
  const operation = await call("unity_operation", { action: "get", operationId: importId });
  assert(["accepted", "outcome_unknown"].includes(operation.status));
  const logs = await call("unity_logs", { source: "compiler", compilationId: afterReload.compilationId, limit: 50 });
  assert(logs.items.some(row => row.message.includes("EvidenceFirst explicit reload probe")));
  assert.equal(logs.sourceAssemblyVerification, "unknown");
  results.push("code create -> explicit import -> compilation warning -> domain reload -> reconnect -> diagnostics");
  const oldReceipt = await runtime.call("unity_object_patch", { ...patchArgs, operationId: id() });
  assert.equal(oldReceipt.errorCode, "receipt_required");
  results.push("old domain receipt rejected after reconnect");
  const compileId = id();
  const compile = await call("unity_editor", { action: "compile", operationId: compileId });
  assert.equal(compile.status, "accepted");
  await waitFor("explicit compile", s => s.domainGeneration > afterReload.domainGeneration, 150000);
  const finished = await call("unity_operation", { action: "get", operationId: compileId });
  assert.equal(finished.status, "completed"); assert.equal(finished.observedCompilation.errorsObserved, false);
  assert.equal(finished.observedCompilation.logs.args.compilationId, finished.observedCompilation.compilationId);
  const firstCompileStatus = await call("unity_status");
  await call("unity_editor", { action: "compile", operationId: id() });
  await waitFor("unrelated second compile", s => s.domainGeneration > firstCompileStatus.domainGeneration, 150000);
  const retainedCompile = await call("unity_operation", { action: "get", operationId: compileId });
  assert.deepEqual(retainedCompile.observedCompilation, finished.observedCompilation);
  results.push("explicit compilation operation remains correlated after an unrelated second compilation");
  assert.equal(observedReloading, true);
  results.push("actual domain reload publishes the reloading discovery state before reconnecting");
  clearInterval(reloadPoll);
  fs.writeFileSync(path.join(root, "rpc-result.json"), JSON.stringify({ status: "passed", editorVersion: afterReload.editorVersion, results }, null, 2));
  console.log(JSON.stringify({ status: "passed", results }, null, 2));
}
main().catch(error => { console.error(error.stack); fs.writeFileSync(path.join(root, "rpc-result.json"), JSON.stringify({ status: "failed", results, error: error.message }, null, 2)); process.exitCode = 1; });

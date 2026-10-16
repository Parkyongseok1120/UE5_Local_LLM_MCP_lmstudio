#!/usr/bin/env node
"use strict";
// Predetermined development tests, never loaded by a product server.
const fs = require("node:fs"), path = require("node:path"), assert = require("node:assert/strict"), crypto = require("node:crypto");
const { createRuntime } = require("../lmstudio-unity-mcp/src/server");
const root = process.argv[2];
if (!root || !path.basename(root).startsWith("unity-bridge-test-") || !fs.existsSync(path.join(root, "framework-fixture-ready.json"))) throw Error("Prepared isolated release fixture required");
const runtime = createRuntime({ UNITY_PROJECT_ROOT: root, ALLOW_WRITE: "1", ALLOW_COMMANDS: "1" });
const results = [], runs = [], id = () => crypto.randomUUID(), sleep = ms => new Promise(r => setTimeout(r, ms));
async function call(name, args = {}) { const r = await runtime.call(name, args); if (r.errorCode) throw Error(`${name}: ${JSON.stringify(r)}`); return r; }
async function idle() { for (let n = 0; n < 180; n++) { const r = await runtime.call("unity_status"); if (r.connection === "connected" && !r.playing && !r.compiling && !r.importing && (await runtime.call("unity_tests", { action: "status" })).busy === false) return; await sleep(500); } throw Error("Editor/test runner not idle"); }
async function waitRun(operationId) {
  for (let n = 0; n < 360; n++) {
    const r = await runtime.call("unity_tests", { action: "status", operationId });
    if (r.operationId === operationId && ["completed", "not_run", "execution_failed", "cancelled", "outcome_unknown"].includes(r.status)) { runs.push(r); return r; }
    await sleep(500);
  }
  throw Error(`Test timeout ${operationId}`);
}
const args = (mode, category, extra = {}) => ({ action: "run", mode, categories: [category], acknowledgeSceneChanges: true, maxTests: 1, maxDurationMs: 90000, operationId: id(), ...extra });
async function run(mode, category, extra) { await idle(); const a = args(mode, category, extra); await call("unity_tests", a); return waitRun(a.operationId); }
async function main() {
  await idle(); assert.equal((await call("unity_tests", { action: "status" })).availability, "active");
  const safe = createRuntime({ UNITY_PROJECT_ROOT: root });
  assert.equal((await safe.call("unity_tests", args("EditMode", "EvidenceFirst.Pass"))).errorCode, "execute_disabled");
  assert.equal((await runtime.call("unity_approval", { action: "approve", approvalId: "forged" })).errorCode, "invalid_arguments");
  results.push("MCP execution permission and no model-callable approval endpoint");
  const passed = await run("EditMode", "EvidenceFirst.Pass"); assert.equal(passed.status, "completed"); assert.equal(passed.passed, 1); assert.equal(passed.failed, 0);
  const page = await call("unity_tests", { action: "results", operationId: passed.operationId, limit: 1 }); assert.equal(page.page.items.length, 1); assert.equal(page.page.items[0].resultState, "Passed");
  assert.equal((await call("unity_operation", { action: "get", operationId: passed.operationId })).status, "completed");
  results.push("actual EditMode NUnit success, operation lookup and paged retained result");
  const failed = await run("EditMode", "EvidenceFirst.Fail"); assert.equal(failed.status, "completed"); assert.equal(failed.failed, 1); assert.notEqual(failed.testStatus, "Passed");
  results.push("actual assertion failure is reported as failed test, not successful verification");
  const absent = await run("EditMode", "EvidenceFirst.Absent"); assert.equal(absent.status, "not_run"); assert.equal(absent.errorCode, "no_tests_matched");
  results.push("empty exact selection does not broaden to all tests");
  const play = await run("PlayMode", "EvidenceFirst.Pass"); assert.equal(play.status, "completed"); assert.equal(play.passed, 1);
  results.push("actual PlayMode coroutine observes advancing frame and survives framework state changes");
  await idle(); const cancel = args("PlayMode", "EvidenceFirst.Cancel"); await call("unity_tests", cancel);
  for (let n = 0; n < 120; n++) { const r = await runtime.call("unity_tests", { action: "status", operationId: cancel.operationId }); if (r.status === "running") break; await sleep(500); }
  await call("unity_tests", { action: "cancel", operationId: cancel.operationId }); const cancelled = await waitRun(cancel.operationId);
  assert.equal(cancelled.cancelReason, "explicit_request"); assert.equal(cancelled.status, "cancelled"); assert.notEqual(cancelled.testStatus, "Passed");
  results.push("explicit cooperative cancellation of owned PlayMode test, without claiming a passed result");
  await idle(); const timeout = await run("PlayMode", "EvidenceFirst.Cancel", { maxDurationMs: 100 });
  assert(["not_run", "cancelled"].includes(timeout.status)); assert.equal(timeout.cancelReason, "time_limit");
  results.push("caller time bound requests cancellation; no forced arbitrary-code abort");
  for (const r of runs) await call("unity_tests", { action: "release", operationId: r.operationId });
  assert.equal((await runtime.call("unity_tests", { action: "status", operationId: passed.operationId })).errorCode, "test_run_unknown");
  results.push("explicit release removes retained test result");
}
main().then(() => { const r = { status: "passed", passed: results.length, results, runs }; fs.writeFileSync(path.join(root, "framework-result.json"), JSON.stringify(r, null, 2)); console.log(JSON.stringify(r, null, 2)); }).catch(e => { fs.writeFileSync(path.join(root, "framework-result.json"), JSON.stringify({ status: "failed", results, runs, error: e.stack }, null, 2)); console.error(e.stack); process.exitCode = 1; });

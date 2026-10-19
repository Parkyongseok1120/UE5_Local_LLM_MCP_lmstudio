"use strict";

// The Editor connection fails in different ways that need different user
// action: no Bridge package, no running Editor, or a domain reload that will
// finish by itself. Each is named, with one next step, and unity_status says
// what still works and which server permissions are on.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { createRuntime } = require("../src/server");
const { BRIDGE_PACKAGE } = require("../src/bridge-client");

function fixture(t, { bridgeBound = true, env = {} } = {}) {
  const root = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "unity-connection-")));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (const folder of ["Assets", "Packages", "ProjectSettings", "Library/EvidenceFirst"]) fs.mkdirSync(path.join(root, folder), { recursive: true });
  fs.writeFileSync(path.join(root, "Packages/manifest.json"),
    JSON.stringify({ dependencies: bridgeBound ? { [BRIDGE_PACKAGE]: "file:../../unity-editor-bridge" } : {} }));
  fs.writeFileSync(path.join(root, "ProjectSettings/ProjectVersion.txt"), "m_EditorVersion: 6000.3.14f1");
  const runtime = createRuntime({ UNITY_PROJECT_ROOT: root, ...env });
  t.after(() => runtime.close());
  const discover = value => fs.writeFileSync(runtime.policy.discovery, JSON.stringify({ protocolVersion: 2,
    canonicalProjectRoot: root, projectIdentity: runtime.policy.projectIdentity, editorSessionId: "s", domainGeneration: 3, ...value }), { mode: 0o600 });
  return { root, ...runtime, discover };
}

// A process that has exited: its pid is no longer alive.
function deadPid() { return spawnSync(process.execPath, ["-e", ""]).pid; }

test("without the Bridge package the status says so and how to install it", async t => {
  const f = fixture(t, { bridgeBound: false });
  const status = await f.call("unity_status");
  assert.equal(status.connection, "disconnected");
  assert.equal(status.reasonCode, "bridge_not_installed");
  assert.match(status.nextStep, /installer/);
  assert.deepEqual(status.serverPermissions, { write: false, commands: false });
  assert.ok(status.offlineTools.includes("read_file") && status.offlineTools.includes("unity_status"));
  assert.ok(!status.offlineTools.includes("unity_object_read"));
  const object = await f.call("unity_find", { kind: "types" });
  assert.equal(object.errorCode, "bridge_not_installed");
  assert.match(object.nextStep, /installer/);
});

test("with the Bridge bound but no Editor, the status asks to open the project; server flags are reported", async t => {
  const f = fixture(t, { env: { ALLOW_WRITE: "1", ALLOW_COMMANDS: "0" } });
  const status = await f.call("unity_status");
  assert.equal(status.reasonCode, "editor_not_running");
  assert.match(status.nextStep, /Open this project in the Unity Editor/);
  assert.deepEqual(status.serverPermissions, { write: true, commands: false });
  f.discover({ state: "ready", processId: deadPid(), port: 4000, token: "a".repeat(64) });
  assert.equal((await f.call("unity_status")).reasonCode, "editor_not_running");
});

test("a reloading Editor is distinguished from a closed one and is the only retryable state", async t => {
  const f = fixture(t);
  f.discover({ state: "reloading", processId: process.pid, reloadStartedAt: "2026-10-18T00:00:00.000Z" });
  const status = await f.call("unity_status");
  assert.equal(status.connection, "reloading");
  assert.equal(status.reasonCode, "editor_reloading");
  assert.equal(status.retryAfterSeconds, 5);
  const call = await f.call("unity_find", { kind: "types" });
  assert.equal(call.errorCode, "editor_reloading");
  assert.equal(call.status, "not_applied");
  f.discover({ state: "reloading", processId: deadPid() });
  assert.equal((await f.call("unity_status")).reasonCode, "editor_not_running");
});

test("unity_tests list is a read; run must state its bounds before anything reaches the Editor", async t => {
  const f = fixture(t);
  const calls = [];
  const bridge = { lastSession: null, async call(name, args) { calls.push([name, args.action]); return { status: "pending", retryAfterSeconds: 1 }; } };
  const runtime = createRuntime({ UNITY_PROJECT_ROOT: f.root, ALLOW_COMMANDS: "0" }, bridge);
  t.after(() => runtime.close());
  const listed = await runtime.call("unity_tests", { action: "list", mode: "EditMode" });
  assert.equal(listed.status, "pending");
  assert.equal((await runtime.call("unity_tests", { action: "list" })).errorCode, "invalid_arguments");
  const unbounded = await runtime.call("unity_tests", { action: "run", operationId: "run-tests-01", mode: "EditMode", testNames: ["A.B"] });
  assert.equal(unbounded.errorCode, "invalid_arguments");
  assert.match(unbounded.message, /maxDurationMs|maxTests|acknowledgeSceneChanges/);
  const bounded = await runtime.call("unity_tests", { action: "run", operationId: "run-tests-01", mode: "EditMode", testNames: ["A.B"],
    maxDurationMs: 1000, maxTests: 1, acknowledgeSceneChanges: true });
  assert.equal(bounded.errorCode, "execute_disabled");
  assert.deepEqual(calls, [["unity_tests", "list"]]);
});

test("permission refusals name the side that is off and how to enable it", async t => {
  const f = fixture(t);
  const bridge = { lastSession: null, async call() {
    throw Object.assign(new Error("Editor Edit permission is off: enable Tools/Evidence First/Allow Edit for this Editor session"),
      { code: "permission_denied", status: "not_applied" });
  } };
  const readOnly = createRuntime({ UNITY_PROJECT_ROOT: f.root, ALLOW_WRITE: "0", ALLOW_COMMANDS: "0" }, bridge);
  t.after(() => readOnly.close());
  const edit = await readOnly.call("unity_asset", { action: "save", operationId: "save-asset-01" });
  assert.equal(edit.errorCode, "edit_disabled");
  assert.match(edit.nextStep, /--unity-allow-write/);
  assert.match(edit.nextStep, /Allow Edit/);
  const run = await readOnly.call("unity_editor", { action: "compile", operationId: "compile-01" });
  assert.equal(run.errorCode, "execute_disabled");
  assert.match(run.nextStep, /--unity-allow-commands/);
  const file = await readOnly.call("create_file", { path: "Assets/New.cs", content: "x", mustNotExist: true });
  assert.equal(file.errorCode, "edit_disabled");
  assert.match(file.nextStep, /--unity-allow-write/);
  const writable = createRuntime({ UNITY_PROJECT_ROOT: f.root, ALLOW_WRITE: "1" }, bridge);
  t.after(() => writable.close());
  const editor = await writable.call("unity_asset", { action: "save", operationId: "save-asset-02" });
  assert.equal(editor.errorCode, "permission_denied");
  assert.match(editor.message, /Allow Edit/);
  assert.match(editor.nextStep, /server permission is already on/);
});

test("run defaults are bounded and reported while schemas publish required limits",async t=>{
 const root=fs.mkdtempSync(path.join(os.tmpdir(),"unity-defaults-"));
 for(const dir of ["Assets","Packages","ProjectSettings"]) fs.mkdirSync(path.join(root,dir));
 fs.writeFileSync(path.join(root,"Packages/manifest.json"),"{}"); fs.writeFileSync(path.join(root,"ProjectSettings/ProjectVersion.txt"),"m_EditorVersion: 6000.3.14f1");
 let dispatched;
 const runtime=createRuntime({UNITY_PROJECT_ROOT:root,ALLOW_COMMANDS:"1"},{call:async(name,args)=>{dispatched=args;return {status:"accepted"};}});
 t.after(()=>{runtime.close();fs.rmSync(root,{recursive:true,force:true});});
 const result=await runtime.call("unity_tests",{action:"run",mode:"EditMode",operationId:"defaults-123",testNames:["Exact.Test"],acknowledgeSceneChanges:true});
 assert.deepEqual(result.defaultedBounds,["maxDurationMs","maxTests"]); assert.equal(dispatched.maxDurationMs,60000);assert.equal(dispatched.maxTests,32);
});

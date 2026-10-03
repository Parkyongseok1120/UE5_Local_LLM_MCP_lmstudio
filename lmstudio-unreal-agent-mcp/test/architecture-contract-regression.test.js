"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { sha256Text } = require("../src/safe-write");
const { createRuntimeTransaction, updateRuntimeTransactionEntry, transactionBackupPath } = require("../src/direct-transaction-store");
const { rollbackRuntimeTransaction } = require("../src/direct-transaction-recovery");
const { createDirectRuntime, serveRuntime } = require("../src/direct-server");
const { createStrictRuntime } = require("../src/strict-server");
const { FileSnapshotRegistry } = require("../src/file-snapshot-registry");
const { EventEmitter } = require("node:events");
const { PassThrough } = require("node:stream");
const { Client } = require("@modelcontextprotocol/sdk/client/index.js");
const { InMemoryTransport } = require("@modelcontextprotocol/sdk/inMemory.js");

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "architecture-contract-"));
  const projectRoot = path.join(root, "Project");
  const projectPath = path.join(projectRoot, "Project.uproject");
  const stateRoot = path.join(root, "state");
  const links = [];
  fs.mkdirSync(path.join(projectRoot, "Config"), { recursive: true });
  fs.writeFileSync(projectPath, "{}");
  t.after(() => {
    for (const link of links) if (fs.existsSync(link) && fs.lstatSync(link).isSymbolicLink()) fs.unlinkSync(link);
    const resolved = fs.realpathSync(root);
    assert.equal(path.dirname(resolved).toLowerCase(), fs.realpathSync(os.tmpdir()).toLowerCase());
    assert.ok(path.basename(resolved).startsWith("architecture-contract-"));
    fs.rmSync(resolved, { recursive: true, force: true });
  });
  return { root, projectRoot, projectPath, stateRoot, links };
}

for (const withinProject of [false, true]) for (const locksHeld of [false, true]) test(`recovery refuses rebound parent (withinProject=${withinProject}, locksHeld=${locksHeld})`, async t => {
  const f = fixture(t);
  const parent = path.join(f.projectRoot, "Config");
  const outside = path.join(withinProject ? f.projectRoot : f.root, "Other");
  fs.mkdirSync(outside);
  const target = path.join(parent, "A.ini");
  fs.writeFileSync(target, "after");
  fs.writeFileSync(path.join(outside, "A.ini"), "after");
  const journal = createRuntimeTransaction({ runtimeOwner: "direct", stateRoot: f.stateRoot, projectRoot: f.projectRoot, projectPath: f.projectPath });
  const backup = transactionBackupPath(f.stateRoot, "direct", journal.transactionId, "Config/A.ini");
  fs.writeFileSync(backup, "before");
  updateRuntimeTransactionEntry(journal, {
    relativePath: "Config/A.ini", canonicalAbsolutePath: target, operation: "patch",
    existedBefore: true, preHash: sha256Text("before"), postHash: sha256Text("after"),
    preContentBackupPath: backup, writeStarted: true,
  }, f.stateRoot);
  fs.renameSync(parent, `${parent}.original`);
  try { fs.symlinkSync(outside, parent, process.platform === "win32" ? "junction" : "dir"); }
  catch (error) { t.skip(`Directory link unavailable: ${error.code}`); return; }
  // Remove only the link before the verified temporary-root recursive cleanup.
  f.links.push(parent);
  const result = await rollbackRuntimeTransaction(journal, f.stateRoot, { locksHeld });
  assert.equal(fs.readFileSync(path.join(outside, "A.ini"), "utf8"), "after");
  assert.equal(result.rollbackIncomplete, true);
  assert.match(result.rollbackErrors[0].error, /identity|physical|rebound|escapes/i);
});

function runtimeOptions(f, extra = {}) {
  return { workspaceRoot: f.root, stateRoot: f.stateRoot, configPath: path.join(f.root, "config.json"),
    env: { ALLOW_WRITE: "1", ALLOW_COMMANDS: "1", ALLOW_UNREAL_BUILD: "0" },
    getActiveProject: () => f.projectPath, ...extra };
}
function fakeChild() {
  const child = new EventEmitter();
  child.pid = 101; child.stdout = new PassThrough(); child.stderr = new PassThrough();
  return child;
}

test("public MCP cancellation reaches the owned command; runtime close prevents future dispatch", async t => {
  const f = fixture(t);
  let child, spawned = 0, killed = 0, started;
  const ready = new Promise(resolve => { started = resolve; });
  const runtime = createDirectRuntime(runtimeOptions(f, {
    spawnCommand: () => { spawned++; child = fakeChild(); started(); return child; },
    killProcessTree: async () => { killed++; child.emit("close", 1); }, shutdownTimeoutMs: 25,
  }));
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const server = await serveRuntime(runtime, "test", serverTransport);
  const client = new Client({ name: "audit-test", version: "1" });
  await client.connect(clientTransport);
  t.after(async () => { await client.close(); await server.close(); });
  const controller = new AbortController();
  const pending = client.callTool({ name: "run_command", arguments: { command: "node --version" } }, undefined, { signal: controller.signal });
  const rejected = assert.rejects(pending);
  await ready; controller.abort(); await rejected;
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(spawned, 1); assert.equal(killed, 1);
  runtime.close();
  const after = (await runtime.callTool("run_command", { command: "node --version" })).structuredContent;
  assert.equal(after.executionState, "not_started"); assert.equal(spawned, 1);
});

for (const strict of [false, true]) test(`runtime close terminates owned command and settles lifecycle (strict=${strict})`, async t => {
  const f = fixture(t);
  let child, started;
  const ready = new Promise(resolve => { started = resolve; });
  const options = runtimeOptions(f, { spawnCommand: () => { child = fakeChild(); started(); return child; },
    killProcessTree: async () => child.emit("close", 0), shutdownTimeoutMs: 25 });
  const runtime = strict ? createStrictRuntime(options) : createDirectRuntime(options);
  const args = { command: "node --version" };
  if (strict) {
    const begun = (await runtime.callTool("strict_begin", { conversationId: "audit-conversation", objective: "test cancellation", project: f.projectPath })).structuredContent;
    args.strictSessionId = begun.strictSession.id; args.conversationId = "audit-conversation";
  }
  const pending = runtime.callTool("run_command", args);
  await ready; runtime.close("connection_closed");
  const result = (await pending).structuredContent;
  assert.equal(result.cancelled, true); assert.equal(result.processExited, true); assert.equal(result.ok, false);
  if (strict) assert.equal(result.strictSession.status, "orphaned");
});

test("pre-dispatch cancellation starts no child and completed calls release abort listeners", async t => {
  const f = fixture(t); let spawned = 0;
  const runtime = createDirectRuntime(runtimeOptions(f, { spawnCommand: () => { spawned++; return fakeChild(); } }));
  const controller = new AbortController(); controller.abort();
  const result = (await runtime.callTool("run_command", { command: "node --version" }, { signal: controller.signal })).structuredContent;
  assert.equal(result.cancelled, true); assert.equal(spawned, 0);
  const active = new AbortController();
  await runtime.callTool("read_file", { path: "Config/missing.ini" }, { signal: active.signal });
  assert.equal(require("node:events").getEventListeners(active.signal, "abort").length, 0);
});

for (const operation of ["write_file", "replace_in_file", "apply_edit_bundle"]) test(`receipt failure preserves actual ${operation} commit`, async t => {
  const f = fixture(t); const target = path.join(f.projectRoot, "Config/A.ini");
  const registry = new FileSnapshotRegistry(); registry.register = () => { throw Error("receipt unavailable"); };
  const runtime = createDirectRuntime(runtimeOptions(f, { fileSnapshots: registry }));
  const patch = { path: "Config/A.ini", oldText: "before", newText: "after", expectedOccurrences: 1, expectedHash: sha256Text("before") };
  if (operation !== "write_file") fs.writeFileSync(target, "before");
  const args = operation === "write_file" ? { path: patch.path, content: "after" }
    : operation === "apply_edit_bundle" ? { patches: [patch] } : patch;
  const result = (await runtime.callTool(operation, args)).structuredContent;
  assert.equal(result.ok, true); assert.equal(fs.readFileSync(target, "utf8"), "after");
  assert.equal(operation === "apply_edit_bundle" ? result.files[0].receiptStatus : result.receiptStatus, "unavailable");
  if (operation === "apply_edit_bundle") assert.ok(result.transactionId);
});

test("failed reads retain resolved scope and searches disclose IO omissions", async t => {
  const f = fixture(t); const target = path.join(f.projectRoot, "Config/A.ini");
  fs.writeFileSync(target, "needle");
  const runtime = createDirectRuntime(runtimeOptions(f));
  const originalRead = fs.promises.readFile, originalOpen = fs.promises.open;
  fs.promises.readFile = async (file, ...args) => {
    if (file === target) throw Object.assign(Error("denied"), { code: "EACCES" });
    return originalRead(file, ...args);
  };
  fs.promises.open = async (file, ...args) => {
    if (file === target) throw Object.assign(Error("denied"), { code: "EACCES" });
    return originalOpen(file, ...args);
  };
  try {
    const search = (await runtime.callTool("search_files", { path: "Config/A.ini", query: "needle" })).structuredContent;
    assert.equal(search.ok, true); assert.equal(search.coverage, "partial");
    assert.equal(search.filesRead, 0); assert.equal(search.readErrorCount, 1); assert.equal(search.results.length, 0);
    for (const name of ["read_file", "read_file_range", "read_symbol"]) {
      const args = { path: "Config/A.ini", ...(name === "read_file_range" ? { startLine: 1, endLine: 2 } : {}), ...(name === "read_symbol" ? { symbol: "A" } : {}) };
      const result = (await runtime.callTool(name, args)).structuredContent;
      assert.equal(result.errorCode, "ACCESS_DENIED", name); assert.equal(result.observationState, "unavailable", name);
      assert.equal(result.path, "project://Config/A.ini"); assert.equal(result.canonicalProject, f.projectPath);
      assert.equal(result.sha256, undefined);
    }
  } finally { fs.promises.readFile = originalRead; fs.promises.open = originalOpen; }
  const recovered = (await runtime.callTool("search_files", { path: "Config/A.ini", query: "needle" })).structuredContent;
  assert.equal(recovered.coverage, "complete"); assert.equal(recovered.filesRead, 1); assert.equal(recovered.results.length, 1);
});

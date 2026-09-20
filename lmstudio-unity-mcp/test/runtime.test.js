"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const { execFileSync } = require("node:child_process");
const { createRuntime } = require("../src/server");
const { BridgeClient } = require("../src/bridge-client");
const { Client } = require("@modelcontextprotocol/sdk/client/index.js");
const { StdioClientTransport } = require("@modelcontextprotocol/sdk/client/stdio.js");
function fixture(t, edit = true) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "unity-tools-test-")));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (const folder of ["Assets", "Packages", "ProjectSettings", "Library/EvidenceFirst"]) fs.mkdirSync(path.join(root, folder), { recursive: true });
  fs.writeFileSync(path.join(root, "Packages/manifest.json"), "{}");
  fs.writeFileSync(path.join(root, "ProjectSettings/ProjectVersion.txt"), "m_EditorVersion: 2022.3.0f1");
  const runtime = createRuntime({ UNITY_PROJECT_ROOT: root, ALLOW_WRITE: edit ? "1" : "0" });
  return { root, ...runtime, put: (p, value) => fs.writeFileSync(path.join(root, p), value), get: p => fs.readFileSync(path.join(root, p), "utf8") };
}
test("offline project detection and catalog does not expose Unreal or autonomous tools", async t => {
  const f = fixture(t);
  const status = await f.call("unity_status");
  assert.equal(status.connection, "disconnected");
  assert.equal(status.fileTools, "available");
  assert(f.tools.every(tool => !/unreal|planner|model_load|agent_run/.test(tool.name)));
  assert.equal((await f.call("unity_status", { arbitrary: true })).errorCode, "invalid_arguments");
});
test("receipt conflicts, forgery, cross-project isolation and re-read", async t => {
  const a = fixture(t), b = fixture(t);
  a.put("Assets/Code.cs", "class Before {}\r\n"); b.put("Assets/Code.cs", a.get("Assets/Code.cs"));
  const read = await a.call("read_file", { path: "Assets/Code.cs" });
  const args = { path: "Assets/Code.cs", receipt: read.receipt, edits: [{ oldText: "Before", newText: "After" }] };
  assert.equal((await b.call("patch_file", args)).errorCode, "invalid_receipt");
  assert.equal((await a.call("patch_file", { ...args, receipt: "forged.signature" })).errorCode, "invalid_receipt");
  a.put("Assets/Code.cs", "class UserEdit {}\r\n");
  assert.equal((await a.call("patch_file", args)).errorCode, "receipt_conflict");
  const current = await a.call("read_file", { path: args.path });
  const result = await a.call("patch_file", { ...args, receipt: current.receipt, edits: [{ oldText: "UserEdit", newText: "After" }] });
  assert.equal(result.status, "applied"); assert.equal(a.get(args.path), "class After {}\r\n");
  assert.equal(result.operation, "modified");
  assert.equal(result.previousHash, current.hash);
  assert.notEqual(result.hash, result.previousHash);
  assert.equal(result.compilation, "unknown"); assert.equal(result.import, "not_requested");
});
test("file observations carry the bound project and the observed file version", async t => {
  const a = fixture(t), b = fixture(t);
  for (const f of [a, b]) {
    f.put("Assets/Code.cs", "class Shared {}\n");
    f.put("Assets/data.json", '{"value":1}');
    f.put("Assets/table.csv", "key,value\na,1\n");
  }
  const aRead = await a.call("read_file", { path: "Assets/Code.cs" });
  const bRead = await b.call("read_file", { path: "Assets/Code.cs" });
  assert.equal(aRead.canonicalProjectRoot, a.root);
  assert.equal(aRead.projectIdentity, a.policy.projectIdentity);
  assert.notEqual(aRead.projectIdentity, bRead.projectIdentity);
  assert.equal(aRead.hash, bRead.hash);
  assert.match(aRead.hash, /^[a-f0-9]{64}$/u);
  assert.ok(Number.isFinite(Date.parse(aRead.observedAt)));

  const json = await a.call("structured_data_read", { path: "Assets/data.json", format: "json" });
  const csv = await a.call("structured_data_read", { path: "Assets/table.csv", format: "csv",
    csv: { header: true, delimiter: ",", keyColumn: "key", duplicateKey: "error", missingKey: "error" } });
  for (const observed of [json, csv]) {
    assert.equal(observed.canonicalProjectRoot, a.root);
    assert.equal(observed.projectIdentity, a.policy.projectIdentity);
    assert.match(observed.hash, /^[a-f0-9]{64}$/u);
    assert.ok(Number.isFinite(Date.parse(observed.observedAt)));
  }
  assert.equal(json.path, "Assets/data.json");
  assert.equal(csv.path, "Assets/table.csv");

  const listing = await a.call("list_directory", { path: "Assets" });
  const search = await a.call("search_files", { path: "Assets", extensions: [".cs", ".json"] });
  for (const observed of [listing, search]) {
    assert.equal(observed.canonicalProjectRoot, a.root);
    assert.equal(observed.projectIdentity, a.policy.projectIdentity);
    assert.equal(Object.hasOwn(observed, "hash"), false);
  }
  const changed = await a.call("patch_file", { path: "Assets/Code.cs", receipt: aRead.receipt,
    edits: [{ oldText: "Shared", newText: "Changed" }] });
  assert.equal(changed.status, "applied");
  assert.equal(changed.canonicalProjectRoot, a.root);
  assert.equal(changed.projectIdentity, a.policy.projectIdentity);
  assert.notEqual(changed.hash, aRead.hash);
});
test("read_file reports exact ranges and reads Unity package and project settings files", async t => {
  const f = fixture(t);
  f.put("Assets/Code.cs", "one\ntwo\nthree\nfour\nfive");

  const middle = await f.call("read_file", { path: "Assets/Code.cs", startLine: 2, limit: 2 });
  assert.equal(middle.text, "two\nthree");
  assert.equal(middle.startLine, 2);
  assert.equal(middle.endLine, 3);
  assert.equal(middle.returnedLineCount, 2);
  assert.equal(middle.totalLines, 5);
  assert.equal(middle.hasMore, true);
  assert.equal(middle.nextStartLine, 4);
  assert.equal(middle.truncated, true);

  const tail = await f.call("read_file", { path: "Assets/Code.cs", startLine: 4, limit: 2 });
  assert.equal(tail.text, "four\nfive");
  assert.equal(tail.endLine, 5);
  assert.equal(tail.returnedLineCount, 2);
  assert.equal(tail.hasMore, false);
  assert.equal(tail.nextStartLine, null);
  assert.equal(tail.truncated, false);

  const beyondEnd = await f.call("read_file", { path: "Assets/Code.cs", startLine: 6, limit: 2 });
  assert.equal(beyondEnd.text, "");
  assert.equal(beyondEnd.endLine, null);
  assert.equal(beyondEnd.returnedLineCount, 0);
  assert.equal(beyondEnd.nextStartLine, null);

  const packageManifest = await f.call("read_file", { path: "Packages/manifest.json" });
  const projectVersion = await f.call("read_file", { path: "ProjectSettings/ProjectVersion.txt" });
  assert.equal(packageManifest.status, "observed");
  assert.equal(projectVersion.status, "observed");
  assert.equal(packageManifest.canonicalProjectRoot, f.root);
  assert.equal(projectVersion.canonicalProjectRoot, f.root);
  const readTool = f.tools.find(tool => tool.name === "read_file");
  assert.match(readTool.description, /Assets, Packages or ProjectSettings/u);
});
test("unity_git returns bounded read-only status, log and diff evidence for the bound project", async t => {
  const f = fixture(t);
  const git = args => execFileSync("git", args, { cwd: f.root, encoding: "utf8", windowsHide: true });
  git(["init", "-q"]);
  git(["config", "user.email", "unity-test@example.invalid"]);
  git(["config", "user.name", "Unity Test"]);
  f.put("Assets/Code.cs", "class Before {}\n");
  git(["add", "."]);
  git(["commit", "-q", "-m", "initial"]);
  f.put("Assets/Code.cs", "class After {}\n");

  const status = await f.call("unity_git", { action: "status", limit: 20 });
  assert.equal(status.status, "observed", JSON.stringify(status));
  assert.equal(status.projectIdentity, f.policy.projectIdentity);
  assert(status.items.some(item => item.path === "Assets/Code.cs" && item.status.includes("M")));

  const log = await f.call("unity_git", { action: "log", limit: 5 });
  assert.equal(log.items.length, 1);
  assert.equal(log.items[0].subject, "initial");

  const diff = await f.call("unity_git", { action: "diff", comparison: "worktree",
    paths: ["Assets/Code.cs"], startLine: 1, limit: 20 });
  assert.match(diff.text, /class Before \{\}/u);
  assert.match(diff.text, /class After \{\}/u);
  assert.equal(diff.startLine, 1);
  assert.ok(diff.returnedLineCount > 0);
  assert.equal(diff.hasMore, false);

  git(["add", "Assets/Code.cs"]);
  git(["commit", "-q", "-m", "refactor C"]);
  const committed = await f.call("unity_git", { action: "diff", comparison: "last_commit",
    paths: ["Assets/Code.cs"], startLine: 1, limit: 20 });
  assert.match(committed.text, /class After \{\}/u);
  assert.equal((await f.call("unity_git", { action: "diff", comparison: "range",
    base: "--help", head: "HEAD" })).errorCode, "invalid_revision");
});
test("permissions, create precondition, exact-match edits, schema validation", async t => {
  const f = fixture(t, false);
  assert.equal((await f.call("create_file", { path: "Assets/New.cs", content: "", mustNotExist: true })).errorCode, "edit_disabled");
  const g = fixture(t);
  const created = await g.call("create_file", { path: "Assets/New.cs", content: "x", mustNotExist: true });
  assert.equal(created.status, "applied");
  assert.equal(created.operation, "created");
  assert.equal(Object.hasOwn(created, "previousHash"), false);
  assert.equal((await g.call("create_file", { path: "Assets/New.cs", content: "y", mustNotExist: true })).errorCode, "already_exists");
  assert.equal(g.get("Assets/New.cs"), "x");
  assert.equal((await g.call("read_file", { path: "Assets/New.cs", limit: 999999 })).errorCode, "invalid_arguments");
});
test("symlinks, hardlinks, protected formats and new-file parent checks", async t => {
  const f = fixture(t);
  const external = fs.mkdtempSync(path.join(os.tmpdir(), "unity-external-"));
  t.after(() => fs.rmSync(external, { recursive: true, force: true }));
  try {
    fs.symlinkSync(external, path.join(f.root, "Assets/Link"), "dir");
    assert.equal((await f.call("create_file", { path: "Assets/Link/x.cs", content: "bad", mustNotExist: true })).errorCode, "symlink_denied");
  } catch (error) {
    if (error.code !== "EPERM") throw error; // Windows may disallow symlink creation without developer mode.
  }
  for (const p of ["Assets/a.prefab", "Assets/a.unity", "Assets/a.asset", "Assets/a.meta", "Packages/a.cs", "ProjectSettings/a.json", "Library/a.cs", "Assets/../a.cs"])
    assert.notEqual((await f.call("create_file", { path: p, content: "bad", mustNotExist: true })).status, "applied");
  f.put("Assets/a.cs", "original"); fs.linkSync(path.join(f.root, "Assets/a.cs"), path.join(f.root, "Assets/b.cs"));
  const read = await f.call("read_file", { path: "Assets/a.cs" });
  assert.equal((await f.call("patch_file", { path: "Assets/a.cs", receipt: read.receipt, edits: [{ oldText: "original", newText: "bad" }] })).errorCode, "hardlink_denied");
});
test("JSON patch preserves untouched text and validates explicit schema before apply", async t => {
  const f = fixture(t);
  const text = '\uFEFF{\r\n  "n": 2, "label" : "001", "keep": [ true, null ]\r\n}\r\n';
  f.put("Assets/data.json", text);
  f.put("Assets/schema.json", JSON.stringify({ type: "object", properties: { n: { type: "integer", maximum: 10 } }, required: ["n"] }));
  const read = await f.call("structured_data_read", { path: "Assets/data.json", format: "json" });
  const args = { path: "Assets/data.json", format: "json", receipt: read.receipt, schemaPath: "Assets/schema.json", changes: [{ op: "replace", path: ["n"], value: 11 }] };
  assert.equal((await f.call("structured_data_patch", args)).errorCode, "schema_invalid"); assert.equal(f.get(args.path), text);
  args.changes[0].value = 4;
  assert.equal((await f.call("structured_data_patch", args)).status, "applied");
  assert.equal(f.get(args.path), text.replace('"n": 2', '"n": 4'));
});
test("CSV preserves BOM, quoting, CRLF, leading zeros and untouched fields", async t => {
  const f = fixture(t);
  const original = '\uFEFFid;label;amount\r\n001;"a; b";0004\r\n002;"line\r\nbreak";03\r\n';
  f.put("Assets/table.csv", original);
  const csv = { header: true, delimiter: ";", keyColumn: "id", duplicateKey: "error", missingKey: "error" };
  const read = await f.call("structured_data_read", { path: "Assets/table.csv", format: "csv", csv });
  assert.equal(read.items[0].values.id, "001"); assert.equal(read.items[1].values.label, "line\r\nbreak");
  const changed = await f.call("structured_data_patch", { path: "Assets/table.csv", format: "csv", csv, receipt: read.receipt, cells: [{ key: "001", column: "label", value: "hello" }] });
  assert.equal(changed.status, "applied"); assert.equal(f.get("Assets/table.csv"), original.replace('"a; b"', '"hello"'));
  f.put("Assets/table.csv", "id;x\n001;a\n001;b\n");
  const duplicate = await f.call("structured_data_read", { path: "Assets/table.csv", format: "csv", csv });
  assert.equal((await f.call("structured_data_patch", { path: "Assets/table.csv", format: "csv", csv, receipt: duplicate.receipt, cells: [{ key: "001", column: "x", value: "c" }] })).errorCode, "duplicate_key");
});
test("query cursor detects changed state and UTF-8 budget is enforced", async t => {
  const f = fixture(t); f.put("Assets/data.json", '["a","b","c"]');
  const query = { path: "Assets/data.json", format: "json", limit: 1 };
  const first = await f.call("structured_data_read", query);
  f.put(query.path, '["a","c"]');
  assert.equal((await f.call("structured_data_read", { ...query, cursor: first.nextCursor })).errorCode, "snapshot_changed");
  f.put("Assets/large.txt", "한".repeat(3000));
  assert.equal((await f.call("read_file", { path: "Assets/large.txt", byteBudget: 1024 })).errorCode, "response_budget_exceeded");
});
test("RPC handshake binds identity and never emits discovery token", async t => {
  const f = fixture(t); let requests = 0;
  const secret = "a".repeat(64);
  const server = net.createServer(socket => {
    let input = "";
    socket.on("data", bytes => { input += bytes; if (!input.endsWith("\n")) return; requests++; const r = JSON.parse(input);
      assert.equal(r.token, secret);
      socket.end(JSON.stringify({ requestId: r.requestId, editorSessionId: "session", domainGeneration: 1, projectIdentity: f.policy.projectIdentity, status: "observed", capabilities: {} }) + "\n");
    });
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.close());
  fs.writeFileSync(f.policy.discovery, JSON.stringify({ protocolVersion: 1, canonicalProjectRoot: f.root, projectIdentity: f.policy.projectIdentity,
    processId: process.pid, editorSessionId: "session", domainGeneration: 1, port: server.address().port, token: secret }), { mode: 0o600 });
  const bridge = new BridgeClient(f.policy);
  const result = await bridge.call("unity_status", {});
  assert.equal(requests, 1); assert(!JSON.stringify(result).includes(secret));
  const discovery = JSON.parse(fs.readFileSync(f.policy.discovery)); discovery.canonicalProjectRoot = f.root + "-copy";
  fs.writeFileSync(f.policy.discovery, JSON.stringify(discovery));
  await assert.rejects(() => bridge.call("unity_status", {}), { code: "binding_mismatch" });
  assert.equal(requests, 1);
});
test("real MCP stdio initialize/list/call preserves structured offline evidence", async t => {
  const f = fixture(t);
  const transport = new StdioClientTransport({ command: process.execPath, args: [path.resolve(__dirname, "../src/server.js")], env: { ...process.env, UNITY_PROJECT_ROOT: f.root, ALLOW_WRITE: "0" }, stderr: "pipe" });
  const client = new Client({ name: "unity-integration-test", version: "1" });
  t.after(async () => { await client.close(); });
  await client.connect(transport);
  const catalog = await client.listTools();
  assert.equal(catalog.tools.length, f.tools.length);
  const status = await client.callTool({ name: "unity_status", arguments: {} });
  assert.equal(status.structuredContent.connection, "disconnected");
  assert.equal(status.structuredContent.projectIdentity, f.policy.projectIdentity);
  assert(catalog.tools.some(tool => tool.name === "list_directory"));
  f.put("Assets/Listed.cs", "class Listed {}");
  const listing = await client.callTool({ name: "list_directory", arguments: { path: "Assets", kind: "files" } });
  assert(listing.structuredContent.items.some(item => item.path === "Assets/Listed.cs"));
  const search = await client.callTool({ name: "search_files", arguments: { path: "Assets", extensions: [".cs"] } });
  assert(search.structuredContent.items.some(item => item.path === "Assets/Listed.cs"));
  const rejected = await client.callTool({ name: "create_file", arguments: { path: "Assets/Denied.cs", mustNotExist: true, content: "class Denied {}" } });
  assert.equal(rejected.isError, true); assert.equal(rejected.structuredContent.errorCode, "edit_disabled");
});

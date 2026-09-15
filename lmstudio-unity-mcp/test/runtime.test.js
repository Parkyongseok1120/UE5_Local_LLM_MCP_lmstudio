"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
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
  assert.equal(result.compilation, "unknown"); assert.equal(result.import, "not_requested");
});
test("permissions, create precondition, exact-match edits, schema validation", async t => {
  const f = fixture(t, false);
  assert.equal((await f.call("create_file", { path: "Assets/New.cs", content: "", mustNotExist: true })).errorCode, "edit_disabled");
  const g = fixture(t);
  assert.equal((await g.call("create_file", { path: "Assets/New.cs", content: "x", mustNotExist: true })).status, "applied");
  assert.equal((await g.call("create_file", { path: "Assets/New.cs", content: "y", mustNotExist: true })).errorCode, "already_exists");
  assert.equal(g.get("Assets/New.cs"), "x");
  assert.equal((await g.call("read_file", { path: "Assets/New.cs", limit: 999999 })).errorCode, "invalid_arguments");
});
test("symlinks, hardlinks, protected formats and new-file parent checks", async t => {
  const f = fixture(t);
  const external = fs.mkdtempSync(path.join(os.tmpdir(), "unity-external-"));
  t.after(() => fs.rmSync(external, { recursive: true, force: true }));
  fs.symlinkSync(external, path.join(f.root, "Assets/Link"), "dir");
  assert.equal((await f.call("create_file", { path: "Assets/Link/x.cs", content: "bad", mustNotExist: true })).errorCode, "symlink_denied");
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
  const rejected = await client.callTool({ name: "create_file", arguments: { path: "Assets/Denied.cs", mustNotExist: true, content: "class Denied {}" } });
  assert.equal(rejected.isError, true); assert.equal(rejected.structuredContent.errorCode, "edit_disabled");
});

"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), os = require("node:os"), path = require("node:path"), { spawnSync } = require("node:child_process");
const configured = process.env.UNITY_DOTNET && process.env.UNITY_SYMBOL_WORKER && process.env.UNITY_SYMBOL_TEST_RUNTIME;
test("real semantic regression: enum/local symbols, explicit constructors/operators/indexers and property override without synthetic diagnostics", { skip: !configured }, t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "semantic spaces-")); t.after(() => fs.rmSync(root, { recursive: true }));
  const file = path.join(root, "Fixture.cs");
  fs.writeFileSync(file, `namespace Audit {
    public enum Mode { Idle, Busy }
    public class Base { public virtual int Value => 1; }
    public class Box : Base {
      public Box() {} public override int Value => 2; public int this[int index] => index;
      public static Box operator +(Box a, Box b) => a;
      public static int Run() { int Local() => 1; var a = new Box(); var b = a + new Box(); return a[1] + Local() + (int)Mode.Busy; }
    }
  }`);
  const references = fs.readdirSync(process.env.UNITY_SYMBOL_TEST_RUNTIME).filter(f => f.endsWith(".dll")).map(f => path.join(process.env.UNITY_SYMBOL_TEST_RUNTIME, f));
  const manifest = { canonicalProjectRoot: root, projectIdentity: "test", editorSessionId: "session", domainGeneration: 1, compilationId: "compile", manifestId: "manifest", selectedAssemblies: ["Audit"], assemblies: [{ name: "Audit", outputPath: path.join(root, "Audit.dll"), sources: [{ path: file, projectPath: "Assets/Fixture.cs", guid: "fixture" }], references, defines: [], languageVersion: "9.0" }] };
  const r = spawnSync(process.env.UNITY_DOTNET, [process.env.UNITY_SYMBOL_WORKER], { input: JSON.stringify(manifest), encoding: "utf8", maxBuffer: 64 * 1024 * 1024, timeout: 90000 });
  assert.equal(r.status, 0, r.stderr); const index = JSON.parse(r.stdout);
  assert(!index.diagnostics.some(d => ["CS2008", "CS1562"].includes(d.id)), JSON.stringify(index.diagnostics));
  assert.equal(index.completeWithinScope, true, JSON.stringify(index.omissions));
  for (const name of ["Idle", "Busy", "Local"]) assert(index.symbols.some(s => s.name === name), name);
  for (const [id, count] of [["Audit|M:Audit.Box.#ctor", 2], ["Audit|M:Audit.Box.op_Addition(Audit.Box,Audit.Box)", 1], ["Audit|P:Audit.Box.Item(System.Int32)", 1]]) assert.equal(index.uses.filter(u => u.to === id).length, count, id);
  assert(index.relations.some(r => r.kind === "overrides" && r.from === "Audit|P:Audit.Box.Value"));
  assert(!fs.existsSync(path.join(root, "__semantic_only__.dll")), "analysis must not emit a project assembly");
});
test("symbol queue reserves before asynchronous manifest export and releases after failure", async () => {
  const { Symbols } = require("../src/symbols"); let resolveExport;
  const client = new Symbols({}, { call: () => new Promise(resolve => { resolveExport = resolve; }) }, { UNITY_DOTNET: "configured", UNITY_SYMBOL_WORKER: "configured" });
  const first = client.call({ action: "index", assemblies: ["Game"] });
  await assert.rejects(client.call({ action: "index", assemblies: ["Game"] }), { code: "analysis_busy" });
  resolveExport({ errorCode: "editor_busy" }); assert.equal((await first).errorCode, "editor_busy"); assert.equal((await client.call({ action: "status" })).job.status, "failed");
});
test("closing during manifest export prevents late worker creation", async () => {
  const { Symbols } = require("../src/symbols"); let resolveExport, spawned = 0;
  const client = new Symbols({}, { call: () => new Promise(resolve => { resolveExport = resolve; }) },
    { UNITY_DOTNET: "configured", UNITY_SYMBOL_WORKER: "configured" }, { spawn() { spawned++; } });
  const pending = client.call({ action: "index", assemblies: ["Game"] });
  client.close(); resolveExport({ manifestId: "late" });
  await assert.rejects(pending, { code: "request_cancelled" });
  assert.equal(spawned, 0); assert.equal(client.job.status, "cancelled");
});
test("worker cancellation retains reservation until close and rejects late index", async t => {
  const { Symbols } = require("../src/symbols"), { EventEmitter } = require("node:events");
  const root = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "symbol-lifecycle-")));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(path.join(root, "analysis-manifest.json"), JSON.stringify({ manifestId: "m", projectIdentity: "p" }));
  const child = new EventEmitter(); child.pid = 123;
  child.stdout = new EventEmitter(); child.stderr = new EventEmitter(); child.stdin = new EventEmitter();
  const unreferenced = new Set();
  for (const handle of [child, child.stdin, child.stdout, child.stderr]) handle.unref = () => unreferenced.add(handle);
  child.stdin.end = () => {}; let killed = 0; child.kill = () => { killed++; return true; };
  const client = new Symbols({ stateRoot: root, projectIdentity: "p" }, { call: async () => ({ manifestId: "m" }) },
    { UNITY_DOTNET: "configured", UNITY_SYMBOL_WORKER: "configured" }, { spawn: () => child, shutdownMs: 5 });
  const controller = new AbortController();
  const result = await client.call({ action: "index" }, { signal: controller.signal });
  assert.equal(result.status, "accepted"); controller.abort();
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(client.job.status, "outcome_unknown"); assert.equal(client.job.terminationConfirmed, false);
  assert.equal(unreferenced.size, 4);
  await assert.rejects(client.call({ action: "index" }), { code: "analysis_busy" });
  child.stdout.emit("data", Buffer.from(JSON.stringify({ projectIdentity: "p", manifestId: "m", indexVersion: "late" })));
  child.emit("close", 0);
  assert.equal(client.index, null); assert.equal(client.job.status, "cancelled");
  assert.equal(client.job.terminationConfirmed, true); assert.equal(client.worker, null); assert.equal(killed, 1);
});
test("real external Roslyn: defines, partial compile error, declarations, binding, inheritance and interface implementations", { skip: !configured }, () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "evidence-symbol-test-"));
  const file = path.join(root, "Fixture.cs");
  fs.writeFileSync(file, "#if FEATURE\nnamespace Game { public interface I { int Run(); } public class Base { public int scale; } public class Child : Base, I { public int Run() => scale; public MissingType broken; } }\n#else\nclass WrongBranch {}\n#endif\n");
  const references = fs.readdirSync(process.env.UNITY_SYMBOL_TEST_RUNTIME).filter(f => f.endsWith(".dll")).map(f => path.join(process.env.UNITY_SYMBOL_TEST_RUNTIME, f));
  const manifest = { canonicalProjectRoot: root, projectIdentity: "test", editorSessionId: "session", domainGeneration: 1, compilationId: "compile", manifestId: "manifest", selectedAssemblies: ["Game"], assemblies: [{ name: "Game", outputPath: path.join(root, "Game.dll"), sources: [{ path: file, projectPath: "Assets/Fixture.cs", guid: "script-guid" }], references, defines: ["FEATURE"], languageVersion: "9.0" }] };
  const result = spawnSync(process.env.UNITY_DOTNET, [process.env.UNITY_SYMBOL_WORKER], { input: JSON.stringify(manifest), encoding: "utf8", maxBuffer: 64 * 1024 * 1024, timeout: 90000 });
  assert.equal(result.status, 0, result.stderr); const index = JSON.parse(result.stdout);
  assert.equal(index.completeWithinScope, false); assert(index.diagnostics.some(d => d.severity === "Error" && d.message.includes("MissingType")));
  const field = index.symbols.find(s => s.name === "scale"); assert(field); assert.equal(field.declarations[0].scriptGuid, "script-guid"); assert.match(field.declarations[0].sourceHash, /^[a-f0-9]{64}$/);
  assert(index.uses.some(u => u.to === field.id)); assert(index.relations.some(r => r.kind === "implements_member")); assert(index.relations.some(r => r.kind === "inherits" && r.to.includes("Game.Base"))); assert(!index.symbols.some(s => s.name === "WrongBranch"));
  fs.rmSync(root, { recursive: true }); // Exact mkdtemp-owned fixture only.
});

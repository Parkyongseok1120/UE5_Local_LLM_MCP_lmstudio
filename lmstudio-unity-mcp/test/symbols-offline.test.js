"use strict";

// The last completed symbol index answers without an Editor, says it is
// persisted, and marks declarations whose files changed. Declarations name a
// readable uri, and source reads the declaration through the file policy.

const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createRuntime } = require("../src/server");

const sha = text => crypto.createHash("sha256").update(text).digest("hex");

function fixture(t) {
  const root = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "unity-symbols-")));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (const folder of ["Assets/Game", "Packages/com.local.embedded/Runtime", "ProjectSettings", "Library/EvidenceFirst"])
    fs.mkdirSync(path.join(root, folder), { recursive: true });
  fs.writeFileSync(path.join(root, "Packages/manifest.json"), "{}");
  fs.writeFileSync(path.join(root, "ProjectSettings/ProjectVersion.txt"), "m_EditorVersion: 6000.3.14f1");
  const player = "namespace Game {\n  public class Player {\n    public void TakeHit(int amount) {\n      health -= amount;\n    }\n    int health;\n  }\n}\n";
  const helper = "namespace Embedded { public static class Helper { } }\n";
  fs.writeFileSync(path.join(root, "Assets/Game/Player.cs"), player);
  fs.writeFileSync(path.join(root, "Packages/com.local.embedded/Runtime/Helper.cs"), helper);
  const env = { UNITY_PROJECT_ROOT: root, UNITY_DOTNET: "configured", UNITY_SYMBOL_WORKER: "configured" };
  const runtime = createRuntime(env);
  t.after(() => runtime.close());
  const index = { indexVersion: "v1", manifestId: "m", projectIdentity: runtime.policy.projectIdentity, compilationId: "c",
    editorSessionId: "s", domainGeneration: 1, observedAt: "t", scope: ["Game"], completeWithinScope: true, compilerVersion: "x",
    omissions: [], diagnostics: [], versions: [], uses: [], relations: [], referenceCoverage: {},
    symbols: [
      { id: "Game|T:Game.Player", name: "Player", display: "Game.Player", kind: "NamedType",
        declarations: [{ path: "Assets/Game/Player.cs", line: 2, column: 16, sourceHash: sha(player) }] },
      { id: "Game|M:Game.Player.TakeHit(System.Int32)", name: "TakeHit", display: "Game.Player.TakeHit(int)", kind: "Method",
        containingSymbol: "Game|T:Game.Player", declarations: [{ path: "Assets/Game/Player.cs", line: 3, column: 17, sourceHash: sha(player) }] },
      { id: "Game|T:Embedded.Helper", name: "Helper", display: "Embedded.Helper", kind: "NamedType",
        declarations: [{ path: "Packages/com.local.embedded/Runtime/Helper.cs", line: 1, column: 41, sourceHash: sha(helper) }] },
      { id: "Game|T:Cached.Thing", name: "Thing", display: "Cached.Thing", kind: "NamedType",
        declarations: [{ path: "Packages/com.example.cached/Runtime/Thing.cs", line: 4, column: 1, sourceHash: "0".repeat(64) }] },
      { id: "Game|T:UnityEngine.Object", name: "Object", display: "UnityEngine.Object", kind: "NamedType", declarations: [] },
    ] };
  const dir = path.join(runtime.policy.stateRoot, "symbol-index");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "latest.json"), JSON.stringify(index));
  return { root, ...runtime };
}

test("a persisted index answers offline with match modes, readable uris and staleness", async t => {
  const f = fixture(t);
  const status = await f.call("unity_symbols", { action: "status" });
  assert.equal(status.indexVersion, "v1");
  assert.equal(status.indexSource, "persisted");
  const exact = await f.call("unity_symbols", { action: "find", indexVersion: "v1", name: "Game.Player.TakeHit" });
  assert.deepEqual(exact.items.map(item => item.name), ["TakeHit"]);
  assert.equal(exact.indexSource, "persisted");
  assert.equal(exact.items[0].declarations[0].uri, "Assets/Game/Player.cs");
  assert.equal(exact.items[0].declarations[0].stale, false);
  assert.equal(exact.items[0].declarations[0].path, undefined);
  const prefix = await f.call("unity_symbols", { action: "find", indexVersion: "v1", name: "Game.Pl", match: "prefix" });
  assert.deepEqual(prefix.items.map(item => item.name), ["Player", "TakeHit"]);
  const contains = await f.call("unity_symbols", { action: "find", indexVersion: "v1", name: "elp", match: "contains" });
  assert.deepEqual(contains.items.map(item => item.declarations[0].uri), ["Packages/com.local.embedded/Runtime/Helper.cs"]);
  const cached = await f.call("unity_symbols", { action: "find", indexVersion: "v1", name: "Thing" });
  assert.equal(cached.items[0].declarations[0].uri, null);
  assert.ok(cached.items[0].declarations[0].uriReason);
  fs.appendFileSync(path.join(f.root, "Assets/Game/Player.cs"), "// edited\n");
  const changed = await f.call("unity_symbols", { action: "find", indexVersion: "v1", name: "Player" });
  assert.equal(changed.items[0].declarations[0].stale, true);
});

test("source reads the declaration lines and says whether they still match the index", async t => {
  const f = fixture(t);
  const source = await f.call("unity_symbols", { action: "source", indexVersion: "v1", symbolId: "Game|M:Game.Player.TakeHit(System.Int32)", limit: 4 });
  assert.equal(source.errorCode, undefined, JSON.stringify(source));
  assert.equal(source.path, "Assets/Game/Player.cs");
  assert.equal(source.startLine, 1);
  assert.match(source.text, /public void TakeHit\(int amount\)/);
  assert.equal(source.declarationLine, 3);
  assert.equal(source.sourceMatchesIndex, true);
  assert.equal(source.symbol.display, "Game.Player.TakeHit(int)");
  const compiled = await f.call("unity_symbols", { action: "source", indexVersion: "v1", symbolId: "Game|T:UnityEngine.Object" });
  assert.equal(compiled.errorCode, "source_unavailable");
  const unknown = await f.call("unity_symbols", { action: "source", indexVersion: "v1", symbolId: "Game|T:Nope" });
  assert.equal(unknown.errorCode, "symbol_not_in_index");
  const offline = await f.call("unity_status");
  assert.ok(offline.offlineTools.includes("unity_symbols"));
});

test("source metadata fits with the body and a valid continuation", async t => {
 const f=fixture(t); fs.writeFileSync(path.join(f.root,"Assets/Game/Player.cs"), Array.from({length:50},(_,i)=>`// line ${i} `+"x".repeat(50)).join("\n"));
 for (const byteBudget of [2048,4096]) {
  const r=await f.call("unity_symbols",{action:"source",indexVersion:"v1",symbolId:"Game|T:Game.Player",byteBudget});
  assert.ok(r.text); assert.equal(r.delivery,undefined); assert.ok(Buffer.byteLength(JSON.stringify(r))<=byteBudget);
  assert.equal(r.suggestion.tool,"read_file"); const next=await f.call(r.suggestion.tool,r.suggestion.args); assert.ok(next.startLine>r.startLine);
 }
});

"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createRuntime } = require("../src/server");
const { Files } = require("../../shared-tool-core/files");

function fixture(t) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "unity-discovery-test-")));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (const folder of ["Assets", "Packages", "ProjectSettings", "Assets/Code", "Assets/Other"])
    fs.mkdirSync(path.join(root, folder), { recursive: true });
  fs.writeFileSync(path.join(root, "Packages/manifest.json"), "{}");
  fs.writeFileSync(path.join(root, "ProjectSettings/ProjectVersion.txt"), "m_EditorVersion: 2022.3.0f1");
  let bridgeCalls = 0;
  const runtime = createRuntime({ UNITY_PROJECT_ROOT: root, ALLOW_WRITE: "0" }, { call: async () => { bridgeCalls++; throw Error("Bridge must not be called"); } });
  return {
    ...runtime,
    put: (relative, value) => fs.writeFileSync(path.join(root, relative), value),
    mkdir: relative => fs.mkdirSync(path.join(root, relative), { recursive: true }),
    bridgeCalls: () => bridgeCalls,
  };
}

test("offline directory listing stays shallow and filters direct children", async t => {
  const f = fixture(t);
  f.put("Assets/Code/Main.cs", "class Main {}");
  f.put("Assets/Code/Main.cs.meta", "guid: main");
  f.mkdir("Assets/Code/Nested");
  f.put("Assets/Code/Nested/Hidden.cs", "class Hidden {}");
  const dirs = await f.call("list_directory", { path: "Assets/Code", kind: "directories" });
  assert.deepEqual(dirs.items, [{ path: "Assets/Code/Nested", kind: "directory" }]);
  const files = await f.call("list_directory", { path: "Assets/Code", kind: "files", limit: 1 });
  assert.deepEqual(files.items, [{ path: "Assets/Code/Main.cs", kind: "file" }]);
  assert.equal(files.total, 2);
  const rest = await f.call("list_directory", { path: "Assets/Code", kind: "files", limit: 1, cursor: files.nextCursor });
  assert.deepEqual(rest.items, [{ path: "Assets/Code/Main.cs.meta", kind: "file" }]);
  assert.equal(rest.nextCursor, null);
  const all = await f.call("list_directory", { path: "Assets/Code" });
  assert.equal(all.total, 3);
  assert.equal((await f.call("list_directory", { path: "Assets/Other" })).total, 0);
  assert.equal((await f.call("list_directory", { path: "Assets/Code/Main.cs" })).errorCode, "not_directory");
  assert.equal((await f.call("list_directory", { path: "Assets/../Library" })).errorCode, "invalid_path");
  assert.equal(f.bridgeCalls(), 0);
});

test("search combines exact extension choices with path query without reading bodies", async t => {
  const f = fixture(t);
  f.put("Assets/Code/Recipe.cs", Buffer.from([0xff, 0xfe]));
  f.put("Assets/Code/Recipe.cs.meta", "guid: recipe");
  f.put("Assets/Code/Recipe.json", '{"name":"Recipe"}');
  f.put("Assets/Code/Recipe.prefab", "prefab");
  f.put("Assets/Code/Other.cs", "Recipe");
  const mixed = await f.call("search_files", { path: "Assets/Code", query: "Recipe", extensions: [".CS", ".json", ".prefab"] });
  assert.deepEqual(mixed.items.map(x => x.path), ["Assets/Code/Recipe.cs", "Assets/Code/Recipe.json", "Assets/Code/Recipe.prefab"]);
  assert(mixed.items.every(x => x.kind === "path"));
  assert.equal(mixed.incomplete, false);
  const kinds = await f.call("search_files", { path: "Assets/Code", extensions: [".cs", ".json"] });
  assert.deepEqual(kinds.items.map(x => x.path), ["Assets/Code/Other.cs", "Assets/Code/Recipe.cs", "Assets/Code/Recipe.json"]);
  const meta = await f.call("search_files", { path: "Assets/Code", extensions: [".meta"] });
  assert.deepEqual(meta.items.map(x => x.path), ["Assets/Code/Recipe.cs.meta"]);
  const old = await f.call("search_files", { path: "Assets/Code", query: ".cs" });
  assert(old.items.some(x => x.path === "Assets/Code/Recipe.cs.meta"));
  assert.equal(f.bridgeCalls(), 0);
});

test("content search requires a query and remains independently scoped", async t => {
  const f = fixture(t);
  f.put("Assets/Code/One.cs", "needle");
  f.put("Assets/Code/One.json", "needle");
  f.put("Assets/Other/Two.cs", "needle");
  for (const args of [{}, { extensions: [] }, { content: true, extensions: [".cs"] }, { query: "", extensions: [".cs"] }, { extensions: ["*.cs"] }])
    assert.equal((await f.call("search_files", { path: "Assets", ...args })).errorCode, "invalid_arguments");
  const [code, json] = await Promise.all([
    f.call("search_files", { path: "Assets/Code", extensions: [".cs"], query: "needle", content: true }),
    f.call("search_files", { path: "Assets/Code", extensions: [".json"], query: "needle", content: true }),
  ]);
  assert.deepEqual(code.items.map(x => x.path), ["Assets/Code/One.cs"]);
  assert.deepEqual(json.items.map(x => x.path), ["Assets/Code/One.json"]);
  assert.equal(code.items[0].line, 1);
  assert.equal(f.bridgeCalls(), 0);
});

test("pages are scoped to the exact query and do not imply a file type is absent", async t => {
  const f = fixture(t);
  f.put("Assets/Code/A.cs", "A");
  f.put("Assets/Code/B.cs", "B");
  f.put("Assets/Code/C.json", "C");
  const args = { path: "Assets/Code", extensions: [".cs", ".json"], limit: 2 };
  const first = await f.call("search_files", args);
  assert.deepEqual(first.items.map(x => x.path), ["Assets/Code/A.cs", "Assets/Code/B.cs"]);
  assert.equal(first.total, 3);
  assert.equal(first.truncated, true);
  assert.equal(first.incomplete, false);
  const second = await f.call("search_files", { ...args, cursor: first.nextCursor });
  assert.deepEqual(second.items.map(x => x.path), ["Assets/Code/C.json"]);
  assert.equal((await f.call("search_files", { ...args, extensions: [".cs"], cursor: first.nextCursor })).errorCode, "snapshot_changed");
  assert.equal((await f.call("search_files", { ...args, path: "Assets/Other", cursor: first.nextCursor })).errorCode, "snapshot_changed");
  assert.equal((await f.call("search_files", { ...args, query: "Assets", cursor: first.nextCursor })).errorCode, "snapshot_changed");
  assert.equal((await f.call("list_directory", { path: "Assets/Code", cursor: first.nextCursor })).errorCode, "snapshot_changed");
  const listing = await f.call("list_directory", { path: "Assets/Code", limit: 1 });
  assert.equal((await f.call("list_directory", { path: "Assets/Code", kind: "files", limit: 1, cursor: listing.nextCursor })).errorCode, "snapshot_changed");
  f.put("Assets/Code/D.cs", "D");
  assert.equal((await f.call("search_files", { ...args, cursor: first.nextCursor })).errorCode, "snapshot_changed");
  assert.equal(f.bridgeCalls(), 0);
});

test("a finished result page does not hide an unfinished scan", async t => {
  const names = Array.from({ length: 5001 }, (_, i) => `${String(i).padStart(4, "0")}.cs`);
  t.mock.method(fs, "readdirSync", () => names.map(name => ({
    name,
    isSymbolicLink: () => false,
  })));
  t.mock.method(fs, "lstatSync", relative => ({
    isDirectory: () => relative === "Assets",
    isFile: () => relative !== "Assets",
    isSymbolicLink: () => false,
  }));
  const files = new Files({ root: "fake-project", projectIdentity: "fake-project-id", resolve: relative => relative });
  const result = await files.search({ path: "Assets", query: "0000.cs", limit: 1 });
  assert.equal(result.total, 1);
  assert.equal(result.scanned, 5000);
  assert.equal(result.truncated, false);
  assert.equal(result.nextCursor, null);
  assert.equal(result.incomplete, true);
});

test("parallel path searches validate the search root without re-resolving every descendant", async t => {
  const f = fixture(t);
  for (let i = 0; i < 100; i++) f.put(`Assets/Code/File${i}.cs`, `class File${i} {}`);
  const originalResolve = f.policy.resolve;
  let resolveCalls = 0;
  f.policy.resolve = (...args) => {
    resolveCalls++;
    return originalResolve(...args);
  };
  const [files, named] = await Promise.all([
    f.call("search_files", { path: "Assets", extensions: [".cs"] }),
    f.call("search_files", { path: "Assets", query: "File9", extensions: [".cs"] }),
  ]);
  assert.equal(files.total, 100);
  assert.equal(named.total, 11);
  assert.equal(resolveCalls, 2);
});

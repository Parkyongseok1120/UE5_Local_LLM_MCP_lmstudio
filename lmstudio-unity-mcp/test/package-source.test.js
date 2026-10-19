"use strict";
// package://<name>/<path> is read-only evidence of an installed package's own source.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createRuntime } = require("../src/server");

function fixture(t, dependencies) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "unity-package-test-")));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (const folder of ["Assets", "Packages", "ProjectSettings"]) fs.mkdirSync(path.join(root, folder), { recursive: true });
  fs.writeFileSync(path.join(root, "Packages/manifest.json"), "{}");
  fs.writeFileSync(path.join(root, "ProjectSettings/ProjectVersion.txt"), "m_EditorVersion: 6000.0.1f1");
  fs.writeFileSync(path.join(root, "Packages/packages-lock.json"), JSON.stringify({ dependencies }));
  const put = (relative, value) => {
    fs.mkdirSync(path.dirname(path.join(root, relative)), { recursive: true });
    fs.writeFileSync(path.join(root, relative), value);
  };
  let bridgeCalls = 0;
  const runtime = createRuntime({ UNITY_PROJECT_ROOT: root, ALLOW_WRITE: "1" },
    { call: async () => { bridgeCalls++; throw Error("Bridge must not be called"); } });
  return { ...runtime, root, put, bridgeCalls: () => bridgeCalls };
}

const LOCK = {
  "com.example.cached": { version: "1.2.3", depth: 0, source: "registry" },
  "com.example.embedded": { version: "file:com.example.embedded", depth: 0, source: "embedded" },
  "com.example.local": { version: "file:../LocalPkg", depth: 0, source: "local" },
  "com.example.builtin": { version: "1.0.0", depth: 0, source: "builtin" },
  "com.example.missing": { version: "9.9.9", depth: 0, source: "registry" },
};

test("package:// reads a registry package from the cache without the Editor", async t => {
  const f = fixture(t, LOCK);
  f.put("Library/PackageCache/com.example.cached@1.2.3/Runtime/Thing.cs", "public class Thing { }\nline2\n");
  f.put("Library/PackageCache/com.example.cached@1.2.3/package.json", "{}");
  const read = await f.call("read_file", { path: "package://com.example.cached/Runtime/Thing.cs" });
  assert.equal(read.status, "observed");
  assert.equal(read.path, "package://com.example.cached/Runtime/Thing.cs");
  assert.match(read.text, /public class Thing/);
  const listing = await f.call("list_directory", { path: "package://com.example.cached" });
  assert.deepEqual(listing.items.map(i => i.path).sort(), ["package://com.example.cached/Runtime", "package://com.example.cached/package.json"]);
  const search = await f.call("search_files", { path: "package://com.example.cached", query: "Thing", content: true });
  assert.ok(search.items.some(i => i.path === "package://com.example.cached/Runtime/Thing.cs" && i.line === 1));
  assert.equal(f.bridgeCalls(), 0);
});

test("embedded and local packages resolve to their own folders", async t => {
  const f = fixture(t, LOCK);
  f.put("Packages/com.example.embedded/Editor/E.cs", "class E {}");
  f.put("LocalPkg/Runtime/L.cs", "class L {}");
  assert.match((await f.call("read_file", { path: "package://com.example.embedded/Editor/E.cs" })).text, /class E/);
  assert.match((await f.call("read_file", { path: "package://com.example.local/Runtime/L.cs" })).text, /class L/);
});

test("packages absent from packages-lock.json, built-in packages and uncached packages are refused with a reason", async t => {
  const f = fixture(t, LOCK);
  f.put("Library/PackageCache/com.example.other@1.0.0/Runtime/X.cs", "class X {}");
  assert.equal((await f.call("read_file", { path: "package://com.example.other/Runtime/X.cs" })).errorCode, "package_not_found");
  assert.equal((await f.call("read_file", { path: "package://com.example.builtin/Runtime/X.cs" })).errorCode, "package_source_unavailable");
  assert.equal((await f.call("read_file", { path: "package://com.example.missing/Runtime/X.cs" })).errorCode, "package_not_cached");
});

test("package reads allow only source-like extensions and reject traversal, hidden segments and write attempts", async t => {
  const f = fixture(t, LOCK);
  const base = "Library/PackageCache/com.example.cached@1.2.3";
  f.put(`${base}/Runtime/Thing.cs`, "class Thing {}");
  f.put(`${base}/Runtime/Thing.dll`, "binary");
  f.put(`${base}/Runtime/Thing.asmdef`, "{}");
  f.put(`${base}/.hidden/Secret.cs`, "class Secret {}");
  f.put("Assets/Outside.cs", "class Outside {}");
  assert.equal((await f.call("read_file", { path: "package://com.example.cached/Runtime/Thing.asmdef" })).status, "observed");
  assert.equal((await f.call("read_file", { path: "package://com.example.cached/Runtime/Thing.dll" })).errorCode, "path_denied");
  assert.equal((await f.call("read_file", { path: "package://com.example.cached/.hidden/Secret.cs" })).errorCode, "invalid_path");
  assert.equal((await f.call("read_file", { path: "package://com.example.cached/../../../../Assets/Outside.cs" })).errorCode, "invalid_path");
  assert.equal((await f.call("read_file", { path: "package://../Assets/Outside.cs" })).errorCode, "invalid_path");
  assert.equal((await f.call("read_file", { path: "package://com.example.cached\\Runtime\\Thing.cs" })).errorCode, "invalid_path");
  const read = await f.call("read_file", { path: "package://com.example.cached/Runtime/Thing.cs" });
  const patch = await f.call("patch_file", { path: "package://com.example.cached/Runtime/Thing.cs", receipt: read.receipt,
    edits: [{ oldText: "Thing", newText: "Other" }] });
  assert.equal(patch.errorCode, "path_denied");
  const create = await f.call("create_file", { path: "package://com.example.cached/Runtime/New.cs", content: "x", mustNotExist: true });
  assert.equal(create.errorCode, "path_denied");
  assert.equal(fs.readFileSync(path.join(f.root, base, "Runtime/Thing.cs"), "utf8"), "class Thing {}");
  assert.equal(fs.existsSync(path.join(f.root, base, "Runtime/New.cs")), false);
});

test("a symlink inside a package cannot reach files outside it", async t => {
  const f = fixture(t, LOCK);
  const base = "Library/PackageCache/com.example.cached@1.2.3";
  f.put(`${base}/Runtime/Thing.cs`, "class Thing {}");
  f.put("Assets/Outside.cs", "class Outside {}");
  try {
    fs.symlinkSync(path.join(f.root, "Assets/Outside.cs"), path.join(f.root, base, "Runtime/Leak.cs"), "file");
  } catch (error) {
    t.skip(`symlink creation unavailable: ${error.code}`);
    return;
  }
  assert.equal((await f.call("read_file", { path: "package://com.example.cached/Runtime/Leak.cs" })).errorCode, "symlink_denied");
});

test("without packages-lock.json package:// is unavailable rather than guessed", async t => {
  const f = fixture(t, LOCK);
  fs.rmSync(path.join(f.root, "Packages/packages-lock.json"));
  assert.equal((await f.call("read_file", { path: "package://com.example.cached/Runtime/Thing.cs" })).errorCode, "package_lock_unreadable");
});

test("a unique stale cached version is not substituted for the locked package", async t => {
  const f = fixture(t, LOCK);
  f.put("Library/PackageCache/com.example.cached@1.0.0/package.json",
    JSON.stringify({ name: "com.example.cached", version: "1.0.0" }));
  f.put("Library/PackageCache/com.example.cached@1.0.0/Runtime/Thing.cs", "class Stale {}");
  assert.equal((await f.call("read_file", { path: "package://com.example.cached/Runtime/Thing.cs" })).errorCode,
    "package_not_cached");
});

test("hashed cache folders require the locked package name and version in package.json", async t => {
  const f = fixture(t, LOCK);
  const base = "Library/PackageCache/com.example.cached@a1b2c3d4";
  f.put(`${base}/Runtime/Thing.cs`, "class Current {}");
  for (const manifest of [null, {}, { name: "com.other.package", version: "1.2.3" },
    { name: "com.example.cached", version: "1.0.0" }]) {
    if (manifest) f.put(`${base}/package.json`, JSON.stringify(manifest));
    assert.equal((await f.call("read_file", { path: "package://com.example.cached/Runtime/Thing.cs" })).errorCode,
      "package_not_cached");
  }
  f.put(`${base}/package.json`, JSON.stringify({ name: "com.example.cached", version: "1.2.3" }));
  assert.match((await f.call("read_file", { path: "package://com.example.cached/Runtime/Thing.cs" })).text, /Current/);
});

test("hashed cache selection skips stale versions and refuses ambiguous matching copies", async t => {
  const f = fixture(t, LOCK);
  for (const [suffix, version] of [["oldhash", "1.0.0"], ["newhash", "1.2.3"]]) {
    f.put(`Library/PackageCache/com.example.cached@${suffix}/package.json`,
      JSON.stringify({ name: "com.example.cached", version }));
    f.put(`Library/PackageCache/com.example.cached@${suffix}/Runtime/Thing.cs`, `class ${suffix} {}`);
  }
  assert.match((await f.call("read_file", { path: "package://com.example.cached/Runtime/Thing.cs" })).text, /newhash/);
  f.put("Library/PackageCache/com.example.cached@duplicate/package.json",
    JSON.stringify({ name: "com.example.cached", version: "1.2.3" }));
  f.put("Library/PackageCache/com.example.cached@duplicate/Runtime/Thing.cs", "class Different {}");
  assert.equal((await f.call("read_file", { path: "package://com.example.cached/Runtime/Thing.cs" })).errorCode,
    "package_not_cached");
});

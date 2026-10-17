"use strict";

// engine:// is read-only evidence of the installed engine's own source. The
// fake engine and project live in a temporary tree; no version or path is built in.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { createDirectRuntime } = require("../src/direct-server.js");
const { isAllowedEngineRelative } = require("../src/engine-source-access.js");

const ACTOR_CPP = [
  "float AActor::TakeDamage(float DamageAmount)",
  "{",
  "\tfloat ActualDamage = DamageAmount;",
  "\treturn ActualDamage;",
  "}",
  "",
].join("\n");

function payloadOf(result) {
  assert.ok(result?.structuredContent);
  return result.structuredContent;
}

function fixture(t, { buildVersion = { MajorVersion: 5, MinorVersion: 3, PatchVersion: 1 }, association = "" } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "engine-source-read-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const engineRoot = path.join(root, "AnyEngineFolder");
  const privateDir = path.join(engineRoot, "Engine", "Source", "Runtime", "Engine", "Private");
  fs.mkdirSync(privateDir, { recursive: true });
  fs.writeFileSync(path.join(privateDir, "Actor.cpp"), ACTOR_CPP, "utf8");
  fs.writeFileSync(path.join(privateDir, "Notes.md"), "not source", "utf8");
  fs.mkdirSync(path.join(engineRoot, "Engine", "Build", "BatchFiles"), { recursive: true });
  fs.writeFileSync(path.join(engineRoot, "Engine", "Build", "BatchFiles", "Build.bat"), "@echo off\r\n", "utf8");
  fs.writeFileSync(path.join(engineRoot, "Engine", "Build", "BatchFiles", "Build.sh"), "#!/bin/sh\n", "utf8");
  if (buildVersion) fs.writeFileSync(path.join(engineRoot, "Engine", "Build", "Build.version"), JSON.stringify(buildVersion), "utf8");
  const pluginSource = path.join(engineRoot, "Engine", "Plugins", "Runtime", "Demo", "Source", "Demo", "Private");
  fs.mkdirSync(pluginSource, { recursive: true });
  fs.writeFileSync(path.join(pluginSource, "Demo.cpp"), "int Demo() { return 1; }\n", "utf8");
  fs.mkdirSync(path.join(engineRoot, "Engine", "Plugins", "Runtime", "Demo", "Binaries"), { recursive: true });
  fs.writeFileSync(path.join(engineRoot, "Engine", "Plugins", "Runtime", "Demo", "Binaries", "Demo.json"), "{}", "utf8");
  fs.mkdirSync(path.join(engineRoot, "Engine", "Config"), { recursive: true });
  fs.writeFileSync(path.join(engineRoot, "Engine", "Config", "BaseEngine.ini"), "[Secret]\n", "utf8");

  const projectRoot = path.join(root, "Game");
  fs.mkdirSync(path.join(projectRoot, "Config"), { recursive: true });
  const projectFile = path.join(projectRoot, "Game.uproject");
  fs.writeFileSync(projectFile, JSON.stringify({ FileVersion: 3, EngineAssociation: association }), "utf8");
  fs.writeFileSync(path.join(projectRoot, "Config", "DefaultGame.ini"), "[A]\nB=1\n", "utf8");
  const stateRoot = path.join(root, "state");
  const runtime = createDirectRuntime({
    workspaceRoot: root,
    configPath: path.join(root, "agent-mcp.json"),
    stateRoot,
    env: { AGENT_STATE_ROOT: stateRoot, ALLOW_WRITE: "1", ALLOW_COMMANDS: "0", ALLOW_UNREAL_BUILD: "0",
      UNREAL_ENGINE_ROOT: engineRoot, SHARED_UNREAL_CONFIG: path.join(root, "shared.json") },
    getActiveProject: () => projectFile,
  });
  return { runtime, root, engineRoot, projectRoot, projectFile };
}

const owner = { sessionId: "engine-read" };

// A refusal is either a thrown error or an ok:false payload; both carry the reason text.
async function refusal(runtime, tool, args) {
  try {
    const payload = payloadOf(await runtime.callTool(tool, args, owner));
    assert.equal(payload.ok, false, `${tool} ${JSON.stringify(args)} must be refused`);
    return JSON.stringify(payload);
  } catch (error) {
    if (error.name === "AssertionError") throw error;
    return String(error.message || error);
  }
}

test("engine:// reads an implementation .cpp with the observed engine identity and no edit receipt", async (t) => {
  const { runtime } = fixture(t);
  const read = payloadOf(await runtime.callTool("read_file", { path: "engine://Engine/Source/Runtime/Engine/Private/Actor.cpp" }, owner));
  assert.equal(read.ok, true);
  assert.match(read.content, /ActualDamage = DamageAmount/);
  assert.equal(read.path, "engine://Engine/Source/Runtime/Engine/Private/Actor.cpp");
  assert.equal(read.evidenceSource, "installed_engine_source");
  assert.equal(read.readOnly, true);
  assert.equal(read.engineIdentity.status, "observed");
  assert.equal(read.engineIdentity.version, "5.3.1");
  assert.match(read.engineIdentity.sha256, /^[a-f0-9]{64}$/);
  assert.equal(read.fileVersionReceipt, undefined);
  const range = payloadOf(await runtime.callTool("read_file_range",
    { path: "engine://Engine/Source/Runtime/Engine/Private/Actor.cpp", startLine: 3, endLine: 4 }, owner));
  assert.equal(range.ok, true);
  assert.match(range.content, /ActualDamage/);
  assert.equal(range.evidenceSource, "installed_engine_source");
  assert.equal(range.fileVersionReceipt, undefined);
  const symbol = payloadOf(await runtime.callTool("read_symbol",
    { path: "engine://Engine/Source/Runtime/Engine/Private/Actor.cpp", symbol: "TakeDamage" }, owner));
  assert.equal(symbol.ok, true);
  assert.equal(symbol.engineIdentity.version, "5.3.1");
});

test("engine identity is reported unknown, not guessed, when Build.version is unreadable", async (t) => {
  const { runtime } = fixture(t, { buildVersion: null });
  const read = payloadOf(await runtime.callTool("read_file", { path: "engine://Engine/Source/Runtime/Engine/Private/Actor.cpp" }, owner));
  assert.equal(read.ok, true);
  assert.equal(read.engineIdentity.status, "unknown");
  assert.equal(read.engineIdentity.version, undefined);
});

test("plugin source and Build.version are readable; plugin binaries, config and non-source files are not", async (t) => {
  const { runtime } = fixture(t);
  const ok = async (p) => payloadOf(await runtime.callTool("read_file", { path: p }, owner));
  assert.equal((await ok("engine://Engine/Plugins/Runtime/Demo/Source/Demo/Private/Demo.cpp")).ok, true);
  const version = await ok("engine://Engine/Build/Build.version");
  assert.equal(version.ok, true);
  assert.match(version.content, /MajorVersion/);
  for (const denied of ["engine://Engine/Plugins/Runtime/Demo/Binaries/Demo.json", "engine://Engine/Config/BaseEngine.ini",
    "engine://Engine/Source/Runtime/Engine/Private/Notes.md", "engine://Engine/Binaries", "engine://Engine/../Secret.cpp",
    "engine://Game/Source/A.cpp"]) {
    assert.match(await refusal(runtime, "read_file", { path: denied }), /engine:\/\/|escapes|outside/, denied);
  }
});

test("engine:// is read-only: every mutation tool rejects it", async (t) => {
  const { runtime, engineRoot } = fixture(t);
  const target = "engine://Engine/Source/Runtime/Engine/Private/Actor.cpp";
  const before = fs.readFileSync(path.join(engineRoot, "Engine/Source/Runtime/Engine/Private/Actor.cpp"), "utf8");
  const attempts = [
    ["replace_in_file", { path: target, oldText: "ActualDamage", newText: "X", expectedOccurrences: 1 }],
    ["write_file", { path: target, content: "x" }],
    ["delete_file", { path: target }],
    ["apply_edit_bundle", { patches: [{ path: target, oldText: "a", newText: "b", expectedOccurrences: 1 }] }],
    ["write_file", { path: "engine://Engine/Source/New.cpp", content: "x" }],
  ];
  for (const [tool, args] of attempts) {
    let rejected = false;
    try {
      const result = await runtime.callTool(tool, args, owner);
      rejected = payloadOf(result).ok === false;
    } catch { rejected = true; }
    assert.equal(rejected, true, tool);
  }
  assert.equal(fs.readFileSync(path.join(engineRoot, "Engine/Source/Runtime/Engine/Private/Actor.cpp"), "utf8"), before);
  assert.equal(fs.existsSync(path.join(engineRoot, "Engine/Source/New.cpp")), false);
});

test("a symlink inside engine source cannot reach files outside the readable engine locations", async (t) => {
  const { runtime, engineRoot, root } = fixture(t);
  const link = path.join(engineRoot, "Engine", "Source", "Runtime", "Engine", "Private", "Leak.cpp");
  try {
    fs.symlinkSync(path.join(engineRoot, "Engine", "Config", "BaseEngine.ini"), link, "file");
  } catch (error) {
    t.skip(`symlink creation unavailable: ${error.code}`);
    return;
  }
  assert.match(await refusal(runtime, "read_file", { path: "engine://Engine/Source/Runtime/Engine/Private/Leak.cpp" }), /engine:\/\//);
  const outside = path.join(root, "outside.cpp");
  fs.writeFileSync(outside, "secret", "utf8");
  const escape = path.join(engineRoot, "Engine", "Source", "Escape.cpp");
  fs.symlinkSync(outside, escape, "file");
  assert.match(await refusal(runtime, "read_file", { path: "engine://Engine/Source/Escape.cpp" }), /escapes/);
});

test("list_directory and search_files work inside readable engine source only", async (t) => {
  const { runtime } = fixture(t);
  const listing = payloadOf(await runtime.callTool("list_directory", { path: "engine://Engine/Source/Runtime/Engine/Private" }, owner));
  assert.equal(listing.ok, true);
  assert.ok(listing.entries.some((entry) => entry.name === "Actor.cpp"));
  const search = payloadOf(await runtime.callTool("search_files",
    { query: "ActualDamage", path: "engine://Engine/Source/Runtime/Engine/Private" }, owner));
  assert.equal(search.ok, true);
  assert.ok(search.results.some((hit) => hit.uri === "engine://Engine/Source/Runtime/Engine/Private/Actor.cpp" && hit.line === 3));
  assert.match(await refusal(runtime, "list_directory", { path: "engine://Engine/Config" }), /engine:\/\//);
});

test("engine:// cannot be used as a command working directory", async (t) => {
  const { runtime } = fixture(t);
  const result = await runtime.callTool("run_command", { command: "git status", cwd: "engine://Engine/Source" }, owner).catch((e) => e);
  const payload = result instanceof Error ? { ok: false, error: result.message } : payloadOf(result);
  assert.equal(payload.ok, false);
});

test("engine directory discovery excludes files denied by the source read policy", async (t) => {
  const { runtime, engineRoot } = fixture(t);
  const directory = "Engine/Source/Runtime/Engine/Private";
  fs.writeFileSync(path.join(engineRoot, directory, "Notes.md"), "DENIED_SOURCE_MARKER", "utf8");
  fs.writeFileSync(path.join(engineRoot, directory, "Other.cpp"), "int Other;", "utf8");
  const first = payloadOf(await runtime.callTool("list_directory", {
    path: `engine://${directory}`, maxEntries: 1,
  }, owner));
  assert.equal(first.ok, true);
  assert.equal(first.omittedByPathPolicy, 1);
  assert.equal(first.totalEntries, 2);
  assert.equal(first.nextOffset, 1);
  const second = payloadOf(await runtime.callTool("list_directory", {
    path: `engine://${directory}`, maxEntries: 1, offset: first.nextOffset,
  }, owner));
  assert.deepEqual([...first.entries, ...second.entries].map(entry => entry.name), ["Actor.cpp", "Other.cpp"]);
  assert.equal(second.nextOffset, null);
  const searched = payloadOf(await runtime.callTool("search_files", {
    path: `engine://${directory}`, query: "DENIED_SOURCE_MARKER",
  }, owner));
  assert.equal(searched.ok, true);
  assert.deepEqual(searched.results, []);
  assert.equal(searched.excludedEntries, 1);
  assert.equal(searched.coverage, "partial");
});

test("engine source discovery skips a directory link into engine non-source folders", async (t) => {
  const { runtime, engineRoot } = fixture(t);
  const directory = "Engine/Source/Runtime/Engine/Private";
  try {
    fs.symlinkSync(path.join(engineRoot, "Engine/Config"), path.join(engineRoot, directory, "ConfigLink"),
      process.platform === "win32" ? "junction" : "dir");
  } catch (error) {
    t.skip(`directory symlink creation unavailable: ${error.code}`);
    return;
  }
  const listing = payloadOf(await runtime.callTool("list_directory", { path: `engine://${directory}` }, owner));
  assert.equal(listing.ok, true);
  assert.ok(listing.entries.every(entry => entry.name !== "ConfigLink" && entry.name !== "Notes.md"));
  assert.equal(listing.omittedByPathPolicy, 2);
  const search = payloadOf(await runtime.callTool("search_files", { path: `engine://${directory}`, query: "Secret" }, owner));
  assert.equal(search.ok, true);
  assert.deepEqual(search.results, []);
  assert.equal(search.excludedEntries, 2);
});

test("without a resolvable project engine the read fails with a clear reason, not a guessed engine", async (t) => {
  const { runtime, projectFile } = fixture(t, { association: "9.99" });
  fs.writeFileSync(projectFile, JSON.stringify({ FileVersion: 3, EngineAssociation: "9.99" }), "utf8");
  assert.match(await refusal(runtime, "read_file", { path: "engine://Engine/Source/Runtime/Engine/Private/Actor.cpp" }),
    /ENGINE_ASSOCIATION_UNRESOLVED|engine/i);
});

test("path policy accepts only readable engine source locations", () => {
  assert.equal(isAllowedEngineRelative("Engine/Source/Runtime/Core/Public/A.h", { isFile: true }), true);
  assert.equal(isAllowedEngineRelative("Engine/Source/Runtime/Core/Private/A.cpp", { isFile: true }), true);
  assert.equal(isAllowedEngineRelative("Engine/Source/Programs/X/X.Build.cs", { isFile: true }), true);
  assert.equal(isAllowedEngineRelative("Engine/Plugins/A/B/Source/C/Private/D.cpp", { isFile: true }), true);
  assert.equal(isAllowedEngineRelative("Engine/Source/Runtime/Foo.uasset", { isFile: true }), false);
  assert.equal(isAllowedEngineRelative("Engine/Plugins/A/Content/X.json", { isFile: true }), false);
  assert.equal(isAllowedEngineRelative("Engine/Binaries/Win64/UnrealEditor.exe", { isFile: true }), false);
  assert.equal(isAllowedEngineRelative("Engine/Build/Build.version", { isFile: true }), true);
  assert.equal(isAllowedEngineRelative("Engine/Build/Other.json", { isFile: true }), false);
  assert.equal(isAllowedEngineRelative("../Engine/Source/A.h", { isFile: true }), false);
  assert.equal(isAllowedEngineRelative("Engine/Source/Runtime/Core", { isFile: false }), true);
});

"use strict";

// Failure and partial results must give one safe, observed next step:
// suggestions name only paths that exist, never widen the path policy, and an
// identical failing retry is answered as a duplicate. Workspace, project and
// engine live in separate temporary folders, like a real installation.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { createDirectRuntime } = require("../src/direct-server.js");

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "failure-guidance-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const workspace = path.join(root, "workspace");
  fs.mkdirSync(path.join(workspace, "docs"), { recursive: true });
  const engineRoot = path.join(root, "AnyEngine");
  const privateDir = path.join(engineRoot, "Engine", "Source", "Runtime", "Engine", "Private");
  fs.mkdirSync(path.join(privateDir, "GameFramework"), { recursive: true });
  fs.writeFileSync(path.join(privateDir, "PlayerController.cpp"), "void APlayerController::PostLogin() {}\n", "utf8");
  // An engine root is recognised by its build tool, as in a real install.
  fs.mkdirSync(path.join(engineRoot, "Engine", "Build", "BatchFiles"), { recursive: true });
  fs.writeFileSync(path.join(engineRoot, "Engine", "Build", "BatchFiles", "Build.bat"), "@echo off\r\n", "utf8");
  fs.writeFileSync(path.join(engineRoot, "Engine", "Build", "BatchFiles", "Build.sh"), "#!/bin/sh\n", "utf8");
  fs.writeFileSync(path.join(engineRoot, "Engine", "Build", "Build.version"),
    JSON.stringify({ MajorVersion: 5, MinorVersion: 2, PatchVersion: 0 }), "utf8");
  const projectRoot = path.join(root, "Game");
  fs.mkdirSync(path.join(projectRoot, "Source", "Game"), { recursive: true });
  const buildCs = "using UnrealBuildTool;\r\npublic class Game : ModuleRules\r\n{\r\n\tpublic Game(ReadOnlyTargetRules Target) : base(Target)\r\n\t{\r\n"
    + "\t\tPublicDependencyModuleNames.AddRange(new string[] { \"Core\", \"CoreUObject\", \"Engine\", \"InputCore\", \"EnhancedInput\" });\r\n"
    + "\t\tPrivateDependencyModuleNames.AddRange(new string[] { });\r\n\t}\r\n}\r\n".repeat(4);
  fs.writeFileSync(path.join(projectRoot, "Source", "Game", "Game.Build.cs"), buildCs, "utf8");
  fs.writeFileSync(path.join(projectRoot, "Source", "Game", "Lines.h"),
    Array.from({ length: 30 }, (_, i) => `// line ${i + 1}`).join("\n"), "utf8");
  const projectFile = path.join(projectRoot, "Game.uproject");
  fs.writeFileSync(projectFile, JSON.stringify({ FileVersion: 3, EngineAssociation: "" }), "utf8");
  const stateRoot = path.join(root, "state");
  const runtime = createDirectRuntime({
    workspaceRoot: workspace,
    configPath: path.join(root, "agent-mcp.json"),
    stateRoot,
    env: { AGENT_STATE_ROOT: stateRoot, ALLOW_WRITE: "1", ALLOW_COMMANDS: "0", ALLOW_UNREAL_BUILD: "0",
      UNREAL_ENGINE_ROOT: engineRoot, SHARED_UNREAL_CONFIG: path.join(root, "shared.json") },
    getActiveProject: () => projectFile,
  });
  return { runtime, engineRoot, projectRoot, buildCs };
}

const owner = { sessionId: "failure-guidance" };
const call = async (runtime, tool, args) => (await runtime.callTool(tool, args, owner)).structuredContent;

test("absolute engine path outside the roots names its engine:// equivalent and repeats as a duplicate", async (t) => {
  const { runtime, engineRoot } = fixture(t);
  const absolute = path.join(engineRoot, "Engine", "Source", "Runtime", "Engine", "Private", "PlayerController.cpp");
  const first = await call(runtime, "read_file", { path: absolute });
  assert.equal(first.ok, false);
  assert.equal(first.errorCode, "PATH_OUTSIDE_ALLOWED_ROOTS");
  assert.deepEqual(first.allowedSchemes, ["project://", "workspace://", "engine://"]);
  assert.equal(first.engineUri, "engine://Engine/Source/Runtime/Engine/Private/PlayerController.cpp");
  assert.deepEqual(first.suggestion, { tool: "read_file", args: { path: first.engineUri } });
  const followed = await call(runtime, "read_file", first.suggestion.args);
  assert.equal(followed.ok, true);
  assert.match(followed.content, /APlayerController::PostLogin/);
  const repeated = await call(runtime, "read_file", { path: absolute });
  assert.equal(repeated.duplicate, true);
  assert.equal(repeated.retry.allowed, false);
});

test("absolute path under the engine but outside engine source is not mapped and gets no guessed URI", async (t) => {
  const { runtime, engineRoot } = fixture(t);
  const wrong = path.join(engineRoot, "Source", "Runtime", "Engine", "Private", "PlayerController.cpp");
  const payload = await call(runtime, "search_files", { path: wrong, query: "PostLogin" });
  assert.equal(payload.errorCode, "PATH_OUTSIDE_ALLOWED_ROOTS");
  assert.equal(payload.engineUri, undefined);
  assert.equal(payload.underInstalledEngine, true);
  assert.equal(payload.engineUriForm, "engine://Engine/Source/...");
  assert.equal(payload.suggestion, undefined);
});

test("engine:// NOT_FOUND suggests a search in the existing owning module, not the project", async (t) => {
  const { runtime } = fixture(t);
  const missing = "engine://Engine/Source/Runtime/Engine/Private/GameFramework/PlayerController.cpp";
  for (const tool of ["read_file", "read_file_range", "read_symbol"]) {
    const args = tool === "read_file" ? { path: missing }
      : tool === "read_file_range" ? { path: missing, startLine: 1, endLine: 5 } : { path: missing, symbol: "PostLogin" };
    const payload = await call(runtime, tool, args);
    assert.equal(payload.errorCode, "NOT_FOUND", tool);
    assert.deepEqual(payload.suggestion, { tool: "search_files",
      args: { query: "PlayerController.cpp", path: "engine://Engine/Source/Runtime/Engine", matchFileNames: true } }, tool);
  }
  const found = await call(runtime, "search_files", (await call(runtime, "read_file", { path: missing })).suggestion?.args
    || { query: "PlayerController.cpp", path: "engine://Engine/Source/Runtime/Engine", matchFileNames: true });
  assert.ok(found.results.some(r => r.uri === "engine://Engine/Source/Runtime/Engine/Private/PlayerController.cpp"));
  const directory = await call(runtime, "list_directory", { path: "engine://Engine/Source/Runtime/Engine/Missing" });
  assert.equal(directory.errorCode, "NOT_FOUND");
  assert.equal(directory.suggestion.args.path, "engine://Engine/Source/Runtime/Engine");
});

test("byteBudget is reported as a whole-response limit when it stops the body", async (t) => {
  const { runtime, buildCs } = fixture(t);
  const payload = await call(runtime, "read_file", { path: "project://Source/Game/Game.Build.cs", byteBudget: 2048 });
  assert.equal(payload.ok, true);
  assert.ok(payload.nextOffsetBytes < Buffer.byteLength(buildCs));
  assert.equal(payload.hasMore, true);
  assert.equal(payload.omissionReason, "byte_budget");
  assert.equal(payload.effectiveByteBudget, 2048);
  assert.equal(payload.budgetScope, "whole_response");
  const full = await call(runtime, "read_file", { path: "project://Source/Game/Game.Build.cs", byteBudget: 16384 });
  assert.equal(full.hasMore, false);
  assert.equal(full.omissionReason, undefined);
});

test("read_file_range marks truncation only when the requested range was cut", async (t) => {
  const { runtime } = fixture(t);
  const whole = await call(runtime, "read_file_range", { path: "project://Source/Game/Lines.h", startLine: 1, endLine: 10 });
  assert.equal(whole.truncated, false);
  assert.equal(whole.requestedRangeComplete, true);
  assert.equal(whole.moreLinesInFile, true);
  assert.equal(whole.nextStartLine, 11);
  assert.equal(whole.omissionReason, undefined);
  const cut = await call(runtime, "read_file_range", { path: "project://Source/Game/Lines.h", startLine: 1, endLine: 30, byteBudget: 2048 });
  if (cut.endLine < 30) {
    assert.equal(cut.truncated, true);
    assert.equal(cut.requestedRangeComplete, false);
    assert.equal(cut.omissionReason, "byte_budget");
  } else {
    assert.equal(cut.truncated, false);
  }
});

test("workspace info reports engine:// access and project:// browsing for a project outside the workspace", async (t) => {
  const { runtime } = fixture(t);
  const info = await call(runtime, "get_workspace_info", {});
  assert.equal(info.engineSource.available, true);
  assert.equal(info.engineSource.uriPrefix, "engine://Engine/Source/");
  assert.equal(info.engineSource.identity.version, "5.2.0");
  assert.equal(info.project.activeProjectInsideWorkspace, false);
  assert.match(info.project.browseNote, /project:\/\/ URIs/);
  const listed = await call(runtime, "list_directory", { path: "project://Source/Game" });
  assert.equal(listed.ok, true);
});

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
    + "\t\tPrivateDependencyModuleNames.AddRange(new string[] { });\r\n\t}\r\n}\r\n".repeat(64);
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
  // The body alone exceeds the budget on every OS, independent of the
  // serialized temporary path length in the response metadata.
  assert.ok(Buffer.byteLength(buildCs) > 2048);
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

// The 2026-10-18 incident shape: a budget-cut search dropped the one match
// that sets the flag, inside a header inline function, and the partial page
// carried no way to continue. A partial page must count what it omitted and
// hand back a cursor that reaches every match.
function deathFlagProject(projectRoot) {
  const dir = path.join(projectRoot, "Source", "Game");
  for (let i = 0; i < 12; i++) {
    fs.writeFileSync(path.join(dir, `A${String(i).padStart(2, "0")}.cpp`),
      `// reader ${i}\nif (PlayerState->bIsDead && PlayerState->DeathCount > ${i}) { HandleDeadPlayerStateWithLongName(); }\n`, "utf8");
  }
  fs.writeFileSync(path.join(dir, "ZPlayerState.h"), ["#pragma once", "class AZPlayerState {", "\tbool bIsDead;",
    "public:", "\tFORCEINLINE void AddDeath() { if (!bIsDead) { DeathCount++; bIsDead = true; } }", "};"].join("\n"), "utf8");
}

async function allPages(runtime, args) {
  const pages = [];
  let next = args;
  for (let guard = 0; next && guard < 50; guard++) {
    const page = await call(runtime, "search_files", next);
    assert.equal(page.ok, true, JSON.stringify(page));
    pages.push(page);
    next = page.nextCursor ? page.suggestion.args : null;
  }
  return pages;
}

test("a budget-cut search counts omitted matches and its cursor reaches the header inline assignment", async (t) => {
  const { runtime, projectRoot } = fixture(t);
  deathFlagProject(projectRoot);
  const full = await call(runtime, "search_files", { path: "project://Source", query: "bIsDead" });
  assert.equal(full.coverage, "complete");
  assert.equal(full.nextCursor, undefined);
  assert.equal(full.absenceVerified, undefined);
  assert.ok(full.results.some(r => /bIsDead = true/.test(r.text)));

  const first = await call(runtime, "search_files", { path: "project://Source", query: "bIsDead", byteBudget: 2048 });
  assert.equal(first.coverage, "partial");
  assert.equal(first.truncated, true);
  assert.equal(first.omissionReason, "response_budget");
  assert.equal(first.absenceVerified, false);
  assert.equal(first.matchesReturned, first.results.length);
  assert.equal(first.matchesOmitted, full.results.length - first.results.length);
  assert.ok(first.matchesOmitted > 0);
  assert.ok(!first.results.some(r => /bIsDead = true/.test(r.text)), "the fixture must hide the assignment on page one");
  assert.deepEqual(first.suggestion, { tool: "search_files",
    args: { path: "project://Source", query: "bIsDead", byteBudget: 2048, cursor: first.nextCursor } });

  const pages = await allPages(runtime, { path: "project://Source", query: "bIsDead", byteBudget: 2048 });
  const collected = pages.flatMap(page => page.results);
  assert.deepEqual(collected.map(r => `${r.uri}:${r.line}`), full.results.map(r => `${r.uri}:${r.line}`));
  assert.ok(collected.some(r => /bIsDead = true/.test(r.text)));
  const last = pages.at(-1);
  assert.equal(last.nextCursor, undefined);
  assert.equal(last.cursorOffset, collected.length - last.results.length);
});

test("a search cursor is rejected for another search and goes stale when files change", async (t) => {
  const { runtime, projectRoot } = fixture(t);
  deathFlagProject(projectRoot);
  const first = await call(runtime, "search_files", { path: "project://Source", query: "bIsDead", byteBudget: 2048 });
  const other = await call(runtime, "search_files", { path: "project://Source", query: "DeathCount", cursor: first.nextCursor });
  assert.equal(other.errorCode, "INVALID_ARGUMENT");
  const garbage = await call(runtime, "search_files", { path: "project://Source", query: "bIsDead", cursor: "not-a-cursor" });
  assert.equal(garbage.errorCode, "INVALID_ARGUMENT");
  fs.appendFileSync(path.join(projectRoot, "Source", "Game", "A00.cpp"), "// bIsDead edited\n", "utf8");
  const stale = await call(runtime, "search_files", first.suggestion.args);
  assert.equal(stale.errorCode, "CURSOR_STALE");
  assert.deepEqual(stale.suggestion, { tool: "search_files",
    args: { path: "project://Source", query: "bIsDead", byteBudget: 2048 } });
});

test("missing read and search targets name the caller's URI and an existing place to search", async (t) => {
  const { runtime, engineRoot } = fixture(t);
  const missing = "engine://Engine/Source/Runtime/Engine/Private/Characters/Character.cpp";
  for (const [tool, args] of [["read_file_range", { path: missing, startLine: 1, endLine: 5 }],
    ["read_symbol", { path: missing, symbol: "OnDeath" }], ["read_file", { path: missing }]]) {
    const payload = await call(runtime, tool, args);
    assert.equal(payload.errorCode, "NOT_FOUND", tool);
    assert.equal(payload.message, `File not found: ${missing}`, tool);
    assert.ok(!payload.message.includes(engineRoot), tool);
  }
  const engineSearch = await call(runtime, "search_files",
    { path: "engine://Engine/Source/Runtime/Engine/Classes/Character.h", query: "bDie" });
  assert.equal(engineSearch.errorCode, "NOT_FOUND");
  assert.deepEqual(engineSearch.suggestion, { tool: "search_files",
    args: { query: "Character.h", path: "engine://Engine/Source/Runtime/Engine", matchFileNames: true } });
  const projectSearch = await call(runtime, "search_files", { path: "project://Source/Game/Missing.h", query: "x" });
  assert.equal(projectSearch.errorCode, "NOT_FOUND");
  assert.deepEqual(projectSearch.suggestion, { tool: "search_files",
    args: { query: "Missing.h", path: "project://", matchFileNames: true } });
});

test("a read cut before the file or requested range ends names the exact continuation", async (t) => {
  const { runtime, buildCs } = fixture(t);
  const first = await call(runtime, "read_file", { path: "project://Source/Game/Game.Build.cs", byteBudget: 2048 });
  assert.equal(first.hasMore, true);
  assert.deepEqual(first.suggestion, { tool: "read_file",
    args: { path: "project://Source/Game/Game.Build.cs", byteBudget: 2048, offsetBytes: first.nextOffsetBytes } });
  const second = await call(runtime, "read_file", first.suggestion.args);
  assert.equal(second.ok, true);
  assert.equal(second.offsetBytes, first.nextOffsetBytes);
  assert.equal(Buffer.from(buildCs, "utf8").subarray(first.nextOffsetBytes, second.nextOffsetBytes).toString("utf8"), second.content);
  const lines = buildCs.split(/\r?\n/).length;
  const cut = await call(runtime, "read_file_range",
    { path: "project://Source/Game/Game.Build.cs", startLine: 1, endLine: lines, byteBudget: 2048 });
  assert.equal(cut.truncated, true);
  assert.deepEqual(cut.suggestion, { tool: "read_file_range", args: { path: "project://Source/Game/Game.Build.cs",
    startLine: cut.endLine + 1, endLine: lines, byteBudget: 2048 } });
  const whole = await call(runtime, "read_file_range", { path: "project://Source/Game/Lines.h", startLine: 1, endLine: 10 });
  assert.equal(whole.moreLinesInFile, true);
  assert.equal(whole.suggestion, undefined);
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

test("search never issues a cursor beyond its continuation cap", async t=>{
 const {runtime,projectRoot}=fixture(t);
 fs.writeFileSync(path.join(projectRoot,"Source/Game/Large.txt"),"needle\n".repeat(21000));
 let cursor, count=0, r;
 for(let page=0;page<100;page++) {
  r=await call(runtime,"search_files",{path:"project://Source/Game/Large.txt",query:"needle",maxResults:1000,byteBudget:65536,...(cursor?{cursor}:{})});
  assert.equal(r.ok,true,JSON.stringify(r)); count+=r.results.length;
  if(!r.nextCursor) break; cursor=r.nextCursor;
  assert.ok(JSON.parse(Buffer.from(cursor,"base64url")).n < 20000);
 }
 assert.equal(count,20000); assert.equal(r.stopReason,"cursor_limit"); assert.equal(r.absenceVerified,false); assert.equal(r.coverage,"partial");
});

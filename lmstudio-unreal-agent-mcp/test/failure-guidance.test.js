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

function fixture(t, env = {}) {
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
      UNREAL_ENGINE_ROOT: engineRoot, SHARED_UNREAL_CONFIG: path.join(root, "shared.json"), ...env },
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
  assert.deepEqual(first.suggestion, { tool: "read_file_range", args: { path: first.engineUri, startLine: 1, endLine: 20 } });
  const followed = await call(runtime, first.suggestion.tool, first.suggestion.args);
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
  const payload = await call(runtime, "read_file", { path: "project://Source/Game/Game.Build.cs", offsetBytes: 0, byteBudget: 2048 });
  assert.equal(payload.ok, true);
  assert.ok(payload.nextOffsetBytes < Buffer.byteLength(buildCs));
  assert.equal(payload.hasMore, true);
  assert.equal(payload.omissionReason, "byte_budget");
  assert.equal(payload.effectiveByteBudget, 2048);
  assert.equal(payload.budgetScope, "whole_response");
  const full = await call(runtime, "read_file", { path: "project://Source/Game/Game.Build.cs", offsetBytes: 0, byteBudget: 16384 });
  assert.equal(full.hasMore, false);
  assert.equal(full.omissionReason, undefined);
});

test("read_file_range marks truncation only when the requested range was cut", async (t) => {
  const { runtime } = fixture(t);
  const whole = await call(runtime, "read_file_range", { path: "project://Source/Game/Lines.h", startLine: 1, endLine: 10 });
  assert.equal(whole.truncated, false);
  assert.equal(whole.requestedRangeComplete, true);
  assert.equal(whole.moreLinesInFile, true);
  // A complete targeted read offers no continuation; the file merely continues.
  assert.equal(whole.nextStartLine, undefined);
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
  const first = await call(runtime, "read_file", { path: "project://Source/Game/Game.Build.cs", offsetBytes: 0, byteBudget: 2048 });
  assert.equal(first.hasMore, true);
  assert.deepEqual(first.suggestion, { tool: "read_file",
    args: { path: "project://Source/Game/Game.Build.cs", byteBudget: 2048, offsetBytes: first.nextOffsetBytes } });
  const second = await call(runtime, first.suggestion.tool, first.suggestion.args);
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

test("a cut range still names nextStartLine inside the requested range only", async (t) => {
  const { runtime } = fixture(t);
  const cut = await call(runtime, "read_file_range", { path: "project://Source/Game/Game.Build.cs", startLine: 1, endLine: 400, byteBudget: 2048 });
  assert.equal(cut.truncated, true);
  assert.equal(cut.nextStartLine, cut.endLine + 1);
  assert.equal(cut.suggestion.args.endLine, cut.requestedEndLine);
});

test("a completed source range points at locating the next symbol, not at sweeping to EOF", async (t) => {
  const { runtime } = fixture(t);
  const head = await call(runtime, "read_file_range", { path: "project://Source/Game/Lines.h", startLine: 1, endLine: 10 });
  assert.equal(head.rangeComplete, true);
  assert.equal(head.moreLinesInFile, true);
  assert.equal(head.nextStartLine, undefined);
  assert.equal(head.afterRange.owed, false);
  assert.deepEqual(head.afterRange.locateNext.map((item) => item.tool), ["search_files", "read_symbol"]);
  assert.equal(head.afterRange.locateNext[0].argsTemplate.path, "project://Source/Game/Lines.h");
  const tail = await call(runtime, "read_file_range", { path: "project://Source/Game/Lines.h", startLine: 21, endLine: 30 });
  assert.equal(tail.moreLinesInFile, false);
  assert.equal(tail.afterRange, undefined, "a range ending at EOF has nothing after it");
  const cut = await call(runtime, "read_file_range", { path: "project://Source/Game/Game.Build.cs", startLine: 1, endLine: 400, byteBudget: 2048 });
  assert.equal(cut.afterRange, undefined, "a cut range keeps its own continuation contract");
  const config = await call(runtime, "read_file_range", { path: "project://Game.uproject", startLine: 1, endLine: 1 });
  assert.equal(config.afterRange, undefined, "non-source files are unchanged");
});

// Claude Code's Read refuses a whole oversized file and points at a portion or a
// search; an engine source file is read by symbol, search hit or line range.
test("whole read of a large engine file is refused with targeted alternatives; explicit windows still read", async (t) => {
  const { runtime, engineRoot } = fixture(t);
  const header = path.join(engineRoot, "Engine", "Source", "Runtime", "Engine", "Private", "GameFramework", "Big.h");
  fs.writeFileSync(header, Array.from({ length: 900 }, (_, i) => `\tint32 Field${i} = ${i}; // ${"x".repeat(20)}`).join("\n"), "utf8");
  const refused = await call(runtime, "read_file", { path: "engine://Engine/Source/Runtime/Engine/Private/GameFramework/Big.h" });
  assert.equal(refused.ok, false);
  assert.equal(refused.errorCode, "WHOLE_READ_TOO_LARGE");
  assert.ok(refused.size > 16384);
  assert.deepEqual(refused.alternatives.map((item) => item.tool), ["search_files", "read_symbol", "read_file_range"]);
  assert.equal(refused.alternatives[0].argsTemplate.path, "engine://Engine/Source/Runtime/Engine/Private/GameFramework/Big.h");
  assert.equal(refused.alternatives[0].argsTemplate.contextLines, 3);
  const small = await call(runtime, "read_file", { path: "engine://Engine/Source/Runtime/Engine/Private/PlayerController.cpp", maxBytes: 1024 });
  assert.equal(small.ok, true);
  const paged = await call(runtime, "read_file_range", {
    path: "engine://Engine/Source/Runtime/Engine/Private/GameFramework/Big.h", startLine: 400, endLine: 402 });
  assert.equal(paged.ok, true);
  assert.match(paged.content, /Field399/);
});

// Source is located first at any size; only a small non-source file reads whole,
// and an explicit byte window remains the recovery path for both.
test("implicit whole reads refuse project source at any size and allow small non-source files only", async (t) => {
  const { runtime, projectRoot } = fixture(t);
  const write = (relative, bytes) => {
    fs.mkdirSync(path.dirname(path.join(projectRoot, relative)), { recursive: true });
    fs.writeFileSync(path.join(projectRoot, relative), "x".repeat(bytes), "utf8");
    return `project://${relative}`;
  };
  for (const source of ["project://Source/Game/Lines.h", "project://Source/Game/Game.Build.cs",
    write("Source/Game/Tiny.cpp", 16), write("Source/Game/Upper.CPP", 16), write("Source/Game/Mixed.Hpp", 16)]) {
    const refused = await call(runtime, "read_file", { path: source });
    assert.equal(refused.errorCode, "WHOLE_READ_TOO_LARGE", source);
    assert.match(refused.message, /source files are located first/u);
    assert.deepEqual(refused.alternatives.map((item) => item.tool), ["search_files", "read_symbol", "read_file_range"]);
    assert.equal(refused.suggestion.tool, "search_files");
    assert.equal(refused.suggestion.args, undefined);
    assert.equal(refused.suggestion.argsTemplate.query, "<symbol>");
    const window = await call(runtime, "read_file", { path: source, offsetBytes: 0, maxBytes: 1024 });
    assert.equal(window.ok, true, source);
  }
  for (const small of ["project://Game.uproject", write("Config/DefaultGame.ini", 8 * 1024), write("Notes/README", 8 * 1024)]) {
    const read = await call(runtime, "read_file", { path: small });
    assert.equal(read.ok, true, small);
    assert.equal(read.hasMore, false);
  }
  for (const large of [write("Config/DefaultEngine.ini", 8 * 1024 + 1), write("Notes/LICENSE", 8 * 1024 + 1)]) {
    const refused = await call(runtime, "read_file", { path: large });
    assert.equal(refused.errorCode, "WHOLE_READ_TOO_LARGE", large);
    assert.match(refused.message, /files over 8192 bytes require a targeted read/u);
    const window = await call(runtime, "read_file", { path: large, maxBytes: 4096 });
    assert.equal(window.ok, true);
    assert.equal(window.hasMore, true);
  }
});

test("the implicit whole-read limit stays configurable but never admits source", async (t) => {
  const { runtime, projectRoot } = fixture(t, { DIRECT_WHOLE_READ_MAX_BYTES: String(16 * 1024) });
  fs.mkdirSync(path.join(projectRoot, "Config"), { recursive: true });
  fs.writeFileSync(path.join(projectRoot, "Config", "DefaultEngine.ini"), "x".repeat(12 * 1024), "utf8");
  assert.equal((await call(runtime, "read_file", { path: "project://Config/DefaultEngine.ini" })).ok, true);
  assert.equal((await call(runtime, "read_file", { path: "project://Source/Game/Lines.h" })).errorCode, "WHOLE_READ_TOO_LARGE");
});

test("edit failures suggest the lines around the intended region, not the file prefix", async (t) => {
  const { runtime, projectRoot } = fixture(t);
  const file = path.join(projectRoot, "Source", "Game", "Edit.cpp");
  const lines = Array.from({ length: 200 }, (_, i) => `\tint32 Value${i} = ${i};`);
  lines[149] = "\tHealth = FMath::Clamp(Health - Damage, 0.f, MaxHealth);";
  lines[169] = "\tReturnValue = Total;";
  lines[179] = "\tReturnValue = Total;";
  fs.writeFileSync(file, lines.join("\n"), "utf8");
  const read = await call(runtime, "read_file_range", { path: "project://Source/Game/Edit.cpp", startLine: 148, endLine: 152 });
  const base = { path: "project://Source/Game/Edit.cpp", expectedOccurrences: 1, fileVersionReceipt: read.fileVersionReceipt };
  const stale = await call(runtime, "replace_in_file", { ...base,
    oldText: "\tHealth = FMath::Clamp(Health - Damage, 0.f, MaxHealth);\n\tbIsDead = true;", newText: "\t// x" });
  // Zero matches are reported as an occurrence mismatch (found 0) by the existing contract.
  assert.equal(stale.errorCode, "OCCURRENCE_MISMATCH");
  assert.deepEqual(stale.candidateLines, [150]);
  assert.deepEqual([stale.suggestion.args.startLine, stale.suggestion.args.endLine], [140, 161]);
  const ambiguous = await call(runtime, "replace_in_file", { ...base, oldText: "\tReturnValue = Total;", newText: "\tReturnValue = 0;" });
  assert.equal(ambiguous.errorCode, "OCCURRENCE_MISMATCH");
  assert.deepEqual(ambiguous.candidateLines, [170, 180]);
  const missing = await call(runtime, "replace_in_file", { path: "project://Source/Game/Edit.cpp", expectedOccurrences: 1,
    oldText: "\tHealth = FMath::Clamp(Health - Damage, 0.f, MaxHealth);", newText: "\t// y" });
  assert.equal(missing.errorCode, "FILE_SNAPSHOT_REQUIRED");
  assert.deepEqual([missing.suggestion.args.startLine, missing.suggestion.args.endLine], [140, 160]);
  assert.equal(missing.currentSha256, read.sha256);
  const nowhere = await call(runtime, "replace_in_file", { ...base, oldText: "\tUnrelatedCall(Spline);", newText: "\t// z" });
  assert.equal(nowhere.errorCode, "OCCURRENCE_MISMATCH");
  assert.equal(nowhere.suggestion.tool, "search_files");
  assert.equal(nowhere.suggestion.args.query, "UnrelatedCall");
});

// Claude Code's Grep -C: a hit with its surrounding lines, so a multi-line
// signature or a struct member is read without a separate range guess.
test("search_files returns numbered context lines and filters by include glob", async (t) => {
  const { runtime, projectRoot } = fixture(t);
  fs.mkdirSync(path.join(projectRoot, "Source", "Game", "Public"), { recursive: true });
  fs.writeFileSync(path.join(projectRoot, "Source", "Game", "Public", "Damage.h"),
    ["struct FHitInfo", "{", "\tfloat Amount;", "\tbool bCritical;", "};", "float ApplyDamage(AActor* Target,", "\tfloat Base,", "\tAController* By);"].join("\n"), "utf8");
  fs.writeFileSync(path.join(projectRoot, "Source", "Game", "Damage.cpp"), "float ApplyDamage(AActor* Target, float Base, AController* By) { return Base; }\n", "utf8");
  const hit = await call(runtime, "search_files", { path: "project://Source/Game", query: "ApplyDamage(", contextLines: 2, include: "*.h" });
  assert.equal(hit.ok, true, JSON.stringify(hit));
  assert.deepEqual(hit.results.map((r) => r.uri), ["project://Source/Game/Public/Damage.h"]);
  const [row] = hit.results;
  assert.equal(row.line, 6);
  assert.equal(row.contextStartLine, 4);
  assert.equal(row.contextEndLine, 8);
  assert.equal(row.context, "4\t\tbool bCritical;\n5\t};\n6\tfloat ApplyDamage(AActor* Target,\n7\t\tfloat Base,\n8\t\tAController* By);");
  assert.equal(hit.filesFilteredByInclude, 2); // Game.Build.cs and Damage.cpp
  const plain = await call(runtime, "search_files", { path: "project://Source/Game", query: "ApplyDamage(" });
  assert.equal(plain.results.length, 2);
  assert.equal(plain.results[0].context, undefined);
  const nested = await call(runtime, "search_files", { path: "project://Source", query: "bCritical", include: "**/Public/**" });
  assert.equal(nested.results.length, 1);
});

test("read_symbol view=declaration returns only the prototype or member statement", async (t) => {
  const { runtime, projectRoot } = fixture(t);
  fs.writeFileSync(path.join(projectRoot, "Source", "Game", "Combat.h"), [
    "#pragma once",
    "UCLASS()",
    "class GAME_API UCombatStatics : public UObject",
    "{",
    "\tGENERATED_BODY()",
    "public:",
    "\tUFUNCTION(BlueprintCallable, Category=\"Game|Damage\",",
    "\t\tmeta=(WorldContext=\"WorldContextObject\"))",
    "\tstatic float ApplyDamage(AActor* DamagedActor, float BaseDamage,",
    "\t\tAController* EventInstigator, AActor* DamageCauser,",
    "\t\tTSubclassOf<UDamageType> DamageTypeClass);",
    "\tvoid Unrelated() { const float V = ApplyDamage(nullptr, 1.f, nullptr, nullptr, nullptr); }",
    "\tUPROPERTY(EditAnywhere)",
    "\tfloat MaxHealth = 100.f;",
    "};",
    "struct FOther { float MaxHealth; };",
  ].join("\n"), "utf8");
  const decl = await call(runtime, "read_symbol", { path: "project://Source/Game/Combat.h", symbol: "UCombatStatics::ApplyDamage", view: "declaration" });
  assert.equal(decl.ok, true, JSON.stringify(decl));
  assert.equal(decl.representation, "lexical_declaration_candidate");
  assert.deepEqual([decl.startLine, decl.endLine], [7, 11]);
  assert.match(decl.content, /TSubclassOf<UDamageType> DamageTypeClass\);$/u);
  assert.doesNotMatch(decl.content, /Unrelated/u);
  const field = await call(runtime, "read_symbol", { path: "project://Source/Game/Combat.h", symbol: "UCombatStatics::MaxHealth", view: "declaration" });
  assert.deepEqual([field.startLine, field.endLine], [13, 14]);
  const member = await call(runtime, "read_symbol", { path: "project://Source/Game/Combat.h", symbol: "UCombatStatics::MaxHealth", view: "member" });
  assert.deepEqual([member.startLine, member.endLine], [13, 14]);
  const unowned = await call(runtime, "read_symbol", { path: "project://Source/Game/Combat.h", symbol: "MaxHealth", view: "member" });
  assert.equal(unowned.errorCode, "INVALID_SYMBOL");
  const leaf = await call(runtime, "read_symbol", { path: "project://Source/Game/Combat.h", symbol: "ApplyDamage", view: "declaration" });
  assert.deepEqual([leaf.startLine, leaf.endLine], [7, 11]);
  assert.equal(leaf.candidateCount, 1);
  const body = await call(runtime, "read_symbol", { path: "project://Source/Game/Combat.h", symbol: "Unrelated" });
  assert.equal(body.representation, "lexical_body_candidate");
  const none = await call(runtime, "read_symbol", { path: "project://Source/Game/Combat.h", symbol: "UCombatStatics::Missing", view: "declaration" });
  assert.equal(none.errorCode, "SYMBOL_NOT_FOUND");
});

test("whole-read refusal locates the caller's target without guessing a first page", async (t) => {
  const { runtime, projectRoot } = fixture(t);
  const file = "project://Source/Game/Large.cpp";
  fs.writeFileSync(path.join(projectRoot, "Source/Game/Large.cpp"), "int Value;\n".repeat(8000));
  const refused = await call(runtime, "read_file", { path: file });
  assert.equal(refused.errorCode, "WHOLE_READ_TOO_LARGE");
  assert.match(refused.message, /source files are located first/u);
  assert.equal(refused.suggestion.tool, "search_files");
  assert.equal(refused.suggestion.args, undefined);
  const located = await call(runtime, refused.suggestion.tool, { ...refused.suggestion.argsTemplate, query: "Value" });
  assert.equal(located.ok, true);
  const window = await call(runtime, "read_file", { path: file, offsetBytes: 0, maxBytes: 100 });
  assert.equal(window.ok, true);
  const range = await call(runtime, "read_file_range", { path: file, startLine: 3, endLine: 4 });
  assert.equal(range.rangeComplete, true);
  assert.equal(range.nextStartLine, undefined);
});

test("engine whole-read refusal preserves the engine scope in its locator template", async (t) => {
  const { runtime } = fixture(t);
  const file = "engine://Engine/Source/Runtime/Engine/Private/PlayerController.cpp";
  const refused = await call(runtime, "read_file", { path: file });
  assert.equal(refused.suggestion.args, undefined);
  assert.deepEqual(refused.suggestion, refused.alternatives[0]);
  assert.equal(refused.suggestion.argsTemplate.path, file);
  const found = await call(runtime, "search_files", { ...refused.suggestion.argsTemplate, query: "PostLogin" });
  assert.equal(found.ok, true);
});

test("include filtering cannot turn a discovery limit into complete absence", async (t) => {
  const { runtime } = fixture(t);
  const result = await call(runtime, "search_files", { path: "project://Source/Game", query: "Missing",
    include: "*.h", maxFiles: 1 });
  assert.equal(result.ok, true);
  assert.equal(result.coverage, "partial");
  assert.equal(result.maxFilesReached, true);
  assert.equal(result.absenceVerified, false);
  const file = await call(runtime, "search_files", { path: "project://Source/Game/Lines.h", query: "line 10", include: "*.h" });
  assert.equal(file.results.length, 1);
});

"use strict";

// Conversation 1794469194476: search_files(query=Spawn, include=**/*.h) spent
// its candidates on Content assets, read 0 files, and the model designed a new
// spawner although one existed. Candidates are now files the search can use,
// a project root is walked Source first, and standard generated folders no
// longer make every root search partial.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { createDirectRuntime } = require("../src/direct-server.js");

function fixture(t) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "search-candidates-"));
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  const projectRoot = path.join(base, "Game");
  const write = (relative, content) => {
    fs.mkdirSync(path.dirname(path.join(projectRoot, relative)), { recursive: true });
    fs.writeFileSync(path.join(projectRoot, relative), content);
  };
  write("Game.uproject", JSON.stringify({ FileVersion: 3 }));
  for (let i = 0; i < 8; i++) write(`Content/Maps/Level${i}.umap`, Buffer.from([0, 1, 2, 3, 4]));
  for (let i = 0; i < 6; i++) write(`Content/Data/Table${i}.json`, "{ \"rows\": [] }\n");
  write("Source/Game/Stage/StageManager.h", "class AStageManager { void ProcessSpawnBatch(); };\n");
  write("Intermediate/Build/Generated.h", "// Spawn generated\n");
  const runtime = createDirectRuntime({ workspaceRoot: base, configPath: path.join(base, "agent.json"),
    env: { AGENT_STATE_ROOT: path.join(base, "state") }, getActiveProject: () => path.join(projectRoot, "Game.uproject") });
  return { call: async (args) => (await runtime.callTool("search_files", args)).structuredContent, projectRoot };
}

test("assets no longer use up a header search's candidates", async (t) => {
  const f = fixture(t);
  const found = await f.call({ query: "Spawn", include: "**/*.h", maxFiles: 5 });
  assert.equal(found.ok, true, JSON.stringify(found));
  assert.deepEqual(found.results.map(result => result.uri), ["project://Source/Game/Stage/StageManager.h"]);
  assert.equal(found.filesRead, 1);
  assert.equal(found.coverage, "complete");
  assert.equal(found.absenceVerified, undefined);
  assert.deepEqual(found.excludedByPolicy, { entries: 1, names: ["Intermediate"] }, "policy exclusions are named, not gaps");
});

test("a project root is walked Source first, so a small budget still reaches Source", async (t) => {
  const f = fixture(t);
  const found = await f.call({ query: "ProcessSpawnBatch", maxFiles: 2 });
  assert.deepEqual(found.results.map(result => result.uri), ["project://Source/Game/Stage/StageManager.h"]);
  assert.equal(found.maxFilesReached, true);
  assert.equal(found.coverage, "partial");
});

test("a file-name search still sees asset names, and they count as covered", async (t) => {
  const f = fixture(t);
  const found = await f.call({ query: "Level3", matchFileNames: true });
  assert.ok(found.results.some(result => result.uri === "project://Content/Maps/Level3.umap"), JSON.stringify(found.results));
  assert.equal(found.coverage, "complete");
  assert.ok(found.filesNameOnly >= 8);
});

test("an empty result from a stopped walk names where it did not go", async (t) => {
  const f = fixture(t);
  const found = await f.call({ query: "NoSuchSymbol", maxFiles: 1 });
  assert.equal(found.results.length, 0);
  assert.equal(found.absenceVerified, false);
  assert.ok(found.unvisitedPaths.includes("project://Content"), JSON.stringify(found));
  assert.match(found.nextStep, /not proof of absence/u);
});

test("an include with a literal prefix is searched inside that prefix only", async (t) => {
  const f = fixture(t);
  const found = await f.call({ query: "rows", include: "Content/Data/*.json" });
  assert.equal(found.results.length, 6);
  assert.equal(found.coverage, "complete");
  const none = await f.call({ query: "Spawn", include: "Source/Missing/**/*.h" });
  assert.equal(none.results.length, 0);
  assert.equal(none.coverage, "complete", "the pruned walk covered everything the include can match");
});

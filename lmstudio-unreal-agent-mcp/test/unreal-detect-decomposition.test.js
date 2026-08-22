"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const facade = require("../src/unreal-detect");

const srcDir = path.resolve(__dirname, "..", "src");
const domainFiles = fs.readdirSync(srcDir)
  .filter((name) => /^unreal-(?:active|browse|build|config|detect|engine|project)(?:-.+)?\.js$/u.test(name))
  .sort();

test("unreal detection facade preserves its public surface", () => {
  const expected = [
    "IGNORE_DIRS",
    "buildProjectBrowsePaths",
    "configuredEngineRootForAssociation",
    "defaultEngineLocations",
    "defaultPlatform",
    "discoverProjects",
    "engineBuildToolCandidates",
    "engineFolderFromAssociation",
    "engineRootMatchesNumericAssociation",
    "engineRootNumericVersion",
    "findEngineInstalls",
    "getActiveProject",
    "listUnrealProjects",
    "loadConfig",
    "normalizeProjectName",
    "pathIdentity",
    "projectNameFromPath",
    "resolveAgentWorkspaceRoot",
    "resolveBuildPlan",
    "resolveEngineBuildTool",
    "resolveEngineRoot",
    "resolveExactProjectNameSelection",
    "resolveProjectSelection",
    "resolveSearchRoots",
    "saveConfig",
    "searchRootDelimiter",
    "setActiveProject",
    "splitSearchRoots",
    "uniquePaths",
  ];
  assert.deepEqual(Object.keys(facade).sort(), expected.sort());
});

test("unreal detection modules stay bounded and acyclic", () => {
  const graph = new Map();
  for (const name of domainFiles) {
    const source = fs.readFileSync(path.join(srcDir, name), "utf8");
    const lineCount = source.split(/\r?\n/u).length;
    assert.ok(lineCount <= 360, `${name} is ${lineCount} lines (limit 360)`);
    if (name === "unreal-detect.js") {
      assert.ok(lineCount <= 100, `facade is ${lineCount} lines (limit 100)`);
      assert.doesNotMatch(source, /\b(?:async\s+)?function\s+/u);
    }
    const dependencies = [...source.matchAll(/require\("\.\/(unreal-[^"]+)"\)/gu)]
      .map((match) => `${match[1]}.js`)
      .filter((dependency) => domainFiles.includes(dependency));
    graph.set(name, dependencies);
  }

  const visiting = new Set();
  const visited = new Set();
  const visit = (name, chain = []) => {
    if (visiting.has(name)) assert.fail(`import cycle: ${[...chain, name].join(" -> ")}`);
    if (visited.has(name)) return;
    visiting.add(name);
    for (const dependency of graph.get(name) || []) visit(dependency, [...chain, name]);
    visiting.delete(name);
    visited.add(name);
  };
  for (const name of graph.keys()) visit(name);
});

test("shared active-project mutation is controller-only and local state cannot revive it", () => {
  const sources = new Map(domainFiles.map((name) => [
    name,
    fs.readFileSync(path.join(srcDir, name), "utf8"),
  ]));
  assert.equal(
    [...sources.values()].some((source) => source.includes("saveSharedConfig")),
    false,
  );
  assert.match(sources.get("unreal-active-project.js"), /\["--clear"\]/u);
  assert.match(
    sources.get("unreal-active-project.js"),
    /\["--switch", resolved\]/u,
  );
  assert.match(
    sources.get("unreal-active-project.js"),
    /\["--switch", selection\.selected\.projectPath\]/u,
  );

  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-state-owner-"));
  const shared = path.join(root, "shared.json");
  const local = path.join(root, "local.json");
  const previous = process.env.SHARED_UNREAL_CONFIG;
  try {
    process.env.SHARED_UNREAL_CONFIG = shared;
    facade.saveConfig(local, { activeProject: "stale-local.uproject" });
    fs.writeFileSync(shared, "{}", "utf8");
    assert.equal(facade.getActiveProject(local), null);
    fs.writeFileSync(shared, JSON.stringify({ activeProject: null }), "utf8");
    assert.equal(facade.getActiveProject(local), null);
    assert.throws(
      () => facade.saveConfig(shared, { activeProject: "forbidden.uproject" }),
      /project_controller\.py/u,
    );
    assert.equal(JSON.parse(fs.readFileSync(shared, "utf8")).activeProject, null);
  } finally {
    if (previous === undefined) delete process.env.SHARED_UNREAL_CONFIG;
    else process.env.SHARED_UNREAL_CONFIG = previous;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

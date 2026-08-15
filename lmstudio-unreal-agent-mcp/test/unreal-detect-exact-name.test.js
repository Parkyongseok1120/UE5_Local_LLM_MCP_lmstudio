"use strict";

const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const {
  discoverProjects,
  listUnrealProjects,
  normalizeProjectName,
  resolveExactProjectNameSelection,
  resolveProjectSelection,
  setActiveProject,
} = require("../src/unreal-detect");

function createProject(parent, directoryName, projectName = directoryName) {
  const projectDir = path.join(parent, directoryName);
  const sourceDir = path.join(projectDir, "Source");
  fs.mkdirSync(sourceDir, { recursive: true });
  const projectPath = path.join(projectDir, `${projectName}.uproject`);
  fs.writeFileSync(
    projectPath,
    JSON.stringify({
      FileVersion: 3,
      EngineAssociation: "5.8",
      Modules: [{ Name: projectName, Type: "Runtime", LoadingPhase: "Default" }],
    }),
    "utf8",
  );
  fs.writeFileSync(
    path.join(sourceDir, `${projectName}Editor.Target.cs`),
    "public class PortableEditorTarget {}\n",
    "utf8",
  );
  return projectPath;
}

function createHarness(prefix = "unreal-exact-name-") {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  const workspaceRoot = path.join(root, "workspace");
  const projectRoot = path.join(root, "projects");
  const sharedConfig = path.join(root, "unreal-workspace.json");
  const localConfig = path.join(root, "agent-mcp.json");
  fs.mkdirSync(workspaceRoot, { recursive: true });
  fs.mkdirSync(projectRoot, { recursive: true });
  fs.writeFileSync(localConfig, "{}", "utf8");
  const previousSharedConfig = process.env.SHARED_UNREAL_CONFIG;
  process.env.SHARED_UNREAL_CONFIG = sharedConfig;
  return {
    root,
    workspaceRoot,
    projectRoot,
    sharedConfig,
    localConfig,
    configure(payload = {}) {
      fs.writeFileSync(
        sharedConfig,
        JSON.stringify({ projectSearchRoots: [projectRoot], ...payload }),
        "utf8",
      );
    },
    cleanup() {
      if (previousSharedConfig === undefined) delete process.env.SHARED_UNREAL_CONFIG;
      else process.env.SHARED_UNREAL_CONFIG = previousSharedConfig;
      fs.rmSync(root, { recursive: true, force: true });
    },
  };
}

test("project names normalize with NFKC, lowercase, separators, and .uproject removal", () => {
  assert.equal(normalizeProjectName("  Ｍｙ＿Project.UPROJECT  "), "myproject");
  assert.equal(normalizeProjectName("my-project"), "myproject");
  assert.equal(normalizeProjectName("MY Project.uproject"), "myproject");
  assert.equal(normalizeProjectName("Cafe\u0301_Game"), "cafégame");
  assert.equal(normalizeProjectName("Straße_Game"), normalizeProjectName("STRASSE-game"));
  assert.equal(normalizeProjectName("ΟΣ_Game"), normalizeProjectName("ος-game"));
  assert.equal(normalizeProjectName("οσ game"), normalizeProjectName("ος-game"));
});

test("setActiveProject applies Python-compatible bounded Unicode casefolding", async () => {
  const harness = createHarness("unreal-casefold-name-");
  const projectPath = createProject(harness.projectRoot, "Straße_Project");
  harness.configure();
  try {
    const result = await setActiveProject(harness.workspaceRoot, harness.localConfig, {
      hint: "STRASSE-project.UPROJECT",
      env: {},
      invokeProjectController: async () => ({ ok: true }),
    });
    assert.equal(result.ok, true);
    assert.equal(result.activeProject, path.resolve(projectPath));
    assert.equal(result.selectionReason, "exact-project-name");
  } finally {
    harness.cleanup();
  }
});

test("setActiveProject resolves a unique normalized exact name", async () => {
  const harness = createHarness();
  const projectPath = createProject(harness.projectRoot, "My_Project");
  harness.configure();
  const controllerCalls = [];
  try {
    const result = await setActiveProject(harness.workspaceRoot, harness.localConfig, {
      hint: "ＭＹ-project.UPROJECT",
      env: {},
      invokeProjectController: async (argv) => {
        controllerCalls.push(argv);
        return { ok: true };
      },
    });
    assert.equal(result.ok, true);
    assert.equal(result.activeProject, path.resolve(projectPath));
    assert.equal(result.projectName, "My_Project");
    assert.equal(result.selectionReason, "exact-project-name");
    assert.deepEqual(controllerCalls, [["--switch", path.resolve(projectPath)]]);
  } finally {
    harness.cleanup();
  }
});

test("partial project names only return suggestions and never auto-select", async () => {
  const harness = createHarness();
  createProject(harness.projectRoot, "AlphaGame");
  createProject(harness.projectRoot, "AlphaTools");
  createProject(harness.projectRoot, "BetaGame");
  harness.configure();
  try {
    const result = await resolveExactProjectNameSelection(
      harness.workspaceRoot,
      harness.localConfig,
      { name: "Alpha", env: {} },
    );
    assert.equal(result.selected, null);
    assert.equal(result.errorCode, "PROJECT_NAME_NOT_FOUND");
    assert.deepEqual(
      new Set(result.suggestions.map((item) => item.projectName)),
      new Set(["AlphaGame", "AlphaTools"]),
    );
  } finally {
    harness.cleanup();
  }
});

test("setActiveProject rejects a partial name without invoking the controller", async () => {
  const harness = createHarness();
  createProject(harness.projectRoot, "AlphaGame");
  harness.configure();
  let controllerCalls = 0;
  try {
    const result = await setActiveProject(harness.workspaceRoot, harness.localConfig, {
      hint: "Alpha",
      env: {},
      invokeProjectController: async () => {
        controllerCalls += 1;
        return { ok: true };
      },
    });
    assert.equal(result.ok, false);
    assert.equal(result.errorCode, "PROJECT_NAME_NOT_FOUND");
    assert.equal(result.suggestions[0]?.projectName, "AlphaGame");
    assert.equal(controllerCalls, 0);
  } finally {
    harness.cleanup();
  }
});

test("raw same-name candidates remain ambiguous after display-list deduplication", async () => {
  const harness = createHarness();
  createProject(harness.projectRoot, "FirstOwner", "Shared_Project");
  createProject(harness.projectRoot, "SecondOwner", "Shared_Project");
  harness.configure();
  try {
    const discovery = await discoverProjects(harness.workspaceRoot, harness.localConfig, {
      env: {},
    });
    assert.equal(discovery.projects.length, 1);
    assert.equal(discovery.rawProjects.length, 2);
    const listed = await listUnrealProjects(
      harness.workspaceRoot,
      harness.localConfig,
      { env: {} },
    );
    assert.equal(listed.projects.length, 2);
    assert.equal(new Set(listed.projects.map((item) => item.projectPath)).size, 2);

    const result = await resolveExactProjectNameSelection(
      harness.workspaceRoot,
      harness.localConfig,
      { name: "shared-project.uproject", env: {} },
    );
    assert.equal(result.selected, null);
    assert.equal(result.errorCode, "PROJECT_NAME_AMBIGUOUS");
    assert.equal(result.suggestions.length, 2);
    assert.equal(new Set(result.suggestions.map((item) => item.projectPath)).size, 2);
  } finally {
    harness.cleanup();
  }
});

test("legacy build selection fails closed for same-name clones unless an exact path is active", async () => {
  const harness = createHarness("unreal-clone-build-selection-");
  const first = createProject(harness.projectRoot, "FirstOwner", "Shared_Project");
  const second = createProject(harness.projectRoot, "SecondOwner", "Shared_Project");
  harness.configure();
  try {
    const ambiguous = await resolveProjectSelection(
      harness.workspaceRoot,
      harness.localConfig,
      { hint: "shared_project.uproject", env: {} },
    );
    assert.equal(ambiguous.selected, null);
    assert.equal(ambiguous.errorCode, "PROJECT_NAME_AMBIGUOUS");
    assert.equal(ambiguous.suggestions.length, 2);

    const partial = await resolveProjectSelection(
      harness.workspaceRoot,
      harness.localConfig,
      { hint: "Shared", env: {} },
    );
    assert.equal(partial.selected, null);
    assert.equal(partial.errorCode, "PROJECT_NAME_AMBIGUOUS");

    const implicit = await resolveProjectSelection(
      harness.workspaceRoot,
      harness.localConfig,
      { env: {} },
    );
    assert.equal(implicit.selected, null);
    assert.equal(implicit.errorCode, "PROJECT_NAME_AMBIGUOUS");

    harness.configure({ activeProject: path.relative(harness.workspaceRoot, second) });
    const active = await resolveProjectSelection(
      harness.workspaceRoot,
      harness.localConfig,
      { hint: "Shared_Project", env: {} },
    );
    assert.equal(active.selected?.projectPath, path.resolve(second));
    assert.equal(active.selectionReason, "config.activeProject");

    const explicit = await resolveProjectSelection(
      harness.workspaceRoot,
      harness.localConfig,
      { project: path.relative(harness.workspaceRoot, first), env: {} },
    );
    assert.equal(explicit.selected?.projectPath, path.resolve(first));
    assert.equal(explicit.selectionReason, "explicit.project");
  } finally {
    harness.cleanup();
  }
});

test("exact name selection excludes test fixtures and outside active-project roots", async () => {
  const harness = createHarness();
  const fixtureParent = path.join(harness.projectRoot, "fixture");
  createProject(fixtureParent, "FixtureOnly");
  createProject(path.join(harness.projectRoot, "test"), "TestOnly");
  createProject(path.join(harness.projectRoot, "tests"), "TestsOnly");
  const legitimateProject = createProject(
    path.join(harness.projectRoot, "testing-projects"),
    "LegitimateOnly",
  );
  const outsideProject = createProject(path.join(harness.root, "outside"), "OutsideOnly");
  harness.configure({ activeProject: outsideProject });
  try {
    for (const name of ["FixtureOnly", "TestOnly", "TestsOnly", "OutsideOnly"]) {
      const result = await resolveExactProjectNameSelection(
        harness.workspaceRoot,
        harness.localConfig,
        { name, env: { ACTIVE_PROJECT: outsideProject } },
      );
      assert.equal(result.selected, null);
      assert.equal(result.errorCode, "PROJECT_NAME_NOT_FOUND");
      assert.deepEqual(result.suggestions, []);
      assert.equal(result.rawProjects.some((item) => item.projectName === name), false);
    }

    const legitimate = await resolveExactProjectNameSelection(
      harness.workspaceRoot,
      harness.localConfig,
      { name: "LegitimateOnly", env: {} },
    );
    assert.equal(legitimate.selected?.projectPath, path.resolve(legitimateProject));
  } finally {
    harness.cleanup();
  }
});

test("exact name lookup clamps per-request depth instead of allowing an unbounded scan", async () => {
  const harness = createHarness();
  const deepParent = path.join(harness.projectRoot, ...Array.from({ length: 9 }, (_, i) => `d${i}`));
  createProject(deepParent, "TooDeep");
  harness.configure();
  try {
    const result = await resolveExactProjectNameSelection(
      harness.workspaceRoot,
      harness.localConfig,
      { name: "TooDeep", maxDepth: 999999, env: {} },
    );
    assert.equal(result.maxDepth, 8);
    assert.equal(result.selected, null);
    assert.equal(result.errorCode, "PROJECT_NAME_NOT_FOUND");
  } finally {
    harness.cleanup();
  }
});

test("legacy fuzzy resolveProjectSelection still accepts partial hints", async () => {
  const harness = createHarness();
  const projectPath = createProject(harness.projectRoot, "AlphaPortable");
  harness.configure();
  try {
    const result = await resolveProjectSelection(harness.workspaceRoot, harness.localConfig, {
      hint: "AlphaPort",
      env: {},
    });
    assert.equal(result.selected?.projectPath, path.resolve(projectPath));
    assert.equal(result.selectionReason, "hint");
  } finally {
    harness.cleanup();
  }
});

test("legacy selection never treats workspace or active-project score as a hint match", async () => {
  const harness = createHarness("unreal-hint-boundary-");
  const projectPath = createProject(harness.workspaceRoot, "WorkspaceGame");
  harness.configure({ activeProject: projectPath });
  try {
    const result = await resolveProjectSelection(harness.workspaceRoot, harness.localConfig, {
      hint: "DefinitelyNotAProject",
      env: {},
    });
    assert.equal(result.selected, null);
    assert.equal(result.selectionReason, "hint-not-matched");
  } finally {
    harness.cleanup();
  }
});

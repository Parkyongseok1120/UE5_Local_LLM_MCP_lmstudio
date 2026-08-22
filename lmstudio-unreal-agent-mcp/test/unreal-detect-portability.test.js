"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const {
  buildProjectBrowsePaths,
  resolveProjectSelection,
  resolveSearchRoots,
  findEngineInstalls,
  resolveEngineRoot,
  resolveBuildPlan,
  setActiveProject,
  defaultPlatform,
  resolveAgentWorkspaceRoot,
  pathIdentity,
  splitSearchRoots,
  uniquePaths,
} = require("../src/unreal-detect");

function createProject(root, name, engineAssociation) {
  const projectDir = path.join(root, name);
  const sourceDir = path.join(projectDir, "Source");
  fs.mkdirSync(sourceDir, { recursive: true });
  const projectPath = path.join(projectDir, `${name}.uproject`);
  fs.writeFileSync(
    projectPath,
    JSON.stringify({
      FileVersion: 3,
      EngineAssociation: engineAssociation,
      Modules: [{ Name: name, Type: "Runtime", LoadingPhase: "Default" }],
    }),
    "utf8"
  );
  fs.writeFileSync(
    path.join(sourceDir, `${name}Editor.Target.cs`),
    "public class PortableEditorTarget {}\n",
    "utf8"
  );
  return projectPath;
}

function createEngine(root, name, hostPlatform = process.platform) {
  const engineRoot = path.join(root, name);
  const batchRoot = path.join(engineRoot, "Engine", "Build", "BatchFiles");
  const buildTool = hostPlatform === "win32"
    ? path.join(batchRoot, "Build.bat")
    : path.join(batchRoot, hostPlatform === "darwin" ? "Mac" : "Linux", "Build.sh");
  fs.mkdirSync(path.dirname(buildTool), { recursive: true });
  fs.writeFileSync(
    buildTool,
    hostPlatform === "win32" ? "@echo off\r\nexit /b 0\r\n" : "#!/usr/bin/env sh\nexit 0\n",
    "utf8",
  );
  return engineRoot;
}

function writeEngineVersion(engineRoot, version) {
  const [major, minor] = String(version).split(".").map((part) => Number(part));
  const versionPath = path.join(engineRoot, "Engine", "Build", "Build.version");
  fs.mkdirSync(path.dirname(versionPath), { recursive: true });
  fs.writeFileSync(versionPath, JSON.stringify({ MajorVersion: major, MinorVersion: minor }), "utf8");
}

function sameFilesystemEntry(leftPath, rightPath) {
  const left = fs.statSync(leftPath);
  const right = fs.statSync(rightPath);
  return left.dev === right.dev && left.ino === right.ino;
}

test("project selection ignores an old PC activeProject and finds projects under current roots", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-portable-select-"));
  const workspaceRoot = path.join(root, "workspace");
  const projectRoot = path.join(root, "current-pc-projects");
  fs.mkdirSync(workspaceRoot, { recursive: true });
  const alpha = createProject(projectRoot, "AlphaPortable", "5.7");
  createProject(projectRoot, "BetaPortable", "5.8");

  const sharedConfig = path.join(root, "unreal-workspace.json");
  const localConfig = path.join(root, "agent-mcp.json");
  fs.writeFileSync(
    sharedConfig,
    JSON.stringify({
      activeProject: path.join(root, "old-pc", "MissingProject.uproject"),
      projectSearchRoots: [projectRoot],
    }),
    "utf8"
  );
  fs.writeFileSync(localConfig, "{}", "utf8");

  const previousSharedConfig = process.env.SHARED_UNREAL_CONFIG;
  process.env.SHARED_UNREAL_CONFIG = sharedConfig;
  try {
    const result = await resolveProjectSelection(workspaceRoot, localConfig, {
      hint: "AlphaPortable",
      maxDepth: 4,
    });
    assert.strictEqual(result.selected?.projectPath, path.resolve(alpha));
    assert.strictEqual(result.selected?.projectName, "AlphaPortable");
    assert.strictEqual(result.selected?.engineAssociation, "5.7");
    assert.strictEqual(result.selectionReason, "hint");
  } finally {
    if (previousSharedConfig === undefined) {
      delete process.env.SHARED_UNREAL_CONFIG;
    } else {
      process.env.SHARED_UNREAL_CONFIG = previousSharedConfig;
    }
  }
});

test("explicit projectSearchRoots do not mix in machine-specific default folders", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-explicit-roots-"));
  const workspaceRoot = path.join(root, "workspace");
  const configuredRoot = path.join(root, "configured");
  const sharedConfig = path.join(root, "unreal-workspace.json");
  const localConfig = path.join(root, "agent-mcp.json");
  fs.mkdirSync(workspaceRoot, { recursive: true });
  fs.mkdirSync(configuredRoot, { recursive: true });
  fs.writeFileSync(sharedConfig, JSON.stringify({ projectSearchRoots: [configuredRoot] }), "utf8");
  fs.writeFileSync(localConfig, "{}", "utf8");

  const previousSharedConfig = process.env.SHARED_UNREAL_CONFIG;
  process.env.SHARED_UNREAL_CONFIG = sharedConfig;
  try {
    const { roots } = resolveSearchRoots(workspaceRoot, localConfig);
    assert.deepStrictEqual(
      roots.map((value) => path.resolve(value)),
      [path.resolve(workspaceRoot), path.resolve(configuredRoot)]
    );
  } finally {
    if (previousSharedConfig === undefined) delete process.env.SHARED_UNREAL_CONFIG;
    else process.env.SHARED_UNREAL_CONFIG = previousSharedConfig;
  }
});

test("setActiveProject resolves an explicit relative projectPath from workspaceRoot", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-relative-set-active-"));
  const workspaceRoot = path.join(root, "작업 공간");
  const projectRoot = path.join(workspaceRoot, "프로젝트 모음");
  const unrelatedCwd = path.join(root, "unrelated cwd");
  const sharedConfig = path.join(root, "shared config.json");
  const localConfig = path.join(root, "agent config.json");
  fs.mkdirSync(workspaceRoot, { recursive: true });
  fs.mkdirSync(unrelatedCwd, { recursive: true });
  const projectPath = createProject(projectRoot, "RelativeGame", "5.8");
  fs.writeFileSync(sharedConfig, "{}", "utf8");
  fs.writeFileSync(localConfig, "{}", "utf8");
  const previousSharedConfig = process.env.SHARED_UNREAL_CONFIG;
  const previousCwd = process.cwd();
  const controllerCalls = [];
  process.env.SHARED_UNREAL_CONFIG = sharedConfig;
  try {
    process.chdir(unrelatedCwd);
    const relativeProject = path.relative(workspaceRoot, projectPath);
    const result = await setActiveProject(workspaceRoot, localConfig, {
      projectPath: relativeProject,
      invokeProjectController: async (argv) => {
        controllerCalls.push(argv);
        return { ok: true };
      },
    });
    assert.strictEqual(result.ok, true);
    assert.strictEqual(result.activeProject, path.resolve(projectPath));
    assert.deepStrictEqual(controllerCalls, [["--switch", path.resolve(projectPath)]]);
    assert.notStrictEqual(
      result.activeProject,
      path.resolve(unrelatedCwd, relativeProject),
    );
  } finally {
    process.chdir(previousCwd);
    if (previousSharedConfig === undefined) delete process.env.SHARED_UNREAL_CONFIG;
    else process.env.SHARED_UNREAL_CONFIG = previousSharedConfig;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("resolveBuildPlan keeps relative project and config roots workspace-relative", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-relative-build-plan-"));
  const workspaceRoot = path.join(root, "workspace with spaces");
  const configuredRoot = path.join(workspaceRoot, "프로젝트 모음");
  const unrelatedCwd = path.join(root, "unrelated cwd");
  const sharedConfig = path.join(root, "shared config.json");
  const localConfig = path.join(root, "agent config.json");
  const association = "{RELATIVE-CUSTOM-ENGINE}";
  fs.mkdirSync(workspaceRoot, { recursive: true });
  fs.mkdirSync(unrelatedCwd, { recursive: true });
  const projectPath = createProject(configuredRoot, "BuildRelativeGame", association);
  const engineRoot = createEngine(root, "Source Engine");
  fs.writeFileSync(sharedConfig, "{}", "utf8");
  fs.writeFileSync(
    localConfig,
    JSON.stringify({
      projectSearchRoots: [path.relative(workspaceRoot, configuredRoot)],
      engineRootsByAssociation: { [association]: engineRoot },
    }),
    "utf8",
  );
  const previousSharedConfig = process.env.SHARED_UNREAL_CONFIG;
  const previousCwd = process.cwd();
  process.env.SHARED_UNREAL_CONFIG = sharedConfig;
  try {
    process.chdir(unrelatedCwd);
    const relativeProject = path.relative(workspaceRoot, projectPath);
    const result = await resolveBuildPlan(workspaceRoot, localConfig, {
      project: relativeProject,
    });
    assert.strictEqual(result.ok, true, JSON.stringify(result));
    assert.strictEqual(result.build.projectPath, path.resolve(projectPath));
    assert.strictEqual(result.build.engineRoot, path.resolve(engineRoot));
    assert.ok(result.roots.includes(path.resolve(configuredRoot)));
    assert.notStrictEqual(
      result.build.projectPath,
      path.resolve(unrelatedCwd, relativeProject),
    );
  } finally {
    process.chdir(previousCwd);
    if (previousSharedConfig === undefined) delete process.env.SHARED_UNREAL_CONFIG;
    else process.env.SHARED_UNREAL_CONFIG = previousSharedConfig;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("association-free build prefers workspace default engine before shared fallback", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-default-engine-precedence-"));
  const workspaceRoot = path.join(root, "workspace");
  const projectPath = createProject(workspaceRoot, "AssociationFreeGame", "");
  const localEngine = createEngine(root, "WorkspaceEngine");
  const sharedEngine = createEngine(root, "SharedEngine");
  const sharedConfig = path.join(root, "unreal-workspace.json");
  const localConfig = path.join(root, "agent-mcp.json");
  fs.writeFileSync(
    sharedConfig,
    JSON.stringify({ defaultEngineRoot: sharedEngine }),
    "utf8",
  );
  fs.writeFileSync(
    localConfig,
    JSON.stringify({ defaultEngineRoot: localEngine }),
    "utf8",
  );

  const previousSharedConfig = process.env.SHARED_UNREAL_CONFIG;
  const previousEngineRoot = process.env.UNREAL_ENGINE_ROOT;
  const previousEngineAssociation = process.env.UNREAL_ENGINE_ROOT_ASSOCIATION;
  process.env.SHARED_UNREAL_CONFIG = sharedConfig;
  delete process.env.UNREAL_ENGINE_ROOT;
  delete process.env.UNREAL_ENGINE_ROOT_ASSOCIATION;
  try {
    const localResult = await resolveBuildPlan(workspaceRoot, localConfig, {
      project: projectPath,
    });
    assert.strictEqual(localResult.ok, true, JSON.stringify(localResult));
    assert.strictEqual(localResult.build.engineAssociation, null);
    assert.strictEqual(localResult.build.engineRoot, path.resolve(localEngine));
    assert.notStrictEqual(localResult.build.engineRoot, path.resolve(sharedEngine));
    assert.strictEqual(localResult.build.engineSource, "config.defaultEngineRoot");

    fs.writeFileSync(localConfig, "{}", "utf8");
    const sharedFallback = await resolveBuildPlan(workspaceRoot, localConfig, {
      project: projectPath,
    });
    assert.strictEqual(sharedFallback.ok, true, JSON.stringify(sharedFallback));
    assert.strictEqual(sharedFallback.build.engineAssociation, null);
    assert.strictEqual(sharedFallback.build.engineRoot, path.resolve(sharedEngine));
    assert.strictEqual(sharedFallback.build.engineSource, "config.defaultEngineRoot");
  } finally {
    if (previousSharedConfig === undefined) delete process.env.SHARED_UNREAL_CONFIG;
    else process.env.SHARED_UNREAL_CONFIG = previousSharedConfig;
    if (previousEngineRoot === undefined) delete process.env.UNREAL_ENGINE_ROOT;
    else process.env.UNREAL_ENGINE_ROOT = previousEngineRoot;
    if (previousEngineAssociation === undefined) {
      delete process.env.UNREAL_ENGINE_ROOT_ASSOCIATION;
    } else {
      process.env.UNREAL_ENGINE_ROOT_ASSOCIATION = previousEngineAssociation;
    }
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("resolveBuildPlan never substitutes discovery for a missing explicit project path", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-missing-explicit-build-"));
  const workspaceRoot = path.join(root, "workspace");
  const configuredRoot = path.join(workspaceRoot, "projects");
  const sharedConfig = path.join(root, "shared.json");
  const localConfig = path.join(root, "agent.json");
  fs.mkdirSync(workspaceRoot, { recursive: true });
  createProject(configuredRoot, "FallbackGame", "5.8");
  fs.writeFileSync(sharedConfig, "{}", "utf8");
  fs.writeFileSync(
    localConfig,
    JSON.stringify({ projectSearchRoots: [path.relative(workspaceRoot, configuredRoot)] }),
    "utf8",
  );
  const previousSharedConfig = process.env.SHARED_UNREAL_CONFIG;
  process.env.SHARED_UNREAL_CONFIG = sharedConfig;
  try {
    const missing = path.join("missing project", "Missing.uproject");
    const result = await resolveBuildPlan(workspaceRoot, localConfig, { project: missing });
    assert.strictEqual(result.ok, false);
    assert.strictEqual(result.selected, null);
    assert.strictEqual(result.errorCode, "PROJECT_PATH_NOT_FOUND");
    assert.match(result.error, new RegExp(path.basename(missing), "u"));
  } finally {
    if (previousSharedConfig === undefined) delete process.env.SHARED_UNREAL_CONFIG;
    else process.env.SHARED_UNREAL_CONFIG = previousSharedConfig;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("host platform maps macOS to Mac instead of Linux", () => {
  assert.strictEqual(defaultPlatform("win32"), "Win64");
  assert.strictEqual(defaultPlatform("darwin"), "Mac");
  assert.strictEqual(defaultPlatform("linux"), "Linux");
});

test("search root environment values use the host path-list delimiter", () => {
  assert.deepStrictEqual(splitSearchRoots("C:\\One;D:\\Two", "win32"), ["C:\\One", "D:\\Two"]);
  assert.deepStrictEqual(splitSearchRoots("/srv/One:/srv/Two", "linux"), ["/srv/One", "/srv/Two"]);
  assert.deepStrictEqual(splitSearchRoots("/Volumes/One:/Volumes/Two", "darwin"), ["/Volumes/One", "/Volumes/Two"]);
});

test("path identity folds case only on Windows hosts", () => {
  assert.strictEqual(pathIdentity("CaseSensitive/Project", "win32"), pathIdentity("casesensitive/project", "win32"));
  assert.notStrictEqual(pathIdentity("CaseSensitive/Project", "linux"), pathIdentity("casesensitive/project", "linux"));
  assert.notStrictEqual(pathIdentity("CaseSensitive/Project", "darwin"), pathIdentity("casesensitive/project", "darwin"));
  assert.notStrictEqual(pathIdentity("Caf\u00e9/Project", "linux"), pathIdentity("Cafe\u0301/Project", "linux"));
  assert.notStrictEqual(pathIdentity("Caf\u00e9/Project", "darwin"), pathIdentity("Cafe\u0301/Project", "darwin"));
  assert.notStrictEqual(pathIdentity("Caf\u00e9/Project", "win32"), pathIdentity("Cafe\u0301/Project", "win32"));
  assert.notStrictEqual(pathIdentity("\u0130/Project", "win32"), pathIdentity("i\u0307/Project", "win32"));
});

test("unique project roots preserve case-distinct POSIX directories", () => {
  const linuxRoots = uniquePaths(["CaseProject", "caseproject"], "linux");
  const windowsRoots = uniquePaths(["CaseProject", "caseproject"], "win32");
  assert.strictEqual(linuxRoots.length, 2);
  assert.strictEqual(windowsRoots.length, 1);
});

test("resolveSearchRoots accepts injected host environment without machine defaults", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-host-roots-"));
  const workspaceRoot = path.join(root, "workspace");
  const localConfig = path.join(root, "agent-mcp.json");
  fs.mkdirSync(workspaceRoot, { recursive: true });
  fs.writeFileSync(localConfig, "{}", "utf8");
  const previousSharedConfig = process.env.SHARED_UNREAL_CONFIG;
  process.env.SHARED_UNREAL_CONFIG = path.join(root, "missing-shared.json");
  try {
    // Relative roots keep this host-independent while exercising the POSIX
    // list delimiter on a Windows CI runner.
    const first = "PortableRootOne";
    const second = "PortableRootTwo";
    const { roots } = resolveSearchRoots(workspaceRoot, localConfig, {
      hostPlatform: "linux",
      env: { PROJECT_SEARCH_ROOTS: `${first}:${second}` },
      homeDirectory: path.join(root, "home"),
    });
    assert.deepStrictEqual(roots, [path.resolve(workspaceRoot), path.resolve(first), path.resolve(second)]);
  } finally {
    if (previousSharedConfig === undefined) delete process.env.SHARED_UNREAL_CONFIG;
    else process.env.SHARED_UNREAL_CONFIG = previousSharedConfig;
  }
});

test("Windows engine discovery does not merge Unicode I-dot lookalike roots", async (t) => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-idot-engines-"));
  const roots = [path.join(parent, "\u0130Engine"), path.join(parent, "i\u0307Engine")];
  try {
    for (const engineRoot of roots) {
      const buildBat = path.join(engineRoot, "Engine", "Build", "BatchFiles", "Build.bat");
      fs.mkdirSync(path.dirname(buildBat), { recursive: true });
      fs.writeFileSync(buildBat, "@echo off\r\nexit /b 0\r\n", "utf8");
    }
    if (sameFilesystemEntry(roots[0], roots[1])) {
      t.skip("host filesystem aliases the two Unicode spellings");
      return;
    }
    const installs = await findEngineInstalls({
      hostPlatform: "win32",
      roots,
      env: {},
    });
    assert.strictEqual(installs.length, 2);
    assert.deepStrictEqual(
      new Set(installs.map((item) => item.engineRoot)),
      new Set(roots.map((item) => path.resolve(item))),
    );
  } finally {
    fs.rmSync(parent, { recursive: true, force: true });
  }
});

test("Windows project discovery keeps Unicode I-dot lookalike owners distinct", async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-idot-projects-"));
  const workspaceRoot = path.join(root, "workspace");
  const projectRoot = path.join(root, "projects");
  const sharedConfig = path.join(root, "unreal-workspace.json");
  const localConfig = path.join(root, "agent-mcp.json");
  fs.mkdirSync(workspaceRoot, { recursive: true });
  const idotProject = createProject(projectRoot, "\u0130Game", "5.8");
  const lookalikeProject = createProject(projectRoot, "i\u0307Game", "5.8");
  if (sameFilesystemEntry(idotProject, lookalikeProject)) {
    t.skip("host filesystem aliases the two Unicode spellings");
    fs.rmSync(root, { recursive: true, force: true });
    return;
  }
  fs.utimesSync(idotProject, new Date("2025-01-01T00:00:00Z"), new Date("2025-01-01T00:00:00Z"));
  fs.utimesSync(lookalikeProject, new Date("2025-01-02T00:00:00Z"), new Date("2025-01-02T00:00:00Z"));
  fs.writeFileSync(sharedConfig, JSON.stringify({ projectSearchRoots: [projectRoot] }), "utf8");
  fs.writeFileSync(localConfig, "{}", "utf8");
  const previousSharedConfig = process.env.SHARED_UNREAL_CONFIG;
  process.env.SHARED_UNREAL_CONFIG = sharedConfig;
  try {
    const result = await resolveProjectSelection(workspaceRoot, localConfig, {
      hostPlatform: "win32",
      maxDepth: 4,
    });
    assert.deepStrictEqual(
      new Set(result.projects.map((item) => item.projectName)),
      new Set(["\u0130Game", "i\u0307Game"]),
    );
    const selected = await resolveProjectSelection(workspaceRoot, localConfig, {
      hostPlatform: "win32",
      hint: "\u0130Game",
      maxDepth: 4,
    });
    assert.strictEqual(selected.selected?.projectName, "\u0130Game");
  } finally {
    if (previousSharedConfig === undefined) delete process.env.SHARED_UNREAL_CONFIG;
    else process.env.SHARED_UNREAL_CONFIG = previousSharedConfig;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("Windows project selection keeps the workspace descendant score", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-win-workspace-score-"));
  const workspaceRoot = path.join(root, "workspace");
  const externalRoot = path.join(root, "external");
  const sharedConfig = path.join(root, "unreal-workspace.json");
  const localConfig = path.join(root, "agent-mcp.json");
  const workspaceProject = createProject(workspaceRoot, "WorkspaceGame", "5.8");
  createProject(externalRoot, "ExternalGame", "5.8");
  fs.writeFileSync(sharedConfig, JSON.stringify({ projectSearchRoots: [externalRoot] }), "utf8");
  fs.writeFileSync(localConfig, "{}", "utf8");
  const previousSharedConfig = process.env.SHARED_UNREAL_CONFIG;
  process.env.SHARED_UNREAL_CONFIG = sharedConfig;
  try {
    const result = await resolveProjectSelection(workspaceRoot, localConfig, {
      hostPlatform: "win32",
      maxDepth: 4,
      env: {},
    });
    assert.strictEqual(result.selected?.projectPath, path.resolve(workspaceProject));
    assert.strictEqual(result.selectionReason, "best-score");
    assert.ok(result.selected.score > result.projects.find(
      (item) => item.projectName === "ExternalGame",
    ).score);
  } finally {
    if (previousSharedConfig === undefined) delete process.env.SHARED_UNREAL_CONFIG;
    else process.env.SHARED_UNREAL_CONFIG = previousSharedConfig;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("Windows workspace descendant remains browseable with slash canonical identities", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-win-browse-scope-"));
  const workspaceRoot = path.join(root, "workspace");
  const projectPath = createProject(workspaceRoot, "BrowseGame", "5.8");
  try {
    const context = buildProjectBrowsePaths(projectPath, workspaceRoot, "win32");
    assert.strictEqual(context.browseAvailable, true);
    assert.strictEqual(context.sourceBrowsePath, "BrowseGame/Source/BrowseGame");
    assert.strictEqual(context.contentBrowsePath, "BrowseGame/Content");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("engine discovery accepts native Mac and Linux Build.sh layouts", async () => {
  for (const [hostPlatform, hostFolder] of [["darwin", "Mac"], ["linux", "Linux"]]) {
    const parent = fs.mkdtempSync(path.join(os.tmpdir(), `unreal-${hostPlatform}-engines-`));
    const engineRoot = path.join(parent, "UE_5.8");
    const script = path.join(engineRoot, "Engine", "Build", "BatchFiles", hostFolder, "Build.sh");
    fs.mkdirSync(path.dirname(script), { recursive: true });
    fs.writeFileSync(script, "#!/usr/bin/env sh\nexit 0\n", "utf8");

    const installs = await findEngineInstalls({
      hostPlatform,
      roots: [parent],
      env: {},
    });
    assert.strictEqual(installs.length, 1);
    assert.strictEqual(installs[0].engineRoot, path.resolve(engineRoot));
    assert.strictEqual(installs[0].buildTool, script);
    assert.strictEqual(installs[0].buildToolKind, "build_sh");
  }
});

test("engine discovery sorts semantic versions so UE 5.10 is newer than UE 5.9", async () => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-semver-engines-"));
  for (const name of ["UE_5.9", "UE_5.10"]) {
    const script = path.join(parent, name, "Engine", "Build", "BatchFiles", "Linux", "Build.sh");
    fs.mkdirSync(path.dirname(script), { recursive: true });
    fs.writeFileSync(script, "#!/usr/bin/env sh\nexit 0\n", "utf8");
  }
  const installs = await findEngineInstalls({ hostPlatform: "linux", roots: [parent], env: {} });
  assert.deepStrictEqual(installs.map((item) => item.folderName), ["UE_5.9", "UE_5.10"]);
});

test("custom EngineAssociation fails closed instead of selecting default or newest engine", async () => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-custom-association-"));
  const fallback = createEngine(parent, "UE_5.9", "linux");
  const sourceBuild = createEngine(parent, "SourceBuild", "linux");
  const association = "{01234567-89AB-CDEF-0123-456789ABCDEF}";
  try {
    const result = await resolveEngineRoot(
      association,
      { defaultEngineRoot: fallback },
      "",
      { hostPlatform: "linux", roots: [parent], env: {} },
    );
    assert.strictEqual(result.errorCode, "ENGINE_ASSOCIATION_UNRESOLVED");
    assert.strictEqual(result.engineRoot, "");
    assert.match(result.error, /engineRootsByAssociation/);
    assert.notStrictEqual(result.engineRoot, sourceBuild);
  } finally {
    fs.rmSync(parent, { recursive: true, force: true });
  }
});

test("custom EngineAssociation accepts an exact configured mapping or intentional override", async () => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-custom-map-"));
  const mapped = createEngine(parent, "SourceBuild", "linux");
  const override = createEngine(parent, "OverrideBuild", "linux");
  const association = "custom-source-build-id";
  try {
    const mappedResult = await resolveEngineRoot(
      association,
      { engineRootsByAssociation: { [association]: mapped } },
      "",
      { hostPlatform: "linux", roots: [parent], env: {} },
    );
    assert.strictEqual(mappedResult.engineRoot, path.resolve(mapped));
    assert.strictEqual(mappedResult.source, "config.engineRootsByAssociation");

    const mappedWithStaleEnvironment = await resolveEngineRoot(
      association,
      { engineRootsByAssociation: { [association]: mapped } },
      "",
      {
        hostPlatform: "linux",
        roots: [parent],
        env: { UNREAL_ENGINE_ROOT: override },
      },
    );
    assert.strictEqual(mappedWithStaleEnvironment.engineRoot, path.resolve(mapped));
    assert.strictEqual(
      mappedWithStaleEnvironment.source,
      "config.engineRootsByAssociation",
    );

    const explicitResult = await resolveEngineRoot(
      association,
      {},
      override,
      { hostPlatform: "linux", roots: [parent], env: {} },
    );
    assert.strictEqual(explicitResult.engineRoot, path.resolve(override));
    assert.strictEqual(explicitResult.source, "argument");

    const environmentResult = await resolveEngineRoot(
      association,
      {},
      "",
      { hostPlatform: "linux", roots: [parent], env: { UNREAL_ENGINE_ROOT: override } },
    );
    assert.strictEqual(environmentResult.engineRoot, path.resolve(override));
    assert.strictEqual(environmentResult.source, "environment");
  } finally {
    fs.rmSync(parent, { recursive: true, force: true });
  }
});

test("numeric EngineAssociation discovers only its exact UE folder across engine generations", async () => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-numeric-association-"));
  const ue427 = createEngine(parent, "UE_4.27", "linux");
  createEngine(parent, "UE_5.10", "linux");
  try {
    const exact = await resolveEngineRoot(
      "UE_4.27",
      {},
      "",
      { hostPlatform: "linux", roots: [parent], env: {} },
    );
    assert.strictEqual(exact.engineRoot, path.resolve(ue427));
    assert.strictEqual(exact.source, "EngineAssociation");

    const missing = await resolveEngineRoot(
      "5.6",
      { defaultEngineRoot: ue427 },
      "",
      { hostPlatform: "linux", roots: [parent], env: {} },
    );
    assert.strictEqual(missing.errorCode, "ENGINE_ASSOCIATION_UNRESOLVED");
    assert.strictEqual(missing.engineRoot, "");

    const associationFree = await resolveEngineRoot(
      "",
      { defaultEngineRoot: ue427 },
      "",
      { hostPlatform: "linux", roots: [parent], env: {} },
    );
    assert.strictEqual(associationFree.engineRoot, path.resolve(ue427));
    assert.strictEqual(associationFree.source, "config.defaultEngineRoot");
  } finally {
    fs.rmSync(parent, { recursive: true, force: true });
  }
});

test("numeric EngineAssociation ignores a stale install-time UNREAL_ENGINE_ROOT pin", async () => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-stale-engine-pin-"));
  const expected = createEngine(parent, "UE_5.6", "linux");
  const stale = createEngine(parent, "UE_5.8", "linux");
  try {
    const result = await resolveEngineRoot(
      "5.6",
      {},
      "",
      {
        hostPlatform: "linux",
        roots: [parent],
        env: { UNREAL_ENGINE_ROOT: stale },
      },
    );
    assert.strictEqual(result.engineRoot, path.resolve(expected));
    assert.strictEqual(result.source, "EngineAssociation");
  } finally {
    fs.rmSync(parent, { recursive: true, force: true });
  }
});

test("numeric EngineAssociation rejects an environment engine with no provable version", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-unknown-env-version-"));
  const unknown = createEngine(root, "Source Engine Without Version", "linux");
  try {
    const unresolved = await resolveEngineRoot(
      "5.8",
      {},
      "",
      {
        hostPlatform: "linux",
        roots: [],
        env: { UNREAL_ENGINE_ROOT: unknown },
        installIniPaths: [],
      },
    );
    const explicit = await resolveEngineRoot(
      "5.8",
      {},
      unknown,
      {
        hostPlatform: "linux",
        roots: [],
        env: {},
        installIniPaths: [],
      },
    );

    assert.strictEqual(unresolved.errorCode, "ENGINE_ASSOCIATION_UNRESOLVED");
    assert.strictEqual(unresolved.engineRoot, "");
    assert.strictEqual(explicit.engineRoot, path.resolve(unknown));
    assert.strictEqual(explicit.source, "argument");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("Windows launcher manifest resolves a numeric association outside common roots", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-launcher-registration-"));
  const programData = path.join(root, "Program Data");
  const engine = createEngine(path.join(root, "다른 드라이브"), "Epic 설치 With Spaces", "win32");
  writeEngineVersion(engine, "5.6");
  const manifest = path.join(
    programData,
    "Epic",
    "UnrealEngineLauncher",
    "LauncherInstalled.dat",
  );
  fs.mkdirSync(path.dirname(manifest), { recursive: true });
  fs.writeFileSync(
    manifest,
    JSON.stringify({
      InstallationList: [
        { AppName: "UE_5.6", InstallLocation: engine },
        { AppName: "UE_5.8", InstallLocation: "../outside" },
        { AppName: "../../bad", InstallLocation: engine },
      ],
    }),
    "utf8",
  );
  try {
    const result = await resolveEngineRoot(
      "5.6",
      {},
      "",
      {
        hostPlatform: "win32",
        roots: [],
        env: { PROGRAMDATA: programData },
        registryInstallations: {},
      },
    );
    assert.strictEqual(result.engineRoot, path.resolve(engine));
    assert.strictEqual(result.source, "registered.launcher-manifest");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("Windows HKCU registration resolves GUID and rejects a relative root", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-registry-registration-"));
  const engine = createEngine(root, "소스 빌드 With Spaces", "win32");
  const association = "{01234567-89AB-CDEF-0123-456789ABCDEF}";
  const mappings = {
    [association]: engine,
    "{BAD-RELATIVE}": "../outside",
  };
  try {
    const result = await resolveEngineRoot(
      association,
      {},
      "",
      {
        hostPlatform: "win32",
        roots: [],
        env: {},
        launcherManifestPaths: [],
        registryInstallations: mappings,
      },
    );
    const rejected = await resolveEngineRoot(
      "{BAD-RELATIVE}",
      {},
      "",
      {
        hostPlatform: "win32",
        roots: [],
        env: {},
        launcherManifestPaths: [],
        registryInstallations: mappings,
      },
    );
    assert.strictEqual(result.engineRoot, path.resolve(engine));
    assert.strictEqual(result.source, "registered.windows-registry");
    assert.strictEqual(rejected.errorCode, "ENGINE_ASSOCIATION_UNRESOLVED");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("exact GUID registration wins over a stale environment root", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-registry-over-env-"));
  const stale = createEngine(root, "Previous Project Engine", "win32");
  const registered = createEngine(root, "현재 프로젝트 엔진", "win32");
  const association = "{BBBBBBBB-1111-2222-3333-CCCCCCCCCCCC}";
  try {
    const result = await resolveEngineRoot(
      association,
      {},
      "",
      {
        hostPlatform: "win32",
        roots: [],
        env: { UNREAL_ENGINE_ROOT: stale },
        launcherManifestPaths: [],
        registryInstallations: { [association]: registered },
      },
    );
    assert.strictEqual(result.engineRoot, path.resolve(registered));
    assert.strictEqual(result.source, "registered.windows-registry");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("installer-managed environment cannot retarget another unregistered custom GUID", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-managed-env-guid-"));
  const installedEngine = createEngine(root, "Installed Project Engine", "linux");
  const installedFor = "{AAAAAAAA-1111-2222-3333-AAAAAAAAAAAA}";
  const requested = "{BBBBBBBB-1111-2222-3333-BBBBBBBBBBBB}";
  try {
    const managedResults = [];
    for (const metadata of [installedFor, ""]) {
      managedResults.push(await resolveEngineRoot(
        requested,
        {},
        "",
        {
          hostPlatform: "linux",
          roots: [],
          env: {
            UNREAL_ENGINE_ROOT: installedEngine,
            UNREAL_ENGINE_ROOT_ASSOCIATION: metadata,
          },
          installIniPaths: [],
        },
      ));
    }
    const matchingManaged = await resolveEngineRoot(
      requested,
      {},
      "",
      {
        hostPlatform: "linux",
        roots: [],
        env: {
          UNREAL_ENGINE_ROOT: installedEngine,
          UNREAL_ENGINE_ROOT_ASSOCIATION: requested,
        },
        installIniPaths: [],
      },
    );
    const intentionalShellOverride = await resolveEngineRoot(
      requested,
      {},
      "",
      {
        hostPlatform: "linux",
        roots: [],
        env: { UNREAL_ENGINE_ROOT: installedEngine },
        installIniPaths: [],
      },
    );
    for (const managed of managedResults) {
      assert.strictEqual(managed.errorCode, "ENGINE_ASSOCIATION_UNRESOLVED");
      assert.strictEqual(managed.engineRoot, "");
    }
    assert.strictEqual(matchingManaged.engineRoot, path.resolve(installedEngine));
    assert.strictEqual(matchingManaged.source, "environment");
    assert.strictEqual(intentionalShellOverride.engineRoot, path.resolve(installedEngine));
    assert.strictEqual(intentionalShellOverride.source, "environment");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("Linux and macOS Install.ini exact mappings stay inside Installations", async () => {
  for (const fixture of [
    {
      hostPlatform: "linux",
      association: "{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}",
      registeredKey: "{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}",
      iniParts: [".config", "Epic", "UnrealEngine", "Install.ini"],
    },
    {
      hostPlatform: "darwin",
      association: "5.7",
      registeredKey: "UE_5.7",
      iniParts: ["Library", "Application Support", "Epic", "UnrealEngine", "Install.ini"],
    },
  ]) {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), `unreal-${fixture.hostPlatform}-install-`));
    const homeDirectory = path.join(root, `${fixture.hostPlatform} home`);
    const engine = createEngine(
      path.join(root, fixture.hostPlatform),
      "엔진 루트 With Spaces",
      fixture.hostPlatform,
    );
    const outside = createEngine(root, `${fixture.hostPlatform}-outside`, fixture.hostPlatform);
    writeEngineVersion(engine, "5.7");
    const installIni = path.join(homeDirectory, ...fixture.iniParts);
    fs.mkdirSync(path.dirname(installIni), { recursive: true });
    fs.writeFileSync(
      installIni,
      [
        "[Outside]",
        `OUTSIDE=${outside}`,
        "[Installations]",
        `${fixture.registeredKey}=${engine}`,
        "RELATIVE=../../outside",
      ].join("\n"),
      "utf8",
    );
    try {
      const options = {
        hostPlatform: fixture.hostPlatform,
        roots: [],
        env: {},
        homeDirectory,
      };
      const result = await resolveEngineRoot(fixture.association, {}, "", options);
      const outsideResult = await resolveEngineRoot("OUTSIDE", {}, "", options);
      const relativeResult = await resolveEngineRoot("RELATIVE", {}, "", options);
      assert.strictEqual(result.engineRoot, path.resolve(engine));
      assert.strictEqual(result.source, "registered.install-ini");
      assert.strictEqual(outsideResult.errorCode, "ENGINE_ASSOCIATION_UNRESOLVED");
      assert.strictEqual(relativeResult.errorCode, "ENGINE_ASSOCIATION_UNRESOLVED");
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  }
});

test("conflicting registered roots for one association fail closed", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-conflicting-registration-"));
  const first = createEngine(path.join(root, "first"), "Engine56", "win32");
  const second = createEngine(path.join(root, "second"), "Engine56", "win32");
  writeEngineVersion(first, "5.6");
  writeEngineVersion(second, "5.6");
  const manifest = path.join(root, "LauncherInstalled.dat");
  fs.writeFileSync(
    manifest,
    JSON.stringify({
      InstallationList: [
        { AppName: "UE_5.6", InstallLocation: first },
        { AppName: "UE_5.6", InstallLocation: second },
      ],
    }),
    "utf8",
  );
  try {
    const result = await resolveEngineRoot(
      "5.6",
      {},
      "",
      {
        hostPlatform: "win32",
        roots: [],
        env: {},
        launcherManifestPaths: [manifest],
        registryInstallations: {},
      },
    );
    assert.strictEqual(result.errorCode, "ENGINE_ASSOCIATION_UNRESOLVED");
    assert.match(result.error, /multiple conflicting registered engine roots/u);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("resolveBuildPlan exposes an unresolved custom association to the caller", async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-build-plan-association-"));
  const workspaceRoot = path.join(root, "workspace");
  const projectRoot = path.join(root, "projects");
  const configPath = path.join(root, "agent-mcp.json");
  const sharedConfig = path.join(root, "unreal-workspace.json");
  const project = createProject(projectRoot, "SourceGame", "source-build-guid");
  const fallback = createEngine(root, "FallbackEngine");
  fs.mkdirSync(workspaceRoot, { recursive: true });
  fs.writeFileSync(
    configPath,
    JSON.stringify({ projectSearchRoots: [projectRoot], defaultEngineRoot: fallback }),
    "utf8",
  );
  fs.writeFileSync(sharedConfig, "{}", "utf8");
  const previousSharedConfig = process.env.SHARED_UNREAL_CONFIG;
  process.env.SHARED_UNREAL_CONFIG = sharedConfig;
  try {
    const result = await resolveBuildPlan(workspaceRoot, configPath, { project });
    assert.strictEqual(result.ok, false);
    assert.strictEqual(result.errorCode, "ENGINE_ASSOCIATION_UNRESOLVED");
    assert.match(result.error, /source-build-guid/);
  } finally {
    if (previousSharedConfig === undefined) delete process.env.SHARED_UNREAL_CONFIG;
    else process.env.SHARED_UNREAL_CONFIG = previousSharedConfig;
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test("workspace controller resolution prefers explicit and packaged roots before legacy home", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "unreal-workspace-root-"));
  const repositoryRoot = path.join(root, "portable-checkout");
  const explicitRoot = path.join(root, "explicit-workspace");
  const home = path.join(root, "home");
  try {
    fs.mkdirSync(path.join(repositoryRoot, "scripts"), { recursive: true });
    fs.writeFileSync(path.join(repositoryRoot, "scripts", "project_controller.py"), "# controller\n");

    assert.strictEqual(
      resolveAgentWorkspaceRoot({
        env: { UNREAL_WORKSPACE_ROOT: explicitRoot },
        repositoryRoot,
        homeDir: home,
      }),
      path.resolve(explicitRoot),
    );
    assert.strictEqual(
      resolveAgentWorkspaceRoot({ env: {}, repositoryRoot, homeDir: home }),
      path.resolve(repositoryRoot),
    );
    assert.strictEqual(
      resolveAgentWorkspaceRoot({
        env: {},
        repositoryRoot: path.join(root, "mcp-only"),
        homeDir: home,
      }),
      path.join(path.resolve(home), ".lmstudio", "Unreal58-RAG"),
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

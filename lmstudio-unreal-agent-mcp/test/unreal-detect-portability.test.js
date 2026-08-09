"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const {
  resolveProjectSelection,
  resolveSearchRoots,
  findEngineInstalls,
  defaultPlatform,
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

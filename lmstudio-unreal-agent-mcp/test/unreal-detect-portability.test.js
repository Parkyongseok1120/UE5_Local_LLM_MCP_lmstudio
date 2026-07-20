"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");
const { resolveProjectSelection, resolveSearchRoots } = require("../src/unreal-detect");

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


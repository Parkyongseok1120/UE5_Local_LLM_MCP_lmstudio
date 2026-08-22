"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { absolutePathIsWithin } = require("./filesystem-path-identity");
const {
  pathIdentity,
  projectNameFromPath,
} = require("./unreal-project-core");

function buildProjectBrowsePaths(
  activeProjectPath,
  workspaceRoot,
  hostPlatform = process.platform,
) {
  const resolvedProject = path.resolve(activeProjectPath);
  const projectDir = path.dirname(resolvedProject);
  const projectName = projectNameFromPath(resolvedProject);
  const workspace = path.resolve(workspaceRoot || process.cwd());
  let modules = [];
  try {
    const data = JSON.parse(fs.readFileSync(resolvedProject, "utf8"));
    modules = Array.isArray(data.Modules)
      ? data.Modules.map((module) => module.Name).filter(Boolean)
      : [];
  } catch {
    modules = [];
  }

  const sourceModules = [];
  const sourceRootDir = path.join(projectDir, "Source");
  if (fs.existsSync(sourceRootDir)) {
    for (const entry of fs.readdirSync(sourceRootDir, { withFileTypes: true })) {
      if (entry.isDirectory()) sourceModules.push(entry.name);
    }
  }
  const primaryModule = modules[0] || sourceModules[0] || projectName;
  let sourceRoot = path.join(projectDir, "Source", primaryModule);
  if (!fs.existsSync(sourceRoot) && sourceModules.length) {
    sourceRoot = path.join(projectDir, "Source", sourceModules[0]);
  }
  const contentRoot = path.join(projectDir, "Content");
  const exportDir = path.join(projectDir, "Saved", "LmStudioMetadataExports");

  let browseAvailable = false;
  try {
    const projectIdentity = pathIdentity(projectDir, hostPlatform);
    const workspaceIdentity = pathIdentity(workspace, hostPlatform);
    browseAvailable = projectIdentity === workspaceIdentity
      || absolutePathIsWithin(projectDir, workspace, hostPlatform);
  } catch {
    browseAvailable = false;
  }
  const relativeToWorkspace = (target) => {
    try {
      const value = path.relative(workspace, target);
      if (!value || value.startsWith("..")) return "";
      return value.split(path.sep).join("/");
    } catch {
      return "";
    }
  };

  return {
    uprojectPath: resolvedProject,
    projectName,
    projectDir,
    modules,
    primaryModule,
    sourceRoot,
    sourceModules,
    contentRoot,
    exportDir,
    workspaceRoot: workspace,
    browseAvailable,
    sourceBrowsePath: browseAvailable ? relativeToWorkspace(sourceRoot) : "",
    contentBrowsePath: browseAvailable ? relativeToWorkspace(contentRoot) : "",
    browseNote: browseAvailable
      ? ""
      : "Project is outside WORKSPACE_ROOT; search_files/list_directory may be unavailable.",
  };
}

module.exports = { buildProjectBrowsePaths };

"use strict";

const path = require("node:path");
const {
  getActiveProject,
  invokeProjectController,
} = require("./unreal-config");
const { discoverProjects } = require("./unreal-project-discovery");
const { resolveExactProjectNameSelection } = require("./unreal-project-name-selection");
const {
  exists,
  findTargetNames,
  pathIdentity,
  readUProject,
} = require("./unreal-project-core");

async function setActiveProject(workspaceRoot, configPath, options = {}) {
  const controllerInvoker = typeof options.invokeProjectController === "function"
    ? options.invokeProjectController
    : invokeProjectController;

  if (options.clear === true || options.projectPath === null) {
    // The Python controller performs the sole shared-state mutation. A failed
    // clear must remain a failure and must not touch or consult local state.
    const controller = await controllerInvoker(["--clear"]);
    if (!controller.ok) return controller;
    return controller;
  }

  const projectPath = String(options.projectPath || "").trim();
  if (projectPath) {
    const resolved = path.isAbsolute(projectPath)
      ? path.resolve(projectPath)
      : path.resolve(workspaceRoot, projectPath);
    if (!(await exists(resolved))) {
      return { ok: false, error: `Project not found: ${resolved}` };
    }
    if (!resolved.toLowerCase().endsWith(".uproject")) {
      return { ok: false, error: "activeProject must be a .uproject file path." };
    }
    const argv = ["--switch", resolved];
    const controller = await controllerInvoker(argv);
    if (!controller.ok) return controller;

    const info = await readUProject(resolved);
    const targets = await findTargetNames(info.projectDir, info.projectName);
    return {
      ...controller,
      activeProject: resolved,
      projectName: info.projectName,
      preferredTarget: targets.preferredTarget,
      readiness: controller.readiness || null,
    };
  }

  const hint = String(options.hint || "").trim();
  if (!hint) {
    return { ok: false, error: "Provide projectPath, hint, or clear=true." };
  }

  const selection = await resolveExactProjectNameSelection(workspaceRoot, configPath, {
    ...options,
    name: hint,
  });
  if (!selection.selected) {
    return {
      ok: false,
      errorCode: selection.errorCode || "PROJECT_NAME_NOT_FOUND",
      error: selection.error || `No project matched hint: ${hint}`,
      selectionReason: selection.selectionReason,
      suggestions: selection.suggestions || [],
    };
  }

  const argv = ["--switch", selection.selected.projectPath];
  const controller = await controllerInvoker(argv);
  if (!controller.ok) return controller;
  return {
    ...controller,
    ok: true,
    activeProject: selection.selected.projectPath,
    projectName: selection.selected.projectName,
    preferredTarget: selection.selected.preferredTarget,
    selectionReason: selection.selectionReason,
    message: `Active project set to ${selection.selected.projectFile}`,
    readiness: controller.readiness || null,
  };
}

async function listUnrealProjects(workspaceRoot, configPath, options = {}) {
  const discovery = await discoverProjects(workspaceRoot, configPath, options);
  const activeProject = getActiveProject(configPath);
  const hostPlatform = options.hostPlatform || process.platform;
  return {
    activeProject,
    searchRoots: discovery.roots,
    projects: (discovery.rawProjects || discovery.projects).map((project) => ({
      projectFile: project.projectFile,
      projectPath: project.projectPath,
      projectName: project.projectName,
      preferredTarget: project.preferredTarget,
      allTargets: project.allTargets,
      engineAssociation: project.engineAssociation,
      modifiedAt: project.modifiedAt,
      isActive: activeProject
        ? pathIdentity(project.projectPath, hostPlatform) === pathIdentity(activeProject, hostPlatform)
        : false,
    })),
  };
}

module.exports = { listUnrealProjects, setActiveProject };

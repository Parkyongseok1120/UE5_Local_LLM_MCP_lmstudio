"use strict";

const {
  buildProjectBrowsePaths,
  listUnrealProjects,
  setActiveProject,
} = require("./unreal-detect.js");
const { success, failure } = require("./direct-response.js");
const { clamp, envFlag, statOrNull, statSignature } = require("./direct-runtime-shared.js");

function createProjectCapabilities(context) {
  const {
    configPath,
    dedupe,
    env,
    getActive,
    options,
    runtimeOwner,
    workspaceRoot,
  } = context;

  async function getWorkspaceInfo(args) {
    const activeProject = getActive();
    const configStat = await statOrNull(configPath);
    return dedupe("get_workspace_info", args, `${activeProject || ""}|${statSignature(configStat)}`, success({
      executionMode: runtimeOwner,
      workspaceRoot,
      configPath,
      activeProject,
      project: activeProject ? buildProjectBrowsePaths(activeProject, workspaceRoot) : null,
      safety: {
        writesEnabled: envFlag(env, "ALLOW_WRITE", false),
        commandsEnabled: envFlag(env, "ALLOW_COMMANDS", false),
        buildsEnabled: envFlag(env, "ALLOW_UNREAL_BUILD", false),
        sourceDeletionEnabled: envFlag(env, "ALLOW_SOURCE_DELETE", false),
        optimisticConcurrency: true,
        atomicWrites: true,
        pathContainment: true,
      },
    }));
  }

  async function listProjects(args) {
    const listed = await listUnrealProjects(workspaceRoot, configPath, {
      maxDepth: clamp(args.maxDepth, 4, 1, 8),
    });
    return success({ executionMode: runtimeOwner, ...listed });
  }

  async function getActiveProject() {
    const activeProject = getActive();
    return activeProject
      ? success({ executionMode: runtimeOwner, activeProject, ...buildProjectBrowsePaths(activeProject, workspaceRoot) })
      : success({ executionMode: runtimeOwner, activeProject: null, selected: false });
  }

  async function selectActiveProject(args) {
    let payload = await (options.setActiveProject || setActiveProject)(workspaceRoot, configPath, {
      projectPath: args.clear === true ? null : args.projectPath,
      hint: args.hint,
      clear: args.clear === true,
    });
    payload = payload.ok === false
      ? failure(payload.errorCode || "PROJECT_SELECTION_FAILED", payload.error || "Could not select project", { details: payload, retryAllowed: true })
      : success({ executionMode: runtimeOwner, ...payload });
    return payload;
  }

  return {
    get_active_project: getActiveProject,
    get_workspace_info: getWorkspaceInfo,
    list_unreal_projects: listProjects,
    set_active_project: selectActiveProject,
  };
}

module.exports = { createProjectCapabilities };

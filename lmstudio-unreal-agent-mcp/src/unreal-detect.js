"use strict";

// Stable compatibility facade. Domain modules own behavior and state; this
// file intentionally contains no selection, discovery, registry, or build logic.
const { listUnrealProjects, setActiveProject } = require("./unreal-active-project");
const { buildProjectBrowsePaths } = require("./unreal-browse-metadata");
const { defaultPlatform, resolveBuildPlan } = require("./unreal-build-plan");
const {
  getActiveProject,
  loadConfig,
  resolveAgentWorkspaceRoot,
  saveConfig,
} = require("./unreal-config");
const {
  configuredEngineRootForAssociation,
  defaultEngineLocations,
  engineBuildToolCandidates,
  engineFolderFromAssociation,
  engineRootMatchesNumericAssociation,
  engineRootNumericVersion,
  resolveEngineBuildTool,
} = require("./unreal-engine-core");
const { findEngineInstalls } = require("./unreal-engine-registry");
const { resolveEngineRoot } = require("./unreal-engine-resolution");
const {
  discoverProjects,
  normalizeProjectName,
  resolveSearchRoots,
} = require("./unreal-project-discovery");
const { resolveExactProjectNameSelection } = require("./unreal-project-name-selection");
const { resolveProjectSelection } = require("./unreal-project-selection");
const {
  IGNORE_DIRS,
  pathIdentity,
  projectNameFromPath,
  searchRootDelimiter,
  splitSearchRoots,
  uniquePaths,
} = require("./unreal-project-core");

module.exports = {
  IGNORE_DIRS,
  buildProjectBrowsePaths,
  configuredEngineRootForAssociation,
  defaultEngineLocations,
  defaultPlatform,
  discoverProjects,
  engineBuildToolCandidates,
  engineFolderFromAssociation,
  engineRootMatchesNumericAssociation,
  engineRootNumericVersion,
  findEngineInstalls,
  getActiveProject,
  listUnrealProjects,
  loadConfig,
  normalizeProjectName,
  pathIdentity,
  projectNameFromPath,
  resolveAgentWorkspaceRoot,
  resolveBuildPlan,
  resolveEngineBuildTool,
  resolveEngineRoot,
  resolveExactProjectNameSelection,
  resolveProjectSelection,
  resolveSearchRoots,
  saveConfig,
  searchRootDelimiter,
  setActiveProject,
  splitSearchRoots,
  uniquePaths,
};

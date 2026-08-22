"use strict";

const {
  absolutePathIsWithin,
} = require("./filesystem-path-identity");
const {
  discoverProjects,
  normalizeProjectName,
} = require("./unreal-project-discovery");
const { isFixtureProjectPath } = require("./unreal-project-core");

const DEFAULT_PROJECT_SEARCH_DEPTH = 4;
const MAX_EXACT_PROJECT_SEARCH_DEPTH = 8;

function boundedExactProjectSearchDepth(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 1) return DEFAULT_PROJECT_SEARCH_DEPTH;
  return Math.min(Math.trunc(parsed), MAX_EXACT_PROJECT_SEARCH_DEPTH);
}

function projectNameSuggestion(project) {
  return {
    projectName: project.projectName,
    projectFile: project.projectFile,
    projectPath: project.projectPath,
    preferredTarget: project.preferredTarget,
  };
}

async function resolveExactProjectNameSelection(workspaceRoot, configPath, options = {}) {
  const requestedName = String(options.name ?? options.hint ?? "").trim();
  const normalizedName = normalizeProjectName(requestedName);
  const hostPlatform = options.hostPlatform || process.platform;
  const env = options.env || process.env;
  const maxDepth = boundedExactProjectSearchDepth(
    options.maxDepth ?? env.PROJECT_SEARCH_MAX_DEPTH,
  );
  const discovery = await discoverProjects(workspaceRoot, configPath, {
    ...options,
    env,
    maxDepth,
    includeActiveProjectRoot: false,
  });
  const rawCandidates = (discovery.rawProjects || discovery.projects).filter((project) => (
    !isFixtureProjectPath(project.projectPath)
    && discovery.roots.some((root) => (
      absolutePathIsWithin(project.projectPath, root, hostPlatform)
    ))
  ));
  const namedCandidates = rawCandidates.map((project) => ({
    project,
    normalizedName: normalizeProjectName(project.projectName),
  }));
  const exactMatches = normalizedName
    ? namedCandidates
      .filter((candidate) => candidate.normalizedName === normalizedName)
      .map((candidate) => candidate.project)
    : [];
  const base = {
    config: discovery.config,
    roots: discovery.roots,
    projects: discovery.projects,
    rawProjects: rawCandidates,
    normalizedName,
    maxDepth,
  };

  if (exactMatches.length === 1) {
    return { ...base, selected: exactMatches[0], selectionReason: "exact-project-name" };
  }
  if (exactMatches.length > 1) {
    return {
      ...base,
      selected: null,
      selectionReason: "exact-project-name-ambiguous",
      errorCode: "PROJECT_NAME_AMBIGUOUS",
      error: `Multiple projects exactly match name "${requestedName}" under configured search roots.`,
      suggestions: exactMatches.slice(0, 10).map(projectNameSuggestion),
    };
  }

  const partialSuggestions = normalizedName
    ? namedCandidates.filter((candidate) => (
      candidate.normalizedName
      && (
        candidate.normalizedName.includes(normalizedName)
        || normalizedName.includes(candidate.normalizedName)
      )
    )).map((candidate) => candidate.project)
    : [];
  return {
    ...base,
    selected: null,
    selectionReason: "exact-project-name-not-found",
    errorCode: "PROJECT_NAME_NOT_FOUND",
    error: `No project exactly matched name "${requestedName}" under configured search roots.`,
    suggestions: partialSuggestions.slice(0, 10).map(projectNameSuggestion),
  };
}

module.exports = {
  projectNameSuggestion,
  resolveExactProjectNameSelection,
};

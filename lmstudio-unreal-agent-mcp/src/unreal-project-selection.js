"use strict";

const path = require("node:path");
const { filesystemPathIdentity } = require("./filesystem-path-identity");
const {
  discoverProjects,
  normalizeProjectName,
  resolveSearchRoots,
  scoreProjectMatch,
} = require("./unreal-project-discovery");
const {
  exists,
  findTargetNames,
  pathIdentity,
  readUProject,
  statSafe,
} = require("./unreal-project-core");
const { projectNameSuggestion } = require("./unreal-project-name-selection");

async function resolveProjectSelection(workspaceRoot, configPath, options = {}) {
  const explicitProject = String(options.project || "").trim();
  const hint = String(options.hint || "").trim();
  const hostPlatform = options.hostPlatform || process.platform;
  const { config, roots } = resolveSearchRoots(workspaceRoot, configPath, options);

  const workspaceRelativePath = (value) => {
    const raw = String(value || "").trim();
    if (!raw) return "";
    return path.isAbsolute(raw) ? path.resolve(raw) : path.resolve(workspaceRoot, raw);
  };

  async function projectFromPath(projectPath, score = 1000) {
    const activePath = workspaceRelativePath(projectPath);
    const info = await readUProject(activePath);
    const targets = await findTargetNames(info.projectDir, info.projectName);
    const st = await statSafe(info.projectPath);
    return {
      ...info,
      ...targets,
      modifiedAt: st ? st.mtime.toISOString() : null,
      score,
    };
  }

  if (explicitProject && explicitProject.toLowerCase().endsWith(".uproject")) {
    const resolved = path.isAbsolute(explicitProject)
      ? path.resolve(explicitProject)
      : path.resolve(workspaceRoot, explicitProject);
    if (!(await exists(resolved))) {
      return {
        config,
        roots,
        projects: [],
        selected: null,
        selectionReason: "explicit-project-not-found",
        errorCode: "PROJECT_PATH_NOT_FOUND",
        error: `Explicit project does not exist: ${resolved}`,
      };
    }
    try {
      const selected = await projectFromPath(resolved, 3000);
      return { config, roots, projects: [selected], selected, selectionReason: "explicit.project" };
    } catch (error) {
      return {
        config,
        roots,
        projects: [],
        selected: null,
        selectionReason: "explicit-project-invalid",
        errorCode: "PROJECT_DESCRIPTOR_INVALID",
        error: `Could not read explicit project descriptor ${resolved}: ${error.message || error}`,
      };
    }
  }

  const discovery = await discoverProjects(workspaceRoot, configPath, options);
  // Keep physical clones for selection; discovery.projects is display-deduped.
  const projects = discovery.rawProjects || discovery.projects;
  const configuredActivePath = workspaceRelativePath(config.activeProject);
  const configuredActiveMatch = configuredActivePath
    ? projects.find((project) => (
      pathIdentity(project.projectPath, hostPlatform)
      === pathIdentity(configuredActivePath, hostPlatform)
    ))
    : null;

  if (projects.length === 0) {
    if (config.activeProject && await exists(configuredActivePath)) {
      try {
        const selected = await projectFromPath(configuredActivePath, 1000);
        return {
          config,
          roots,
          projects: [selected],
          selected,
          selectionReason: "config.activeProject",
        };
      } catch {
        // Fall through to a deterministic not-found result.
      }
    }
    return {
      config,
      roots,
      projects,
      selected: null,
      selectionReason: "none-found",
      error: "No .uproject files found under configured search roots.",
    };
  }

  if (hint) {
    const hintName = hint.replace(/\.uproject$/iu, "");
    const literalNameMatches = projects.filter((project) => project.projectName === hintName);
    if (literalNameMatches.length === 1) {
      return {
        config,
        roots,
        projects,
        selected: literalNameMatches[0],
        selectionReason: "hint",
      };
    }
    const normalizedHintName = normalizeProjectName(hintName);
    const exactNameMatches = literalNameMatches.length > 1
      ? literalNameMatches
      : normalizedHintName
        ? projects.filter((project) => (
          normalizeProjectName(project.projectName) === normalizedHintName
        ))
        : [];
    if (exactNameMatches.length === 1) {
      return {
        config,
        roots,
        projects,
        selected: exactNameMatches[0],
        selectionReason: "hint",
      };
    }
    if (exactNameMatches.length > 1) {
      if (configuredActiveMatch && exactNameMatches.some((project) => (
        pathIdentity(project.projectPath, hostPlatform)
        === pathIdentity(configuredActiveMatch.projectPath, hostPlatform)
      ))) {
        return {
          config,
          roots,
          projects,
          selected: configuredActiveMatch,
          selectionReason: "config.activeProject",
        };
      }
      return {
        config,
        roots,
        projects,
        selected: null,
        selectionReason: "hint-ambiguous",
        errorCode: "PROJECT_NAME_AMBIGUOUS",
        error: `Multiple projects exactly match hint "${hint}". Pass an explicit .uproject path or set config.activeProject.`,
        suggestions: exactNameMatches.slice(0, 10).map(projectNameSuggestion),
      };
    }
  }

  const scored = projects.map((project) => ({
    ...project,
    score: scoreProjectMatch(project, hint, workspaceRoot, options),
  }));
  scored.sort((left, right) => (
    right.score - left.score
    || (right.modifiedAt || "").localeCompare(left.modifiedAt || "")
  ));
  const best = scored[0];

  if (hint) {
    const hintIdentity = filesystemPathIdentity(hint, hostPlatform, { stripProjectUri: false });
    const normalizedHintName = normalizeProjectName(hint.replace(/\.uproject$/iu, ""));
    const hintMatches = scored.filter((project) => {
      const projectName = normalizeProjectName(project.projectName);
      const projectFile = normalizeProjectName(project.projectFile);
      const projectDir = pathIdentity(project.projectDir, hostPlatform);
      return Boolean(normalizedHintName && (
        projectName === normalizedHintName
        || projectFile === normalizedHintName
        || projectName.includes(normalizedHintName)
        || (hintIdentity && projectDir.includes(hintIdentity))
      ));
    });
    const hintMatch = hintMatches[0];
    if (hintMatch) {
      const selectedName = normalizeProjectName(hintMatch.projectName);
      const sameNameClones = hintMatches.filter((project) => (
        normalizeProjectName(project.projectName) === selectedName
      ));
      if (sameNameClones.length > 1) {
        if (configuredActiveMatch && sameNameClones.some((project) => (
          pathIdentity(project.projectPath, hostPlatform)
          === pathIdentity(configuredActiveMatch.projectPath, hostPlatform)
        ))) {
          return {
            config,
            roots,
            projects: scored,
            selected: configuredActiveMatch,
            selectionReason: "config.activeProject",
          };
        }
        return {
          config,
          roots,
          projects: scored,
          selected: null,
          selectionReason: "hint-ambiguous",
          errorCode: "PROJECT_NAME_AMBIGUOUS",
          error: `Multiple same-name projects match hint "${hint}". Pass an explicit .uproject path or set config.activeProject.`,
          suggestions: sameNameClones.slice(0, 10).map(projectNameSuggestion),
        };
      }
      return { config, roots, projects: scored, selected: hintMatch, selectionReason: "hint" };
    }
    return {
      config,
      roots,
      projects: scored,
      selected: null,
      selectionReason: "hint-not-matched",
      error: `No project matched hint "${hint}".`,
      suggestions: scored.slice(0, 10).map((project) => ({
        projectFile: project.projectFile,
        projectPath: project.projectPath,
        preferredTarget: project.preferredTarget,
      })),
    };
  }

  if (config.activeProject) {
    const activeMatch = configuredActiveMatch
      ? scored.find((project) => (
        pathIdentity(project.projectPath, hostPlatform)
        === pathIdentity(configuredActiveMatch.projectPath, hostPlatform)
      ))
      : null;
    if (activeMatch) {
      return {
        config,
        roots,
        projects: scored,
        selected: activeMatch,
        selectionReason: "config.activeProject",
      };
    }
    if (await exists(configuredActivePath)) {
      try {
        const selected = await projectFromPath(configuredActivePath, 1000);
        return {
          config,
          roots,
          projects: [selected, ...scored],
          selected,
          selectionReason: "config.activeProject",
        };
      } catch {
        // Fall through to discovered candidates.
      }
    }
  }

  if (!configuredActiveMatch && scored.length > 1) {
    const bestName = filesystemPathIdentity(best.projectName, hostPlatform, {
      stripProjectUri: false,
    });
    const sameNameClones = scored.filter((project) => (
      filesystemPathIdentity(project.projectName, hostPlatform, {
        stripProjectUri: false,
      }) === bestName
    ));
    if (sameNameClones.length > 1) {
      return {
        config,
        roots,
        projects: scored,
        selected: null,
        selectionReason: "same-name-ambiguous",
        errorCode: "PROJECT_NAME_AMBIGUOUS",
        error: "Multiple same-name Unreal projects found. Pass an explicit .uproject path or set config.activeProject.",
        suggestions: sameNameClones.slice(0, 10).map(projectNameSuggestion),
      };
    }
  }

  if (scored.length > 1 && scored[0].score === scored[1].score && scored[0].score <= 10) {
    return {
      config,
      roots,
      projects: scored,
      selected: null,
      selectionReason: "ambiguous",
      error: "Multiple Unreal projects found. Pass hint or set config.activeProject.",
      suggestions: scored.slice(0, 10).map((project) => ({
        projectFile: project.projectFile,
        projectPath: project.projectPath,
        preferredTarget: project.preferredTarget,
        modifiedAt: project.modifiedAt,
      })),
    };
  }

  return {
    config,
    roots,
    projects: scored,
    selected: best,
    selectionReason: scored.length === 1 ? "single-project" : "best-score",
  };
}

module.exports = { resolveProjectSelection };

"use strict";

const fs = require("node:fs");
const fsp = fs.promises;
const path = require("node:path");
const os = require("node:os");
const {
  absolutePathIsWithin,
  filesystemPathIdentity,
} = require("./filesystem-path-identity");
const { loadMergedConfig } = require("./unreal-config");
const {
  exists,
  findTargetNames,
  isFixtureProjectPath,
  pathIdentity,
  readUProject,
  shouldIgnoreDirName,
  splitSearchRoots,
  statSafe,
  uniquePaths,
} = require("./unreal-project-core");

function resolveSearchRoots(workspaceRoot, configPath, options = {}) {
  const hostPlatform = options.hostPlatform || process.platform;
  const env = options.env || process.env;
  const homeDirectory = options.homeDirectory || os.homedir();
  const config = loadMergedConfig(configPath);
  const fromEnv = splitSearchRoots(env.PROJECT_SEARCH_ROOTS, hostPlatform);
  const fromConfig = Array.isArray(config.projectSearchRoots)
    ? config.projectSearchRoots.map((root) => {
      if (!root || typeof root !== "string") return "";
      return path.isAbsolute(root)
        ? path.resolve(root)
        : path.resolve(workspaceRoot, root);
    })
    : [];
  const explicitRoots = [...fromEnv, ...fromConfig];
  const fallbackRoots = explicitRoots.length === 0
    ? [
      path.join(homeDirectory, "Documents", "Git"),
      path.join(homeDirectory, "Documents", "Unreal Projects"),
      path.join(homeDirectory, "Unreal Projects"),
    ]
    : [];
  const roots = uniquePaths([
    workspaceRoot,
    options.includeActiveProjectRoot === false || !env.ACTIVE_PROJECT
      ? ""
      : path.dirname(path.resolve(env.ACTIVE_PROJECT)),
    ...explicitRoots,
    ...fallbackRoots,
  ], hostPlatform);
  return { config, roots };
}

async function walkForUProjects(root, maxDepth, depth = 0, results = []) {
  if (depth > maxDepth || results.length >= 200) return results;
  const st = await statSafe(root);
  if (!st || !st.isDirectory()) return results;
  if (depth > 0 && shouldIgnoreDirName(path.basename(root))) return results;

  let entries = [];
  try {
    entries = await fsp.readdir(root, { withFileTypes: true });
  } catch {
    return results;
  }
  for (const entry of entries) {
    const full = path.join(root, entry.name);
    if (entry.isFile() && entry.name.toLowerCase().endsWith(".uproject")) {
      results.push(full);
    } else if (entry.isDirectory() && !shouldIgnoreDirName(entry.name)) {
      await walkForUProjects(full, maxDepth, depth + 1, results);
    }
  }
  return results;
}

function scoreProjectMatch(candidate, hint, workspaceRoot, options = {}) {
  const hostPlatform = options.hostPlatform || process.platform;
  const env = options.env || process.env;
  const hintIdentity = filesystemPathIdentity(hint, hostPlatform, { stripProjectUri: false });
  let score = 0;
  const projectDir = pathIdentity(candidate.projectDir, hostPlatform);
  const projectFile = filesystemPathIdentity(candidate.projectFile, hostPlatform, {
    stripProjectUri: false,
  });
  const projectName = filesystemPathIdentity(candidate.projectName, hostPlatform, {
    stripProjectUri: false,
  });

  const workspaceIdentity = workspaceRoot ? pathIdentity(workspaceRoot, hostPlatform) : "";
  if (workspaceIdentity && (
    projectDir === workspaceIdentity
    || absolutePathIsWithin(candidate.projectDir, workspaceRoot, hostPlatform)
  )) score += 20;

  if (env.ACTIVE_PROJECT) {
    const active = pathIdentity(env.ACTIVE_PROJECT, hostPlatform);
    if (pathIdentity(candidate.projectPath, hostPlatform) === active) score += 100;
  }

  if (hintIdentity) {
    if (projectName === hintIdentity) score += 80;
    if (projectFile === hintIdentity || projectFile === `${hintIdentity}.uproject`) score += 70;
    if (projectName.includes(hintIdentity)) score += 40;
    if (projectDir.includes(hintIdentity)) score += 30;
  } else if (filesystemPathIdentity(
    path.basename(candidate.projectDir),
    hostPlatform,
    { stripProjectUri: false },
  ) === projectName) {
    score += 10;
  }
  return score;
}

async function discoverProjects(workspaceRoot, configPath, options = {}) {
  const hostPlatform = options.hostPlatform || process.platform;
  const { config, roots } = resolveSearchRoots(workspaceRoot, configPath, options);
  const maxDepth = Number(options.maxDepth || process.env.PROJECT_SEARCH_MAX_DEPTH || 4);
  const found = new Map();

  for (const root of roots) {
    if (!(await exists(root))) continue;
    const matches = await walkForUProjects(root, maxDepth);
    for (const uprojectPath of matches) {
      found.set(pathIdentity(uprojectPath, hostPlatform), uprojectPath);
    }
  }

  const projects = [];
  for (const uprojectPath of found.values()) {
    if (isFixtureProjectPath(uprojectPath)) continue;
    try {
      const info = await readUProject(uprojectPath);
      const targets = await findTargetNames(info.projectDir, info.projectName);
      const st = await statSafe(info.projectPath);
      projects.push({
        ...info,
        ...targets,
        modifiedAt: st ? st.mtime.toISOString() : null,
        score: 0,
      });
    } catch {
      // Ignore malformed or concurrently removed descriptors.
    }
  }

  const byName = new Map();
  for (const project of projects) {
    const key = filesystemPathIdentity(project.projectName, hostPlatform, {
      stripProjectUri: false,
    });
    const existing = byName.get(key);
    if (!existing) {
      byName.set(key, project);
      continue;
    }
    const existingFixture = isFixtureProjectPath(existing.projectPath);
    const candidateFixture = isFixtureProjectPath(project.projectPath);
    if (existingFixture && !candidateFixture) {
      byName.set(key, project);
    } else if (existingFixture === candidateFixture
      && (project.modifiedAt || "").localeCompare(existing.modifiedAt || "") > 0) {
      byName.set(key, project);
    }
  }

  const deduped = [...byName.values()].sort((left, right) => (
    (right.modifiedAt || "").localeCompare(left.modifiedAt || "")
  ));
  const rawProjects = [...projects].sort((left, right) => {
    const byTime = (right.modifiedAt || "").localeCompare(left.modifiedAt || "");
    return byTime || String(left.projectPath || "").localeCompare(String(right.projectPath || ""));
  });
  return {
    config,
    roots,
    projects: deduped,
    rawProjects,
  };
}

function boundedUnicodeCasefold(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[\u00df\u1e9e]/gu, "ss")
    .replace(/\u03c2/gu, "\u03c3")
    .replace(/\u0345/gu, "\u03b9");
}

function normalizeProjectName(value) {
  const normalized = String(value || "")
    .normalize("NFKC")
    .trim()
    .replace(/\.uproject$/iu, "");
  return boundedUnicodeCasefold(normalized).replace(/[\s_-]+/gu, "");
}

module.exports = {
  discoverProjects,
  normalizeProjectName,
  resolveSearchRoots,
  scoreProjectMatch,
};

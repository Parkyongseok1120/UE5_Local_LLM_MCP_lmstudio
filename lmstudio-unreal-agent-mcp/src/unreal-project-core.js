"use strict";

const fs = require("node:fs");
const fsp = fs.promises;
const path = require("node:path");
const {
  canonicalAbsolutePathIdentity,
} = require("./filesystem-path-identity");

const IGNORE_DIRS = new Set([
  ".git",
  ".vs",
  ".idea",
  "Binaries",
  "DerivedDataCache",
  "Intermediate",
  "Saved",
  "node_modules",
  ".gradle",
  ".cache",
  ".pytest_cache",
  ".pytest_tmp",
  "wrapper_runs",
  "data",
  "local_holdout_fixtures",
  "text_snapshot",
  "editor_export_jobs",
]);

function pathIdentity(value, hostPlatform = process.platform) {
  return canonicalAbsolutePathIdentity(String(value || "."), hostPlatform);
}

function searchRootDelimiter(hostPlatform = process.platform) {
  return hostPlatform === "win32" ? ";" : ":";
}

function splitSearchRoots(value, hostPlatform = process.platform) {
  return String(value || "")
    .split(searchRootDelimiter(hostPlatform))
    .map((item) => item.trim())
    .filter(Boolean);
}

function uniquePaths(paths, hostPlatform = process.platform) {
  const seen = new Set();
  const result = [];
  for (const candidate of paths) {
    if (!candidate || typeof candidate !== "string") continue;
    const resolved = path.resolve(candidate);
    const key = pathIdentity(resolved, hostPlatform);
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(resolved);
  }
  return result;
}

async function exists(candidatePath) {
  try {
    await fsp.access(candidatePath);
    return true;
  } catch {
    return false;
  }
}

async function statSafe(candidatePath) {
  try {
    return await fsp.stat(candidatePath);
  } catch {
    return null;
  }
}

function projectNameFromPath(uprojectPath) {
  return path.basename(uprojectPath, ".uproject");
}

async function readUProject(uprojectPath) {
  const raw = await fsp.readFile(uprojectPath, "utf8");
  const data = JSON.parse(raw);
  const projectName = projectNameFromPath(uprojectPath);
  const modules = Array.isArray(data.Modules)
    ? data.Modules.map((module) => module.Name).filter(Boolean)
    : [];
  return {
    projectPath: path.resolve(uprojectPath),
    projectDir: path.dirname(path.resolve(uprojectPath)),
    projectFile: path.basename(uprojectPath),
    projectName,
    engineAssociation: data.EngineAssociation || null,
    modules,
  };
}

async function findTargetNames(projectDir, projectName) {
  const sourceDir = path.join(projectDir, "Source");
  const targets = new Set();
  if (await exists(sourceDir)) {
    const entries = await fsp.readdir(sourceDir, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isFile()) continue;
      const match = entry.name.match(/^(.+)\.Target\.cs$/i);
      if (match) targets.add(match[1]);
    }
  }

  const editorTarget = `${projectName}Editor`;
  const gameTarget = projectName;
  const preferredTarget = targets.has(editorTarget)
    ? editorTarget
    : [...targets].find((name) => /editor$/i.test(name))
      || [...targets][0]
      || editorTarget;
  return {
    preferredTarget,
    allTargets: [...targets].sort(),
    inferredGameTarget: targets.has(gameTarget) ? gameTarget : null,
  };
}

function shouldIgnoreDirName(name) {
  const lower = String(name || "").toLowerCase();
  return IGNORE_DIRS.has(name)
    || IGNORE_DIRS.has(lower)
    || lower.startsWith("pytest-")
    || lower.startsWith("pytest-of-");
}

function isFixtureProjectPath(projectPath) {
  const normalized = String(projectPath || "").replace(/\\/g, "/").toLowerCase();
  const segments = normalized.split("/").filter(Boolean);
  return (
    normalized.includes("/local_holdout_fixtures/")
    || normalized.includes("/data/local_holdout_fixtures/")
    || normalized.includes("/text_snapshot/")
    || /\/data\/[^/]+\/holdout/i.test(normalized)
    || segments.some((segment) => (
      segment === "test"
      || segment === "tests"
      || segment === "fixture"
      || segment === "fixtures"
      || /(?:^|[_-])fixtures?$/.test(segment)
    ))
  );
}

module.exports = {
  IGNORE_DIRS,
  exists,
  findTargetNames,
  isFixtureProjectPath,
  pathIdentity,
  projectNameFromPath,
  readUProject,
  searchRootDelimiter,
  shouldIgnoreDirName,
  splitSearchRoots,
  statSafe,
  uniquePaths,
};

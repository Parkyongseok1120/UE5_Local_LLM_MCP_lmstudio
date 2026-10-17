"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const { filesystemPathIdentity } = require("./filesystem-path-identity");
const { ENGINE_URI_PREFIX, isAllowedEngineRelative, isEngineUri } = require("./engine-source-access.js");

const PROJECT_PREFIXES = new Set(["Source", "Plugins", "Config", "Content", "Saved"]);
const WORKSPACE_PREFIXES = new Set([
  "scripts", "prompts", "docs", "config", "tests", "tools",
  "lmstudio-unreal-agent-mcp", "RAG_Project_Guidelines", ".agent"
]);

function isWithin(candidate, root) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

async function realpathOrResolved(value) {
  try {
    return await fsp.realpath(value);
  } catch {
    return path.resolve(value);
  }
}

async function nearestExistingRealpath(candidate) {
  let current = path.resolve(candidate);
  const tail = [];
  while (true) {
    try {
      const real = await fsp.realpath(current);
      return path.join(real, ...tail.reverse());
    } catch {
      const parent = path.dirname(current);
      if (parent === current) {
        return path.resolve(candidate);
      }
      tail.push(path.basename(current));
      current = parent;
    }
  }
}

function stripProjectNamePrefix(input, projectDir, hostPlatform = process.platform) {
  if (!projectDir) return input;
  const normalized = String(input || "").replace(/\\/g, "/");
  const projectName = path.basename(projectDir);
  const projectIdentity = filesystemPathIdentity(projectName, hostPlatform, {
    stripProjectUri: false,
  });
  const parts = normalized.split("/").filter(Boolean);
  const projectIndex = parts.findIndex(
    (part) => filesystemPathIdentity(part, hostPlatform, {
      stripProjectUri: false,
    }) === projectIdentity
  );
  if (projectIndex < 0 || projectIndex + 1 >= parts.length) {
    return input;
  }
  const projectRelativeParts = parts.slice(projectIndex + 1);
  if (!PROJECT_PREFIXES.has(projectRelativeParts[0])) {
    return input;
  }
  return projectRelativeParts.join("/");
}

function chooseRelativeRoot(input, workspaceRoot, projectDir) {
  const normalized = String(input || "").replace(/\\/g, "/").replace(/^\.\//, "");
  const first = normalized.split("/")[0];
  if (projectDir && PROJECT_PREFIXES.has(first)) {
    return { rootType: "active_project", root: projectDir, relative: normalized };
  }
  if (WORKSPACE_PREFIXES.has(first)) {
    return { rootType: "workspace", root: workspaceRoot, relative: normalized };
  }
  if (projectDir) {
    const projectCandidate = path.resolve(projectDir, normalized);
    if (fs.existsSync(projectCandidate)) {
      return { rootType: "active_project", root: projectDir, relative: normalized };
    }
  }
  return { rootType: "workspace", root: workspaceRoot, relative: normalized };
}

/** `resolveEngine(activeProject)` is supplied only by read-only callers. Without
 * it (mutation paths) `engine://` is rejected, so engine source stays read-only. */
async function resolveReadPath(input, { workspaceRoot, activeProject, resolveEngine } = {}) {
  if (!input || typeof input !== "string") {
    throw new Error("path must be a non-empty string");
  }
  const workspace = path.resolve(workspaceRoot || process.cwd());
  const projectDir = activeProject ? path.dirname(path.resolve(activeProject)) : null;
  let requested = input.trim();
  let selection;

  let engine = null;
  if (isEngineUri(requested)) {
    if (typeof resolveEngine !== "function") {
      throw new Error("engine:// is read-only and is not available for this operation");
    }
    engine = await resolveEngine(activeProject || null);
    selection = {
      rootType: "installed_engine",
      root: engine.engineRoot,
      relative: requested.slice(ENGINE_URI_PREFIX.length).replace(/^[/\\]+/, ""),
    };
  } else if (/^project:\/\//i.test(requested)) {
    if (!projectDir) {
      throw new Error("project:// requires an activeProject");
    }
    selection = {
      rootType: "active_project",
      root: projectDir,
      relative: requested.slice("project://".length).replace(/^[/\\]+/, ""),
    };
  } else if (/^workspace:\/\//i.test(requested)) {
    selection = {
      rootType: "workspace",
      root: workspace,
      relative: requested.slice("workspace://".length).replace(/^[/\\]+/, ""),
    };
  } else if (path.isAbsolute(requested)) {
    const absolute = path.resolve(requested);
    if (projectDir && isWithin(absolute, projectDir)) {
      selection = { rootType: "active_project", root: projectDir, relative: path.relative(projectDir, absolute) };
    } else if (isWithin(absolute, workspace)) {
      selection = { rootType: "workspace", root: workspace, relative: path.relative(workspace, absolute) };
    } else {
      throw new Error(`read path is outside WORKSPACE_ROOT and activeProject: ${input}`);
    }
  } else {
    requested = stripProjectNamePrefix(requested, projectDir);
    selection = chooseRelativeRoot(requested, workspace, projectDir);
  }

  const lexicalRoot = path.resolve(selection.root);
  const lexicalTarget = path.resolve(lexicalRoot, selection.relative || ".");
  if (!isWithin(lexicalTarget, lexicalRoot)) {
    throw new Error(`path escapes ${selection.rootType}: ${input}`);
  }

  const realRoot = await realpathOrResolved(lexicalRoot);
  const realTarget = await nearestExistingRealpath(lexicalTarget);
  if (!isWithin(realTarget, realRoot)) {
    throw new Error(`path escapes ${selection.rootType} through symlink/junction: ${input}`);
  }
  if (engine) {
    const stat = await fsp.stat(lexicalTarget).catch(() => null);
    const isFile = stat ? stat.isFile() : null;
    const lexicalRelative = path.relative(lexicalRoot, lexicalTarget).replace(/\\/g, "/");
    const realRelative = path.relative(realRoot, realTarget).replace(/\\/g, "/");
    // Both the requested and the symlink-resolved location must be readable
    // engine source; a link inside Source cannot reach other engine files.
    if (!isAllowedEngineRelative(lexicalRelative, { isFile }) || !isAllowedEngineRelative(realRelative, { isFile })) {
      throw new Error(`engine:// allows only Engine/Source/**, Engine/Plugins/**/Source/** and Engine/Build/Build.version `
        + `(.h .hpp .inl .cpp .cs .ini .json .version): ${input}`);
    }
  }

  return {
    absolutePath: lexicalTarget,
    realPath: realTarget,
    allowedRealRoot: realRoot,
    resolvedRootType: selection.rootType,
    projectRelativePath: projectDir && isWithin(lexicalTarget, projectDir)
      ? path.relative(projectDir, lexicalTarget).replace(/\\/g, "/")
      : null,
    workspaceRelativePath: isWithin(lexicalTarget, workspace)
      ? path.relative(workspace, lexicalTarget).replace(/\\/g, "/")
      : null,
    activeProject: activeProject || null,
    ...(engine ? { engineRelativePath: path.relative(lexicalRoot, lexicalTarget).replace(/\\/g, "/"),
      engineLexicalRoot: lexicalRoot, engineIdentity: engine.identity } : {}),
  };
}

async function assertReadChildContained(candidate, resolution) {
  const real = await nearestExistingRealpath(candidate);
  if (!isWithin(real, resolution.allowedRealRoot)) {
    throw new Error(`read path escapes ${resolution.resolvedRootType} through symlink/junction`);
  }
  if (resolution.resolvedRootType === "installed_engine") {
    // Directory traversal must enforce the same policy as an explicit file
    // read. Root containment alone would allow a search to read denied types
    // or links from Source into other directories of the installed engine.
    const stat = await fsp.stat(candidate).catch(() => null);
    const options = { isFile: stat ? stat.isFile() : null };
    const lexicalRelative = path.relative(resolution.engineLexicalRoot, candidate).replace(/\\/g, "/");
    const realRelative = path.relative(resolution.allowedRealRoot, real).replace(/\\/g, "/");
    if (!isAllowedEngineRelative(lexicalRelative, options) || !isAllowedEngineRelative(realRelative, options)) {
      const error = new Error("engine:// child is outside the readable source policy");
      error.code = "ENGINE_SOURCE_PATH_DENIED";
      throw error;
    }
  }
  return real;
}

function displayPath(resolution) {
  if (resolution.resolvedRootType === "installed_engine") {
    return `${ENGINE_URI_PREFIX}${resolution.engineRelativePath || ""}`;
  }
  if (resolution.resolvedRootType === "active_project") {
    return `project://${resolution.projectRelativePath || ""}`;
  }
  return `workspace://${resolution.workspaceRelativePath || ""}`;
}

function pathMetadata(resolution) {
  return {
    resolvedRootType: resolution.resolvedRootType,
    projectRelativePath: resolution.projectRelativePath,
    workspaceRelativePath: resolution.workspaceRelativePath,
    absolutePath: resolution.absolutePath,
    activeProject: resolution.activeProject,
    displayPath: displayPath(resolution),
    ...(resolution.resolvedRootType === "installed_engine"
      ? { evidenceSource: "installed_engine_source", readOnly: true, engineIdentity: resolution.engineIdentity }
      : {}),
  };
}

module.exports = {
  PROJECT_PREFIXES,
  WORKSPACE_PREFIXES,
  isWithin,
  stripProjectNamePrefix,
  resolveReadPath,
  assertReadChildContained,
  displayPath,
  pathMetadata,
};

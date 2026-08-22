"use strict";

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const cp = require("node:child_process");
const { promisify } = require("node:util");
const { atomicWriteText } = require("./atomic-io");
const { canonicalAbsolutePathIdentity } = require("./filesystem-path-identity");

const execFile = promisify(cp.execFile);

function sharedConfigPath() {
  if (process.env.SHARED_UNREAL_CONFIG) {
    return path.resolve(process.env.SHARED_UNREAL_CONFIG);
  }
  return path.join(os.homedir(), ".lmstudio", "config", "unreal-workspace.json");
}

function loadConfig(configPath) {
  try {
    return JSON.parse(fs.readFileSync(configPath, "utf8"));
  } catch {
    return {};
  }
}

function saveConfig(configPath, config) {
  if (canonicalAbsolutePathIdentity(configPath) === canonicalAbsolutePathIdentity(sharedConfigPath())) {
    throw new Error(
      "Shared Unreal config mutations are owned by project_controller.py; use setActiveProject.",
    );
  }
  fs.mkdirSync(path.dirname(configPath), { recursive: true });
  atomicWriteText(configPath, `${JSON.stringify(config, null, 2)}\n`, "utf8");
}

function loadSharedConfig() {
  return loadConfig(sharedConfigPath());
}

function loadMergedConfig(configPath) {
  const local = loadConfig(configPath);
  const shared = loadSharedConfig();
  const localEngineRoots = local.engineRootsByAssociation
    && typeof local.engineRootsByAssociation === "object"
    && !Array.isArray(local.engineRootsByAssociation)
    ? local.engineRootsByAssociation
    : {};
  const sharedEngineRoots = shared.engineRootsByAssociation
    && typeof shared.engineRootsByAssociation === "object"
    && !Array.isArray(shared.engineRootsByAssociation)
    ? shared.engineRootsByAssociation
    : {};
  return {
    ...local,
    ...shared,
    // Project-local source-build mappings override machine-wide fallbacks.
    engineRootsByAssociation: { ...sharedEngineRoots, ...localEngineRoots },
    // Match the Python/workspace resolver: an association-free project uses
    // the workspace-local default before the machine-wide shared fallback.
    defaultEngineRoot: String(local.defaultEngineRoot || "").trim()
      ? local.defaultEngineRoot
      : shared.defaultEngineRoot,
    projectSearchRoots: shared.projectSearchRoots?.length
      ? shared.projectSearchRoots
      : local.projectSearchRoots,
    // Shared config is the only activeProject owner. Missing and explicit null
    // both stay cleared; a stale local selection must never be resurrected.
    activeProject: Object.prototype.hasOwnProperty.call(shared, "activeProject")
      ? shared.activeProject
      : null,
  };
}

function getActiveProject(configPath) {
  return loadMergedConfig(configPath).activeProject || null;
}

function resolveAgentWorkspaceRoot(options = {}) {
  const env = options.env && typeof options.env === "object" ? options.env : process.env;
  const configured = String(env.UNREAL_WORKSPACE_ROOT || env.UNREAL58_ROOT || "").trim();
  if (configured) return path.resolve(configured);

  const repositoryRoot = path.resolve(options.repositoryRoot || path.join(__dirname, "..", ".."));
  if (fs.existsSync(path.join(repositoryRoot, "scripts", "project_controller.py"))) {
    return repositoryRoot;
  }

  const home = options.homeDir ? path.resolve(options.homeDir) : os.homedir();
  return path.join(home, ".lmstudio", "Unreal58-RAG");
}

async function invokeProjectController(argv) {
  const script = path.join(resolveAgentWorkspaceRoot(), "scripts", "project_controller.py");
  const python = process.env.PYTHON_EXE || (process.platform === "win32" ? "python" : "python3");
  try {
    const { stdout } = await execFile(python, [script, ...argv], {
      timeout: Number(process.env.PROJECT_CONTROLLER_TIMEOUT_MS || 600000),
      maxBuffer: 8 * 1024 * 1024,
    });
    return JSON.parse(stdout);
  } catch (error) {
    return { ok: false, error: error.message || String(error) };
  }
}

module.exports = {
  getActiveProject,
  invokeProjectController,
  loadConfig,
  loadMergedConfig,
  loadSharedConfig,
  resolveAgentWorkspaceRoot,
  saveConfig,
  sharedConfigPath,
};

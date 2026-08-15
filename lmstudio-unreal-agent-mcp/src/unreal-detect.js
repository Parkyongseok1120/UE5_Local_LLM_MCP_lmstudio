"use strict";

const fs = require("fs");
const fsp = fs.promises;
const path = require("path");
const os = require("os");
const cp = require("child_process");
const { promisify } = require("util");
const { atomicWriteText } = require("./atomic-io");
const {
  absolutePathIsWithin,
  canonicalAbsolutePathIdentity,
  filesystemPathIdentity,
} = require("./filesystem-path-identity");

const execFile = promisify(cp.execFile);

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
  "editor_export_jobs"
]);

const DEFAULT_EPIC_ROOT = path.join("C:", "Program Files", "Epic Games");
const DEFAULT_PROJECT_SEARCH_DEPTH = 4;
const MAX_EXACT_PROJECT_SEARCH_DEPTH = 8;
const MAX_ENGINE_REGISTRATION_BYTES = 2 * 1024 * 1024;
const WINDOWS_ENGINE_BUILDS_KEY = "HKCU\\SOFTWARE\\Epic Games\\Unreal Engine\\Builds";

function sharedConfigPath() {
  if (process.env.SHARED_UNREAL_CONFIG) {
    return path.resolve(process.env.SHARED_UNREAL_CONFIG);
  }
  return path.join(os.homedir(), ".lmstudio", "config", "unreal-workspace.json");
}

function loadSharedConfig() {
  return loadConfig(sharedConfigPath());
}

function saveSharedConfig(config) {
  const target = sharedConfigPath();
  const merged = { ...loadSharedConfig(), ...config, updatedAt: new Date().toISOString() };
  saveConfig(target, merged);
  return merged;
}

function loadMergedConfig(configPath) {
  const local = loadConfig(configPath);
  const shared = loadSharedConfig();
  const localEngineRoots = local.engineRootsByAssociation && typeof local.engineRootsByAssociation === "object"
    && !Array.isArray(local.engineRootsByAssociation)
    ? local.engineRootsByAssociation
    : {};
  const sharedEngineRoots = shared.engineRootsByAssociation && typeof shared.engineRootsByAssociation === "object"
    && !Array.isArray(shared.engineRootsByAssociation)
    ? shared.engineRootsByAssociation
    : {};
  return {
    ...local,
    ...shared,
    // A project-local source-build mapping must take precedence over a
    // machine-wide fallback so switching projects cannot retarget a GUID.
    engineRootsByAssociation: { ...sharedEngineRoots, ...localEngineRoots },
    projectSearchRoots: shared.projectSearchRoots?.length
      ? shared.projectSearchRoots
      : local.projectSearchRoots,
    activeProject: shared.activeProject ?? local.activeProject ?? null
  };
}

function loadConfig(configPath) {
  try {
    const raw = fs.readFileSync(configPath, "utf8");
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function saveConfig(configPath, config) {
  const directory = path.dirname(configPath);
  fs.mkdirSync(directory, { recursive: true });
  atomicWriteText(configPath, `${JSON.stringify(config, null, 2)}\n`, "utf8");
}

function getActiveProject(configPath) {
  const config = loadMergedConfig(configPath);
  return config.activeProject || null;
}

function resolveAgentWorkspaceRoot(options = {}) {
  const env = options.env && typeof options.env === "object" ? options.env : process.env;
  const configured = String(env.UNREAL_WORKSPACE_ROOT || env.UNREAL58_ROOT || "").trim();
  if (configured) return path.resolve(configured);

  // A packaged MCP is normally installed beside its workspace scripts. Prefer
  // that portable layout over a historical user-home clone so a second project
  // or checkout cannot accidentally execute another repository's controller.
  const repositoryRoot = path.resolve(options.repositoryRoot || path.join(__dirname, "..", ".."));
  if (fs.existsSync(path.join(repositoryRoot, "scripts", "project_controller.py"))) {
    return repositoryRoot;
  }

  // Retain the legacy location solely for older split installs that do not ship
  // scripts with the MCP. It is never the first choice for a current checkout.
  const home = options.homeDir ? path.resolve(options.homeDir) : os.homedir();
  return path.join(home, ".lmstudio", "Unreal58-RAG");
}

async function invokeProjectController(argv) {
  const script = path.join(resolveAgentWorkspaceRoot(), "scripts", "project_controller.py");
  const python = process.env.PYTHON_EXE || (process.platform === "win32" ? "python" : "python3");
  try {
    const { stdout } = await execFile(python, [script, ...argv], {
      timeout: Number(process.env.PROJECT_CONTROLLER_TIMEOUT_MS || 600000),
      maxBuffer: 8 * 1024 * 1024
    });
    return JSON.parse(stdout);
  } catch (error) {
    return { ok: false, error: error.message || String(error) };
  }
}

async function setActiveProject(workspaceRoot, configPath, options = {}) {
  const controllerInvoker = typeof options.invokeProjectController === "function"
    ? options.invokeProjectController
    : invokeProjectController;
  if (options.clear === true || options.projectPath === null) {
    const controller = await controllerInvoker(["--clear"]);
    if (controller.ok) {
      saveConfig(configPath, { ...loadConfig(configPath), activeProject: null });
    }
    return controller.ok ? controller : {
      ok: true,
      activeProject: null,
      message: "Active project cleared. Auto-detection will use hint or single-project heuristics again."
    };
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
    if (options.prepare === true) {
      argv.push("--prepare");
    }
    if (options.force === true) {
      argv.push("--force-prepare");
    }
    const controller = await controllerInvoker(argv);
    if (!controller.ok) {
      return controller;
    }
    saveConfig(configPath, { ...loadConfig(configPath), activeProject: resolved });
    const info = await readUProject(resolved);
    const targets = await findTargetNames(info.projectDir, info.projectName);
    return {
      ...controller,
      activeProject: resolved,
      projectName: info.projectName,
      preferredTarget: targets.preferredTarget,
      readiness: controller.readiness || null
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
      suggestions: selection.suggestions || []
    };
  }

  const argv = ["--switch", selection.selected.projectPath];
  if (options.prepare === true) {
    argv.push("--prepare");
  }
  const controller = await controllerInvoker(argv);
  if (!controller.ok) {
    return controller;
  }
  saveConfig(configPath, { ...loadConfig(configPath), activeProject: selection.selected.projectPath });
  return {
    ...controller,
    ok: true,
    activeProject: selection.selected.projectPath,
    projectName: selection.selected.projectName,
    preferredTarget: selection.selected.preferredTarget,
    selectionReason: selection.selectionReason,
    message: `Active project set to ${selection.selected.projectFile}`,
    readiness: controller.readiness || null
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
        : false
    }))
  };
}

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
  const out = [];
  for (const p of paths) {
    if (!p || typeof p !== "string") continue;
    const resolved = path.resolve(p);
    const key = pathIdentity(resolved, hostPlatform);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(resolved);
  }
  return out;
}

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
    ...fallbackRoots
  ], hostPlatform);
  return { config, roots };
}

async function exists(p) {
  try {
    await fsp.access(p);
    return true;
  } catch {
    return false;
  }
}

async function statSafe(p) {
  try {
    return await fsp.stat(p);
  } catch {
    return null;
  }
}

function projectNameFromPath(uprojectPath) {
  return path.basename(uprojectPath, ".uproject");
}

function engineFolderFromAssociation(value) {
  if (!value) return null;
  const text = String(value).trim();
  const match = text.match(/^(?:UE_)?(\d+(?:\.\d+)+)$/i);
  return match ? `UE_${match[1]}` : null;
}

function engineAssociationVersion(value) {
  return String(value || "").trim().match(/^(?:UE_)?(\d+(?:\.\d+)+)$/i)?.[1] || "";
}

function engineAssociationsMatch(left, right) {
  const leftText = String(left || "").trim();
  const rightText = String(right || "").trim();
  const leftVersion = engineAssociationVersion(leftText);
  const rightVersion = engineAssociationVersion(rightText);
  if (leftVersion || rightVersion) {
    return Boolean(leftVersion && rightVersion && leftVersion === rightVersion);
  }
  return Boolean(leftText && leftText === rightText);
}

function configuredEngineRootForAssociation(engineAssociation, config) {
  const association = String(engineAssociation || "").trim();
  const roots = config?.engineRootsByAssociation;
  if (!association || !roots || typeof roots !== "object" || Array.isArray(roots)) return "";
  if (!Object.prototype.hasOwnProperty.call(roots, association)) return "";
  return String(roots[association] || "").trim();
}

function engineAssociationUnresolved(engineAssociation, detail) {
  const association = String(engineAssociation || "").trim();
  return {
    engineRoot: "",
    buildTool: "",
    buildToolKind: "",
    buildBat: "",
    source: "",
    requestedEngineAssociation: association,
    warning: null,
    errorCode: "ENGINE_ASSOCIATION_UNRESOLVED",
    error: `ENGINE_ASSOCIATION_UNRESOLVED: EngineAssociation ${JSON.stringify(association)} ${detail}. `
      + "Set engineRoot, UNREAL_ENGINE_ROOT, or an exact engineRootsByAssociation entry."
  };
}

function engineRootUnresolved(detail) {
  return {
    engineRoot: "",
    buildTool: "",
    buildToolKind: "",
    buildBat: "",
    source: "",
    requestedEngineAssociation: "",
    warning: null,
    errorCode: "ENGINE_ROOT_UNRESOLVED",
    error: detail,
  };
}

function compareEngineFolders(left, right) {
  const parts = (value) => {
    const match = String(value || "").match(/UE[_ -]?(\d+(?:\.\d+)*)/i);
    return match ? match[1].split(".").map((part) => Number(part)) : [];
  };
  const leftParts = parts(left);
  const rightParts = parts(right);
  const length = Math.max(leftParts.length, rightParts.length);
  for (let index = 0; index < length; index += 1) {
    const delta = Number(leftParts[index] || 0) - Number(rightParts[index] || 0);
    if (delta !== 0) return delta;
  }
  return String(left || "").localeCompare(String(right || ""));
}

function engineBuildToolCandidates(engineRoot, hostPlatform = process.platform) {
  const batchRoot = path.join(engineRoot, "Engine", "Build", "BatchFiles");
  const ubtRoot = path.join(engineRoot, "Engine", "Binaries", "DotNET", "UnrealBuildTool");
  if (hostPlatform === "win32") {
    return [
      { path: path.join(ubtRoot, "UnrealBuildTool.exe"), kind: "ubt" },
      { path: path.join(batchRoot, "Build.bat"), kind: "build_bat" },
      { path: path.join(ubtRoot, "UnrealBuildTool.dll"), kind: "ubt_dotnet" },
    ];
  }
  const hostFolder = hostPlatform === "darwin" ? "Mac" : "Linux";
  return [
    { path: path.join(batchRoot, hostFolder, "Build.sh"), kind: "build_sh" },
    { path: path.join(batchRoot, "Build.sh"), kind: "build_sh" },
    { path: path.join(ubtRoot, "UnrealBuildTool.dll"), kind: "ubt_dotnet" },
    { path: path.join(ubtRoot, "UnrealBuildTool.exe"), kind: "ubt" },
  ];
}

async function resolveEngineBuildTool(engineRoot, hostPlatform = process.platform) {
  for (const candidate of engineBuildToolCandidates(engineRoot, hostPlatform)) {
    if (await exists(candidate.path)) return candidate;
  }
  return null;
}

function defaultEngineLocations(
  hostPlatform = process.platform,
  env = process.env,
  homeDirectory = os.homedir()
) {
  if (hostPlatform === "win32") {
    return uniquePaths([
      env.ProgramFiles ? path.join(env.ProgramFiles, "Epic Games") : "",
      env["ProgramFiles(x86)"] ? path.join(env["ProgramFiles(x86)"], "Epic Games") : "",
      DEFAULT_EPIC_ROOT,
    ], hostPlatform);
  }
  if (hostPlatform === "darwin") {
    return ["/Users/Shared/Epic Games", "/Applications/Epic Games"];
  }
  return uniquePaths([
    path.join(homeDirectory, "UnrealEngine"),
    path.join(homeDirectory, "Epic Games"),
    "/opt/UnrealEngine",
    "/opt/Epic Games",
  ], hostPlatform);
}

function applicationSettingsDirs(
  hostPlatform = process.platform,
  env = process.env,
  homeDirectory = os.homedir(),
) {
  const candidates = [];
  if (hostPlatform === "win32") {
    const programData = String(
      env.PROGRAMDATA || env.ProgramData || env.ALLUSERSPROFILE || "",
    ).trim();
    if (programData) candidates.push(path.join(programData, "Epic"));
  } else if (hostPlatform === "darwin") {
    if (homeDirectory) {
      candidates.push(path.join(homeDirectory, "Library", "Application Support", "Epic"));
    }
  } else {
    // FUnixPlatformProcess::ApplicationSettingsDir intentionally ignores
    // XDG_CONFIG_HOME and uses this fixed home-relative directory.
    if (homeDirectory) candidates.push(path.join(homeDirectory, ".config", "Epic"));
  }
  return uniquePaths(candidates, hostPlatform);
}

function defaultLauncherManifestPaths(hostPlatform, env, homeDirectory) {
  if (hostPlatform !== "win32") return [];
  return applicationSettingsDirs(hostPlatform, env, homeDirectory).map((root) => (
    path.join(root, "UnrealEngineLauncher", "LauncherInstalled.dat")
  ));
}

function defaultInstallIniPaths(hostPlatform, env, homeDirectory) {
  if (hostPlatform !== "darwin" && hostPlatform !== "linux") return [];
  return applicationSettingsDirs(hostPlatform, env, homeDirectory).map((root) => (
    path.join(root, "UnrealEngine", "Install.ini")
  ));
}

async function readBoundedRegistrationText(filePath) {
  try {
    const stat = await fsp.stat(filePath);
    if (!stat.isFile() || stat.size < 0 || stat.size > MAX_ENGINE_REGISTRATION_BYTES) return "";
    return (await fsp.readFile(filePath, "utf8")).replace(/^\uFEFF/u, "");
  } catch {
    return "";
  }
}

function validEngineAssociationToken(value) {
  const token = String(value || "").trim();
  if (!token || token.length > 256 || token === "." || token === ".." || token === "(Default)") {
    return "";
  }
  if (/[\u0000-\u001f/\\=\[\]]/u.test(token)) return "";
  return token;
}

function normalizedRegisteredEngineRoot(value) {
  let raw = String(value || "").trim();
  if (!raw || raw.length > 32767 || /[\u0000\r\n]/u.test(raw)) return "";
  if (raw.length >= 2 && raw[0] === raw[raw.length - 1] && (raw[0] === '"' || raw[0] === "'")) {
    raw = raw.slice(1, -1).trim();
  }
  if (!path.isAbsolute(raw)) return "";
  return path.resolve(raw);
}

function parseLauncherRegistrationRows(text) {
  let payload;
  try {
    payload = JSON.parse(String(text || ""));
  } catch {
    return [];
  }
  const installations = Array.isArray(payload?.InstallationList) ? payload.InstallationList : [];
  return installations
    .filter((item) => item && typeof item === "object" && !Array.isArray(item))
    .map((item) => ({
      association: item.AppName,
      engineRoot: item.InstallLocation,
      source: "launcher-manifest",
    }));
}

function parseInstallIniRegistrationRows(text) {
  const rows = [];
  let section = "";
  for (const rawLine of String(text || "").split(/\r?\n/u)) {
    const line = rawLine.trim();
    if (!line || line.startsWith(";") || line.startsWith("#")) continue;
    if (line.startsWith("[") && line.endsWith("]")) {
      section = line.slice(1, -1).trim();
      continue;
    }
    if (section !== "Installations") continue;
    const separator = line.indexOf("=");
    if (separator < 0) continue;
    rows.push({
      association: line.slice(0, separator),
      engineRoot: line.slice(separator + 1),
      source: "install-ini",
    });
  }
  return rows;
}

function mappingRegistrationRows(mapping, source) {
  const entries = mapping instanceof Map
    ? Array.from(mapping.entries())
    : mapping && typeof mapping === "object" && !Array.isArray(mapping)
      ? Object.entries(mapping)
      : [];
  return entries.map(([association, engineRoot]) => ({ association, engineRoot, source }));
}

function parseWindowsRegistryRegistrationRows(text) {
  const rows = [];
  for (const line of String(text || "").split(/\r?\n/u)) {
    const match = line.match(/^\s*(.*?)\s+REG_(?:SZ|EXPAND_SZ)\s+(.+?)\s*$/iu);
    if (!match) continue;
    rows.push({
      association: match[1],
      engineRoot: match[2],
      source: "windows-registry",
    });
  }
  return rows;
}

async function windowsRegistryRegistrationRows(options) {
  if (Object.prototype.hasOwnProperty.call(options, "registryInstallations")) {
    return mappingRegistrationRows(options.registryInstallations, "windows-registry");
  }
  if (typeof options.registryReader === "function") {
    try {
      return mappingRegistrationRows(await options.registryReader(), "windows-registry");
    } catch {
      return [];
    }
  }
  if (options.readSystemRegistry !== true || process.platform !== "win32") return [];
  try {
    const { stdout } = await execFile("reg.exe", ["query", WINDOWS_ENGINE_BUILDS_KEY], {
      windowsHide: true,
      maxBuffer: MAX_ENGINE_REGISTRATION_BYTES,
    });
    return parseWindowsRegistryRegistrationRows(stdout);
  } catch {
    return [];
  }
}

async function registeredEngineInstallations(options = {}) {
  const hostPlatform = options.hostPlatform || process.platform;
  const env = options.env || process.env;
  const homeDirectory = options.homeDirectory || os.homedir();
  const rows = [];

  if (hostPlatform === "win32") {
    const manifestPaths = Object.prototype.hasOwnProperty.call(options, "launcherManifestPaths")
      ? options.launcherManifestPaths || []
      : defaultLauncherManifestPaths(hostPlatform, env, homeDirectory);
    for (const manifestPath of manifestPaths) {
      rows.push(...parseLauncherRegistrationRows(await readBoundedRegistrationText(manifestPath)));
    }
    rows.push(...await windowsRegistryRegistrationRows(options));
  } else if (hostPlatform === "darwin" || hostPlatform === "linux") {
    const configPaths = Object.prototype.hasOwnProperty.call(options, "installIniPaths")
      ? options.installIniPaths || []
      : defaultInstallIniPaths(hostPlatform, env, homeDirectory);
    for (const configPath of configPaths) {
      rows.push(...parseInstallIniRegistrationRows(await readBoundedRegistrationText(configPath)));
    }
  }

  const installations = [];
  const seen = new Set();
  for (const row of rows) {
    const association = validEngineAssociationToken(row.association);
    const engineRoot = normalizedRegisteredEngineRoot(row.engineRoot);
    if (!association || !engineRoot) continue;
    const buildTool = await resolveEngineBuildTool(engineRoot, hostPlatform);
    if (!buildTool) continue;
    const key = `${association}\u0000${pathIdentity(engineRoot, hostPlatform)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    installations.push({
      association,
      engineRoot,
      buildTool: buildTool.path,
      buildToolKind: buildTool.kind,
      buildBat: buildTool.path,
      source: row.source,
    });
  }
  installations.sort((left, right) => (
    String(left.association).localeCompare(String(right.association))
    || pathIdentity(left.engineRoot, hostPlatform).localeCompare(
      pathIdentity(right.engineRoot, hostPlatform),
    )
  ));
  return installations;
}

function engineRootNumericVersion(engineRoot) {
  const versionPath = path.join(engineRoot, "Engine", "Build", "Build.version");
  try {
    const payload = JSON.parse(fs.readFileSync(versionPath, "utf8"));
    const major = Number(payload.MajorVersion);
    const minor = Number(payload.MinorVersion);
    if (Number.isInteger(major) && Number.isInteger(minor)) return `${major}.${minor}`;
  } catch {
    // Fall back to a conventional UE_<major>.<minor> directory name.
  }
  const match = path.basename(String(engineRoot || "")).match(/UE[_ -]?(\d+(?:\.\d+)*)/i);
  return match ? match[1] : "";
}

function engineRootMatchesNumericAssociation(engineRoot, association) {
  const requested = String(association || "").match(/^(?:UE_)?(\d+(?:\.\d+)+)$/i)?.[1] || "";
  const actual = engineRootNumericVersion(engineRoot);
  // Numeric associations are bindings.  If neither Build.version nor the
  // conventional engine folder name establishes a version, fail closed.
  return !requested || Boolean(actual && requested === actual);
}

async function findEngineInstalls(options = {}) {
  const hostPlatform = options.hostPlatform || process.platform;
  const env = options.env || process.env;
  const explicitEngineRoot = options.explicitEngineRoot ?? env.UNREAL_ENGINE_ROOT ?? "";
  const locations = options.roots || defaultEngineLocations(hostPlatform, env, options.homeDirectory);
  const installs = [];
  const byIdentity = new Map();

  const addInstall = async (candidateRoot, source, registration = null) => {
    if (!candidateRoot) return;
    const root = path.resolve(candidateRoot);
    const key = pathIdentity(root, hostPlatform);
    const existing = byIdentity.get(key);
    if (existing) {
      if (registration && !existing.registrations.some((item) => (
        item.association === registration.association && item.source === registration.source
      ))) {
        existing.registrations.push({
          association: registration.association,
          source: registration.source,
        });
      }
      return;
    }
    const buildTool = await resolveEngineBuildTool(root, hostPlatform);
    if (!buildTool) return;
    const install = {
      engineRoot: root,
      folderName: path.basename(root),
      numericVersion: engineRootNumericVersion(root),
      buildTool: buildTool.path,
      buildToolKind: buildTool.kind,
      buildBat: buildTool.path,
      source,
      registrations: registration
        ? [{ association: registration.association, source: registration.source }]
        : [],
    };
    byIdentity.set(key, install);
    installs.push(install);
  };

  await addInstall(explicitEngineRoot, "environment");
  const injectedRegistrationContext = (
    Object.prototype.hasOwnProperty.call(options, "hostPlatform")
    || Object.prototype.hasOwnProperty.call(options, "env")
    || Object.prototype.hasOwnProperty.call(options, "homeDirectory")
    || Object.prototype.hasOwnProperty.call(options, "roots")
    || Object.prototype.hasOwnProperty.call(options, "launcherManifestPaths")
    || Object.prototype.hasOwnProperty.call(options, "registryInstallations")
    || Object.prototype.hasOwnProperty.call(options, "installIniPaths")
    || typeof options.registryReader === "function"
  );
  const readSystemRegistry = options.readSystemRegistry === undefined
    ? !injectedRegistrationContext
    : options.readSystemRegistry === true;
  const registrations = await registeredEngineInstallations({
    ...options,
    hostPlatform,
    env,
    readSystemRegistry,
  });
  for (const registration of registrations) {
    await addInstall(registration.engineRoot, registration.source, registration);
  }
  for (const location of locations) {
    if (!(await exists(location))) continue;
    await addInstall(location, "common-location");
    let entries;
    try {
      entries = await fsp.readdir(location, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (!entry.isDirectory() || !/^UE_/i.test(entry.name)) continue;
      await addInstall(path.join(location, entry.name), "discovered");
    }
  }

  installs.sort((a, b) => compareEngineFolders(
    a.numericVersion ? `UE_${a.numericVersion}` : a.folderName,
    b.numericVersion ? `UE_${b.numericVersion}` : b.folderName,
  ));
  return installs;
}

async function resolveEngineRoot(engineAssociation, config, explicitEngineRoot, options = {}) {
  const hostPlatform = options.hostPlatform || process.platform;
  const env = options.env || process.env;
  const association = String(engineAssociation || "").trim();
  const requestedFolder = engineFolderFromAssociation(association);
  const configBase = options.workspaceRoot || process.cwd();
  const injectedRegistrationContext = (
    Object.prototype.hasOwnProperty.call(options, "hostPlatform")
    || Object.prototype.hasOwnProperty.call(options, "env")
    || Object.prototype.hasOwnProperty.call(options, "homeDirectory")
    || Object.prototype.hasOwnProperty.call(options, "roots")
    || Object.prototype.hasOwnProperty.call(options, "launcherManifestPaths")
    || Object.prototype.hasOwnProperty.call(options, "registryInstallations")
    || Object.prototype.hasOwnProperty.call(options, "installIniPaths")
    || typeof options.registryReader === "function"
  );
  const readSystemRegistry = options.readSystemRegistry === undefined
    ? !injectedRegistrationContext
    : options.readSystemRegistry === true;
  const discoveryOptions = {
    ...options,
    hostPlatform,
    env,
    readSystemRegistry,
  };

  const resolveCandidate = async (value, source, { relativeToConfig = false } = {}) => {
    const raw = String(value || "").trim();
    if (!raw) return null;
    const resolved = path.isAbsolute(raw)
      ? path.resolve(raw)
      : path.resolve(relativeToConfig ? configBase : process.cwd(), raw);
    const buildTool = await resolveEngineBuildTool(resolved, hostPlatform);
    if (!buildTool) return null;
    return {
      engineRoot: resolved,
      buildTool: buildTool.path,
      buildToolKind: buildTool.kind,
      buildBat: buildTool.path,
      source,
      requestedEngineAssociation: association,
      warning: source === "environment" && association
        ? `Using UNREAL_ENGINE_ROOT for EngineAssociation ${association}.`
        : null,
    };
  };

  const explicit = await resolveCandidate(explicitEngineRoot, "argument");
  if (explicit) return explicit;
  if (explicitEngineRoot && association) {
    return engineAssociationUnresolved(association, "could not use the explicit engineRoot");
  }

  const environmentEngineRoot = String(env.UNREAL_ENGINE_ROOT || "").trim();
  const environment = await resolveCandidate(environmentEngineRoot, "environment");
  const environmentAssociationIsManaged = Object.prototype.hasOwnProperty.call(
    env,
    "UNREAL_ENGINE_ROOT_ASSOCIATION",
  );
  const environmentAssociation = String(env.UNREAL_ENGINE_ROOT_ASSOCIATION || "").trim();
  const staleManagedEnvironment = Boolean(
    association
    && environmentAssociationIsManaged
    && !engineAssociationsMatch(environmentAssociation, association)
  );
  const staleNumericEnvironment = Boolean(
    environment
    && requestedFolder
    && !engineRootMatchesNumericAssociation(environment.engineRoot, association)
  );
  const sameResolvedRoot = (left, right) => Boolean(
    left?.engineRoot
    && right?.engineRoot
    && pathIdentity(left.engineRoot, hostPlatform) === pathIdentity(right.engineRoot, hostPlatform)
  );

  if (association) {
    const mappedEngineRoot = configuredEngineRootForAssociation(association, config);
    if (mappedEngineRoot) {
      const mapped = await resolveCandidate(
        mappedEngineRoot,
        "config.engineRootsByAssociation",
        { relativeToConfig: true },
      );
      if (mapped) {
        return !staleManagedEnvironment && sameResolvedRoot(environment, mapped)
          ? environment
          : mapped;
      }
      return engineAssociationUnresolved(
        association,
        "has an engineRootsByAssociation entry that is not a usable engine root",
      );
    }

    const installs = await findEngineInstalls(discoveryOptions);
    const registeredMatches = [];
    const registeredRoots = new Set();
    for (const install of installs) {
      const registration = (install.registrations || []).find((item) => (
        engineAssociationsMatch(item.association, association)
      ));
      if (!registration) continue;
      if (requestedFolder && !engineRootMatchesNumericAssociation(install.engineRoot, association)) {
        continue;
      }
      const key = pathIdentity(install.engineRoot, hostPlatform);
      if (registeredRoots.has(key)) continue;
      registeredRoots.add(key);
      registeredMatches.push({ install, registration });
    }
    if (registeredMatches.length > 1) {
      return engineAssociationUnresolved(
        association,
        "has multiple conflicting registered engine roots",
      );
    }
    if (registeredMatches.length === 1) {
      const { install, registration } = registeredMatches[0];
      const registered = {
        engineRoot: install.engineRoot,
        buildTool: install.buildTool,
        buildToolKind: install.buildToolKind,
        buildBat: install.buildBat,
        source: `registered.${registration.source}`,
        requestedEngineAssociation: association,
        warning: null,
      };
      return !staleManagedEnvironment && sameResolvedRoot(environment, registered)
        ? environment
        : registered;
    }

    if (environment && !staleManagedEnvironment && !staleNumericEnvironment) return environment;
    if (environmentEngineRoot && !staleManagedEnvironment && !staleNumericEnvironment) {
      return engineAssociationUnresolved(association, "could not use UNREAL_ENGINE_ROOT");
    }

    // A numeric association is allowed to discover only its exact install;
    // a custom/GUID association must be bound explicitly or by the mapping.
    if (!requestedFolder) {
      return engineAssociationUnresolved(
        association,
        "is a custom/source-build identifier without an exact mapping",
      );
    }

    const requestedKey = filesystemPathIdentity(requestedFolder, hostPlatform, {
      stripProjectUri: false,
    });
    const exact = installs.find((item) => filesystemPathIdentity(
      item.folderName,
      hostPlatform,
      { stripProjectUri: false },
    ) === requestedKey);
    if (exact) {
      return {
        engineRoot: exact.engineRoot,
        buildTool: exact.buildTool,
        buildToolKind: exact.buildToolKind,
        buildBat: exact.buildBat,
        source: "EngineAssociation",
        requestedEngineAssociation: association,
        warning: null,
      };
    }
    return engineAssociationUnresolved(
      association,
      `does not have an installed ${requestedFolder} engine`,
    );
  }

  if (environment) return environment;

  const configured = await resolveCandidate(
    config?.defaultEngineRoot,
    "config.defaultEngineRoot",
    { relativeToConfig: true },
  );
  if (configured) return configured;

  const installs = await findEngineInstalls(discoveryOptions);
  const fallback = installs[installs.length - 1];
  if (fallback) {
    return {
      engineRoot: fallback.engineRoot,
      buildTool: fallback.buildTool,
      buildToolKind: fallback.buildToolKind,
      buildBat: fallback.buildBat,
      source: fallback.source === "environment" ? "environment" : "latest-installed",
      requestedEngineAssociation: "",
      warning: null,
    };
  }

  return engineRootUnresolved(
    "Could not resolve Unreal Engine installation. Set engineRoot, UNREAL_ENGINE_ROOT, or config.defaultEngineRoot.",
  );
}

async function readUProject(uprojectPath) {
  const raw = await fsp.readFile(uprojectPath, "utf8");
  const data = JSON.parse(raw);
  const projectName = projectNameFromPath(uprojectPath);
  const modules = Array.isArray(data.Modules) ? data.Modules.map((m) => m.Name).filter(Boolean) : [];
  return {
    projectPath: path.resolve(uprojectPath),
    projectDir: path.dirname(path.resolve(uprojectPath)),
    projectFile: path.basename(uprojectPath),
    projectName,
    engineAssociation: data.EngineAssociation || null,
    modules
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
  const preferred = targets.has(editorTarget)
    ? editorTarget
    : [...targets].find((name) => /editor$/i.test(name))
      || [...targets][0]
      || editorTarget;

  return {
    preferredTarget: preferred,
    allTargets: [...targets].sort(),
    inferredGameTarget: targets.has(gameTarget) ? gameTarget : null
  };
}

function shouldIgnoreDirName(name) {
  const lower = String(name || "").toLowerCase();
  return IGNORE_DIRS.has(name) || IGNORE_DIRS.has(lower) || lower.startsWith("pytest-") || lower.startsWith("pytest-of-");
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

async function walkForUProjects(root, maxDepth, depth = 0, results = []) {
  if (depth > maxDepth || results.length >= 200) return results;
  const st = await statSafe(root);
  if (!st || !st.isDirectory()) return results;

  const base = path.basename(root);
  if (depth > 0 && shouldIgnoreDirName(base)) return results;

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
      continue;
    }
    if (entry.isDirectory()) {
      if (shouldIgnoreDirName(entry.name)) continue;
      await walkForUProjects(full, maxDepth, depth + 1, results);
    }
  }
  return results;
}

function scoreProjectMatch(candidate, hint, workspaceRoot, options = {}) {
  const hostPlatform = options.hostPlatform || process.platform;
  const env = options.env || process.env;
  const hintIdentity = filesystemPathIdentity(hint, hostPlatform, {
    stripProjectUri: false,
  });
  let score = 0;
  const projectDir = pathIdentity(candidate.projectDir, hostPlatform);
  const projectFile = filesystemPathIdentity(candidate.projectFile, hostPlatform, {
    stripProjectUri: false,
  });
  const projectName = filesystemPathIdentity(candidate.projectName, hostPlatform, {
    stripProjectUri: false,
  });

  const workspaceIdentity = workspaceRoot ? pathIdentity(workspaceRoot, hostPlatform) : "";
  if (
    workspaceIdentity
    && (
      projectDir === workspaceIdentity
      || absolutePathIsWithin(candidate.projectDir, workspaceRoot, hostPlatform)
    )
  ) {
    score += 20;
  }

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
    if (isFixtureProjectPath(uprojectPath)) {
      continue;
    }
    try {
      const info = await readUProject(uprojectPath);
      const targets = await findTargetNames(info.projectDir, info.projectName);
      const st = await statSafe(info.projectPath);
      projects.push({
        ...info,
        ...targets,
        modifiedAt: st ? st.mtime.toISOString() : null,
        score: 0
      });
    } catch {
      // skip invalid uproject
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
      continue;
    }
    if (!existingFixture && candidateFixture) {
      continue;
    }
    const existingTime = existing.modifiedAt || "";
    const candidateTime = project.modifiedAt || "";
    if (candidateTime.localeCompare(existingTime) > 0) {
      byName.set(key, project);
    }
  }

  const deduped = Array.from(byName.values());
  deduped.sort((a, b) => {
    const timeA = a.modifiedAt || "";
    const timeB = b.modifiedAt || "";
    return timeB.localeCompare(timeA);
  });

  const rawProjects = [...projects].sort((a, b) => {
    const timeA = a.modifiedAt || "";
    const timeB = b.modifiedAt || "";
    if (timeB !== timeA) return timeB.localeCompare(timeA);
    return String(a.projectPath || "").localeCompare(String(b.projectPath || ""));
  });

  return { config, roots, projects: deduped, rawProjects };
}

function boundedUnicodeCasefold(value) {
  // JavaScript has locale-independent lowercasing but no Unicode casefold API.
  // NFKC (applied by the caller) handles compatibility characters; these are
  // the remaining common folds that change project-name identity compared with
  // Python's str.casefold(). Keep this deterministic and locale-independent.
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
    options.maxDepth ?? env.PROJECT_SEARCH_MAX_DEPTH
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
    return {
      ...base,
      selected: exactMatches[0],
      selectionReason: "exact-project-name",
    };
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
    ? namedCandidates
      .filter((candidate) => (
        candidate.normalizedName
        && (
          candidate.normalizedName.includes(normalizedName)
          || normalizedName.includes(candidate.normalizedName)
        )
      ))
      .map((candidate) => candidate.project)
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
      return {
        config,
        roots,
        projects: [selected],
        selected,
        selectionReason: "explicit.project",
      };
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
  // Selection must retain every physical candidate.  The display-oriented
  // list is deduplicated by project name and would otherwise turn two clones
  // into one arbitrary (mtime-dependent) build target.
  const projects = discovery.rawProjects || discovery.projects;
  const configuredActivePath = workspaceRelativePath(config.activeProject);
  const configuredActiveMatch = configuredActivePath
    ? projects.find((project) => (
      pathIdentity(project.projectPath, hostPlatform)
      === pathIdentity(configuredActivePath, hostPlatform)
    ))
    : null;

  if (projects.length === 0) {
    if (config.activeProject) {
      const activePath = configuredActivePath;
      if (await exists(activePath)) {
        try {
          const selected = await projectFromPath(activePath, 1000);
          return {
            config,
            roots,
            projects: [selected],
            selected,
            selectionReason: "config.activeProject",
          };
        } catch {
          // no project
        }
      }
    }
    return {
      config,
      roots,
      projects,
      selected: null,
      selectionReason: "none-found",
      error: "No .uproject files found under configured search roots."
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
    score: scoreProjectMatch(project, hint, workspaceRoot, options)
  }));

  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return (b.modifiedAt || "").localeCompare(a.modifiedAt || "");
  });

  const best = scored[0];
  if (hint) {
    const hintIdentity = filesystemPathIdentity(hint, hostPlatform, {
      stripProjectUri: false,
    });
    const normalizedHintName = normalizeProjectName(hint.replace(/\.uproject$/iu, ""));
    const hintMatches = scored.filter((project) => {
      const projectName = normalizeProjectName(project.projectName);
      const projectFile = normalizeProjectName(project.projectFile);
      const projectDir = pathIdentity(project.projectDir, hostPlatform);
      return Boolean(
        normalizedHintName
        && (
          projectName === normalizedHintName
          || projectFile === normalizedHintName
          || projectName.includes(normalizedHintName)
          || (hintIdentity && projectDir.includes(hintIdentity))
        )
      );
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
      return {
        config,
        roots,
        projects: scored,
        selected: hintMatch,
        selectionReason: "hint",
      };
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
    const activePath = configuredActivePath;
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
    if (await exists(activePath)) {
      try {
        const selected = await projectFromPath(activePath, 1000);
        return {
          config,
          roots,
          projects: [selected, ...scored],
          selected,
          selectionReason: "config.activeProject",
        };
      } catch {
        // Fall through to best discovered project.
      }
    }
  }

  if (!hint && !configuredActiveMatch && scored.length > 1) {
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

  if (!hint && scored.length > 1 && scored[0].score === scored[1].score && scored[0].score <= 10) {
    return {
      config,
      roots,
      projects: scored,
      selected: null,
      selectionReason: "ambiguous",
      error: "Multiple Unreal projects found. Pass hint or set config.activeProject.",
      suggestions: scored.slice(0, 10).map((p) => ({
        projectFile: p.projectFile,
        projectPath: p.projectPath,
        preferredTarget: p.preferredTarget,
        modifiedAt: p.modifiedAt
      }))
    };
  }

  return {
    config,
    roots,
    projects: scored,
    selected: best,
    selectionReason: hint ? "hint-match" : scored.length === 1 ? "single-project" : "best-score"
  };
}

async function resolveBuildPlan(workspaceRoot, configPath, args = {}) {
  const selection = await resolveProjectSelection(workspaceRoot, configPath, {
    hint: args.hint,
    project: args.project,
    maxDepth: args.searchMaxDepth
  });

  if (!selection.selected) {
    return {
      ok: false,
      ...selection
    };
  }

  const project = selection.selected;
  const projectPath = project.projectPath;

  const engine = await resolveEngineRoot(
    project.engineAssociation,
    selection.config,
    args.engineRoot,
    { workspaceRoot }
  );

  if (!engine || engine.errorCode) {
    return {
      ok: false,
      ...selection,
      requestedEngineAssociation: engine?.requestedEngineAssociation || project.engineAssociation || null,
      errorCode: engine?.errorCode || "ENGINE_ROOT_UNRESOLVED",
      error: engine?.error || "Could not resolve Unreal Engine installation. Set engineRoot or config.defaultEngineRoot."
    };
  }

  const target = String(args.target || project.preferredTarget || `${project.projectName}Editor`).trim();
  const platform = String(
    args.platform
    || selection.config.defaultPlatform
    || process.env.UNREAL_PLATFORM
    || defaultPlatform()
  ).trim();
  const configuration = String(
    args.configuration
    || selection.config.defaultConfiguration
    || process.env.UNREAL_CONFIGURATION
    || "Development"
  ).trim();

  return {
    ok: true,
    ...selection,
    build: {
      engineRoot: engine.engineRoot,
      buildTool: engine.buildTool,
      buildToolKind: engine.buildToolKind,
      buildBat: engine.buildBat,
      engineSource: engine.source,
      engineWarning: engine.warning,
      requestedEngineAssociation: engine.requestedEngineAssociation || project.engineAssociation,
      projectPath,
      projectFile: path.basename(projectPath),
      projectDir: path.dirname(projectPath),
      projectName: projectNameFromPath(projectPath),
      target,
      platform,
      configuration,
      allTargets: project.allTargets,
      engineAssociation: project.engineAssociation
    }
  };
}

function defaultPlatform(hostPlatform = process.platform) {
  if (hostPlatform === "win32") return "Win64";
  if (hostPlatform === "darwin") return "Mac";
  return "Linux";
}

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
    const raw = fs.readFileSync(resolvedProject, "utf8");
    const data = JSON.parse(raw);
    modules = Array.isArray(data.Modules) ? data.Modules.map((m) => m.Name).filter(Boolean) : [];
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
  const rel = (target) => {
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
    sourceBrowsePath: browseAvailable ? rel(sourceRoot) : "",
    contentBrowsePath: browseAvailable ? rel(contentRoot) : "",
    browseNote: browseAvailable
      ? ""
      : "Project is outside WORKSPACE_ROOT; search_files/list_directory may be unavailable."
  };
}

module.exports = {
  IGNORE_DIRS,
  loadConfig,
  saveConfig,
  getActiveProject,
  setActiveProject,
  listUnrealProjects,
  pathIdentity,
  searchRootDelimiter,
  splitSearchRoots,
  uniquePaths,
  resolveSearchRoots,
  normalizeProjectName,
  defaultEngineLocations,
  engineBuildToolCandidates,
  resolveEngineBuildTool,
  engineRootNumericVersion,
  engineRootMatchesNumericAssociation,
  engineFolderFromAssociation,
  configuredEngineRootForAssociation,
  resolveEngineRoot,
  findEngineInstalls,
  discoverProjects,
  resolveExactProjectNameSelection,
  resolveProjectSelection,
  resolveBuildPlan,
  defaultPlatform,
  resolveAgentWorkspaceRoot,
  projectNameFromPath,
  buildProjectBrowsePaths
};

"use strict";

const fs = require("node:fs");
const fsp = fs.promises;
const path = require("node:path");
const os = require("node:os");
const cp = require("node:child_process");
const { promisify } = require("node:util");
const {
  compareEngineFolders,
  defaultEngineLocations,
  engineRootNumericVersion,
  resolveEngineBuildTool,
} = require("./unreal-engine-core");
const {
  exists,
  pathIdentity,
  uniquePaths,
} = require("./unreal-project-core");

const execFile = promisify(cp.execFile);
const MAX_ENGINE_REGISTRATION_BYTES = 2 * 1024 * 1024;
const WINDOWS_ENGINE_BUILDS_KEY = "HKCU\\SOFTWARE\\Epic Games\\Unreal Engine\\Builds";

function applicationSettingsDirs(hostPlatform, env, homeDirectory) {
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
  } else if (homeDirectory) {
    // Mirrors FUnixPlatformProcess::ApplicationSettingsDir.
    candidates.push(path.join(homeDirectory, ".config", "Epic"));
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
  return /[\u0000-\u001f/\\=\[\]]/u.test(token) ? "" : token;
}

function normalizedRegisteredEngineRoot(value) {
  let raw = String(value || "").trim();
  if (!raw || raw.length > 32767 || /[\u0000\r\n]/u.test(raw)) return "";
  if (raw.length >= 2 && raw[0] === raw[raw.length - 1] && (raw[0] === '"' || raw[0] === "'")) {
    raw = raw.slice(1, -1).trim();
  }
  return path.isAbsolute(raw) ? path.resolve(raw) : "";
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
    if (match) {
      rows.push({ association: match[1], engineRoot: match[2], source: "windows-registry" });
    }
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

function usesInjectedRegistrationContext(options) {
  return [
    "hostPlatform",
    "env",
    "homeDirectory",
    "roots",
    "launcherManifestPaths",
    "registryInstallations",
    "installIniPaths",
  ].some((key) => Object.prototype.hasOwnProperty.call(options, key))
    || typeof options.registryReader === "function";
}

async function findEngineInstalls(options = {}) {
  const hostPlatform = options.hostPlatform || process.platform;
  const env = options.env || process.env;
  const explicitEngineRoot = options.explicitEngineRoot ?? env.UNREAL_ENGINE_ROOT ?? "";
  const locations = options.roots
    || defaultEngineLocations(hostPlatform, env, options.homeDirectory);
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
  const readSystemRegistry = options.readSystemRegistry === undefined
    ? !usesInjectedRegistrationContext(options)
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
      if (entry.isDirectory() && /^UE_/i.test(entry.name)) {
        await addInstall(path.join(location, entry.name), "discovered");
      }
    }
  }

  installs.sort((left, right) => compareEngineFolders(
    left.numericVersion ? `UE_${left.numericVersion}` : left.folderName,
    right.numericVersion ? `UE_${right.numericVersion}` : right.folderName,
  ));
  return installs;
}

module.exports = { findEngineInstalls };
